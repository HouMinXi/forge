"""Real failing pytest replay plus credential and workflow boundary checks."""

import os
import re
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts import ci_publish_failure_log as replay

ROOT = Path(__file__).resolve().parents[1]


def test_real_failed_pytest_transcript_preserves_race_evidence(tmp_path, capsys):
    test = tmp_path / "test_race.py"
    test.write_text('def test_race():\n    raise OSError(5, "race pid=731995 fd=14")\n')
    with (tmp_path / "local.log").open("w") as stream:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(test)],
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    assert result.returncode == 1
    replay.publish(tmp_path, "local")
    output = capsys.readouterr().out
    assert "OSError: [Errno 5] race pid=731995 fd=14" in output
    assert "test_race" in output
    assert "1 failed" in output
    assert not list(tmp_path.glob("receipt-*.json"))


def test_credentials_are_removed_but_runtime_diagnostics_remain():
    environment = {
        "ACTIONS_RUNTIME_TOKEN": "runtime-canary",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-canary",
        "GH_TOKEN": "gh-canary",
        "API_KEY": "api-canary",
        "PATH": "/bin",
        "GITHUB_SHA": "abc",
        "TMPDIR": "/tmp/race",
    }
    assert replay.test_environment(environment) == {
        "PATH": "/bin",
        "GITHUB_SHA": "abc",
        "TMPDIR": "/tmp/race",
    }
    text = (
        " ".join(environment.values())
        + " ghp_"
        + "a" * 36
        + " Authorization: Bearer value https://user:pass@host/ pid=731995 EIO /tmp/race"
    )
    clean = replay.redact(text, environment)
    for value in (
        "runtime-canary",
        "oidc-canary",
        "gh-canary",
        "api-canary",
        "a" * 36,
        "Bearer value",
        "user:pass",
    ):
        assert value not in clean
    assert "pid=731995 EIO /tmp/race" in clean


def test_commands_disabled_with_fresh_noncolliding_marker(tmp_path, capsys, monkeypatch):
    (tmp_path / "local.log").write_text("first-token\n::error::fake\n::add-mask::hidden\n")
    tokens = iter(("first-token", "new-token"))
    monkeypatch.setattr(replay.secrets, "token_hex", lambda _: next(tokens))
    replay.publish(tmp_path, "local")
    output = capsys.readouterr().out
    assert output.startswith("::stop-commands::new-token\n")
    assert output.endswith("::new-token::\n")
    assert "::error::fake" in output


@pytest.mark.parametrize("phase", ["local", "setup-bwrap"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "oversized"])
def test_rejects_nonregular_or_unbounded_input(tmp_path, kind, monkeypatch, phase):
    path = tmp_path / replay.LOG_NAMES[phase]
    target = tmp_path / "secret"
    target.write_text("secret canary")
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_text("12345")
        monkeypatch.setattr(replay, "MAX_BYTES", 4)
    with pytest.raises((OSError, ValueError)):
        replay.read_log(tmp_path, phase)


def test_rejects_parent_symlink_and_unknown_phase(tmp_path):
    parent = tmp_path / "link"
    parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        replay.read_log(parent, "local")
    with pytest.raises(ValueError):
        replay.read_log(tmp_path, "../secret")


def test_workflow_failure_only_sidecars_and_no_credential_injection():
    raw = (ROOT / ".github/workflows/linux-tests.yml").read_text()
    workflow = yaml.safe_load(raw)
    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert not re.search(r"\$\{\{[^}]*\b(?:secrets|github\.token)", raw)
    expected = {
        "process-contracts": [(phase, phase + "_receipt") for phase in ("ownership", "cgroup", "local")],
        "service-free": [("shard-${{ matrix.shard }}", "shard_receipt")],
    }
    for name, job in workflow["jobs"].items():
        actual = [s for s in job["steps"] if s["name"].startswith("Publish failed pytest transcript (")]
        assert actual == [
            {
                "name": f"Publish failed pytest transcript ({phase})",
                "if": "${{ always() && steps." + producer + ".outcome == 'failure' }}",
                "run": 'set +x\npython scripts/ci_publish_failure_log.py --directory "$RUNNER_TEMP/forge-evidence" --phase "'
                + phase
                + '"\n',
            }
            for phase, producer in expected.get(name, [])
        ]
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False
                assert "token" not in step["with"]
    # Gate admits only actual producer failure, never success/skipped/cancelled.
    assert all(
        "outcome == 'failure'" in s["if"]
        for job in workflow["jobs"].values()
        for s in job["steps"]
        if s["name"].startswith("Publish failed pytest transcript (")
    )
