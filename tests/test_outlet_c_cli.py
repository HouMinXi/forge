# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Phase 24.1-02: cli.py subagent dispatch passes real legs to run_outlet_c.

These tests use structural assertion (grep on the source block) rather than
full integration mocks, because the subagent dispatch path requires too many
mock layers to be a reliable integration test. The structural assertions
confirm the code was actually written correctly and are immune to mock-chain
false-positives.
"""

import pathlib

# Absolute path so tests pass even when another test calls chdir
_SRC = pathlib.Path(__file__).parent.parent / "src" / "code_forge" / "cli.py"


def _subagent_block() -> str:
    src = _SRC.read_text()
    start = src.index('def _dispatch_subagent(')
    return src[start: start + 2500]


class TestSubagentDispatchLegs:
    """cli.py subagent dispatch passes registry, backend, advisory_runners."""

    def test_subagent_dispatch_passes_registry(self):
        """Subagent block passes registry=registry to run_outlet_c."""
        assert "registry=registry" in _subagent_block(), (
            "subagent dispatch must pass registry=registry to run_outlet_c"
        )

    def test_subagent_dispatch_passes_advisory_runners(self):
        """Subagent block constructs 5 runners and passes advisory_runners=."""
        block = _subagent_block()
        assert "advisory_runners=" in block
        for name in ["_c_taint", "_c_runtime", "_c_graph", "_c_daemon", "_c_legacy"]:
            assert name in block, (
                "runner %s must be constructed in subagent block" % name
            )

    def test_subagent_dispatch_passes_engine(self):
        """Subagent block passes engine=engine_choice to run_outlet_c."""
        assert "engine=engine_choice" in _subagent_block()

    def test_subagent_block_no_pre_graph_findings(self):
        """_pre_graph_findings must NOT appear in subagent block (not in scope)."""
        assert "_pre_graph_findings" not in _subagent_block(), (
            "_pre_graph_findings is defined after subagent early-return; "
            "using it here causes NameError"
        )


class TestInlineCanaryEnvPassthrough:
    """_dispatch_inline_canary must receive env and pass it to resolve_backend.

    The extraction moved resolve_backend(env, ...) from _run's body into a
    top-level helper.  If env is not in the signature, NameError is swallowed
    by the surrounding except Exception, silently disabling canary generation.
    This test exercises the real code path and asserts resolve_backend is
    actually called with the env argument.
    """

    def test_env_reaches_resolve_backend(self, tmp_path):
        """resolve_backend must be called with env when outlet is inline."""
        from unittest.mock import patch, MagicMock

        # Minimal args mock with the fields _dispatch_inline_canary reads.
        args = MagicMock()
        args.backend = None
        args.contract = None

        # canary_config must be non-None to enter the canary path.
        gate_data = {"canary": {"n": 1, "threshold_ratio": 0.6}}

        # Stub resolve_backend to return a sentinel backend object.
        fake_backend = MagicMock()
        fake_backend.name = "test-backend"

        env = {"TEST_KEY": "1"}

        with patch(
            "code_forge.cli._load_canary_config",
            return_value=gate_data["canary"],
        ), patch(
            "code_forge.backend.resolve_backend",
            return_value=fake_backend,
        ) as mock_resolve, patch(
            "code_forge.canary_gen.run_inline_canary",
            return_value=(MagicMock(), []),
        ):
            from code_forge.cli import _dispatch_inline_canary

            _dispatch_inline_canary("inline", args, env, {}, gate_data, tmp_path)

            # The critical assertion: resolve_backend was called with env.
            assert mock_resolve.called, (
                "resolve_backend was never called -- canary path is dead"
            )
            call_kwargs = mock_resolve.call_args
            assert call_kwargs[0][0] is env, (
                "resolve_backend received wrong env: %r instead of %r"
                % (call_kwargs[0][0], env)
            )

    def test_sampling_raises_clierror(self, tmp_path):
        """outlet='sampling' must raise CliError."""
        from unittest.mock import MagicMock
        from code_forge.cli import _dispatch_inline_canary, CliError
        args = MagicMock()
        try:
            _dispatch_inline_canary("sampling", args, {}, {}, {}, tmp_path)
            assert False, "should have raised CliError"
        except CliError:
            pass

    def test_non_inline_returns_none(self, tmp_path):
        """outlet='subprocess' must return None (fall through)."""
        from unittest.mock import MagicMock
        from code_forge.cli import _dispatch_inline_canary
        args = MagicMock()
        result = _dispatch_inline_canary("subprocess", args, {}, {}, {}, tmp_path)
        assert result is None

    def test_canary_config_none_returns_delegated(self, tmp_path):
        """inline with no canary config returns DELEGATED."""
        from unittest.mock import patch, MagicMock
        from code_forge.cli import _dispatch_inline_canary
        from code_forge.state import Verdict
        args = MagicMock()
        args.backend = None
        with patch("code_forge.cli._load_canary_config", return_value=None):
            result = _dispatch_inline_canary("inline", args, {}, {}, {}, tmp_path)
        assert result == Verdict.DELEGATED


class TestDispatchCrossRepo:
    """_dispatch_cross_repo returns None when no siblings exist."""

    def test_returns_none_when_no_siblings(self, tmp_path):
        from unittest.mock import patch
        from code_forge.cli import _dispatch_cross_repo
        with patch("code_forge.cli._cross_repo_verdict_or_none", return_value=None):
            result = _dispatch_cross_repo(
                tmp_path / "gate.yaml", tmp_path,
                None, None, None, None, None, None, None, None, lambda m: None,
            )
        assert result is None

    def test_returns_verdict_when_siblings_exist(self, tmp_path):
        from unittest.mock import patch, MagicMock
        from code_forge.cli import _dispatch_cross_repo
        sentinel = MagicMock()
        with patch("code_forge.cli._cross_repo_verdict_or_none", return_value=sentinel):
            result = _dispatch_cross_repo(
                tmp_path / "gate.yaml", tmp_path,
                None, None, None, None, None, None, None, None, lambda m: None,
            )
        assert result is sentinel


class TestDispatchSubagent:
    """_dispatch_subagent returns None when outlet is not subagent."""

    def test_returns_none_for_non_subagent(self, tmp_path):
        from unittest.mock import MagicMock
        from code_forge.cli import _dispatch_subagent
        resolved = MagicMock()
        resolved.git_diff = None
        result = _dispatch_subagent(
            "subprocess", lambda m: None, "", MagicMock(),
            resolved, "hash", {}, "stub", 3, tmp_path,
        )
        assert result is None
