# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Integration tests for contract spec wiring into review prompts and trust CLI.

Tests cover:
  - Outlet A (build_l1_provider) contract_spec injection
  - Outlet C (_make_subagent_spawn) contract_spec injection
  - Prompt section ordering
  - No-contracts backward compatibility
  - Trust CLI (record, status, revoke) for contracts.yaml
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

@pytest.fixture(autouse=True)
def _isolate_project_dir(monkeypatch):
    """Walk-up resolution reads FORGE_PROJECT_DIR from os.environ.

    _run_trust takes no env parameter, so an exported value on the
    host would hijack every resolution -- isolate it for all tests
    here, not just the new walk-up ones.
    """
    monkeypatch.delenv("FORGE_PROJECT_DIR", raising=False)



# ---------------------------------------------------------------------------
# Outlet A: build_l1_provider
# ---------------------------------------------------------------------------


class TestOutletAContractSpec:
    """Outlet A injects ## Design Intent into the L1 prompt."""

    def test_outlet_a_includes_contract_spec(self):
        """When contract_spec is non-empty, the prompt contains
        '## Design Intent' followed by the spec content, before 'Diff:'.
        """
        from code_forge.factories import build_l1_provider
        from code_forge.baseline import ResolvedReview

        resolved = ResolvedReview(
            mode_hint="git",
            git_diff="--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n",
            source_files=[],
            baseline_content=None,
        )

        contract_text = "## Contract: test/api.yaml\nendpoint: /foo\nmethod: GET"

        captured_prompts = []

        fake_result = MagicMock()
        fake_result.content = json.dumps({
            "findings": [],
            "code_excerpts": [
                {"file": "foo.py", "start_line": 1, "end_line": 1, "content": "new"}
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
                contract_spec=contract_text,
            )
            provider()

        assert captured_prompts, "llm_invoke was never called"
        prompt = captured_prompts[0]
        assert "## Design Intent" in prompt
        assert contract_text in prompt
        # Design Intent must appear before Diff:
        cr_idx = prompt.index("## Design Intent")
        diff_idx = prompt.index("\nDiff:\n")
        assert cr_idx < diff_idx, (
            "## Design Intent must appear before Diff:"
        )


# ---------------------------------------------------------------------------
# Outlet C: _make_subagent_spawn
# ---------------------------------------------------------------------------


class TestOutletCContractSpec:
    """Outlet C injects ## Design Intent into the subagent prompt."""

    def test_outlet_c_includes_contract_spec(self):
        """_make_subagent_spawn with contract_spec injects it before Diff:."""
        from code_forge.cli import _make_subagent_spawn

        contract_text = "## Contract: test/api.yaml\nendpoint: /bar\nmethod: POST"
        captured_prompts = []

        fake_result = MagicMock()
        fake_result.content = '{"findings": [], "code_excerpts": []}'

        def _capture(prompt, **kwargs):
            captured_prompts.append(prompt)
            return fake_result

        with patch("code_forge.llm_invoke.llm_invoke", _capture):
            spawn_fn = _make_subagent_spawn(
                backend=None, conv_digest="", post_image="",
                contract_spec=contract_text,
            )
            spawn_fn("qodo", "--- a/bar.py\n+++ b/bar.py\n@@ -1 +1 @@\n-x\n+y\n")

        assert captured_prompts
        prompt = captured_prompts[0]
        assert "## Design Intent" in prompt
        assert contract_text in prompt
        cr_idx = prompt.index("## Design Intent")
        diff_idx = prompt.index("\nDiff:\n")
        assert cr_idx < diff_idx


# ---------------------------------------------------------------------------
# No contracts.yaml: backward compatibility
# ---------------------------------------------------------------------------


class TestNoContractsYaml:
    """When contract_spec is empty, no ## Design Intent appears."""

    def test_no_contracts_yaml_no_contract_in_prompt(self):
        """Default contract_spec='' produces no contract section."""
        from code_forge.factories import build_l1_provider
        from code_forge.baseline import ResolvedReview

        resolved = ResolvedReview(
            mode_hint="git",
            git_diff="--- a/z.py\n+++ b/z.py\n@@ -1 +1 @@\n-a\n+b\n",
            source_files=[],
            baseline_content=None,
        )

        captured_prompts = []

        fake_result = MagicMock()
        fake_result.content = json.dumps({
            "findings": [],
            "code_excerpts": [
                {"file": "z.py", "start_line": 1, "end_line": 1, "content": "b"}
            ],
        })
        fake_result.usage = MagicMock(input_tokens=10, output_tokens=5)
        fake_result.duration_s = 0.1

        def _capture(prompt, **kwargs):
            captured_prompts.append(prompt)
            return fake_result

        with patch("code_forge.llm_invoke.llm_invoke", _capture):
            provider = build_l1_provider(
                "auto", resolved, backend=None,
                contract_spec="",
            )
            provider()

        assert captured_prompts
        for p in captured_prompts:
            assert "## Design Intent" not in p


# ---------------------------------------------------------------------------
# Prompt section order
# ---------------------------------------------------------------------------


class TestPromptSectionOrder:
    """order: Post-Image < Conventions < Blast Radius < Contract < Diff."""

    def test_prompt_section_order(self):
        from code_forge.factories import build_l1_provider
        from code_forge.baseline import ResolvedReview

        resolved = ResolvedReview(
            mode_hint="git",
            git_diff="--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n",
            source_files=[],
            baseline_content=None,
        )

        captured_prompts = []

        fake_result = MagicMock()
        fake_result.content = json.dumps({
            "findings": [],
            "code_excerpts": [
                {"file": "f.py", "start_line": 1, "end_line": 1, "content": "new"}
            ],
        })
        fake_result.usage = MagicMock(input_tokens=10, output_tokens=5)
        fake_result.duration_s = 0.1

        def _capture(prompt, **kwargs):
            captured_prompts.append(prompt)
            return fake_result

        with patch("code_forge.llm_invoke.llm_invoke", _capture):
            provider = build_l1_provider(
                "auto", resolved, backend=None,
                post_image="file content here",
                conventions_digest="naming rules",
                graph_impact_context="| Entity | File | Downstream | Deps |",
                contract_spec="## Contract: r/s\ncontent",
            )
            provider()

        assert captured_prompts
        prompt = captured_prompts[0]

        # All five sections must be present
        assert "## Post-Image" in prompt
        assert "## Conventions Digest" in prompt
        assert "## Blast Radius Context" in prompt
        assert "## Design Intent" in prompt
        assert "\nDiff:\n" in prompt

        # Verify ordering
        pi_idx = prompt.index("## Post-Image")
        cd_idx = prompt.index("## Conventions Digest")
        br_idx = prompt.index("## Blast Radius Context")
        cr_idx = prompt.index("## Design Intent")
        di_idx = prompt.index("\nDiff:\n")

        assert pi_idx < cd_idx < br_idx < cr_idx < di_idx, (
            "Section order must be: Post-Image < Conventions < "
            "Blast Radius < Design Intent < Diff"
        )


# ---------------------------------------------------------------------------
# Trust CLI: record, status, revoke for contracts.yaml
# ---------------------------------------------------------------------------


class TestRunTrustRecordsContracts:
    """'code-forge trust' records both gate.yaml and contracts.yaml trust."""

    def test_run_trust_records_contracts_yaml(self, tmp_path):
        """Bare trust with contracts.yaml records contracts trust entry."""
        import yaml
        from code_forge.cli import _run_trust

        # Set up gate.yaml
        code_forge_dir = tmp_path / ".code-forge"
        code_forge_dir.mkdir()
        gate_path = code_forge_dir / "gate.yaml"
        gate_data = {"backends": [{"name": "test", "type": "api"}]}
        gate_path.write_text(yaml.dump(gate_data))

        # Set up contracts.yaml + spec file
        contracts_data = {
            "repos": {
                "peer": {
                    "path": str(tmp_path / "peer"),
                    "specs": ["api.yaml"],
                },
            },
        }
        contracts_path = code_forge_dir / "contracts.yaml"
        contracts_path.write_text(yaml.dump(contracts_data))

        peer_dir = tmp_path / "peer"
        peer_dir.mkdir()
        spec_file = peer_dir / "api.yaml"
        spec_file.write_text("endpoint: /test\nmethod: GET\n")

        # Mock trust store to use tmp_path
        config_dir = tmp_path / "trust-config"
        config_dir.mkdir()

        args = SimpleNamespace(status=False, revoke=False)

        with patch("code_forge.trust.record_trust") as mock_gate_trust, \
             patch("code_forge.trust.record_trust_contracts") as mock_contracts_trust:
            _run_trust(args, tmp_path)

        mock_gate_trust.assert_called_once()
        mock_contracts_trust.assert_called_once()
        # Verify contracts trust received the right path
        call_args = mock_contracts_trust.call_args
        assert str(contracts_path) == str(call_args[0][0])


class TestRunTrustStatusShowsContracts:
    """'code-forge trust --status' reports contracts trust status."""

    def test_run_trust_status_shows_contracts(self, tmp_path, capsys):
        """Trust status includes contracts information when contracts.yaml exists."""
        import yaml
        from code_forge.cli import _run_trust
        from code_forge.trust import TrustStatus

        code_forge_dir = tmp_path / ".code-forge"
        code_forge_dir.mkdir()
        gate_path = code_forge_dir / "gate.yaml"
        gate_data = {"backends": [{"name": "test", "type": "api"}]}
        gate_path.write_text(yaml.dump(gate_data))

        contracts_data = {
            "repos": {
                "peer": {
                    "path": str(tmp_path / "peer"),
                    "specs": ["api.yaml"],
                },
            },
        }
        contracts_path = code_forge_dir / "contracts.yaml"
        contracts_path.write_text(yaml.dump(contracts_data))

        peer_dir = tmp_path / "peer"
        peer_dir.mkdir()
        (peer_dir / "api.yaml").write_text("endpoint: /status\n")

        gate_status = TrustStatus(
            trusted=True, stored_hash="abc", current_hash="abc",
            gate_yaml_path=str(gate_path),
        )
        contracts_status = TrustStatus(
            trusted=False, stored_hash=None, current_hash="def",
            gate_yaml_path=str(contracts_path),
        )

        args = SimpleNamespace(status=True, revoke=False)

        with patch("code_forge.trust.trust_status", return_value=gate_status), \
             patch("code_forge.trust.trust_status_contracts", return_value=contracts_status):
            result = _run_trust(args, tmp_path)

        assert result == 0
        captured = capsys.readouterr()
        # Contracts status should appear in stderr output
        assert "contracts" in captured.err.lower() or "Contracts" in captured.err


class TestRunTrustRevokeCoversContracts:
    """'code-forge trust --revoke' revokes both gate and contracts trust."""

    def test_run_trust_revoke_covers_contracts(self, tmp_path, capsys):
        """Revoke trust removes both gate.yaml and contracts.yaml entries."""
        import yaml
        from code_forge.cli import _run_trust

        code_forge_dir = tmp_path / ".code-forge"
        code_forge_dir.mkdir()
        gate_path = code_forge_dir / "gate.yaml"
        gate_data = {"backends": [{"name": "test", "type": "api"}]}
        gate_path.write_text(yaml.dump(gate_data))

        contracts_data = {
            "repos": {
                "peer": {
                    "path": str(tmp_path / "peer"),
                    "specs": ["api.yaml"],
                },
            },
        }
        contracts_path = code_forge_dir / "contracts.yaml"
        contracts_path.write_text(yaml.dump(contracts_data))

        (tmp_path / "peer").mkdir()
        (tmp_path / "peer" / "api.yaml").write_text("endpoint: /test\n")

        args = SimpleNamespace(status=False, revoke=True)

        with patch("code_forge.trust.revoke_trust") as mock_revoke_gate, \
             patch("code_forge.trust.revoke_trust_contracts") as mock_revoke_contracts:
            result = _run_trust(args, tmp_path)

        assert result == 0
        mock_revoke_gate.assert_called_once()
        mock_revoke_contracts.assert_called_once()


class TestRunTrustNoContractsStillWorks:
    """Trust CLI works with gate.yaml only (no contracts.yaml)."""

    def test_run_trust_no_contracts_yaml_still_works(self, tmp_path, capsys):
        """When contracts.yaml is absent, trust operations succeed for gate only."""
        import yaml
        from code_forge.cli import _run_trust

        code_forge_dir = tmp_path / ".code-forge"
        code_forge_dir.mkdir()
        gate_path = code_forge_dir / "gate.yaml"
        gate_data = {"backends": [{"name": "test", "type": "api"}]}
        gate_path.write_text(yaml.dump(gate_data))

        # No contracts.yaml

        args = SimpleNamespace(status=False, revoke=False)

        with patch("code_forge.trust.record_trust") as mock_gate_trust:
            result = _run_trust(args, tmp_path)

        assert result == 0
        mock_gate_trust.assert_called_once()
        captured = capsys.readouterr()
        assert "Trusted:" in captured.err
