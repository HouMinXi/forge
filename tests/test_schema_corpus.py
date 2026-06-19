# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Corpus round-trip test: gate.schema.json vs real loader.

Each snippet is judged by BOTH jsonschema (gate.schema.json) and the real
loader (load_backend_configs / load_gate_config). Valid snippets must pass
both. Invalid snippets must fail the loader. Loader-only constraints (those
the schema cannot enforce mechanically) are declared in $comment fields in
the schema and tested against the loader in labelled test functions.

This test is the anti-drift gate that prevents gate.schema.json from
diverging from the actual loader behaviour as new fields are added.
"""
from __future__ import annotations

import importlib.resources
import json
import os
import pathlib
import tempfile

import jsonschema
import pytest
from jsonschema import Draft202012Validator

from code_forge.backend import load_backend_configs
from code_forge.errors import CliError
from code_forge.gate_check import load_gate_config
from code_forge.outlet_resolver import resolve_outlet

# ---------------------------------------------------------------------------
# Schema: loaded once at module level
# ---------------------------------------------------------------------------

_SCHEMA_TEXT = (
    importlib.resources.files("code_forge")
    .joinpath("gate.schema.json")
    .read_text(encoding="utf-8")
)
SCHEMA = json.loads(_SCHEMA_TEXT)

# ---------------------------------------------------------------------------
# Shared constant: a minimal valid gate.yaml with a test section.
# load_gate_config requires a 'test' section (gate_check.py:63).
# ---------------------------------------------------------------------------

VALID_YAML_WITH_TEST = """
test:
  command: [pytest, -q]
  timeout_seconds: 60
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema_validate(doc: dict) -> None:
    """Validate doc against gate.schema.json using Draft202012Validator.

    Raises jsonschema.ValidationError on failure.
    """
    Draft202012Validator(SCHEMA).validate(doc)


def _loader_accepts(yaml_text: str) -> bool:
    """Write yaml_text to a temp file, call load_gate_config, return True if ok.

    Catches ValueError and returns False. Does NOT catch FileNotFoundError
    (that would mask a path bug).
    """
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        os.write(fd, yaml_text.encode("utf-8"))
        os.close(fd)
        fd = -1
        load_gate_config(pathlib.Path(path))
        return True
    except ValueError:
        return False
    finally:
        if fd != -1:
            os.close(fd)
        try:
            os.unlink(path)
        except OSError:
            pass


def _backends_accept(data: dict) -> bool:
    """Call load_backend_configs(data), return True if no exception.

    Catches CliError and returns False.
    """
    try:
        load_backend_configs(data)
        return True
    except CliError:
        return False


# ===========================================================================
# VALID CORPUS -- schema passes AND loader passes
# ===========================================================================


def test_valid_empty() -> None:
    """Empty dict: all fields optional, no test section, no backends."""
    _schema_validate({})
    assert load_backend_configs({}) == []


def test_valid_api_anthropic() -> None:
    """Single api backend, format=anthropic with full fields."""
    data = {
        "backends": {
            "claude-api": {
                "type": "api",
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key_env": "ANTHROPIC_API_KEY",
                "model": "claude-sonnet-4-6",
                "max_tokens": 16384,
                "default": True,
            }
        }
    }
    _schema_validate(data)
    cfgs = load_backend_configs(data)
    assert len(cfgs) == 1
    assert cfgs[0].type == "api"
    assert cfgs[0].format == "anthropic"


def test_valid_api_openai() -> None:
    """Single api backend, format=openai, no model, no max_tokens."""
    data = {
        "backends": {
            "openai-compatible": {
                "type": "api",
                "format": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
            }
        }
    }
    _schema_validate(data)
    cfgs = load_backend_configs(data)
    assert cfgs[0].format == "openai"


def test_valid_api_vertex() -> None:
    """Single api backend, format=vertex with project_id and region."""
    data = {
        "backends": {
            "vertex-claude": {
                "type": "api",
                "format": "vertex",
                "project_id": "my-project",
                "region": "us-central1",
            }
        }
    }
    _schema_validate(data)
    cfgs = load_backend_configs(data)
    assert cfgs[0].format == "vertex"


def test_valid_cli_backend() -> None:
    """Single cli backend with model and command."""
    data = {
        "backends": {
            "local-claude": {
                "type": "cli",
                "model": "claude-sonnet-4-6",
                "command": "claude",
            }
        }
    }
    _schema_validate(data)
    cfgs = load_backend_configs(data)
    assert cfgs[0].type == "cli"


