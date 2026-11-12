"""Setup phase diagnostics, using mock commands only (never real sudo/bwrap)."""

import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml


from scripts import ci_publish_failure_log as replay

ROOT = Path(__file__).resolve().parents[1]
PHASES = ("apt-update", "apt-install", "stock-security", "version", "smoke")
MARKERS = """# Fixed stage labels only; never dump environment or package configuration.
setup_start() {
  setup_phase=$1
  setup_started=$SECONDS
  printf 'setup:%s:start\\n' "$setup_phase"
}
setup_end() {
  printf 'setup:%s:end rc=%s elapsed_s=%s\\n' "$setup_phase" "$1" "$((SECONDS - setup_started))"
  setup_phase=
}
trap 'setup_rc=$?; set +e; if [[ -n ${setup_phase:-} ]]; then setup_end "$setup_rc"; fi; exit "$setup_rc"' EXIT
"""


def without_setup_markers(body):
    """Exact approved additions only; old command fingerprint remains authoritative."""
    assert body.count(MARKERS) == 1
    body = body.replace(MARKERS, "")
    for phase in PHASES:
        assert body.count(f"setup_start {phase}\n") == 1
        body = body.replace(f"setup_start {phase}\n", "")
    assert body.count("setup_end 0\n") == 5
    return body.replace("setup_end 0\n", "")


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/linux-tests.yml").read_text())


def test_setup_sidecars_are_failure_only_fixed_paths_and_original_budget():
    count = 0
    for job in workflow()["jobs"].values():
        for index, step in enumerate(job["steps"]):
            if step.get("id") != "bwrap":
                continue
            count += 1
            assert step["timeout-minutes"] == 5
            assert job["steps"][index + 1] == {
                "name": "Publish failed bubblewrap setup transcript",
                "if": "${{ always() && steps.bwrap.outcome == 'failure' }}",
                "run": 'set +x\npython scripts/ci_publish_failure_log.py --directory "$RUNNER_TEMP/forge-evidence" --phase "setup-bwrap"\n',
            }
    assert count == 3
    assert replay.LOG_NAMES["setup-bwrap"] == "step-04.log"
    for name in ("bwrap", "step-04", "git-config", "env", "../step-04"):
        with pytest.raises(ValueError):
            replay.read_log("/nonexistent", name)


@pytest.mark.parametrize("job", ["plan", "process-contracts", "service-free"])
@pytest.mark.parametrize("fail", [None, *PHASES])
@pytest.mark.parametrize("failure_rc", [17, 124])
def test_mock_setup_retains_order_status_and_fixed_phase_markers(tmp_path, job, fail, failure_rc):
    commands = tmp_path / "bin"
    commands.mkdir()
    mock = """#!/bin/bash
if [[ ${0##*/} == sudo ]]; then
  case "$1 $2" in
    'apt-get update') phase=apt-update ;;
    'apt-get install') phase=apt-install ;;
    *) exit 91 ;;
  esac
elif [[ ${0##*/} == python ]]; then
  [[ "$*" == "scripts/ci_stock_runner.py host" ]] || exit 92
  phase=stock-security
else
  if [[ $1 == --version ]]; then phase=version; else phase=smoke; fi
fi
printf '%s\\n' "$phase" >> "$MOCK_TRACE"
printf 'fixture %s https://mirror.invalid/pkg?token=QUERY_CANARY\\n' "$phase"
if [[ $phase == ${MOCK_FAIL:-} ]]; then
  if [[ $MOCK_RC == 124 ]]; then timeout 0.02 sleep 0.2; exit $?; fi
  exit "$MOCK_RC"
fi
"""
    for name in ("sudo", "bwrap", "python"):
        file = commands / name
        file.write_text(mock)
        file.chmod(0o700)
    step = next(s for s in workflow()["jobs"][job]["steps"] if s.get("id") == "bwrap")
    env = dict(
        os.environ,
        RUNNER_TEMP=str(tmp_path),
        PATH=str(commands) + ":" + os.environ["PATH"],
        MOCK_TRACE=str(tmp_path / "trace"),
        MOCK_FAIL=fail or "",
        MOCK_RC=str(failure_rc),
    )
    result = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, check=False)
    assert result.returncode == (failure_rc if fail else 0)
    assert result.stdout == result.stderr == b""
    expected = PHASES[: PHASES.index(fail) + 1] if fail else PHASES
    assert (tmp_path / "trace").read_text().splitlines() == list(expected)
    text = replay.read_log(tmp_path / "forge-evidence", "setup-bwrap")
    starts = re.findall(r"^setup:([^:]+):start$", text, re.M)
    ends = re.findall(r"^setup:([^:]+):end rc=(\d+) elapsed_s=(\d+)$", text, re.M)
    assert starts == list(expected)
    assert [(phase, int(rc)) for phase, rc, _ in ends] == [
        (phase, failure_rc if phase == fail else 0) for phase in expected
    ]
    assert (tmp_path / "forge-evidence/step-04.log").stat().st_mode & 0o777 == 0o600


