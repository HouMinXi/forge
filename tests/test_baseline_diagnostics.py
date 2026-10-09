"""Bounded causes survive real baseline children and every failure branch."""

import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from code_forge.baseline_guard import _output_detail, _run_baseline_guard
from code_forge.disposition import Disposition


CAUSE = "ERROR at setup: required variable TEST_EVIDENCE_DIR is unset"


def _ticks(pid):
    path = Path(f"/proc/{pid}/stat")
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    return int(raw.rsplit(")", 1)[1].split()[19])


def _recording_runner(monkeypatch, root):
    original_run, original_popen = subprocess.run, subprocess.Popen
    calls, processes = [], []

    def launch(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append((process, _ticks(process.pid)))
        return process

    def execute(argv, **kwargs):
        row = {"argv": argv}
        calls.append(row)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(subprocess, "Popen", launch)
                result = original_run(argv, **kwargs)
            row.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
            return result
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            row.update(exception=type(error).__name__, stdout=getattr(error, "output", None),
                       stderr=getattr(error, "stderr", None))
            raise
        finally:
            row["identities"] = [dict(pid=p.pid, start_ticks=t, returncode=p.poll(),
                                      identity_closed=t is None or _ticks(p.pid) != t)
                                 for p, t in processes]
            serial = dict(row)
            for key in ("stdout", "stderr"):
                if isinstance(serial.get(key), bytes):
                    serial[key] = serial[key].decode("utf-8", errors="replace")
            (root / "child-result.json").write_text(json.dumps(serial, indent=2) + "\n")

    return execute, calls


def _environment():
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("VIRTUAL_ENV", None)
    return env


def _failure(root, monkeypatch, argv, *, timeout=10, fingerprint="mutation-flaky"):
    execute, calls = _recording_runner(monkeypatch, root)
    status, findings, infra = _run_baseline_guard(
        argv, _environment(), str(root), allow_strip_retry=True, timeout=timeout, run_command=execute,
    )
    assert status == "skip" and len(calls) == 1 and len(findings) == 1
    finding = findings[0]
    assert (finding.id, finding.source, finding.disposition, finding.file, finding.line_range,
            finding.fingerprint) == ("MUTATION_SKIPPED", "MUTANT", Disposition.DISMISSED, "", [], fingerprint)
    assert infra == [finding.description]
    assert all(row["returncode"] is not None and row["identity_closed"] for row in calls[0]["identities"])
    return finding.description, calls[0]


@pytest.mark.parametrize("stream,placement", [
    (stream, placement) for stream in ("stdout", "stderr") for placement in ("short", "leading", "trailing", "both")
], ids=[f"{stream}-{placement}" for stream in ("stdout", "stderr") for placement in ("short", "leading", "trailing", "both")])
def test_real_python_diagnostic(tmp_path, monkeypatch, stream, placement):
    payload = f"import sys\ns = sys.{stream}\n"
    if placement in ("leading", "both"):
        payload += "print('.' * 250, file=s, flush=True)\n"
    payload += f"print({CAUSE!r}, file=s, flush=True)\n"
    if placement in ("trailing", "both"):
        payload += "print('.' * 250, file=s, flush=True)\n"
    payload += "sys.exit(1)\n"
    detail, raw = _failure(tmp_path, monkeypatch, [sys.executable, "-B", "-c", payload])
    assert raw["returncode"] == 1 and CAUSE in raw[stream]
    assert "baseline failed" in detail and "returncode 1" in detail
    assert CAUSE in detail, "baseline diagnostic missing"


@pytest.mark.parametrize("kind,placement", [
    (kind, placement) for kind in ("assertion", "setup", "collection") for placement in ("leading", "trailing")
], ids=[f"{kind}-{placement}" for kind in ("assertion", "setup", "collection") for placement in ("leading", "trailing")])
def test_real_pytest_diagnostic(tmp_path, monkeypatch, kind, placement):
    marker = f"diagnostic-{kind}-322"
    body = {
        "assertion": f"def test_body():\n    assert False, {marker!r}\n",
        "setup": f"import pytest\n@pytest.fixture\ndef broken():\n    raise RuntimeError({marker!r})\ndef test_body(broken):\n    pass\n",
        "collection": f"raise RuntimeError({marker!r})\n",
    }[kind]
    (tmp_path / "test_probe.py").write_text(body)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    child_argv = [sys.executable, "-B", "-m", "pytest", "-q", "--tb=short", "--color=no",
                  "-c", str(tmp_path / "pytest.ini"), "--confcutdir", str(tmp_path),
                  "-p", "no:cacheprovider", "--junitxml", str(tmp_path / "child-junit.xml"), "test_probe.py"]
    launcher = (
        "import json, pathlib, subprocess, sys\n"
        f"placement = {placement!r}\n"
        "if placement == 'leading': print('.' * 250, flush=True)\n"
        f"child = subprocess.Popen({child_argv!r}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
        "stat = pathlib.Path(f'/proc/{child.pid}/stat')\n"
        "ticks = int(stat.read_text().rsplit(')',1)[1].split()[19]) if stat.exists() else None\n"
        "try:\n"
        "    out, err = child.communicate(timeout=8)\n"
        "except subprocess.TimeoutExpired:\n"
        "    child.kill(); child.wait(timeout=1); raise\n"
        "pathlib.Path('pytest-child.json').write_text(json.dumps(dict(pid=child.pid, start_ticks=ticks, returncode=child.returncode, reaped=True)))\n"
        "pathlib.Path('pytest-stdout').write_bytes(out)\n"
        "pathlib.Path('pytest-stderr').write_bytes(err)\n"
        "sys.stdout.buffer.write(out); sys.stdout.flush()\n"
        "sys.stderr.buffer.write(err); sys.stderr.flush()\n"
        "if placement == 'trailing': print('.' * 250, flush=True)\n"
        "sys.exit(child.returncode)\n"
    )
    detail, raw = _failure(tmp_path, monkeypatch, [sys.executable, "-B", "-c", launcher])
    expected = 2 if kind == "collection" else 1
    assert raw["returncode"] == expected and marker in raw["stdout"]
    child = json.loads((tmp_path / "pytest-child.json").read_text())
    assert child["returncode"] == expected and child["reaped"]
    assert child["start_ticks"] is None or _ticks(child["pid"]) != child["start_ticks"]
    # Only the owned pytest child writes this fresh fixture's JUnit file.
    cases = ET.parse(tmp_path / "child-junit.xml").findall(".//testcase")  # noqa: S314
    assert len(cases) == 1
    error = cases[0].find("error" if kind != "assertion" else "failure")
    assert error is not None and marker in (error.get("message", "") + (error.text or ""))
    if kind == "setup":
        assert error.get("message", "").startswith("failed on setup")
    elif kind == "collection":
        assert error.get("message") == "collection failure"
    assert cases[0].find("skipped") is None
    assert marker in detail, "pytest diagnostic missing"


@pytest.mark.parametrize("kind", ["startup", "timeout", "nonzero"])
def test_physical_failure_wiring(tmp_path, monkeypatch, kind):
    marker = f"diagnostic-{kind}-322"
    if kind == "startup":
        detail, raw = _failure(tmp_path, monkeypatch, ["forge-" + marker])
        assert raw["exception"] == "FileNotFoundError" and not raw["identities"]
        assert "runner could not start" in detail
        assert marker in detail, "runner-start detail lost"
    elif kind == "timeout":
        payload = f"import time\nprint('ERROR: {marker}', flush=True)\ntime.sleep(20)\n"
        detail, raw = _failure(tmp_path, monkeypatch, [sys.executable, "-B", "-c", payload],
                               timeout=2, fingerprint="mutation-baseline-timeout")
        assert raw["exception"] == "TimeoutExpired" and marker in raw["stdout"].decode()
        assert "timed out after 2s" in detail
        assert marker in detail, "timeout detail lost"
    else:
        payload = f"import sys\nprint('.' * 250)\nprint('ERROR: {marker}')\nsys.exit(1)\n"
        detail, raw = _failure(tmp_path, monkeypatch, [sys.executable, "-B", "-c", payload])
        assert raw["returncode"] == 1 and marker in raw["stdout"] and "returncode 1" in detail
        assert marker in detail, "nonzero detail lost"


@pytest.mark.parametrize("stderr,stdout,expected", [
    ("err", "out", "; stderr: err; stdout: out"),
    (None, None, ""),
    (" \n\t", " ", ""),
    (b"err\xff\n x", b"out\xfe\t y", "; stderr: err\ufffd x; stdout: out\ufffd y"),
    (" err\n x ", "out\t y", "; stderr: err x; stdout: out y"),
    ("y" * 250, "x" * 250, "; stderr: " + "y" * 200 + "; stdout: " + "x" * 200),
    (None, "." * 250 + "\nERROR heading\nValueError: exception\nE first cause\nE second cause\n",
     "; stdout: E first cause"),
    ("." * 250 + "\nValueError: " + "s" * 250, "." * 250 + "\nERROR: " + "o" * 250,
     "; stderr: " + ("ValueError: " + "s" * 250)[:200] + "; stdout: " + ("ERROR: " + "o" * 250)[:200]),
], ids=["mixed-short", "none", "blank", "bytes", "whitespace", "fallback", "priority", "independent-caps"])
def test_formatter_compatibility(stderr, stdout, expected):
    detail = _output_detail(stderr, stdout)
    assert detail == expected and len(detail) <= 420


@pytest.mark.parametrize("stream,placement", [
    (stream, placement) for stream in ("stdout", "stderr") for placement in ("leading", "trailing")
], ids=[f"{stream}-{placement}" for stream in ("stdout", "stderr") for placement in ("leading", "trailing")])
def test_real_python_bare_exception(tmp_path, monkeypatch, stream, placement):
    marker = "diagnostic-base-exception"
    payload = f"import sys, traceback\ns = sys.{stream}\n"
    if placement == "leading":
        payload += "print('.' * 250, file=s, flush=True)\n"
    payload += (
        f"try:\n    raise Exception({marker!r})\n"
        "except Exception:\n    traceback.print_exc(file=s)\n"
    )
    if placement == "trailing":
        payload += "print('.' * 250, file=s, flush=True)\n"
    payload += "sys.exit(1)\n"
    detail, raw = _failure(tmp_path, monkeypatch, [sys.executable, "-B", "-c", payload])
    assert raw["returncode"] == 1 and marker in raw[stream]
    assert "Traceback (most recent call last):" in raw[stream]
    assert "baseline failed" in detail and "returncode 1" in detail
    assert detail == f"run 1: baseline failed (returncode 1); {stream}: Exception: {marker}", "bare exception diagnostic missing"


@pytest.mark.parametrize("kind", ["StopIteration", "StopAsyncIteration", "GeneratorExit", "Aborted", "ExceptionGroup", "chain"])
@pytest.mark.parametrize("stream,placement", [
    (stream, placement) for stream in ("stdout", "stderr") for placement in ("leading", "trailing")
], ids=[f"{stream}-{placement}" for stream in ("stdout", "stderr") for placement in ("leading", "trailing")])
def test_real_python_traceback_terminal(tmp_path, monkeypatch, kind, stream, placement):
    marker = f"diagnostic-{kind}"
    payload = f"import sys, traceback\ns = sys.{stream}\nclass Aborted(Exception):\n    pass\n"
    if kind == "Aborted":
        payload += "Aborted.__module__ = 'worker.jobs'\n"
    if placement == "leading":
        payload += "print('.' * 250, file=s, flush=True)\n"
    if kind == "chain":
        body = f"    try:\n        raise ValueError('inner-cause')\n    except ValueError as cause:\n        raise Aborted({marker!r}) from cause\n"
        summary = f"Aborted: {marker}"
    elif kind == "ExceptionGroup":
        body = f"    raise ExceptionGroup({marker!r}, [ValueError('group-child')])\n"
        summary = f"ExceptionGroup: {marker} (1 sub-exception)"
    else:
        body = f"    raise {kind}({marker!r})\n"
        summary = f"{'worker.jobs.' if kind == 'Aborted' else ''}{kind}: {marker}"
    payload += "try:\n" + body + "except BaseException:\n    traceback.print_exc(file=s)\n"
    if placement == "trailing":
        payload += "print('.' * 250, file=s, flush=True)\n"
    payload += "sys.exit(1)\n"
    detail, raw = _failure(tmp_path, monkeypatch, [sys.executable, "-B", "-c", payload])
    assert raw["returncode"] == 1 and summary in raw[stream]
    assert "Traceback (most recent call last):" in raw[stream]
    assert detail == f"run 1: baseline failed (returncode 1); {stream}: {summary}", "traceback terminal cause missing"


@pytest.mark.parametrize("stream", ["stderr", "stdout"])
@pytest.mark.parametrize("content,expected", [
    ("INFO: preparation complete\n", "." * 200),
    ("metadata: revision abc123\n", "." * 200),
    ("    Aborted: source-looking label\n", "." * 200),
    ("Traceback (most recent call last):\nINFO: no frame\n", "Traceback (most recent call last):"),
    ('  File "probe.py", line 1, in run\nAborted: orphan frame\n', "." * 200),
    ('  + Exception Group Traceback (most recent call last):\nINFO: broken margin\n  |   File "probe.py", line 1, in run\n  | Aborted: orphan group\n', "." * 200),
], ids=["info", "metadata", "indented-source", "header-only", "orphan-frame", "broken-group-margin"])
def test_non_traceback_colons(stream, content, expected):
    channels = {"stderr": None, "stdout": None, stream: "." * 250 + "\n" + content}
    assert _output_detail(**channels) == f"; {stream}: {expected}"


@pytest.mark.parametrize("stream,placement", [
    (stream, placement) for stream in ("stdout", "stderr") for placement in ("leading", "trailing")
], ids=[f"{stream}-{placement}" for stream in ("stdout", "stderr") for placement in ("leading", "trailing")])
def test_real_python_empty_traceback(tmp_path, monkeypatch, stream, placement):
    payload = f"import sys, traceback\ns = sys.{stream}\n"
    if placement == "leading":
        payload += "print('.' * 250, file=s, flush=True)\n"
    payload += "try:\n    raise GeneratorExit()\nexcept BaseException:\n    traceback.print_exc(file=s)\n"
    if placement == "trailing":
        payload += "print('.' * 250, file=s, flush=True)\n"
    payload += "sys.exit(1)\n"
    detail, raw = _failure(tmp_path, monkeypatch, [sys.executable, "-B", "-c", payload])
    assert raw["returncode"] == 1 and "\nGeneratorExit\n" in raw[stream]
    assert detail == f"run 1: baseline failed (returncode 1); {stream}: GeneratorExit", "traceback terminal cause missing"


@pytest.mark.parametrize("blank_lines", [1, 2])
@pytest.mark.parametrize("stream,placement", [
    (stream, placement) for stream in ("stdout", "stderr") for placement in ("leading", "trailing")
], ids=[f"{stream}-{placement}" for stream in ("stdout", "stderr") for placement in ("leading", "trailing")])
def test_real_python_blank_first_message(tmp_path, monkeypatch, blank_lines, stream, placement):
    marker = "diagnostic-newline-message"
    message = "\n" * blank_lines + marker
    payload = f"import sys, traceback\ns = sys.{stream}\n"
    if placement == "leading":
        payload += "print('.' * 250, file=s, flush=True)\n"
    payload += f"try:\n    raise StopIteration({message!r})\nexcept StopIteration:\n    traceback.print_exc(file=s)\n"
    if placement == "trailing":
        payload += "print('.' * 250, file=s, flush=True)\n"
    payload += "sys.exit(1)\n"
    detail, raw = _failure(tmp_path, monkeypatch, [sys.executable, "-B", "-c", payload])
    assert raw["returncode"] == 1 and f"StopIteration: {message}\n" in raw[stream]
    assert detail == f"run 1: baseline failed (returncode 1); {stream}: StopIteration: {marker}", "empty message cause missing"