def test_valid_outlet_subprocess() -> None:
    """outlet: subprocess is a valid enum value."""
    _schema_validate({"outlet": "subprocess"})
    assert _loader_accepts(VALID_YAML_WITH_TEST + "\noutlet: subprocess\n") is True


def test_valid_outlet_inline() -> None:
    """outlet: inline is a valid enum value."""
    _schema_validate({"outlet": "inline"})
    assert _loader_accepts(VALID_YAML_WITH_TEST + "\noutlet: inline\n") is True


def test_valid_outlet_subagent() -> None:
    """outlet: subagent is a valid enum value."""
    _schema_validate({"outlet": "subagent"})
    assert _loader_accepts(VALID_YAML_WITH_TEST + "\noutlet: subagent\n") is True


def test_valid_non_ascii_strict() -> None:
    """non_ascii: strict is a valid value."""
    _schema_validate({"non_ascii": "strict"})
    assert _loader_accepts(VALID_YAML_WITH_TEST + "\nnon_ascii: strict\n") is True


def test_valid_graph_triage_enabled() -> None:
    """graph_triage with enabled: true is valid."""
    _schema_validate({"graph_triage": {"enabled": True}})
    assert (
        _loader_accepts(VALID_YAML_WITH_TEST + "\ngraph_triage:\n  enabled: true\n")
        is True
    )


def test_valid_daemon_state_with_conflicts() -> None:
    """daemon_state with enabled, subsystems, conflicts (additionalProperties:true)."""
    _schema_validate(
        {
            "daemon_state": {
                "enabled": True,
                "subsystems": ["nftables"],
                "conflicts": [
                    {
                        "subsystem": "netfilter",
                        "mutates": "nf_tables",
                        "interferes_with": "iptables",
                    }
                ],
            }
        }
    )
    yaml_text = VALID_YAML_WITH_TEST + (
        "\ndaemon_state:\n"
        "  enabled: true\n"
        "  subsystems: [nftables]\n"
        "  conflicts:\n"
        "    - subsystem: netfilter\n"
        "      mutates: nf_tables\n"
        "      interferes_with: iptables\n"
        "      extra_unknown_key: tolerated\n"
    )
    assert _loader_accepts(yaml_text) is True


def test_valid_presubmit_diff() -> None:
    """presubmit entry with on: diff is valid in schema and loader."""
    _schema_validate(
        {
            "presubmit": [
                {
                    "command": ["ruff", "check", "--diff"],
                    "applies_to": "*.py",
                    "on": "diff",
                }
            ]
        }
    )
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\npresubmit:\n"
        "  - command: [ruff, check, --diff]\n"
        "    applies_to: '*.py'\n"
        "    on: diff\n"
    )
    assert _loader_accepts(yaml_text) is True


def test_valid_extra_field_tolerated() -> None:
    """presubmit entries use additionalProperties:true -- extra fields pass both.

    This test will fail if any future schema edit accidentally sets
    additionalProperties:false on presubmit entries.
    """
    _schema_validate(
        {
            "presubmit": [
                {
                    "command": ["pytest"],
                    "applies_to": "*.py",
                    "on": "diff",
                    "extra_unknown_field": "tolerated",
                }
            ]
        }
    )
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\npresubmit:\n"
        "  - command: [pytest]\n"
        "    applies_to: '*.py'\n"
        "    on: diff\n"
        "    extra_unknown_field: tolerated\n"
    )
    assert _loader_accepts(yaml_text) is True


# ===========================================================================
# INVALID CORPUS -- loader MUST reject; schema may or may not reject
# ===========================================================================


def test_invalid_backends_as_list() -> None:
    """backends as a list is rejected by both schema and loader."""
    data: dict = {"backends": [{"name": "x", "type": "api"}]}
    assert _backends_accept(data) is False


def test_invalid_api_missing_format() -> None:
    """api backend with no format field is rejected by loader."""
    data = {"backends": {"my-api": {"type": "api"}}}
    assert _backends_accept(data) is False