def test_setup_redacts_url_queries_and_authentication_without_losing_phase(capsys, tmp_path):
    (tmp_path / "step-04.log").write_text(
        "setup:apt-update:start\nhttps://user:pass@host/path?token=QUERY_CANARY&sig=SIGNATURE#FRAGMENT\n"
        "https://TOKEN_ONLY@host/path#FRAGMENT_ONLY\n"
        'Authorization: Digest username="USER_CANARY", response="AUTH_CANARY"\n'
        "Proxy-Authorization: Negotiate PROXY_CANARY\n"
        "::error::fixture command\nsetup:apt-update:end rc=100 elapsed_s=2\n"
    )
    replay.publish(tmp_path, "setup-bwrap")
    text = capsys.readouterr().out
    for secret in (
        "user:pass",
        "QUERY_CANARY",
        "SIGNATURE",
        "FRAGMENT",
        "TOKEN_ONLY",
        "USER_CANARY",
        "AUTH_CANARY",
        "PROXY_CANARY",
    ):
        assert secret not in text
    assert "setup:apt-update:end rc=100 elapsed_s=2" in text
    assert text.startswith("::stop-commands::")
    token = text.splitlines()[0].split("::")[-1]
    assert text.endswith(f"::{token}::\n")


def test_setup_terminal_controls_and_overlapping_credentials(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("LONG_SECRET", "short-value-long")
    monkeypatch.setenv("SHORT_TOKEN", "short-value")
    (tmp_path / "step-04.log").write_text(
        "short-value-long short-value\nAuthorization: Basic BASIC_CANARY\n"
        "ghp_" + "a" * 36 + " github_pat_" + "b" * 40 + "\n\x1b[2J\r\x00EIO fd=14 elapsed_s=2\n"
    )
    replay.publish(tmp_path, "setup-bwrap")
    text = capsys.readouterr().out
    for secret in ("short-value", "-long", "BASIC_CANARY", "a" * 36, "b" * 40):
        assert secret not in text
    assert not any(ord(char) < 32 and char not in "\n\t" for char in text)
    assert "EIO fd=14 elapsed_s=2" in text


def test_setup_changed_read_is_refused_without_printing_contents(tmp_path, monkeypatch, capsys):
    (tmp_path / "step-04.log").write_text("SECRET_CANARY")
    original = replay.os.fstat
    calls = 0

    def changed(fd):
        nonlocal calls
        result = original(fd)
        if __import__("stat").S_ISREG(result.st_mode):
            calls += 1
            if calls == 2:
                from types import SimpleNamespace

                return SimpleNamespace(
                    st_size=result.st_size + 1,
                    st_mtime_ns=result.st_mtime_ns,
                    st_ctime_ns=result.st_ctime_ns,
                )
        return result

    monkeypatch.setattr(replay.os, "fstat", changed)
    monkeypatch.setattr(
        __import__("sys"), "argv", ["publish", "--directory", str(tmp_path), "--phase", "setup-bwrap"]
    )
    assert replay.main() == 1
    assert capsys.readouterr().out == (
        "Failure transcript unavailable (reason=CHANGED); original test result is unchanged.\n"
    )
