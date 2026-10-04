"""Detection must preserve source and refuse incomplete tool evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from code_forge.machine import _default_l0_runner
from code_forge.parsers import parse_output
from code_forge.parsers.base import Finding, ToolError
from code_forge.parsers.ruff import parse_ruff
from code_forge.parsers.semgrep import parse_semgrep
from code_forge.registry import ToolConfig
from code_forge.runner import capture_tool_version, run_tool, sarif_producer_profile


def _result():
    return {
        "ruleId": "F821",
        "message": {"text": "undefined name"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": "sample.py"},
                    "region": {"startLine": 1},
                }
            }
        ],
    }


def _report(results=()):
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "ruff"}},
                "results": list(results),
            }
        ],
    }


def _errors(items):
    return [item for item in items if isinstance(item, ToolError)]


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "  ",
        "{",
        "[]",
        "null",
        "{}",
        json.dumps({"version": "2.0.0", "runs": []}),
        json.dumps({"version": "2.1.0", "runs": None}),
        json.dumps({"version": "2.1.0", "runs": []}),
        json.dumps({"version": "2.1.0", "runs": [None]}),
        json.dumps({"version": "2.1.0", "runs": [{"results": []}]}),
        json.dumps({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "ruff"}}}]}),
    ],
)
@pytest.mark.parametrize("parser", [parse_ruff, parse_semgrep])
def test_incomplete_sarif_is_infrastructure_failure(payload, parser):
    items = parser(payload)
    assert _errors(items), "missing evidence must not become a clean scan"
    assert not any(isinstance(item, Finding) for item in items)


@pytest.mark.parametrize(
    "damage",
    [
        None,
        {"message": "bad", "locations": []},
        {"message": {"text": "unlocated"}},
        {"message": {"text": "bad location"}, "locations": [None]},
        {
            "message": {"text": "bad uri"},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": 7}}}],
        },
        {
            "message": {"text": "bad position"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "a.py"},
                        "region": {"startLine": "one"},
                    }
                }
            ],
        },
    ],
)
def test_damaged_sibling_does_not_erase_valid_finding_or_error(damage):
    items = parse_ruff(json.dumps(_report([_result(), damage])), exit_code=1)
    assert [item.rule_id for item in items if isinstance(item, Finding)] == ["F821"]
    assert _errors(items), "a valid sibling must not hide incomplete results"


@pytest.mark.parametrize(
    "exit_code,has_findings,refused",
    [
        (0, False, False),
        (0, True, False),
        (1, True, False),
        (1, False, True),
        (2, False, True),
        (2, True, True),
        (-15, True, True),
        (7, False, True),
    ],
)
def test_ruff_status_contract_in_generic_dispatch(exit_code, has_findings, refused):
    payload = json.dumps(_report([_result()] if has_findings else []))
    items = parse_output(payload, "sarif", "registry-alias", exit_code, producer_profile="ruff")
    assert bool(_errors(items)) is refused
    assert bool([item for item in items if isinstance(item, Finding)]) is has_findings
    assert all(item.tool_name == "registry-alias" for item in items)


def test_nonruff_status_two_with_valid_findings_keeps_its_own_policy():
    items = parse_semgrep(json.dumps(_report([_result()])), exit_code=2)
    assert len(items) == 1 and isinstance(items[0], Finding)


def test_failed_invocation_retains_diagnostic_but_refuses_completion():
    payload = _report([_result()])
    payload["runs"][0]["invocations"] = [{"executionSuccessful": False}]
    items = parse_ruff(json.dumps(payload), exit_code=1)
    assert len([item for item in items if isinstance(item, Finding)]) == 1
    assert _errors(items)


def test_clean_packet_and_trailing_summary_are_supported():
    assert parse_ruff(json.dumps(_report()) + "\nFinished scan") == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "2.0.0"),
        ("tool", {}),
        ("results", {}),
        ("invocations", None),
        ("invocations", [None]),
        ("invocations", [{"executionSuccessful": "true"}]),
    ],
)
def test_one_damaged_metadata_field_cannot_be_clean(field, value):
    packet = _report()
    if field == "version":
        packet[field] = value
    else:
        packet["runs"][0][field] = value
    assert _errors(parse_ruff(json.dumps(packet)))


@pytest.mark.parametrize(
    "field,value",
    [
        ("uri", ""),
        ("text", ""),
        ("ruleId", None),
        ("level", "fatal"),
        ("region", None),
        ("startLine", 0),
        ("startLine", True),
        ("endLine", -1),
        ("endLine", "two"),
        ("startColumn", 0),
    ],
)
def test_unrepresentable_diagnostic_is_refused(field, value):
    diagnostic = _result()
    physical = diagnostic["locations"][0]["physicalLocation"]
    if field == "uri":
        physical["artifactLocation"][field] = value
    elif field == "text":
        diagnostic["message"][field] = value
    elif field in ("ruleId", "level"):
        diagnostic[field] = value
    elif field == "region":
        physical[field] = value
    else:
        physical["region"][field] = value
    assert _errors(parse_ruff(json.dumps(_report([diagnostic])), exit_code=1))


def test_reversed_line_range_is_refused():
    diagnostic = _result()
    diagnostic["locations"][0]["physicalLocation"]["region"] = {"startLine": 5, "endLine": 2}
    assert _errors(parse_ruff(json.dumps(_report([diagnostic])), exit_code=1))


def test_direct_ruff_wrapper_enforces_abnormal_status():
    items = parse_ruff(json.dumps(_report([_result()])), exit_code=2)
    assert len([item for item in items if isinstance(item, Finding)]) == 1
    assert _errors(items)


def test_successful_invocation_and_optional_region_are_supported():
    packet = _report([_result()])
    packet["runs"][0]["invocations"] = [{"executionSuccessful": True}]
    packet["runs"][0]["results"][0]["locations"][0]["physicalLocation"].pop("region")
    items = parse_ruff(json.dumps(packet), exit_code=1)
    assert len(items) == 1 and isinstance(items[0], Finding) and items[0].line == 0


def test_nonruff_nonzero_empty_packet_is_refused():
    assert _errors(parse_semgrep(json.dumps(_report()), exit_code=7))


@pytest.fixture
def installed_ruff():
    path = shutil.which("ruff")
    if path is None:
        pytest.skip("real Ruff executable is unavailable")
    return path


def _tool(command, args=()):
    # A fixed diagnostic selection keeps the oracle independent of user config.
    return ToolConfig("registry-alias", command, ["--select=F", *args], "sarif", ["*.py"])


@pytest.mark.parametrize("invocation", ["binary", "symlink", "module"])
def test_real_ruff_project_fix_true_cannot_mutate_detection(
    tmp_path, monkeypatch, installed_ruff, invocation
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nfix=true\n", encoding="utf-8")
    source = tmp_path / "sample.py"
    original = b"import os\nvalue = 1\n"
    source.write_bytes(original)
    if invocation == "module":
        command = f"{sys.executable} -m ruff check --output-format=sarif"
    elif invocation == "symlink":
        alias = tmp_path / "lint-alias"
        alias.symlink_to(installed_ruff)
        command = f"{alias} check --output-format=sarif"
    else:
        command = f"{installed_ruff} check --output-format=sarif"
    tool = _tool(command)
    findings, infra = _default_l0_runner({tool.name: tool}, [source])
    assert source.read_bytes() == original, "detection changed reviewed source bytes"
    assert len(findings) == 1 and "unused" in findings[0].description
    assert infra == []


@pytest.mark.parametrize("flag", ["--fix", "--fix-only", "--unsafe-fixes", "--diff", "--add-noqa"])
def test_explicit_mutating_flags_are_refused_before_execution(tmp_path, installed_ruff, flag):
    source = tmp_path / "sample.py"
    original = b"import os\n"
    source.write_bytes(original)
    result = run_tool(_tool(f"{installed_ruff} check --output-format=sarif", [flag]), [str(source)])
    assert result is not None
    stdout, status, stderr = result
    assert status != 0 and "detection" in stderr.lower()
    assert not stdout and source.read_bytes() == original


@pytest.mark.parametrize("fault", ["exit2-clean", "exit2-finding", "empty", "truncated"])
def test_real_ruff_native_fault_survives_l0_consumer(tmp_path, monkeypatch, installed_ruff, fault):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sample.py"
    source.write_text("unknown_name\n" if fault == "exit2-finding" else "value = 1\n", encoding="utf-8")
    wrapper = tmp_path / "ruff"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, subprocess, sys\n"
        f"r=subprocess.run([{installed_ruff!r},*sys.argv[1:]],capture_output=True,text=True)\n"
        "if 'check' not in sys.argv:\n    print(r.stdout,end='');sys.exit(r.returncode)\n"
        "pathlib.Path('native.stdout.log').write_text(r.stdout)\n"
        "pathlib.Path('native.stderr.log').write_text(r.stderr)\n"
        f"fault={fault!r}\n"
        "print('' if fault=='empty' else (r.stdout[:20] if fault=='truncated' else r.stdout),end='')\n"
        "print('AUDIT_NATIVE_FAULT',file=sys.stderr)\n"
        "sys.exit(2 if fault.startswith('exit2') else r.returncode)\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    tool = _tool(f"{wrapper} check --output-format=sarif")
    findings, infra = _default_l0_runner({tool.name: tool}, [source])
    assert infra and "AUDIT_NATIVE_FAULT" in infra[0]
    assert len(findings) == (1 if fault == "exit2-finding" else 0)
    assert json.loads((tmp_path / "native.stdout.log").read_text())["version"] == "2.1.0"


def test_real_exit_zero_preserves_findings_and_stderr(tmp_path, installed_ruff):
    source = tmp_path / "sample.py"
    source.write_text("unknown_name\n", encoding="utf-8")
    result = run_tool(
        _tool(f"{installed_ruff} check --output-format=sarif", ["--exit-zero"]), [str(source)]
    )
    assert result is not None and result[1] == 0
    items = parse_ruff(result[0], exit_code=result[1])
    assert len(items) == 1 and isinstance(items[0], Finding)
    assert isinstance(result[2], str), "native stderr remains part of the runner result"


def test_missing_binary_is_refusal():
    tool = _tool("forge-ruff-known-missing check")
    findings, infra = _default_l0_runner({tool.name: tool}, [Path("sample.py")])
    assert not findings and infra


def test_module_version_records_ruff_not_the_interpreter(installed_ruff):
    expected = capture_tool_version(f"{installed_ruff} check")
    actual = capture_tool_version(f"{sys.executable} -m ruff check")
    assert expected.startswith("ruff ") and actual == expected


def test_real_invalid_toml_is_refusal_and_preserves_source(tmp_path, monkeypatch, installed_ruff):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\ninvalid=[\n", encoding="utf-8")
    source = tmp_path / "sample.py"
    source.write_bytes(b"value = 1\n")
    original = source.read_bytes()
    tool = _tool(f"{installed_ruff} check --output-format=sarif")
    findings, infra = _default_l0_runner({tool.name: tool}, [source])
    assert not findings and infra and "TOML" in infra[0]
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    "command", ["", "nonexistent check", "ruff format", "/usr/bin/python3 -m json.tool check"]
)
def test_unrecognized_commands_do_not_inherit_ruff_policy(command):
    assert sarif_producer_profile(command) is None


def test_no_fix_is_before_file_separator(tmp_path, monkeypatch, installed_ruff):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nfix=true\n", encoding="utf-8")
    source = tmp_path / "sample.py"
    original = b"import os\n"
    source.write_bytes(original)
    result = run_tool(_tool(f"{installed_ruff} check --output-format=sarif", ["--"]), [str(source)])
    assert result is not None and result[1] == 1
    assert source.read_bytes() == original
    assert any(item.rule_id == "F401" for item in parse_ruff(result[0], exit_code=1))


@pytest.mark.parametrize(
    "suffix,args,expected",
    [
        ("check", [], "ruff"),
        ("", ["check", "--output-format=sarif"], "ruff"),
        ("--quiet --isolated check", [], "ruff"),
        ("--config check check", [], "ruff"),
        ("--config=check --color=never check", [], "ruff"),
        ("--color never", ["check"], "ruff"),
        ("--verbose -s --silent -v -q --help -h --version -V check", [], "ruff"),
        ("format --check --stdin-filename check", [], None),
        ("format", ["check"], None),
        ("--config check format --check", [], None),
        ("--color check format", [], None),
        ("--config=check format", [], None),
        ("help check", [], None),
        ("-- check", [], None),
        ("--config", [], None),
        ("--config check", [], None),
        ("--color", [], None),
        ("--unknown check", [], None),
        ("", [], None),
    ],
)
def test_profile_uses_subcommand_after_global_options(installed_ruff, suffix, args, expected):
    assert sarif_producer_profile(f"{installed_ruff} {suffix}", args) == expected


@pytest.fixture
def record_native_runs(tmp_path, monkeypatch):
    native_run = subprocess.run
    records = []

    def recorded(argv, **kwargs):
        result = native_run(argv, **kwargs)
        records.append(
            {
                "argv": list(argv),
                "cwd": os.getcwd(),
                "exit_code": result.returncode,
                "stdin": kwargs.get("input"),
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        (tmp_path / "native-runs.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        return result

    monkeypatch.setattr("code_forge.runner.subprocess.run", recorded)


@pytest.mark.parametrize("placement", ["command", "args", "command-separator", "args-separator"])
def test_real_configured_no_fix_is_not_duplicated(
    tmp_path, monkeypatch, installed_ruff, placement, record_native_runs
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nfix=true\n", encoding="utf-8")
    source = tmp_path / "sample.py"
    original = b"import os\nvalue = 1\n"
    source.write_bytes(original)
    command = f"{installed_ruff} check --output-format=sarif"
    args = []
    if placement.startswith("command"):
        command += " --no-fix"
    else:
        args.append("--no-fix")
    if placement.endswith("separator"):
        args.append("--")
    tool = _tool(command, args)
    result = run_tool(tool, [str(source)])
    assert result is not None and result[1] == 1, result
    assert source.read_bytes() == original
    assert any(item.rule_id == "F401" for item in parse_ruff(result[0], exit_code=1))
    findings, infra = _default_l0_runner({tool.name: tool}, [source])
    assert len(findings) == 1 and infra == []
    assert source.read_bytes() == original


@pytest.mark.parametrize("filename_placement", ["files", "configured"])
def test_no_fix_filename_does_not_disable_option(
    tmp_path, monkeypatch, installed_ruff, filename_placement, record_native_runs
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nfix=true\n", encoding="utf-8")
    source = tmp_path / "--no-fix"
    original = b"import os\n"
    source.write_bytes(original)
    args = ["--"]
    files = [source.name]
    if filename_placement == "configured":
        args.append(source.name)
        files = []
    result = run_tool(_tool(f"{installed_ruff} check --output-format=sarif", args), files)
    assert result is not None and result[1] == 1, result
    assert source.read_bytes() == original
    assert any(item.rule_id == "F401" for item in parse_ruff(result[0], exit_code=1))


@pytest.mark.parametrize("global_config", [False, True])
def test_real_formatter_check_values_keep_formatter_contract(
    tmp_path, monkeypatch, installed_ruff, global_config, record_native_runs
):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sample.py"
    original = b"value = 1\n"
    source.write_bytes(original)
    # The file named check is a global option value, never a subcommand.
    (tmp_path / "check").write_text("line-length=88\n", encoding="utf-8")
    prefix = "--config check" if global_config else "--isolated"
    command = f"{installed_ruff} {prefix} format --no-cache --check --stdin-filename check"
    assert sarif_producer_profile(command) is None
    native_run = subprocess.run
    stdin = original.decode()

    def feed_stdin(argv, **kwargs):
        return native_run(argv, input=stdin, **kwargs)

    # --stdin-filename makes Ruff consume stdin instead of the file argument.
    monkeypatch.setattr("code_forge.runner.subprocess.run", feed_stdin)
    tool = ToolConfig("format", command, [], "grep_line", ["*.py"])
    result = run_tool(tool, [str(source)])
    assert result is not None and result[1] == 0, result
    assert source.read_bytes() == original
    (tmp_path / "formatted-stdin.stdout.log").write_text(result[0], encoding="utf-8")
    (tmp_path / "formatted-stdin.stderr.log").write_text(result[2], encoding="utf-8")
    stdin = "value=1\n"
    result = run_tool(tool, [str(source)])
    assert result is not None and result[1] == 1, result
    assert source.read_bytes() == original
    (tmp_path / "unformatted-stdin.stdout.log").write_text(result[0], encoding="utf-8")
    (tmp_path / "unformatted-stdin.stderr.log").write_text(result[2], encoding="utf-8")


def test_real_check_after_global_value_keeps_detection(
    tmp_path, monkeypatch, installed_ruff, record_native_runs
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "check").write_text("fix=true\n", encoding="utf-8")
    source = tmp_path / "sample.py"
    original = b"import os\n"
    source.write_bytes(original)
    tool = _tool(f"{installed_ruff} --config check --color never check --output-format=sarif")
    findings, infra = _default_l0_runner({tool.name: tool}, [source])
    assert len(findings) == 1 and infra == []
    assert source.read_bytes() == original


@pytest.mark.parametrize("configuration", ["fix-only=true", "fix=true\nfix-only=true"])
@pytest.mark.parametrize(
    "invocation",
    [
        "binary",
        "symlink",
        "module-options",
        "module-args",
        "module-attached-options",
        "module-attached-args",
        "module-stacked",
        "module-explicit-main",
    ],
)
def test_real_fix_only_config_preserves_source_and_findings(
    tmp_path, monkeypatch, installed_ruff, configuration, invocation, record_native_runs
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n" + configuration + "\n")
    source = tmp_path / "sample.py"
    original = b"import os\nvalue = 1\n"
    source.write_bytes(original)
    args = ["--output-format=sarif", "--select=F", "--no-cache"]
    command = f"{installed_ruff} check"
    if invocation == "symlink":
        alias = tmp_path / "lint-alias"
        alias.symlink_to(installed_ruff)
        command = f"{alias} check"
    elif invocation == "module-options":
        command = f"{sys.executable} -B -m ruff check"
    elif invocation == "module-args":
        command = sys.executable
        args = ["-B", "-m", "ruff", "check", *args]
    elif invocation == "module-attached-options":
        command = f"{sys.executable} -B -mruff check"
    elif invocation == "module-attached-args":
        command = sys.executable
        args = ["-B", "-mruff", "check", *args]
    elif invocation == "module-stacked":
        command = f"{sys.executable} -Bmruff check"
    elif invocation == "module-explicit-main":
        command = f"{sys.executable} -m ruff.__main__ check"
    tool = ToolConfig("registry-alias", command, args, "sarif", ["*.py"])
    findings, infra = _default_l0_runner({tool.name: tool}, [source])
    assert source.read_bytes() == original, "fix-only changed the reviewed source"
    assert len(findings) == 1 and "unused" in findings[0].description, "fix-only suppressed diagnostics"
    assert infra == []


@pytest.mark.parametrize("flag", ["--add-ignore", "--add-ignore=reason"])
@pytest.mark.parametrize("placement", ["command", "args"])
def test_real_add_ignore_is_refused_without_native_execution(
    tmp_path, installed_ruff, flag, placement, record_native_runs
):
    source = tmp_path / "sample.py"
    original = b"import os\n"
    source.write_bytes(original)
    command = f"{installed_ruff} check --output-format=sarif"
    args = []
    if placement == "command":
        command += " " + flag
    else:
        args.append(flag)
    result = run_tool(_tool(command, args), [str(source)])
    assert result is not None and result[1] == 2 and "detection" in result[2]
    assert source.read_bytes() == original
    assert not (tmp_path / "native-runs.json").exists(), "mutating invocation reached Ruff"


@pytest.mark.parametrize("placement", ["command", "args", "command-separator", "args-separator"])
def test_real_existing_no_fix_only_is_not_duplicated(
    tmp_path, monkeypatch, installed_ruff, placement, record_native_runs
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nfix-only=true\nfix=true\n")
    source = tmp_path / "sample.py"
    original = b"import os\n"
    source.write_bytes(original)
    command = f"{installed_ruff} check --output-format=sarif"
    args = []
    if placement.startswith("command"):
        command += " --no-fix-only"
    else:
        args.append("--no-fix-only")
    if placement.endswith("separator"):
        args.append("--")
    result = run_tool(_tool(command, args), [str(source)])
    assert result is not None and result[1] == 1, result
    assert source.read_bytes() == original
    assert [item.rule_id for item in parse_ruff(result[0], exit_code=1)] == ["F401"]


def test_no_fix_only_filename_keeps_detection_disable_flags(
    tmp_path, monkeypatch, installed_ruff, record_native_runs
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nfix-only=true\n")
    source = tmp_path / "--no-fix-only"
    original = b"import os\n"
    source.write_bytes(original)
    result = run_tool(_tool(f"{installed_ruff} check --output-format=sarif", ["--"]), [source.name])
    assert result is not None and result[1] == 1, result
    assert source.read_bytes() == original
    assert [item.rule_id for item in parse_ruff(result[0], exit_code=1)] == ["F401"]


@pytest.mark.parametrize(
    "options",
    [
        ["-B"],
        ["-BP"],
        ["-u", "-O"],
        ["-P", "-q"],
        ["-W", "ignore"],
        ["-Wignore"],
        ["-X", "utf8"],
        ["-Xutf8"],
        ["--check-hash-based-pycs", "always"],
        ["--check-hash-based-pycs", "default"],
    ],
)
@pytest.mark.parametrize("placement", ["command", "args"])
def test_real_python_options_share_run_profile_and_version(
    tmp_path, monkeypatch, installed_ruff, options, placement, record_native_runs
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nfix=true\n")
    source = tmp_path / "sample.py"
    original = b"import os\n"
    source.write_bytes(original)
    command = sys.executable
    args = [*options, "-m", "ruff", "check", "--output-format=sarif", "--select=F", "--no-cache"]
    if placement == "command":
        command += " " + " ".join(args[: len(options) + 3])
        args = args[len(options) + 3 :]
    tool = ToolConfig("alias", command, args, "sarif", ["*.py"])
    assert sarif_producer_profile(command, args) == "ruff"
    assert capture_tool_version(command, args) == capture_tool_version(f"{installed_ruff} check")
    findings, infra = _default_l0_runner({tool.name: tool}, [source])
    assert source.read_bytes() == original and infra == []
    assert len(findings) == 1 and "unused" in findings[0].description


@pytest.mark.parametrize(
    "tail",
    [
        "-c print -m ruff check",
        "script.py -B -m ruff check",
        "-- -m ruff check",
        "--unknown -m ruff check",
        "-W",
        "-X",
        "-B",
        "-m",
        "-m json.tool check",
        "-W -m ruff check",
        "-X -m ruff check",
        "--check-hash-based-pycs",
        "--check-hash-based-pycs invalid -m ruff check",
        "--check-hash-based-pycs=invalid -m ruff check",
        "--check-hash-based-pycs=never -m ruff check",
        "-i -m ruff check",
        "-V -m ruff check",
        "-h -m ruff check",
    ],
)
def test_python_scripts_unknown_or_incomplete_options_are_not_ruff(tail):
    assert sarif_producer_profile(f"{sys.executable} {tail}") is None


@pytest.mark.parametrize("value", ["--", "--fix", "--no-fix-only"])
@pytest.mark.parametrize("placement", ["command", "args", "split-value"])
@pytest.mark.parametrize("separator", [False, True])
@pytest.mark.parametrize("configured_disable", [False, True])
def test_real_python_option_values_do_not_select_ruff_flags(
    tmp_path,
    monkeypatch,
    installed_ruff,
    value,
    placement,
    separator,
    configured_disable,
    record_native_runs,
):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / ("--fix" if separator else "sample.py")
    original = b"import os\n"
    source.write_bytes(original)
    prefix = [sys.executable, "-X", value, "-m", "ruff"]
    ruff_args = [
        "check",
        "--isolated",
        "--no-cache",
        "--output-format=sarif",
        "--select=F",
        "--config",
        "fix-only=true",
    ]
    if configured_disable:
        ruff_args.extend(["--no-fix", "--no-fix-only"])
    if separator:
        ruff_args.append("--")
    if placement == "command":
        command, args = " ".join(prefix), ruff_args
    elif placement == "split-value":
        command, args = f"{sys.executable} -X", prefix[2:] + ruff_args
    else:
        command, args = sys.executable, prefix[1:] + ruff_args
    tool = ToolConfig("alias", command, args, "sarif", ["*.py"])
    assert sarif_producer_profile(command, args) == "ruff"
    result = run_tool(tool, [source.name])
    assert result is not None and result[1] == 1, result
    assert source.read_bytes() == original
    items = parse_ruff(result[0], exit_code=result[1])
    assert [item.rule_id for item in items if isinstance(item, Finding)] == ["F401"]
    assert _errors(items) == []
    records = json.loads((tmp_path / "native-runs.json").read_text())
    assert len(records) == 1
    argv = records[0]["argv"]
    assert argv[: len(prefix)] == prefix
    tail = argv[len(prefix) :]
    before_separator = tail[: tail.index("--")] if "--" in tail else tail
    assert before_separator.count("--no-fix") == 1
    assert before_separator.count("--no-fix-only") == 1
    if separator:
        assert tail[tail.index("--") + 1 :] == [source.name]


@pytest.mark.parametrize("value", ["--", "--fix", "--no-fix-only"])
@pytest.mark.parametrize("flag", ["--fix", "--add-ignore=reason"])
def test_python_option_value_does_not_hide_real_mutating_ruff_flag(
    tmp_path, installed_ruff, value, flag, record_native_runs
):
    source = tmp_path / "sample.py"
    original = b"import os\n"
    source.write_bytes(original)
    args = ["-X", value, "-m", "ruff", "check", "--isolated", "--no-cache", flag]
    result = run_tool(ToolConfig("alias", sys.executable, args, "sarif", ["*.py"]), [str(source)])
    assert result is not None and result[1] == 2 and "detection refuses" in result[2]
    assert source.read_bytes() == original
    assert not (tmp_path / "native-runs.json").exists()


@pytest.mark.parametrize(
    "options",
    [["-B"], ["-X", "--"], ["-X", "--fix"], ["-X", "--no-fix-only"], ["-t"], ["-tt"], ["-Bt"]],
)
def test_all_version_consumers_use_module_prefix_from_args(
    tmp_path, monkeypatch, installed_ruff, capsys, record_native_runs, options
):
    from code_forge.cli import _emit_ci_output
    from code_forge.doctor import _audit_tools
    from code_forge.runner import run_tools
    from code_forge.state import Mode, State, Verdict, save_state
    import yaml

    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sample.py"
    source.write_bytes(b"unknown_name\n")
    config = tmp_path / ".code-forge"
    config.mkdir()
    args = [
        *options,
        "-m",
        "ruff",
        "check",
        "--isolated",
        "--output-format=sarif",
        "--select=F",
        "--no-cache",
    ]
    tool = ToolConfig("alias", sys.executable, args, "sarif", ["*.py"])
    expected = capture_tool_version(installed_ruff)
    results, versions, skipped, errors = run_tools({tool.name: tool}, [str(source)])
    assert versions == {"alias": expected} and skipped == errors == []
    assert results["alias"][1] == 1
    state_path = config / "state.json"
    save_state(State(mode=Mode.CI, verdict=Verdict.PASS), state_path)
    _emit_ci_output(state_path, {tool.name: tool})
    sarif = json.loads(capsys.readouterr().out)
    assert "alias=" + expected in sarif["runs"][0]["tool"]["driver"]["semanticVersion"]
    (config / "tools.yaml").write_text(
        yaml.safe_dump(
            {
                "tools": {
                    "alias": {
                        "command": sys.executable,
                        "args": args,
                        "output_format": "sarif",
                        "file_patterns": ["*.py"],
                    }
                }
            }
        )
    )
    assert _audit_tools(tmp_path) == [(True, "alias: " + expected)]


@pytest.mark.parametrize("binary", ["env", "opaque-wrapper", "/bin/sh"])
def test_non_python_binary_cannot_claim_python_module_prefix(binary):
    assert sarif_producer_profile(f"{binary} -B -m ruff check") is None


def _reference_report():
    packet = _report([_result(), _result()])
    run = packet["runs"][0]
    run["artifacts"] = [
        {"location": {"uri": "sample.py"}},
        {"location": {"uri": "second.py"}},
    ]
    run["tool"]["driver"]["rules"] = [
        {
            "id": "F821",
            "guid": "11111111-0000-1111-8888-000000000001",
            "messageStrings": {"d": {"text": "undefined {0}"}},
        }
    ]
    run["results"][1]["ruleIndex"] = 0
    return packet, run, run["results"][1]


def _assert_reference_finding(packet, expected="undefined name"):
    items = parse_output(json.dumps(packet), "sarif", "alias", 0)
    findings = [item for item in items if isinstance(item, Finding)]
    assert len(findings) == 2
    assert not _errors(items)
    assert findings[1].file == "sample.py" and findings[1].message == expected
    return findings[1]


def _assert_reference_refusal(packet):
    items = parse_output(json.dumps(packet), "sarif", "alias", 0)
    assert len([item for item in items if isinstance(item, Finding)]) == 1
    assert _errors(items), "a broken reference must retain an infrastructure error"


@pytest.mark.parametrize("parser", [parse_ruff, parse_semgrep])
@pytest.mark.parametrize("reference", ["artifact", "message"])
def test_reference_forms_survive_adapters_dispatch_and_l0(monkeypatch, parser, reference):
    packet, _run, result = _reference_report()
    if reference == "artifact":
        result["locations"][0]["physicalLocation"]["artifactLocation"] = {"index": 0}
    else:
        result["message"] = {"id": "d", "arguments": ["name"]}
    payload = json.dumps(packet)
    _assert_reference_finding(packet)
    assert len(parser(payload)) == 2
    tool = ToolConfig("alias", "opaque-wrapper", [], "sarif", ["*.py"])

    def supplied_tools(registry, files, *, cwd):
        assert registry == {"alias": tool} and files == ["sample.py"]
        assert cwd is None
        return {"alias": (payload, 0, "")}, {}, [], []

    monkeypatch.setattr("code_forge.runner.run_tools", supplied_tools)
    findings, infra = _default_l0_runner({"alias": tool}, [Path("sample.py")])
    assert len(findings) == 2 and not infra
    assert findings[1].description == "undefined name"


@pytest.mark.parametrize("value", [True, False, -1, -2, 2, "0", None, 1.0])
def test_artifact_bad_index_cannot_select_a_neighbor(value):
    packet, _run, result = _reference_report()
    result["locations"][0]["physicalLocation"]["artifactLocation"] = {"index": value}
    _assert_reference_refusal(packet)


@pytest.mark.parametrize(
    "damage", ["table", "entry", "location", "cycle", "cached-index", "uri", "base"]
)
def test_broken_artifact_reference_retains_neighbor(damage):
    packet, run, result = _reference_report()
    artifact = {"index": 0}
    result["locations"][0]["physicalLocation"]["artifactLocation"] = artifact
    if damage == "table":
        run["artifacts"] = {}
    elif damage == "entry":
        run["artifacts"][0] = None
    elif damage == "location":
        run["artifacts"][0]["location"] = []
    elif damage == "cycle":
        run["artifacts"][0]["location"] = {"index": 0}
    elif damage == "cached-index":
        run["artifacts"][0]["location"]["index"] = 1
    elif damage == "uri":
        artifact["uri"] = "other.py"
    else:
        artifact.update(uri="sample.py", uriBaseId="OTHER")
    _assert_reference_refusal(packet)


def test_artifact_matching_inline_and_cached_identities_are_supported():
    packet, run, result = _reference_report()
    run["artifacts"][0]["location"]["index"] = 0
    result["locations"][0]["physicalLocation"]["artifactLocation"] = {"uri": "sample.py", "index": 0}
    _assert_reference_finding(packet)


@pytest.mark.parametrize(
    "selector", ["index", "later-index", "guid", "id", "rule-alias", "hierarchical"]
)
def test_rule_reference_selects_its_message(selector):
    packet, run, result = _reference_report()
    rule = run["tool"]["driver"]["rules"][0]
    result["message"] = {"id": "d", "arguments": ["name"]}
    if selector == "later-index":
        run["tool"]["driver"]["rules"].insert(0, {"id": "decoy"})
        result["ruleIndex"] = 1
    elif selector == "guid":
        del result["ruleIndex"]
        result["rule"] = {"guid": rule["guid"]}
    elif selector == "id":
        del result["ruleIndex"]
    elif selector == "rule-alias":
        result["rule"] = {"index": 0, "id": "F821"}
    elif selector == "hierarchical":
        result["ruleId"] = "F821/detail"
    _assert_reference_finding(packet)


@pytest.mark.parametrize("selector", ["index", "later-index", "guid", "driver-guid", "driver-name"])
def test_message_lookup_stays_in_one_selected_component(selector):
    packet, run, result = _reference_report()
    driver = run["tool"]["driver"]
    driver["guid"] = "11111111-0000-1111-8888-000000000002"
    extension = json.loads(json.dumps(driver))
    extension.update(name="extension", guid="11111111-0000-1111-8888-000000000003")
    extension["rules"][0]["messageStrings"]["d"]["text"] = "extension {0}"
    run["tool"]["extensions"] = [extension]
    component = {"name": "extension", "guid": extension["guid"]}
    expected = "extension name"
    if selector == "later-index":
        run["tool"]["extensions"].insert(0, driver.copy())
        component["index"] = 1
    elif selector == "index":
        component["index"] = 0
    elif selector.startswith("driver"):
        component = {"name": "ruff"}
        if selector == "driver-guid":
            component["guid"] = driver["guid"]
        expected = "undefined name"
    result["rule"] = {"toolComponent": component}
    result["message"] = {"id": "d", "arguments": ["name"]}
    _assert_reference_finding(packet, expected)


@pytest.mark.parametrize("level", ["rule", "global", "global-no-rule", "direct"])
def test_message_lookup_precedence_and_fallback(level):
    packet, run, result = _reference_report()
    driver = run["tool"]["driver"]
    driver["globalMessageStrings"] = {"d": {"text": "global {0}"}}
    result["message"] = {"id": "d", "arguments": ["name"]}
    expected = "undefined name"
    if level.startswith("global"):
        del driver["rules"][0]["messageStrings"]
        expected = "global name"
        if level == "global-no-rule":
            del result["ruleIndex"]
            driver["rules"] = []
    elif level == "direct":
        result["message"]["text"] = "direct {0}"
        result["ruleIndex"] = -1
        expected = "direct name"
    _assert_reference_finding(packet, expected)


@pytest.mark.parametrize(
    "damage",
    [
        "rule-bool",
        "rule-negative",
        "rule-outside",
        "rule-guid",
        "rule-guid-alias",
        "rule-id",
        "rule-hierarchy",
        "rule-alias",
        "rule-index-alias",
        "rule-reference-bool",
        "rule-container",
        "rule-table",
        "rule-id-table",
        "rule-object",
        "rule-duplicate",
        "guid-duplicate",
        "component-bool",
        "component-negative",
        "component-outside",
        "component-guid",
        "component-name",
        "component-guid-duplicate",
        "component-container",
        "component-guid-table",
        "component-default-name",
        "message-missing",
        "message-table",
        "message-object",
    ],
)
def test_broken_rule_and_component_references_retain_neighbor(damage):
    packet, run, result = _reference_report()
    driver = run["tool"]["driver"]
    result["message"] = {"id": "d", "arguments": ["name"]}
    if damage.startswith("rule-"):
        if damage == "rule-bool":
            result["ruleIndex"] = False
        elif damage == "rule-negative":
            result["ruleIndex"] = -1
        elif damage == "rule-outside":
            result["ruleIndex"] = 1
        elif damage == "rule-guid":
            result["rule"] = {"guid": "missing"}
        elif damage == "rule-guid-alias":
            result["rule"] = {"index": 0, "guid": "missing"}
        elif damage == "rule-id":
            result["ruleId"] = "other"
        elif damage == "rule-hierarchy":
            result["ruleId"] = "F821/too/deep"
        elif damage == "rule-alias":
            result["rule"] = {"id": "other"}
        elif damage == "rule-index-alias":
            result["rule"] = {"index": 1}
        elif damage == "rule-reference-bool":
            result["rule"] = {"index": False}
        elif damage == "rule-container":
            result["rule"] = [["index", 0]]
        elif damage == "rule-table":
            driver["rules"] = {}
        elif damage == "rule-id-table":
            del result["ruleIndex"]
            driver["rules"] = {}
        elif damage == "rule-object":
            driver["rules"][0] = None
        else:
            del result["ruleIndex"]
            driver["rules"].append(driver["rules"][0].copy())
    elif damage == "guid-duplicate":
        del result["ruleIndex"]
        result["rule"] = {"guid": driver["rules"][0]["guid"]}
        driver["rules"].append(driver["rules"][0].copy())
    elif damage.startswith("component-"):
        driver["guid"] = "shared"
        run["tool"]["extensions"] = [{"name": "extension", "guid": "shared"}]
        component = {"index": 0}
        if damage == "component-bool":
            component["index"] = False
        elif damage == "component-negative":
            component["index"] = -1
        elif damage == "component-outside":
            component["index"] = 1
        elif damage == "component-guid":
            component = {"guid": "missing"}
        elif damage == "component-name":
            component["name"] = "wrong"
        elif damage == "component-container":
            component = None
        elif damage == "component-guid-table":
            component = {"guid": "shared"}
            run["tool"]["extensions"] = {}
        elif damage == "component-default-name":
            component = {"name": "wrong"}
        else:
            component = {"guid": "shared"}
        result["rule"] = {"toolComponent": component}
    elif damage == "message-missing":
        result["message"]["id"] = "missing"
    elif damage == "message-table":
        driver["rules"][0]["messageStrings"] = []
    else:
        driver["rules"][0]["messageStrings"]["d"] = {}
    _assert_reference_refusal(packet)


@pytest.mark.parametrize(
    "text,args,expected",
    [
        ("{0}/{1}/{0}", ["a", "b"], "a/b/a"),
        ("{{{0}}}", ["x"], "{x}"),
        ("{{0}} {0}", ["{1}"], "{0} {1}"),
        ("{name} {0!r} {0:>8} {0.attr}", [], "{name} {0!r} {0:>8} {0.attr}"),
    ],
)
def test_numeric_message_arguments_are_bounded_and_nonrecursive(text, args, expected):
    packet, _run, result = _reference_report()
    result["message"] = {"text": text, "arguments": args}
    _assert_reference_finding(packet, expected)


@pytest.mark.parametrize("args", [None, {}, "x", [1], [True], []])
def test_missing_or_malformed_message_arguments_retain_neighbor(args):
    packet, _run, result = _reference_report()
    result["message"] = {"id": "d", "arguments": args}
    _assert_reference_refusal(packet)


def test_indexed_artifact_selects_the_requested_slot():
    packet, run, result = _reference_report()
    run["artifacts"][0]["location"]["uri"] = "decoy.py"
    run["artifacts"][1]["location"]["uri"] = "sample.py"
    result["locations"][0]["physicalLocation"]["artifactLocation"] = {"index": 1}
    _assert_reference_finding(packet)


def test_message_id_selects_its_own_template():
    packet, run, result = _reference_report()
    run["tool"]["driver"]["rules"][0]["messageStrings"]["alternative"] = {"text": "alternative {0}"}
    result["message"] = {"id": "alternative", "arguments": ["name"]}
    _assert_reference_finding(packet, "alternative name")


@pytest.mark.parametrize(
    "message",
    [
        {"text": "literal", "arguments": [True]},
        {"text": "{1}", "arguments": ["name"]},
    ],
)
def test_invalid_argument_metadata_cannot_hide_behind_literal_text(message):
    packet, _run, result = _reference_report()
    result["message"] = message
    _assert_reference_refusal(packet)


def test_empty_inline_text_resolves_the_referenced_message():
    packet, _run, result = _reference_report()
    result["message"] = {"text": "", "id": "d", "arguments": ["name"]}
    _assert_reference_finding(packet)


@pytest.mark.parametrize("text", ["literal {0}", "literal {{0}}", "literal {999}"])
def test_inline_ruff_without_argument_metadata_remains_literal(text):
    result = _result()
    result["message"] = {"text": text}
    items = parse_output(json.dumps(_report([result])), "sarif", "alias", 1, producer_profile="ruff")
    assert len(items) == 1 and isinstance(items[0], Finding)
    assert items[0].message == text


def test_generic_inline_placeholders_still_require_arguments():
    result = _result()
    result["message"] = {"text": "literal {0}"}
    items = parse_output(json.dumps(_report([result])), "sarif", "alias", 0)
    assert len(items) == 1 and isinstance(items[0], ToolError)


def test_real_ruff_docstring_braces_reach_l0_unchanged(tmp_path, installed_ruff):
    source = tmp_path / "sample.py"
    original = b'def f():\n    """Returns {0}."""\n    return 1\n'
    source.write_bytes(original)
    tool = _tool(
        f"{installed_ruff} check --isolated --output-format=sarif",
        ["--select=D401", "--no-cache"],
    )
    findings, infra = _default_l0_runner({tool.name: tool}, [source])
    assert source.read_bytes() == original
    assert len(findings) == 1 and "Returns {0}." in findings[0].description
    assert infra == []


_PYTHON_CLUSTER_PREFIXES = [
    ["-BWignore", "-m", "ruff"],
    ["-BXutf8", "-m", "ruff"],
    ["-BW", "ignore", "-m", "ruff"],
    ["-BX", "utf8", "-m", "ruff"],
    ["-R", "-m", "ruff"],
    ["-BR", "-m", "ruff"],
    ["-Bm", "ruff"],
    ["-Bmruff"],
    ["-BWignore", "-Bmruff"],
    ["-BXutf8", "-mruff.__main__"],
    ["-BX", "mruff", "-m", "ruff"],
    ["-BWignore:mruff", "-m", "ruff"],
    ["-t", "-m", "ruff"],
    ["-tt", "-m", "ruff"],
    ["-Bt", "-m", "ruff"],
]


@pytest.mark.parametrize("prefix", _PYTHON_CLUSTER_PREFIXES)
@pytest.mark.parametrize("configuration", ["fix=true", "fix-only=true"])
@pytest.mark.parametrize("placement", ["command", "args"])
def test_real_python_clusters_preserve_source_findings_and_version(
    tmp_path, monkeypatch, installed_ruff, record_native_runs, prefix, configuration, placement
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n" + configuration + "\n")
    source = tmp_path / "sample.py"
    original = b"import os\nvalue = 1\n"
    source.write_bytes(original)
    command = sys.executable
    args = [*prefix, "check", "--select=F401", "--output-format=sarif", "--no-cache"]
    if placement == "command":
        command += " " + " ".join(prefix)
        args = args[len(prefix) :]
    tool = ToolConfig("alias", command, args, "sarif", ["*.py"])
    assert sarif_producer_profile(command, args) == "ruff"
    assert capture_tool_version(command, args) == capture_tool_version(installed_ruff)
    findings, infra = _default_l0_runner({tool.name: tool}, [source])
    assert source.read_bytes() == original, "Python option clusters bypassed detection protection"
    assert len(findings) == 1 and "unused" in findings[0].description
    assert infra == []


@pytest.mark.parametrize("prefix", _PYTHON_CLUSTER_PREFIXES)
def test_real_python_clusters_preserve_literal_native_diagnostics(
    tmp_path, installed_ruff, prefix, record_native_runs
):
    source = tmp_path / "sample.py"
    original = b'def f():\n    """Returns {0}."""\n    return 1\n'
    source.write_bytes(original)
    args = [*prefix, "check", "--isolated", "--select=D401", "--output-format=sarif", "--no-cache"]
    tool = ToolConfig("alias", sys.executable, args, "sarif", ["*.py"])
    findings, infra = _default_l0_runner({tool.name: tool}, [source])
    assert source.read_bytes() == original
    assert len(findings) == 1 and "Returns {0}." in findings[0].description
    assert infra == []


@pytest.mark.parametrize("prefix", _PYTHON_CLUSTER_PREFIXES)
@pytest.mark.parametrize("flag", ["--fix", "--add-ignore=reason"])
def test_python_clusters_refuse_mutating_flags_before_execution(
    tmp_path, installed_ruff, prefix, flag, record_native_runs
):
    source = tmp_path / "sample.py"
    original = b"import os\n"
    source.write_bytes(original)
    args = [*prefix, "check", "--isolated", "--output-format=sarif", flag]
    result = run_tool(ToolConfig("alias", sys.executable, args, "sarif", ["*.py"]), [str(source)])
    assert result is not None and result[1] == 2 and "detection refuses" in result[2]
    assert source.read_bytes() == original
    assert not (tmp_path / "native-runs.json").exists(), "mutating invocation reached Ruff"


@pytest.mark.parametrize(
    "prefix",
    [
        ["-BWmruff"],
        ["-BXmruff"],
        ["-BW", "mruff"],
        ["-BX", "mruff"],
        ["-BWmruff", "check"],
        ["-BXmruff", "check"],
        ["-BW", "-mruff"],
        ["-BX", "-mruff"],
        ["-Bm", "json.tool"],
        ["-Bmjson.tool"],
        ["-Bm"],
        ["-BW"],
        ["-BX"],
        ["-BZ", "-m", "ruff"],
        ["-BWignore", "script.py", "-m", "ruff"],
        ["-BR", "--", "-m", "ruff"],
        ["-BR", "-c", "print", "-m", "ruff"],
        ["-BR", "-"],
    ],
)
def test_python_cluster_values_and_scripts_do_not_claim_ruff(prefix):
    assert sarif_producer_profile(sys.executable, [*prefix, "check"]) is None