def test_invalid_api_missing_base_url() -> None:
    """api/anthropic backend missing base_url is rejected by loader."""
    data = {
        "backends": {
            "my-api": {
                "type": "api",
                "format": "anthropic",
                "api_key_env": "ANTHROPIC_API_KEY",
            }
        }
    }
    assert _backends_accept(data) is False


def test_invalid_api_missing_api_key_env() -> None:
    """api/openai backend missing api_key_env is rejected by loader."""
    data = {
        "backends": {
            "my-api": {
                "type": "api",
                "format": "openai",
                "base_url": "https://api.openai.com/v1",
            }
        }
    }
    assert _backends_accept(data) is False


def test_invalid_vertex_missing_project_id() -> None:
    """vertex backend missing project_id is rejected by BOTH schema and loader."""
    vertex_doc = {"backends": {"v": {"type": "api", "format": "vertex"}}}
    with pytest.raises(jsonschema.ValidationError):
        _schema_validate(vertex_doc)
    assert _backends_accept(vertex_doc) is False


def test_invalid_multiple_defaults() -> None:
    """Two backends with default:true -- schema PASSES (cannot enforce cardinality).

    This verifies the $comment boundary: cross-item cardinality is a loader-only
    constraint. The schema intentionally cannot enforce it. The loader rejects it.
    """
    data = {
        "backends": {
            "alpha": {
                "type": "api",
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key_env": "KEY_A",
                "default": True,
            },
            "beta": {
                "type": "api",
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key_env": "KEY_B",
                "default": True,
            },
        }
    }
    # Schema PASSES -- cannot enforce cross-item uniqueness
    _schema_validate(data)
    # Loader FAILS
    assert _backends_accept(data) is False


def test_invalid_inline_api_key() -> None:
    """Inline api_key (raw secret) is rejected by BOTH schema and loader.

    Backend entry uses additionalProperties:false -- api_key is not in the
    allowed properties. Loader explicitly rejects at backend.py:102-106.
    """
    data = {
        "backends": {
            "my-api": {
                "type": "api",
                "format": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key": "sk-ant-secret",
            }
        }
    }
    with pytest.raises(jsonschema.ValidationError):
        _schema_validate(data)
    assert _backends_accept(data) is False


def test_invalid_test_command_string() -> None:
    """test.command as a string (not a list) is rejected by loader."""
    yaml_text = "test:\n  command: 'pytest'\n  timeout_seconds: 60\n"
    assert _loader_accepts(yaml_text) is False


def test_invalid_test_command_empty() -> None:
    """test.command as an empty list is rejected by loader."""
    yaml_text = "test:\n  command: []\n  timeout_seconds: 60\n"
    assert _loader_accepts(yaml_text) is False


def test_invalid_non_ascii_bad_value() -> None:
    """non_ascii: unicode is rejected by loader (only ai-smell or strict allowed)."""
    yaml_text = VALID_YAML_WITH_TEST + "\nnon_ascii: unicode\n"
    assert _loader_accepts(yaml_text) is False


def test_invalid_presubmit_on_message() -> None:
    """presubmit on: message is rejected by BOTH schema (enum) and loader."""
    bad_doc = {
        "presubmit": [
            {"command": ["ruff"], "applies_to": "*.py", "on": "message"}
        ]
    }
    with pytest.raises(jsonschema.ValidationError):
        _schema_validate(bad_doc)
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\npresubmit:\n"
        "  - command: [ruff, check]\n"
        "    applies_to: '*.py'\n"
        "    on: message\n"
    )
    assert _loader_accepts(yaml_text) is False


def test_invalid_test_timeout_zero() -> None:
    """test.timeout_seconds=0 is rejected by BOTH schema (minimum:1) and loader."""
    with pytest.raises(jsonschema.ValidationError):
        _schema_validate({"test": {"command": ["pytest"], "timeout_seconds": 0}})
    yaml_text = "test:\n  command: [pytest]\n  timeout_seconds: 0\n"
    assert _loader_accepts(yaml_text) is False


def test_invalid_backend_missing_type() -> None:
    """Backend entry with no type field is rejected by BOTH schema and loader."""
    data = {"backends": {"noType": {"format": "openai"}}}
    with pytest.raises(jsonschema.ValidationError):
        _schema_validate(data)
    assert _backends_accept(data) is False


