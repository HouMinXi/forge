# SPDX-License-Identifier: Apache-2.0
"""Subprocess fixtures must not write bytecode into the reviewed checkout (#37)."""

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
# Python -I ignores both bytecode controls below. Cache checks must observe real writes.
BYTECODE_ENV_VARS = {"PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"}
PYTHON_LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output"}

# Modules whose launches build their own sanitized environment. The variable
# never reaches those children, so only an explicit `-B` keeps them from
# writing src/**/__pycache__.
SANITIZED_LAUNCHERS = (
    "tests/test_cli_empty_diff.py",
    "tests/test_trust_empty_backends.py",
    "tests/test_pytest_report_plugin.py",
)
# Isolated Python (-I) ignores PYTHONDONTWRITEBYTECODE even when inherited.
ISOLATED_LAUNCHERS = (
    ("test_cli_diagnostic_probe", "test_abrupt_exit_has_no_success_footer"),
    ("test_cli_diagnostic_probe", "test_install_does_not_preimport_forge_and_atexit_emits_footer"),
    ("test_cli_diagnostic_probe", "test_output_security_fixture_is_mask_independent_in_child"),
    (
        "test_cli_diagnostic_run",
        "test_documented_parser_usage_keeps_raw_result_private",
    ),
    (
        "test_cli_diagnostic_run",
        "test_modules_import_without_reader_audit_runner_or_product_preimport",
    ),
)


def _caches(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("__pycache__"))


def _env_without_bytecode_flag(**extra: str) -> dict[str, str]:
    """Drop both bytecode controls; CI may export either one."""
    env = {k: v for k, v in os.environ.items() if k not in BYTECODE_ENV_VARS}
    env.update(extra)
    return env


def _copy_checkout(dest: Path) -> Path:
    """Copy what a test run needs, minus any caches already on disk."""
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in ("src", "tests", "qualification"):
        shutil.copytree(ROOT / name, dest / name, ignore=skip)
    for name in ("pyproject.toml", "conftest.py"):
        shutil.copy2(ROOT / name, dest / name)
    return dest


def test_conftest_exports_no_bytecode_to_children():
    """Import conftest in a child that starts without the variable and read it back."""
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import os, tests.conftest; print(os.environ.get('PYTHONDONTWRITEBYTECODE'))",
        ],
        cwd=ROOT,
        env=_env_without_bytecode_flag(PYTHONPATH=os.pathsep.join([str(ROOT), str(SRC)])),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "1"


def test_inheriting_child_leaves_no_cache_in_checkout_copy(tmp_path):
    copy = tmp_path / "src"
    shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert _caches(copy) == []
    subprocess.run(
        [sys.executable, "-c", "import code_forge.cli"],
        env=_env_without_bytecode_flag(PYTHONPATH=str(copy), **{"PYTHONDONTWRITEBYTECODE": "1"}),
        cwd=tmp_path,
        check=True,
    )
    assert _caches(copy) == []


def test_sanitized_launcher_checklist_is_complete():
    """Pin the launcher list itself: a launcher dropped from the tuple is not measured.

    The checklist below is written out on purpose, apart from SANITIZED_LAUNCHERS.
    Adding a sanitized launcher means touching both places.
    """
    checklist = (
        "tests/test_cli_empty_diff.py",
        "tests/test_trust_empty_backends.py",
        "tests/test_pytest_report_plugin.py",
    )
    assert SANITIZED_LAUNCHERS == checklist
    for rel in checklist:
        assert '"-B"' in (ROOT / rel).read_text(encoding="utf-8"), rel


