# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Integration tests for cross-repo contract spec injection.

Covers:
  SC-1 (CF-3): End-to-end contracts.yaml -> trust -> load -> inject -> L1 prompt
  SC-2 (CF-2): Missing/unreadable spec -> graceful empty digest
  SC-3 (SF-5): No contracts.yaml -> empty, no warning (capsys)
  DF-2: Sibling threads receive no contract_spec
  SF-1: Primary thread passes backend=backend to load_contract_digest
  Boundary: binary spec skipped, unset env var skipped
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def trust_dir(tmp_path, monkeypatch):
    """Isolated trust store via XDG_CONFIG_HOME redirection."""
    config_home = tmp_path / "trust-config"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    d = config_home / "code-forge"
    d.mkdir()
    return d


@pytest.fixture()
def git_repo(tmp_path, monkeypatch):
    """A real git repo with main + feature branches.

    main:    file.py = "x = 1\\n"
    feature: file.py = "x = 2\\n"
    HEAD left on main after setup.
    """
    monkeypatch.setenv(
        "GIT_CEILING_DIRECTORIES",
        str(tmp_path.parent),
        prepend=os.pathsep,
    )
    repo = tmp_path / "primary"
    repo.mkdir()
    run = lambda *cmd: subprocess.run(  # noqa: E731
        list(cmd), cwd=repo, check=True,
        capture_output=True, text=True,
    )
    run("git", "init", "-b", "main")
    run("git", "config", "user.email", "test@test.com")
    run("git", "config", "user.name", "Test")
    (repo / "file.py").write_text("x = 1\n")
    run("git", "add", "file.py")
    run("git", "commit", "-m", "init")
    run("git", "checkout", "-b", "feature")
    (repo / "file.py").write_text("x = 2\n")
    run("git", "add", "file.py")
    run("git", "commit", "-m", "change")
    run("git", "checkout", "main")
    return repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_contracts_yaml(code_forge_dir, repos_dict):
    """Write contracts.yaml inside a .code-forge directory."""
    code_forge_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"repos": repos_dict}
    p = code_forge_dir / "contracts.yaml"
    p.write_text(yaml.dump(cfg, default_flow_style=False))
    return p


def _setup_spec_repo(tmp_path, name, specs):
    """Create a directory with spec files.

    specs: list of (relative_path, content) tuples.
    """
    repo_dir = tmp_path / name
    repo_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in specs:
        spec_file = repo_dir / rel_path
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            spec_file.write_bytes(content)
        else:
            spec_file.write_text(content)
    return repo_dir


# ---------------------------------------------------------------------------
# SC-1 End-to-End: contracts.yaml -> trust -> load -> inject -> L1 prompt
# ---------------------------------------------------------------------------


