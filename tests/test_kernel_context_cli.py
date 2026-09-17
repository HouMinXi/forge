"""Exercise actual entry ordering before any backend/model side effect."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from code_forge import cli, trust
from code_forge.kernel_context import validate_kernel_context


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user"))
    monkeypatch.delenv("FORGE_PROJECT_DIR", raising=False)
    gate = tmp_path / ".code-forge" / "gate.yaml"
    gate.parent.mkdir()
    data = {"kernel_context": {"enabled": True, "defconfig": "defconfig"}}
    gate.write_text(yaml.safe_dump(data))
    trust.record_trust(gate, data)
    args = SimpleNamespace(quiet=False, allow_main=True)
    return tmp_path, gate, data, args


def test_trust_kernel_only_real_entry(setup, capsys):
    root, gate, data, _ = setup
    assert cli._run_trust(SimpleNamespace(status=False, revoke=False), root) == 0
    assert trust.is_trusted_kernel_context(gate, root, validate_kernel_context(data["kernel_context"]))
    output = capsys.readouterr().err
    assert "defconfig" in output and str(root) in output and "enabled=True" in output


@pytest.mark.parametrize("siblings", [[{}], ["other"], False, 0, "", {}])
def test_siblings_reject_before_outlet_or_model(setup, monkeypatch, siblings):
    root, gate, data, args = setup
    data["siblings"] = siblings
    gate.write_text(yaml.safe_dump(data))
    from code_forge import outlet_resolver
    resolve = Mock(side_effect=AssertionError("outlet must not run"))
    monkeypatch.setattr(outlet_resolver, "resolve_outlet", resolve)
    error = cli.CliError
    expected = ("kernel-context: siblings are not supported while kernel_context is enabled"
                if isinstance(siblings, list) else "siblings: must be a list")
    with pytest.raises(error) as caught:
        cli._run(args, {}, root)
    assert str(caught.value) == expected
    resolve.assert_not_called()


def test_authorization_before_contract_and_outlet(setup, monkeypatch):
    root, _, _, args = setup
    args.contract = "not-opened"
    load = Mock(side_effect=AssertionError("contract before authorization"))
    monkeypatch.setattr(cli, "_load_contract_file", load)
    with pytest.raises(cli.CliError, match="code-forge trust"):
        cli._run(args, {}, root)
    load.assert_not_called()


@pytest.mark.parametrize("outlet", ["inline", "sampling", "subagent"])
def test_supported_path_guard_after_authorization(setup, monkeypatch, outlet):
    root, gate, data, args = setup
    trust.record_kernel_context_trust(gate, root, validate_kernel_context(data["kernel_context"]))
    from code_forge import outlet_resolver
    monkeypatch.setattr(outlet_resolver, "resolve_outlet", lambda *a, **kw: outlet)
    dispatch = Mock(side_effect=AssertionError("dispatch must not run"))
    monkeypatch.setattr(cli, "_dispatch_inline_canary", dispatch)
    with pytest.raises(cli.CliError) as caught:
        cli._run(args, {}, root)
    assert str(caught.value) == (
        f"kernel-context: outlet {outlet} is not supported; only the CLI subprocess review path is supported"
    )
    dispatch.assert_not_called()


@pytest.mark.parametrize("section", [{"enabled": "true"}, {"enabled": True, "defconfig": "defconfig"}])
def test_gate_check_validates_raw_section(setup, section):
    from io import StringIO

    from code_forge.gate_check import run_gate_check
    root, gate, data, _ = setup
    data.update(test={"command": ["pytest"]}, kernel_context=section)
    gate.write_text(yaml.safe_dump(data))
    trust.revoke_trust(gate)
    err = StringIO()
    result = run_gate_check(SimpleNamespace(quiet=False), env={"FORGE_SKIP_TESTS": "1"},
                            cwd=root, stderr=err)
    if section["enabled"] == "true":
        assert result == 1
        assert "kernel_context.enabled" in err.getvalue()
    else:
        assert result == 0
        assert "kernel-context: file access is not authorized" in err.getvalue()


@pytest.mark.parametrize("section,siblings", [(None, None), ({"enabled": "true"}, None),
    ({"enabled": True, "defconfig": "defconfig"}, False)])
def test_invalid_review_configuration_is_cli_error(setup, monkeypatch, capsys, section, siblings):
    import sys
    root, gate, data, _ = setup
    data["kernel_context"] = section
    if siblings is not None:
        data["siblings"] = siblings
    gate.write_text(yaml.safe_dump(data))
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["code-forge", "review", "--allow-main"])
    assert cli.main() == 2
    output = capsys.readouterr().err
    assert "Traceback" not in output
    assert "kernel_context" in output or "siblings: must be a list" in output


@pytest.mark.parametrize("section", [None, {"enabled": "true"}])
def test_trust_invalid_configuration_has_no_traceback(setup, monkeypatch, capsys, section):
    import sys
    root, gate, data, _ = setup
    data["kernel_context"] = section
    gate.write_text(yaml.safe_dump(data))
    trust.revoke_trust(gate)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["code-forge", "trust"])
    assert cli.main() == 2
    assert "Traceback" not in capsys.readouterr().err
    assert not trust.is_trusted(gate, data)


def test_estimate_includes_optional_text():
    base = cli._estimate_l1_prompt_tokens("", "", "", "", "", "", "")
    assert cli._estimate_l1_prompt_tokens("", "", "", "", "", "", "", "x" * 400) == base + 100