@pytest.mark.parametrize(
    "entry,_missing_field",
    [
        ({"applies_to": "*.py", "on": "diff"}, "command"),
        ({"command": ["pytest"], "on": "diff"}, "applies_to"),
        ({"command": ["pytest"], "applies_to": "*.py"}, "on"),
    ],
)
def test_invalid_presubmit_missing_required(
    entry: dict, _missing_field: str
) -> None:
    """presubmit items with a missing required field are rejected by schema."""
    with pytest.raises(jsonschema.ValidationError):
        _schema_validate({"presubmit": [entry]})


# ===========================================================================
# Loader-only constraints: schema uses $comment, loader enforces.
# ===========================================================================


def test_loader_only_outlet_cli_alias() -> None:
    """outlet: cli is deprecated alias for subprocess.

    (a) Schema rejects 'cli' (not in enum [subprocess, inline, subagent]).
    (b) resolve_outlet maps 'cli' -> 'subprocess' via the deprecated alias dict.
    (c) load_gate_config ignores the outlet field entirely -- the unknown value
        is silently tolerated (gate_check.py is outlet-agnostic). Assertion (c)
        documents that, NOT that load_gate_config resolves the alias.
    """
    with pytest.raises(jsonschema.ValidationError):
        _schema_validate({"outlet": "cli"})

    result = resolve_outlet(env={}, cli_value="cli")
    assert result == "subprocess"

    assert _loader_accepts(VALID_YAML_WITH_TEST + "\noutlet: cli\n") is True


# ===========================================================================
# SIBLINGS CORPUS -- cross-repo review config
# ===========================================================================


def test_valid_siblings_minimal() -> None:
    """Minimal siblings: one entry with repo + ref only, no label."""
    doc = {
        "siblings": [
            {"repo": "../sibling", "ref": "main..feature"}
        ]
    }
    _schema_validate(doc)
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  - repo: ../sibling\n"
        "    ref: main..feature\n"
    )
    assert _loader_accepts(yaml_text) is True


def test_valid_siblings_with_label() -> None:
    """Siblings entry with explicit label passes both schema and loader."""
    doc = {
        "siblings": [
            {"repo": "../plugin", "ref": "main..feature-x", "label": "plugin"}
        ]
    }
    _schema_validate(doc)
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  - repo: ../plugin\n"
        "    ref: main..feature-x\n"
        "    label: plugin\n"
    )
    assert _loader_accepts(yaml_text) is True


def test_valid_siblings_multiple() -> None:
    """Two siblings with distinct labels both pass."""
    doc = {
        "siblings": [
            {"repo": "../alpha", "ref": "main..feat-a", "label": "alpha"},
            {"repo": "../beta", "ref": "main..feat-b", "label": "beta"},
        ]
    }
    _schema_validate(doc)
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  - repo: ../alpha\n"
        "    ref: main..feat-a\n"
        "    label: alpha\n"
        "  - repo: ../beta\n"
        "    ref: main..feat-b\n"
        "    label: beta\n"
    )
    assert _loader_accepts(yaml_text) is True


def test_invalid_siblings_duplicate_label() -> None:
    """Two siblings defaulting to the same label (same repo basename) rejected."""
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  - repo: ../sibling\n"
        "    ref: main..feat-a\n"
        "  - repo: ../../sibling\n"
        "    ref: main..feat-b\n"
    )
    assert _loader_accepts(yaml_text) is False


def test_invalid_siblings_reserved_primary_label() -> None:
    """Explicit label 'primary' is reserved and rejected."""
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  - repo: ../sibling\n"
        "    ref: main..feature\n"
        "    label: primary\n"
    )
    assert _loader_accepts(yaml_text) is False


def test_invalid_siblings_remote_https() -> None:
    """Remote repo URL (https) rejected in v1 (local paths only)."""
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  - repo: https://github.com/x/y\n"
        "    ref: main..feature\n"
    )
    assert _loader_accepts(yaml_text) is False


def test_invalid_siblings_remote_git_at() -> None:
    """Remote repo URL (git@) rejected in v1."""
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  - repo: 'git@github.com:x/y'\n"
        "    ref: main..feature\n"
    )
    assert _loader_accepts(yaml_text) is False


def test_invalid_siblings_missing_ref() -> None:
    """Sibling entry without ref: is rejected."""
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  - repo: ../sibling\n"
    )
    assert _loader_accepts(yaml_text) is False