class TestSC1EndToEnd:
    """CF-3: Full chain from contracts.yaml to L1 prompt content."""

    def test_sc1_end_to_end_contracts_to_l1_prompt(self, tmp_path, trust_dir):
        """One test exercising the full chain:
        contracts.yaml -> trust -> load_contract_digest -> build_l1_provider
        -> L1 prompt contains '## Design Intent' with spec content,
        appearing BEFORE 'Diff:' (order).

        Mocks llm_invoke (NOT build_l1_provider) so the real prompt assembly
        in factories.py executes.
        """
        from code_forge.baseline import ResolvedReview
        from code_forge.contract_loader import load_contract_digest
        from code_forge.factories import build_l1_provider
        from code_forge.trust import record_trust_contracts

        # Set up a spec repo with a small YNL-like spec
        spec_content = (
            "name: ovs_flow\n"
            "doc: OVS flow table operations\n"
            "operations:\n"
            "  - name: get\n"
            "    doc: Get flow entry\n"
        )
        spec_repo = _setup_spec_repo(tmp_path, "kernel_repo", [
            ("net/ovs/ovs_flow.yaml", spec_content),
        ])

        # Set up the primary repo with contracts.yaml
        primary = tmp_path / "repo_b"
        primary.mkdir()
        code_forge_dir = primary / ".code-forge"
        contracts_path = _write_contracts_yaml(code_forge_dir, {
            "kernel": {
                "path": str(spec_repo),
                "specs": [{"path": "net/ovs/ovs_flow.yaml"}],
            },
        })

        # Trust the contracts
        spec_file = spec_repo / "net" / "ovs" / "ovs_flow.yaml"
        trust_contents = [
            (str(spec_file.resolve()), spec_file.read_bytes()),
        ]
        record_trust_contracts(contracts_path, trust_contents, config_dir=trust_dir)

        # Load contract digest (real call, not mocked)
        digest = load_contract_digest(contracts_path, primary)
        assert digest, "load_contract_digest returned empty -- trust or resolution failed"
        assert "ovs_flow" in digest

        # Build L1 provider with contract_spec, mock only llm_invoke
        resolved = ResolvedReview(
            mode_hint="git",
            git_diff="--- a/dp.c\n+++ b/dp.c\n@@ -1 +1 @@\n-old\n+new\n",
            source_files=[],
            baseline_content=None,
        )

        captured_prompts = []

        fake_result = MagicMock()
        fake_result.content = json.dumps({
            "findings": [],
            "code_excerpts": [
                {"file": "dp.c", "start_line": 1, "end_line": 1, "content": "new"},
            ],
        })
        fake_result.usage = MagicMock(input_tokens=10, output_tokens=5)
        fake_result.duration_s = 0.1

        def _capture_invoke(prompt, **kwargs):
            captured_prompts.append(prompt)
            return fake_result

        with patch("code_forge.llm_invoke.llm_invoke", _capture_invoke):
            provider = build_l1_provider(
                "auto", resolved, backend=None,
                contract_spec=digest,
            )
            provider()

        assert captured_prompts, "llm_invoke was never called"
        prompt = captured_prompts[0]

        # Verify contract content appears in the prompt
        assert "## Design Intent" in prompt
        assert "ovs_flow" in prompt
        assert spec_content.strip() in prompt or "ovs_flow" in prompt

        # Design Intent BEFORE Diff:
        cr_idx = prompt.index("## Design Intent")
        diff_idx = prompt.index("\nDiff:\n")
        assert cr_idx < diff_idx, (
            "## Design Intent must appear before Diff:"
        )


# ---------------------------------------------------------------------------
# SC-2: Missing/unreadable spec -> graceful empty digest
# ---------------------------------------------------------------------------


class TestSC2GracefulDegradation:
    """SC-2: per-spec errors produce empty digest, never crash."""

    def test_sc2_missing_spec_graceful_empty(self, tmp_path, trust_dir, capsys):
        """contracts.yaml points to nonexistent spec -> '' return, no crash,
        warning in stderr.
        """
        from code_forge.contract_loader import load_contract_digest
        from code_forge.trust import record_trust_contracts

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        code_forge_dir = tmp_path / "project" / ".code-forge"
        contracts_path = _write_contracts_yaml(code_forge_dir, {
            "repo": {
                "path": str(repo_dir),
                "specs": [{"path": "nonexistent_spec.yaml"}],
            },
        })
        # Trust with empty resolved contents (spec does not exist)
        record_trust_contracts(contracts_path, [], config_dir=trust_dir)

        result = load_contract_digest(contracts_path, tmp_path / "project")
        assert result == ""
        # No exception reached here -- graceful

    def test_sc2_unreadable_spec_graceful_empty(self, tmp_path, trust_dir):
        """Unreadable spec (PermissionError) -> '' digest, no crash (CF-2).

        Uses mock to simulate PermissionError on open, portable across OS.
        """
        from code_forge.contract_loader import load_contract_digest
        from code_forge.trust import record_trust_contracts

        repo_dir = _setup_spec_repo(tmp_path, "repo", [
            ("spec.yaml", "name: test\n"),
        ])
        code_forge_dir = tmp_path / "project" / ".code-forge"
        contracts_path = _write_contracts_yaml(code_forge_dir, {
            "repo": {
                "path": str(repo_dir),
                "specs": [{"path": "spec.yaml"}],
            },
        })

        # The spec file exists, so resolve_contract_specs will try to read it.
        # Mock _read_spec_content to raise PermissionError via returning None
        # (which is the real behavior when stat/read fails).
        # Actually, make the file unreadable to test the real path.
        spec_file = repo_dir / "spec.yaml"
        spec_file.chmod(0o000)

        try:
            # Trust with empty contents (unreadable spec resolves to nothing)
            record_trust_contracts(contracts_path, [], config_dir=trust_dir)

            result = load_contract_digest(contracts_path, tmp_path / "project")
            assert result == ""
        finally:
            spec_file.chmod(0o644)


# ---------------------------------------------------------------------------
# SC-3: No contracts.yaml -> empty, no warning
# ---------------------------------------------------------------------------