def _isolated_flags(module: str, name: str) -> bool:
    path = ROOT / "tests" / f"{module}.py"
    function = next(
        (
            node
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ),
        None,
    )
    assert function is not None, (module, name)
    found = False
    for node in ast.walk(function):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr in PYTHON_LAUNCHERS
        ):
            continue
        command = next(
            (
                value
                for value in (*node.args, *(item.value for item in node.keywords if item.arg == "args"))
                if isinstance(value, (ast.List, ast.Tuple))
            ),
            None,
        )
        if command is None:
            continue
        values = []

        def collect(item, found_values):
            if isinstance(item, ast.Starred):
                item = item.value
                if isinstance(item, ast.Name):
                    return False
            if isinstance(item, (ast.Name, ast.Attribute, ast.Call)):
                return True
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                found_values.append(item.value)
                return True
            if isinstance(item, (ast.List, ast.Tuple, ast.Set)):
                return all(collect(part, found_values) for part in item.elts)
            return False

        shaped = all(collect(item, values) for item in command.elts)
        if "-I" in values:
            assert shaped and "-B" in values, (module, name)
            found = True
    return found


def test_isolated_launcher_checklist_is_complete():
    """Literal isolated launches in the diagnostic tests must be measured."""
    detected = sorted(
        (path.stem, node.name)
        for path in (ROOT / "tests").glob("test_cli_diagnostic_*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _isolated_flags(path.stem, node.name)
    )
    assert ISOLATED_LAUNCHERS == tuple(detected)


def test_sanitized_launchers_leave_no_cache_in_checkout_copy(tmp_path, monkeypatch):
    """Run launchers that cannot rely on the inherited flag in a cache-free copy.

    The launchers resolve their imports relative to the copied tests. Pytest
    itself runs with -B, so cache writes come from its children.
    """
    copy = _copy_checkout(tmp_path / "checkout")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "redirected-cache"))
    child_env = _env_without_bytecode_flag(PYTHONPATH=str(copy / "src"))
    inherited_bytecode_flags = {"PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"}.intersection(child_env)
    assert not inherited_bytecode_flags, sorted(inherited_bytecode_flags)
    real_before = _caches(SRC), _caches(ROOT / "tests"), _caches(ROOT / "qualification")
    report = tmp_path / "sanitized-launchers.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-v",
            f"--junitxml={report}",
            # Absolute paths under the copy: the run cannot fall back to the real checkout.
            *[str(copy / rel) for rel in SANITIZED_LAUNCHERS],
            *[f"{copy / 'tests' / module}.py::{name}" for module, name in ISOLATED_LAUNCHERS],
        ],
        cwd=copy,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    # A failed inner run cannot establish the launchers' isolation, even when
    # unrelated tests pass. Exit zero alone also permits skipped witnesses.
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert " passed" in result.stdout, result.stdout[-2000:]
    # These successful calls cover every distinct Python launch under test,
    # including the CLI fixture's trust call. Collection or fixture-only
    # passes do not prove that a child imported the copied source.
    required = {
        ("tests.test_cli_empty_diff.TestEmptyDiffGate", "test_empty_diff_prints_no_changes"),
        ("tests.test_trust_empty_backends.TestTrustEmptyBackends", "test_empty_backends_rejected"),
        (
            "tests.test_trust_empty_backends.TestTrustEmptyBackends",
            "test_commented_out_backends_rejected",
        ),
        ("tests.test_trust_empty_backends.TestTrustEmptyBackends", "test_real_backend_accepted"),
        ("tests.test_pytest_report_plugin", "test_passing_suite_records_final_event"),
        ("tests.test_pytest_report_plugin", "test_disarmed_without_environment"),
        *((f"tests.{module}", name) for module, name in ISOLATED_LAUNCHERS),
    }
    # The XML is produced by our own pytest child in this private test directory.
    cases = list(ElementTree.parse(report).iter("testcase"))  # noqa: S314
    for classname, name in required:
        matches = [
            case
            for case in cases
            if case.get("classname") == classname and (case.get("name") or "").split("[", 1)[0] == name
        ]
        assert matches, (classname, name)
        assert not any(
            child.tag in {"failure", "error", "skipped"} for case in matches for child in case
        ), (classname, name)
    assert (_caches(copy / "src"), _caches(copy / "tests"), _caches(copy / "qualification")) == (
        [],
        [],
        [],
    )
    for rel in SANITIZED_LAUNCHERS:
        assert Path(rel).name in result.stdout, rel
    # The real checkout must not have grown a cache either.
    assert (_caches(SRC), _caches(ROOT / "tests"), _caches(ROOT / "qualification")) == real_before