def test_invalid_siblings_bad_ref_format() -> None:
    """ref with three dots (main...feature) is rejected."""
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  - repo: ../sibling\n"
        "    ref: main...feature\n"
    )
    assert _loader_accepts(yaml_text) is False


def test_invalid_siblings_not_a_list() -> None:
    """siblings: as a dict (not a list) is rejected."""
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  repo: ../sibling\n"
        "  ref: main..feature\n"
    )
    assert _loader_accepts(yaml_text) is False


def test_invalid_siblings_label_special_chars() -> None:
    """Label with spaces or slashes is rejected."""
    yaml_text = (
        VALID_YAML_WITH_TEST
        + "\nsiblings:\n"
        "  - repo: ../sibling\n"
        "    ref: main..feature\n"
        "    label: 'bad label/here'\n"
    )
    assert _loader_accepts(yaml_text) is False


def test_schema_siblings_extra_property() -> None:
    """Unknown field in sibling item rejected by schema (additionalProperties: false)."""
    doc = {
        "siblings": [
            {
                "repo": "../sibling",
                "ref": "main..feature",
                "unknown_field": "oops",
            }
        ]
    }
    with pytest.raises(jsonschema.ValidationError):
        _schema_validate(doc)


def test_invalid_siblings_symlink_escape(tmp_path: pathlib.Path) -> None:
    """Absolute repo path outside project root rejected by symlink guard."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir()
    siblings = [{"repo": "/etc/passwd", "ref": "main..feature"}]
    with pytest.raises(ValueError, match="traverses outside"):
        validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)


def test_invalid_siblings_symlink_escape_abs(tmp_path: pathlib.Path) -> None:
    """Another absolute out-of-bounds path rejected by symlink guard."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir()
    siblings = [{"repo": "/usr/bin/env", "ref": "main..feature"}]
    with pytest.raises(ValueError, match="traverses outside"):
        validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)


def test_valid_siblings_sibling_repo_accepted(tmp_path: pathlib.Path) -> None:
    """repo: ../forge-plugin resolves within project parent and passes."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir()
    siblings = [{"repo": "../forge-plugin", "ref": "main..feature"}]
    validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)


def test_invalid_siblings_label_defaulting_reserved(
    tmp_path: pathlib.Path,
) -> None:
    """repo basename defaults to 'primary' which is reserved -- rejected."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir()
    primary_dir = tmp_path.parent / "primary"
    siblings = [{"repo": str(primary_dir), "ref": "main..feature"}]
    with pytest.raises(ValueError, match="reserved"):
        validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)


def test_invalid_siblings_empty_repo(tmp_path: pathlib.Path) -> None:
    """Empty string repo is rejected at the repo-required guard, not downstream."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir()
    siblings = [{"repo": "", "ref": "main..feature"}]
    with pytest.raises(ValueError, match="repo.*required"):
        validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)


def test_invalid_siblings_empty_ref_baseline(tmp_path: pathlib.Path) -> None:
    """ref with empty baseline (..head) rejected at the ref-format guard."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir()
    siblings = [{"repo": "../sibling", "ref": "..feature"}]
    with pytest.raises(ValueError, match="baseline[.][.]head"):
        validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)


def test_invalid_siblings_empty_ref_head(tmp_path: pathlib.Path) -> None:
    """ref with empty head (baseline..) rejected at the ref-format guard."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir()
    siblings = [{"repo": "../sibling", "ref": "main.."}]
    with pytest.raises(ValueError, match="baseline[.][.]head"):
        validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)


def test_invalid_siblings_ref_dash_start(tmp_path: pathlib.Path) -> None:
    """ref baseline starting with dash rejected by _validate_ref_part."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir()
    siblings = [{"repo": "../sibling", "ref": "-malicious..feature"}]
    with pytest.raises(ValueError, match="must not start with"):
        validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)


def test_invalid_siblings_ref_shell_chars(tmp_path: pathlib.Path) -> None:
    """ref containing shell metacharacters rejected by _validate_ref_part."""
    from code_forge.gate_check import validate_siblings

    gate_yaml_dir = tmp_path / ".code-forge"
    gate_yaml_dir.mkdir()
    siblings = [{"repo": "../sibling", "ref": "main; rm -rf /..feature"}]
    with pytest.raises(ValueError, match="invalid characters"):
        validate_siblings(siblings, gate_yaml_dir=gate_yaml_dir)