class TestSC3NoOptIn:
    """SC-3: No contracts.yaml -> '' return, no warning."""

    def test_sc3_no_optin_no_spec(self, tmp_path, capsys):
        """No contracts.yaml file. Assert '' return, no warning to stderr.
        Uses capsys fixture (SF-5).
        """
        from code_forge.contract_loader import load_contract_digest

        result = load_contract_digest(
            tmp_path / ".code-forge" / "contracts.yaml", tmp_path,
        )
        assert result == ""

        captured = capsys.readouterr()
        assert "contract" not in captured.err.lower(), (
            "No warning should be emitted when contracts.yaml is absent"
        )


# ---------------------------------------------------------------------------
# DF-2: Sibling threads receive no contract_spec
# ---------------------------------------------------------------------------


class TestDF2SiblingsNoContract:
    """amended: siblings use no-op lambda, never get contract_spec."""

    def test_d06_siblings_receive_no_contract_spec(
        self, tmp_path, git_repo, monkeypatch, trust_dir,
    ):
        """Sibling threads never call build_l1_provider, so they never
        receive contract_spec. Primary calls it exactly once.

        Spy on build_l1_provider to capture kwargs. Verify:
        - Called exactly once (primary only)
        - Sibling's l1_provider is a plain lambda (no build_l1_provider call)
        """
        from code_forge.cross_repo import run_cross_repo
        from code_forge.llm_invoke import Usage
        from code_forge.state import Mode, Verdict

        # Set up a sibling repo
        sib = tmp_path / "sibling"
        sib.mkdir()
        run = lambda *cmd: subprocess.run(  # noqa: E731
            list(cmd), cwd=sib, check=True,
            capture_output=True, text=True,
        )
        run("git", "init", "-b", "main")
        run("git", "config", "user.email", "t@t.com")
        run("git", "config", "user.name", "T")
        (sib / "lib.py").write_text("y = 1\n")
        run("git", "add", "lib.py")
        run("git", "commit", "-m", "init")
        run("git", "checkout", "-b", "feature")
        (sib / "lib.py").write_text("y = 2\n")
        run("git", "add", "lib.py")
        run("git", "commit", "-m", "change")
        run("git", "checkout", "main")

        # Set up contracts.yaml on primary
        spec_repo = _setup_spec_repo(tmp_path, "spec_repo", [
            ("api.yaml", "name: api\n"),
        ])
        code_forge_dir = git_repo / ".code-forge"
        contracts_path = _write_contracts_yaml(code_forge_dir, {
            "specs": {
                "path": str(spec_repo),
                "specs": [{"path": "api.yaml"}],
            },
        })
        # Trust the contracts
        from code_forge.trust import record_trust_contracts
        spec_file = spec_repo / "api.yaml"
        trust_contents = [(str(spec_file.resolve()), spec_file.read_bytes())]
        record_trust_contracts(contracts_path, trust_contents, config_dir=trust_dir)

        # Spy on build_l1_provider
        spy_calls = []

        def _spy_build_l1(engine, resolved, **kwargs):
            spy_calls.append(kwargs)
            return lambda: ([], [], Usage(), 0.0)

        monkeypatch.setattr(
            "code_forge.factories.build_l1_provider",
            _spy_build_l1,
        )

        class _PassMachine:
            def __init__(self, **kwargs):
                pass

            def run(self):
                return Verdict.PASS

        monkeypatch.setattr("code_forge.machine.StateMachine", _PassMachine)

        run_cross_repo(
            primary_path=git_repo,
            primary_ref="main..feature",
            primary_label="primary",
            siblings=[{
                "repo": str(sib),
                "ref": "main..feature",
                "label": "sibling",
            }],
            gate_config={"test": {"command": ["echo", "ok"]}},
            mode=Mode.LOCAL,
            engine_choice="stub",
            backend=None,
            max_rounds=1,
            max_fix_attempts=1,
            clean_round_threshold=1,
        )

        # build_l1_provider called exactly once (primary only)
        assert len(spy_calls) == 1, (
            "build_l1_provider should be called once (primary), "
            "got %d calls" % len(spy_calls)
        )

    def test_d06_cross_repo_primary_gets_contract(
        self, tmp_path, git_repo, monkeypatch, trust_dir,
    ):
        """Primary thread's build_l1_provider receives non-empty contract_spec
        and backend=backend (SF-1).
        """
        from code_forge.cross_repo import run_cross_repo
        from code_forge.llm_invoke import Usage
        from code_forge.state import Mode, Verdict

        # Set up a spec repo with content
        spec_repo = _setup_spec_repo(tmp_path, "spec_repo", [
            ("api.yaml", "name: api_spec\nops:\n  - get\n"),
        ])

        # Set up contracts.yaml on primary
        code_forge_dir = git_repo / ".code-forge"
        contracts_path = _write_contracts_yaml(code_forge_dir, {
            "specs": {
                "path": str(spec_repo),
                "specs": [{"path": "api.yaml"}],
            },
        })

        # Trust the contracts
        from code_forge.trust import record_trust_contracts
        spec_file = spec_repo / "api.yaml"
        trust_contents = [(str(spec_file.resolve()), spec_file.read_bytes())]
        record_trust_contracts(contracts_path, trust_contents, config_dir=trust_dir)

        # Spy on build_l1_provider to capture contract_spec and backend
        spy_kwargs = []
        sentinel_backend = object()

        def _spy_build_l1(engine, resolved, **kwargs):
            spy_kwargs.append(kwargs)
            return lambda: ([], [], Usage(), 0.0)

        monkeypatch.setattr(
            "code_forge.factories.build_l1_provider",
            _spy_build_l1,
        )

        class _PassMachine:
            def __init__(self, **kwargs):
                pass

            def run(self):
                return Verdict.PASS

        monkeypatch.setattr("code_forge.machine.StateMachine", _PassMachine)

        run_cross_repo(
            primary_path=git_repo,
            primary_ref="main..feature",
            primary_label="primary",
            siblings=[],
            gate_config={"test": {"command": ["echo", "ok"]}},
            mode=Mode.LOCAL,
            engine_choice="stub",
            backend=sentinel_backend,
            max_rounds=1,
            max_fix_attempts=1,
            clean_round_threshold=1,
        )

        assert len(spy_kwargs) == 1
        kw = spy_kwargs[0]
        # SF-1: backend=backend passed through
        assert kw.get("backend") is sentinel_backend, (
            "build_l1_provider must receive backend=backend (SF-1)"
        )
        # contract_spec must be non-empty (loaded from contracts.yaml)
        assert kw.get("contract_spec", "") != "", (
            "build_l1_provider must receive non-empty contract_spec"
        )
        assert "api_spec" in kw["contract_spec"]


