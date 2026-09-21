"""Exercise smoke-run error boundaries without changing receipt policy."""

import datetime
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from code_forge import cli, runtime


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent.resolve()))
    return tmp_path


def _args(command=None):
    return SimpleNamespace(
        command=command or [sys.executable, "-c", "print('smoke')"],
        surface="check name/one",
        target="HEAD",
        timeout=5,
    )


@pytest.mark.parametrize("stage", ["root", "diff", "command", "receipt"])
@pytest.mark.parametrize("error_type", [TypeError, RuntimeError])
def test_programming_errors_are_not_hidden(isolated_cwd, monkeypatch, stage, error_type):
    real_run = subprocess.run
    fault = error_type("injected implementation failure")

    def run(command, **kwargs):
        point = "root" if command[:2] == ["git", "rev-parse"] else (
            "diff" if command[:2] == ["git", "diff"] else "command"
        )
        if point == stage:
            raise fault
        return real_run(command, **kwargs)

    def write_receipt(**kwargs):
        raise fault

    monkeypatch.setattr(cli.subprocess, "run", run)
    if stage == "receipt":
        monkeypatch.setattr(runtime, "write_smoke_receipt", write_receipt)
    with pytest.raises(error_type) as caught:
        cli._handle_smoke_run(_args(), isolated_cwd)
    assert caught.value is fault
    assert not list(isolated_cwd.rglob("smoke-receipt-*.json"))


@pytest.mark.parametrize("stage", ["root", "diff"])
def test_git_os_errors_keep_existing_fallback(isolated_cwd, monkeypatch, stage):
    real_run = subprocess.run

    def run(command, **kwargs):
        point = "root" if command[:2] == ["git", "rev-parse"] else "diff"
        if command[0] == "git" and point == stage:
            raise OSError("git unavailable")
        return real_run(command, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli._handle_smoke_run(_args(), isolated_cwd) == 0
    receipts = list(isolated_cwd.rglob("smoke-receipt-*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["status"] == "VERIFIED"


def test_permission_error_is_a_command_error(isolated_cwd, capsys):
    program = isolated_cwd / "not-executable"
    program.write_text("not executable\n")
    program.chmod(0o600)
    assert cli._handle_smoke_run(_args([str(program)]), isolated_cwd) == cli.EXIT_CLI_ERROR
    assert "error running command:" in capsys.readouterr().err
    assert not list(isolated_cwd.rglob("smoke-receipt-*.json"))


@pytest.mark.parametrize("code", [0, 7])
def test_receipt_os_errors_preserve_command_status(isolated_cwd, capsys, code):
    (isolated_cwd / ".code-forge").write_text("blocks receipt directory\n")
    args = _args([sys.executable, "-c", f"raise SystemExit({code})"])
    assert cli._handle_smoke_run(args, isolated_cwd) == code
    assert "warning: could not write receipt:" in capsys.readouterr().err


def test_timestamp_requests_aware_utc(isolated_cwd, monkeypatch):
    real_datetime = datetime.datetime
    requested = []

    class Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            requested.append(tz)
            return real_datetime(2026, 1, 2, 3, 4, 5, tzinfo=tz)

        @classmethod
        def utcnow(cls):
            pytest.fail("smoke-run requested deprecated naive UTC")

    monkeypatch.setattr(datetime, "datetime", Clock)
    assert cli._handle_smoke_run(_args(), isolated_cwd) == 0
    assert requested == [datetime.timezone.utc]
    receipt = json.loads(next(isolated_cwd.rglob("smoke-receipt-*.json")).read_text())
    assert receipt["timestamp"] == "2026-01-02T03:04:05Z"


@pytest.mark.parametrize("code", [0, 7])
def test_real_git_command_and_receipt(isolated_cwd, capsys, code):
    subprocess.run(["git", "init", "-q", str(isolated_cwd)], check=True)
    source = isolated_cwd / "sample.txt"
    source.write_text("before\n")
    subprocess.run(["git", "add", "sample.txt"], cwd=isolated_cwd, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Smoke Test", "-c", "user.email=smoke@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
         "commit", "-qm", "fixture"], cwd=isolated_cwd, check=True,
    )
    source.write_text("after\n")
    diff = subprocess.check_output(["git", "diff", "HEAD"], cwd=isolated_cwd)
    assert b"+after" in diff
    child = "import sys;sys.stdout.buffer.write(b'out\\xff');sys.stderr.buffer.write(b'err\\xfe');sys.exit(%s)" % code
    assert cli._handle_smoke_run(_args(["--", sys.executable, "-c", child]), isolated_cwd) == code
    receipt_path = isolated_cwd / ".code-forge/smoke-receipts/smoke-receipt-check-name-one.json"
    receipt = json.loads(receipt_path.read_text())
    normalized_diff = "\n".join(line.rstrip() for line in diff.decode().splitlines())
    expected_hash = hashlib.sha256(b"mode=git\n" + normalized_diff.encode()).hexdigest()
    assert receipt["diff_sha256"] == expected_hash
    assert receipt["transcript_sha256"] == hashlib.sha256(b"out\xfferr\xfe").hexdigest()
    assert receipt["exit_code"] == code
    assert receipt["status"] == ("VERIFIED" if code == 0 else "FAILED")
    assert receipt["surface"] == "check-name-one"
    stamp = datetime.datetime.strptime(receipt["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
    assert stamp.utcoffset() == datetime.timedelta(0)
    assert receipt["status"] in capsys.readouterr().err


def test_missing_command_is_reported(isolated_cwd, capsys):
    args = _args([str(isolated_cwd / "missing-program")])
    assert cli._handle_smoke_run(args, isolated_cwd) == cli.EXIT_CLI_ERROR
    assert "command not found:" in capsys.readouterr().err


def test_invalid_command_argument_is_reported(isolated_cwd, capsys):
    args = _args([sys.executable, "bad\x00argument"])
    assert cli._handle_smoke_run(args, isolated_cwd) == cli.EXIT_CLI_ERROR
    assert "error running command:" in capsys.readouterr().err
    assert not list(isolated_cwd.rglob("smoke-receipt-*.json"))


def test_timeout_is_reported(isolated_cwd, monkeypatch, capsys):
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] != "git":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return real_run(command, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli._handle_smoke_run(_args(), isolated_cwd) == cli.EXIT_TIMEOUT
    assert "timed out after 5 seconds" in capsys.readouterr().err


def test_empty_command_is_a_usage_error(isolated_cwd, capsys):
    args = _args()
    args.command = ["--"]
    assert cli._handle_smoke_run(args, isolated_cwd) == cli.EXIT_CLI_ERROR
    assert "no command specified" in capsys.readouterr().err
