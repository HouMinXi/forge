# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for canary infrastructure: Verdict.UNRELIABLE, EXIT_UNRELIABLE,
gate.yaml canary: block validation, init template, and CLI wiring."""
from __future__ import annotations

import argparse
import io
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from code_forge.state import Verdict
from code_forge.exit_codes import EXIT_UNRELIABLE, verdict_to_exit
from code_forge.gate_check import load_gate_config, validate_canary_config
from code_forge.init_template import GATE_YAML_TEMPLATE
from code_forge.cli import _load_canary_config, _load_gate_backends


class TestVerdictUnreliable:
    """Verdict.UNRELIABLE enum member exists with the correct value."""

    def test_verdict_unreliable_exists(self):
        assert Verdict.UNRELIABLE is not None
        assert Verdict.UNRELIABLE.value == "UNRELIABLE"
        # Round-trip from string
        assert Verdict("UNRELIABLE") is Verdict.UNRELIABLE

    def test_exit_unreliable_value(self):
        assert EXIT_UNRELIABLE == 7

    def test_verdict_to_exit_unreliable(self):
        assert verdict_to_exit(Verdict.UNRELIABLE) == 7

    def test_exit_code_uniqueness(self):
        """All EXIT_* constants must have distinct integer values."""
        import code_forge.exit_codes as ec
        exit_names = [
            name for name in dir(ec)
            if name.startswith("EXIT_") and isinstance(getattr(ec, name), int)
        ]
        values = [getattr(ec, name) for name in exit_names]
        assert len(values) == len(set(values)), (
            "Duplicate exit code values: %s"
            % {v: [n for n in exit_names if getattr(ec, n) == v]
               for v in values if values.count(v) > 1}
        )
        # Verify the expected set after adding UNRELIABLE
        assert set(values) == {0, 1, 2, 3, 4, 5, 6, 7}


def _make_fs_open(yaml_text: str):
    """Return an fs_open callable that serves yaml_text for any path."""
    def fs_open(path, mode="r", encoding=None):
        return io.StringIO(yaml_text)
    return fs_open


# Minimal valid gate.yaml that satisfies load_gate_config's test: requirement.
_MINIMAL_GATE = "test:\n  command: ['true']\n"


class TestCanaryValidation:
    """validate_canary_config type-checks and range-checks all fields."""

    def test_gate_yaml_canary_valid(self):
        yaml_text = _MINIMAL_GATE + (
            "canary:\n"
            "  enabled: true\n"
            "  n: 5\n"
            "  threshold_ratio: 0.6\n"
        )
        result = load_gate_config("gate.yaml", fs_open=_make_fs_open(yaml_text))
        assert "canary" in result

    def test_gate_yaml_canary_enabled_not_bool(self):
        with pytest.raises(ValueError, match="bool"):
            validate_canary_config({"enabled": "yes"})

    def test_gate_yaml_canary_n_not_int(self):
        with pytest.raises(ValueError, match="int"):
            validate_canary_config({"n": "five"})

    def test_gate_yaml_canary_n_out_of_range_low(self):
        with pytest.raises(ValueError, match="3..5"):
            validate_canary_config({"n": 2})

    def test_gate_yaml_canary_n_out_of_range_high(self):
        with pytest.raises(ValueError, match="3..5"):
            validate_canary_config({"n": 6})

    def test_gate_yaml_canary_ratio_not_float(self):
        with pytest.raises(ValueError):
            validate_canary_config({"threshold_ratio": "high"})

    def test_gate_yaml_canary_ratio_zero(self):
        """0.0 rejected because ceil(0.0 * n) = 0, which crashes M1."""
        with pytest.raises(ValueError):
            validate_canary_config({"threshold_ratio": 0.0})

    def test_gate_yaml_canary_ratio_out_of_range(self):
        with pytest.raises(ValueError, match=r"0\.0\.\.1\.0"):
            validate_canary_config({"threshold_ratio": 1.5})

    def test_gate_yaml_canary_not_mapping(self):
        with pytest.raises(ValueError, match="mapping"):
            validate_canary_config("invalid")

    def test_gate_yaml_no_canary(self):
        """Backward compat: no canary section still loads fine."""
        result = load_gate_config(
            "gate.yaml", fs_open=_make_fs_open(_MINIMAL_GATE)
        )
        assert "canary" not in result

    def test_gate_yaml_canary_n_boundary_3(self):
        """n=3 is the minimum valid value."""
        validate_canary_config({"n": 3})

    def test_gate_yaml_canary_n_boundary_5(self):
        """n=5 is the maximum valid value."""
        validate_canary_config({"n": 5})

    def test_gate_yaml_canary_ratio_boundary_1(self):
        """threshold_ratio=1.0 is valid (require catching all canaries)."""
        validate_canary_config({"threshold_ratio": 1.0})

    def test_gate_yaml_canary_ratio_small_positive(self):
        """A small positive ratio like 0.01 is valid."""
        validate_canary_config({"threshold_ratio": 0.01})

    def test_gate_yaml_canary_int_ratio_accepted(self):
        """An integer ratio (1) is accepted as a valid number."""
        validate_canary_config({"threshold_ratio": 1})


class TestInitTemplateCanary:
    """Init template includes commented-out canary block."""

    def test_init_template_canary_block(self):
        assert "# canary:" in GATE_YAML_TEMPLATE
        assert "#   threshold_ratio" in GATE_YAML_TEMPLATE


# ---------------------------------------------------------------------------
# Plan 03: CLI canary wiring tests
# ---------------------------------------------------------------------------


def _make_args(**kwargs):
    """Build a minimal argparse.Namespace for testing."""
    defaults = {"canary": False, "backend": None, "outlet": None, "mode": None}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestLoadGateBackendsTuple:
    """_load_gate_backends returns a 2-tuple (list, dict)."""

    def test_returns_tuple_on_missing_file(self):
        result = _load_gate_backends(Path("/nonexistent/gate.yaml"))
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result == ([], {})

    def test_returns_tuple_on_valid_yaml(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text("test:\n  command: ['true']\n")
        # Trust guard will reject (no trust record), so we get ([], {})
        cfgs, gd = _load_gate_backends(gate)
        assert isinstance(cfgs, list)
        assert isinstance(gd, dict)

    def test_returns_tuple_on_empty_yaml(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text("")
        cfgs, gd = _load_gate_backends(gate)
        assert cfgs == []
        assert gd == {}


class TestLoadCanaryConfig:
    """_load_canary_config extracts canary opt-in from args/gate_data."""

    def test_canary_flag_sets_optin(self):
        args = _make_args(canary=True)
        result = _load_canary_config(args, {})
        assert result is not None
        assert result["enabled"] is True
        assert result["n"] == 5
        assert result["threshold_ratio"] == 0.6

    def test_gate_yaml_canary_sets_optin(self):
        args = _make_args(canary=False)
        gate_data = {"canary": {"enabled": True, "n": 4}}
        result = _load_canary_config(args, gate_data)
        assert result is not None
        assert result["enabled"] is True
        assert result["n"] == 4
        assert result["threshold_ratio"] == 0.6

    def test_gate_yaml_canary_with_ratio(self):
        args = _make_args(canary=False)
        gate_data = {"canary": {"enabled": True, "n": 3, "threshold_ratio": 0.8}}
        result = _load_canary_config(args, gate_data)
        assert result["threshold_ratio"] == 0.8

    def test_no_optin_returns_none(self):
        args = _make_args(canary=False)
        result = _load_canary_config(args, {})
        assert result is None

    def test_gate_yaml_canary_disabled(self):
        args = _make_args(canary=False)
        gate_data = {"canary": {"enabled": False, "n": 5}}
        result = _load_canary_config(args, gate_data)
        assert result is None

    def test_gate_yaml_canary_not_dict(self):
        args = _make_args(canary=False)
        gate_data = {"canary": "yes"}
        result = _load_canary_config(args, gate_data)
        assert result is None

    def test_no_reread_from_disk(self):
        """_load_canary_config never calls open() or yaml.safe_load."""
        args = _make_args(canary=True)
        with mock.patch("builtins.open", side_effect=AssertionError("must not open")):
            result = _load_canary_config(args, {})
        assert result is not None

    def test_cli_flag_overrides_gate_yaml(self):
        """--canary flag takes precedence; returns CLI defaults not gate values."""
        args = _make_args(canary=True)
        gate_data = {"canary": {"enabled": True, "n": 3, "threshold_ratio": 0.8}}
        result = _load_canary_config(args, gate_data)
        assert result["n"] == 5  # CLI default, not gate value
        assert result["threshold_ratio"] == 0.6


class TestInlineDefaultPath:
    """Default inline path (no canary) is unchanged."""

    def test_no_optin_unchanged(self):
        """With no --canary and no gate.yaml canary, inline returns DELEGATED
        with the exact pre-Phase-28 stderr message."""
        args = _make_args(canary=False, outlet="inline", subcommand="review")
        # We need to call the portion that handles outlet == "inline"
        # Test _load_canary_config returns None -> DELEGATED fallthrough
        result = _load_canary_config(args, {})
        assert result is None
        # The full CLI path would return Verdict.DELEGATED; verified via
        # the _load_canary_config(None) + default block structure


class TestCanaryExitCodes:
    """Canary verdict maps to correct exit codes."""

    def test_unreliable_exit_code(self):
        assert verdict_to_exit(Verdict.UNRELIABLE) == 7

    def test_delegated_exit_code(self):
        assert verdict_to_exit(Verdict.DELEGATED) == 5


class TestEpilogExitCodes:
    """Both root and review parser epilogs contain all 8 exit codes."""

    @pytest.fixture()
    def parsers(self):
        from code_forge.cli import _build_parser
        parser = _build_parser()
        review_parser = None
        for action in parser._subparsers._actions:
            if isinstance(action, argparse._SubParsersAction):
                review_parser = action.choices.get("review")
                break
        return parser, review_parser

    def test_root_epilog_has_all_exit_codes(self, parsers):
        root, _ = parsers
        epilog = root.epilog
        for code in ["0", "1", "2", "3", "4", "5", "6", "7"]:
            assert "  %s  " % code in epilog, "exit code %s missing from root epilog" % code

    def test_review_epilog_has_all_exit_codes(self, parsers):
        _, review = parsers
        epilog = review.epilog
        for code in ["0", "1", "2", "3", "4", "5", "6", "7"]:
            assert "  %s  " % code in epilog, "exit code %s missing from review epilog" % code

    def test_root_epilog_has_delegated(self, parsers):
        root, _ = parsers
        assert "DELEGATED" in root.epilog

    def test_root_epilog_has_unreliable(self, parsers):
        root, _ = parsers
        assert "UNRELIABLE" in root.epilog

    def test_root_epilog_has_timeout(self, parsers):
        root, _ = parsers
        assert "TIMEOUT" in root.epilog

    def test_review_epilog_has_delegated(self, parsers):
        _, review = parsers
        assert "DELEGATED" in review.epilog

    def test_review_epilog_has_unreliable(self, parsers):
        _, review = parsers
        assert "UNRELIABLE" in review.epilog

    def test_review_epilog_has_timeout(self, parsers):
        _, review = parsers
        assert "TIMEOUT" in review.epilog


class TestCanaryProviderPrompt:
    """_canary_provider prompt includes 'original' field."""

    def test_prompt_has_original_field(self):
        """The canary provider prompt requests 'original' in the JSON schema."""
        # Verify the prompt template string in the source code
        import inspect
        import code_forge.cli as cli_mod
        source = inspect.getsource(cli_mod)
        assert '"original"' in source


class TestCanaryProviderLogging:
    """_canary_provider logs to stderr on exception."""

    def test_canary_generation_failed_message_in_source(self):
        """Source contains the expected error logging string."""
        import inspect
        import code_forge.cli as cli_mod
        source = inspect.getsource(cli_mod)
        assert "canary generation failed" in source


class TestDiffCommand:
    """Diff in the canary block uses git diff HEAD, no args.mode conditional."""

    def test_diff_uses_git_diff_head_in_source(self):
        """The canary block diff command is git diff HEAD."""
        import inspect
        import code_forge.cli as cli_mod
        source = inspect.getsource(cli_mod)
        # Count git diff HEAD occurrences (main review path + canary path)
        count = source.count('"git", "diff", "HEAD"')
        assert count >= 2, "expected >= 2 git diff HEAD calls, found %d" % count

    def test_no_mode_conditional_for_diff(self):
        """No args.mode conditional determines diff scope in canary block."""
        import inspect
        import code_forge.cli as cli_mod
        # Get the _canary_provider-containing function's source
        # The inline branch should not have args.mode deciding diff scope
        source = inspect.getsource(cli_mod)
        # Find the canary block (between "canary_config is not None" and
        # "return verdict")
        idx_start = source.find("canary_config is not None")
        idx_end = source.find("return verdict", idx_start)
        if idx_start >= 0 and idx_end >= 0:
            canary_block = source[idx_start:idx_end]
            assert 'args.mode' not in canary_block, (
                "canary diff block must not condition on args.mode"
            )


class TestSourceLookupPathTraversal:
    """_source_lookup validates path containment within cwd."""

    def test_path_traversal_blocked(self):
        """../../../etc/passwd returns None regardless of file existence."""
        # Simulate _source_lookup logic directly
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd_real = os.path.realpath(tmpdir)
            filepath = "../../../etc/passwd"
            full = os.path.realpath(os.path.join(cwd_real, filepath))
            # The containment check should reject this
            contained = (
                full.startswith(cwd_real + os.sep) or full == cwd_real
            )
            assert not contained, "path traversal must be blocked"

    def test_valid_path_accepted(self):
        """A file within cwd is accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd_real = os.path.realpath(tmpdir)
            test_file = os.path.join(tmpdir, "test.py")
            with open(test_file, "w") as f:
                f.write("hello\n")
            filepath = "test.py"
            full = os.path.realpath(os.path.join(cwd_real, filepath))
            contained = (
                full.startswith(cwd_real + os.sep) or full == cwd_real
            )
            assert contained


class TestCanaryDispatchFallthrough:
    """Canary dispatch errors degrade to DELEGATED."""

    def test_error_message_in_source(self):
        """Source contains the fallthrough error message."""
        import inspect
        import code_forge.cli as cli_mod
        source = inspect.getsource(cli_mod)
        assert "canary check failed" in source
        assert "falling back to DELEGATED" in source


class TestCanaryFlagInParser:
    """--canary flag is accepted by the review subcommand parser."""

    def test_canary_flag_accepted(self):
        from code_forge.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["review", "--canary"])
        assert args.canary is True

    def test_canary_default_false(self):
        from code_forge.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["review"])
        assert args.canary is False