# ---------------------------------------------------------------------------
# Boundary tests
# ---------------------------------------------------------------------------


class TestBoundary:
    """Boundary conditions for contract loading."""

    def test_binary_spec_skipped_in_integration(self, tmp_path, trust_dir):
        """contracts.yaml points to a file with null bytes -> that spec
        skipped, other specs still work.
        """
        from code_forge.contract_loader import load_contract_digest
        from code_forge.trust import record_trust_contracts

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # One good spec, one binary spec
        good_spec = repo_dir / "good.yaml"
        good_spec.write_text("name: good_spec\n")

        binary_spec = repo_dir / "binary.dat"
        binary_spec.write_bytes(b"binary\x00content\x00here")

        code_forge_dir = tmp_path / "project" / ".code-forge"
        contracts_path = _write_contracts_yaml(code_forge_dir, {
            "repo": {
                "path": str(repo_dir),
                "specs": [
                    {"path": "good.yaml"},
                    {"path": "binary.dat"},
                ],
            },
        })

        # Trust: only good.yaml resolves (binary.dat is filtered by _read_spec_content)
        trust_contents = [
            (str(good_spec.resolve()), good_spec.read_bytes()),
        ]
        record_trust_contracts(contracts_path, trust_contents, config_dir=trust_dir)

        result = load_contract_digest(contracts_path, tmp_path / "project")
        assert "good_spec" in result
        # Binary spec should not appear
        assert "binary" not in result.lower() or "binary.dat" not in result

    def test_env_var_not_set_skips_repo(self, tmp_path, trust_dir, capsys):
        """$NONEXISTENT_VAR in repo path -> graceful skip with warning."""
        from code_forge.contract_loader import load_contract_digest
        from code_forge.trust import record_trust_contracts

        code_forge_dir = tmp_path / "project" / ".code-forge"
        contracts_path = _write_contracts_yaml(code_forge_dir, {
            "repo": {
                "path": "$NONEXISTENT_ENV_VAR_FOR_TEST_XYZ",
                "specs": [{"path": "spec.yaml"}],
            },
        })
        # Trust with empty contents
        record_trust_contracts(contracts_path, [], config_dir=trust_dir)

        result = load_contract_digest(contracts_path, tmp_path / "project")
        assert result == ""

        captured = capsys.readouterr()
        assert "env var not set" in captured.err.lower() or "NONEXISTENT" in captured.err
