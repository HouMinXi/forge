# SPDX-License-Identifier: Apache-2.0
"""Mutation tool failures must retain diagnostics and never look successful."""
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from code_forge.disposition import Disposition
from code_forge.mutation import launch_detached_mutation, run_mutation
from code_forge.state import StateFinding


@pytest.mark.parametrize("phase", ["run", "results"])
@pytest.mark.parametrize("stdout,stderr", [
    ("FileNotFoundError: missing source_paths", ""),
    ("", "cannot open cache"),
    ("x" * 5000 + "stdout cause", "y" * 5000 + "stderr cause"),
    ("", ""),
])
def test_process_failure_keeps_both_stream_tails(tmp_path, phase, stdout, stderr):
    def command(args, **kwargs):
        # The mutmut subcommand is a token of the argv, not the last
        # element: run_mutation appends "--max-children <n>" after "run".
        failed = phase in args
        return subprocess.CompletedProcess(
            args, 7 if failed else 0,
            stdout=stdout if failed else "", stderr=stderr if failed else "",
        )

    with patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut"), \
         patch("code_forge.mutation._resolve_mutmut_invocation", return_value=["mutmut"]), \
         patch("code_forge.mutation.subprocess.run", side_effect=command):
        findings, errors = run_mutation(["source.py"], ["pytest"], cwd=tmp_path)
    assert len(findings) == 1
    assert findings[0].id == "MUTATION_ERROR"
    assert findings[0].disposition == Disposition.CONFIRMED
    assert errors
    for message in [findings[0].description, *errors]:
        assert phase in message and "7" in message
        assert len(message) < 2500
        if stdout:
            assert "stdout" in message and stdout[-30:] in message
        if stderr:
            assert "stderr" in message and stderr[-30:] in message
        if not stdout and not stderr:
            assert "no output" in message


@pytest.mark.parametrize("outcome", ["error", "exception", "survivor", "clean"])
def test_detached_script_preserves_outcome(tmp_path, outcome):
    captured = {}

    def spawn(args, **kwargs):
        captured["script"] = args[2]
        return type("Child", (), {"pid": 1234})()

    result_path = tmp_path / "result.json"
    with patch("code_forge.mutation.subprocess.Popen", side_effect=spawn):
        assert launch_detached_mutation(
            ["source.py"], ["pytest"], tmp_path, result_path,
        ) == 1234
    # Execute the generated production script, replacing only the external run.
    finding = StateFinding(
        id="MUTATION_ERROR" if outcome == "error" else "mutant-example",
        fingerprint="test", source="MUTANT", disposition=Disposition.CONFIRMED,
        file="", line_range=[], description="tool failed on stdout",
    )
    runner = "code_forge.mutation.run_mutation"
    with patch(runner) as run:
        if outcome == "exception":
            run.side_effect = RuntimeError("runner exploded")
        else:
            run.return_value = ([finding] if outcome != "clean" else [], [])
        # Only the script generated above by our launcher is executed.
        exec(compile(captured["script"], "<detached-mutation>", "exec"), {})  # noqa: S102
    data = json.loads(result_path.read_text())
    if outcome in ("error", "exception"):
        assert data["status"] == "error"
        assert data["message"] == (
            "runner exploded" if outcome == "exception" else "tool failed on stdout"
        )
        assert not data["survivors"]
    else:
        assert data["status"] == "done"
        assert data["survivors"] == (["mutant-example"] if outcome == "survivor" else [])


@pytest.mark.integration
def test_multiple_paths_are_read_by_real_mutmut(tmp_path):
    pytest.importorskip("mutmut")
    from code_forge.mutation import _build_mutmut_config

    paths = ["src/first.py", "lib/second.py"]
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(exist_ok=True)
        target.write_text("def value():\n    return 1\n")
    (tmp_path / "setup.cfg").write_text(_build_mutmut_config(paths, ["pytest"]))
    result = subprocess.run(
        [sys.executable, "-c", (
            "import json; from mutmut.configuration import Config; "
            "c=Config.get(); print(json.dumps([list(map(str,c.source_paths)), c.only_mutate]))"
        )],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == [["lib", "src"], paths]


@pytest.mark.integration
def test_real_mutmut_checks_both_changed_files(tmp_path):
    pytest.importorskip("mutmut")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    for name in ("first", "second"):
        (tmp_path / "src" / f"{name}.py").write_text(
            "def value(x):\n    return x + 1\n"
        )
    # Deliberately weak assertions leave a survivor in each real source file.
    (tmp_path / "tests" / "test_values.py").write_text(
        "from first import value as a\nfrom second import value as b\n"
        "def test_values():\n    assert a(10) > 0\n    assert b(10) > 0\n"
    )
    findings, errors = run_mutation(
        ["src/first.py", "src/second.py"],
        [sys.executable, "-m", "pytest", "-q", "tests"], cwd=tmp_path, timeout=60,
    )
    assert not errors
    assert {f.id.split(".")[0] for f in findings} == {"mutant-first", "mutant-second"}
    assert all(f.disposition == Disposition.CONFIRMED for f in findings)
    assert not (tmp_path / "setup.cfg").exists()
    assert not (tmp_path / "mutants").exists()


@pytest.mark.integration
def test_real_mutmut_stdout_failure_is_visible(tmp_path):
    pytest.importorskip("mutmut")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "probe.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "tests" / "test_probe.py").write_text(
        "import subprocess, sys\nfrom pathlib import Path\n"
        "def test_child(tmp_path):\n"
        "    script = Path(__file__).resolve().parents[1] / 'src/probe.py'\n"
        "    p = subprocess.run([sys.executable, str(script)], cwd=tmp_path, "
        "capture_output=True, text=True)\n"
        "    assert p.returncode == 0, p.stderr\n"
    )
    findings, errors = run_mutation(
        ["src/probe.py"], [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=tmp_path, timeout=60,
    )
    assert findings[0].id == "MUTATION_ERROR"
    assert "source_paths" in findings[0].description
    assert "stdout" in findings[0].description
    assert errors
    assert not (tmp_path / "setup.cfg").exists()
    assert not (tmp_path / "mutants").exists()
