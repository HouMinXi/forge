"""Real failing pytest replay plus credential and workflow boundary checks."""

import os
import errno
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
    assert workflow["jobs"]["service-free"]["env"] == {"FORGE_CI_AUDIT_TRACE": "1"}
    assert all(
        "FORGE_CI_AUDIT_TRACE" not in job.get("env", {})
        for name, job in workflow["jobs"].items()
        if name != "service-free"
    )
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


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("missing-file", "NOT_FOUND"),
        ("missing-directory", "NOT_FOUND"),
        ("parent-symlink", "UNSAFE_PATH"),
        ("symlink", "UNSAFE_PATH"),
        ("hardlink", "UNSAFE_FILE"),
        ("fifo", "UNSAFE_FILE"),
        ("directory", "UNSAFE_FILE"),
        ("oversized", "TOO_LARGE"),
    ],
)
def test_real_cli_unavailable_reasons_never_publish_private_data(tmp_path, kind, reason):
    private = "PRIVATE_PATH_AND_CONTENT_CANARY"
    directory = tmp_path / private
    directory.mkdir()
    target = directory / "private-target"
    target.write_text(private)
    for phase in ("local", "setup-bwrap"):
        path = directory / replay.LOG_NAMES[phase]
        if kind == "symlink":
            path.symlink_to(target)
        elif kind == "hardlink":
            os.link(target, path)
        elif kind == "fifo":
            os.mkfifo(path)
        elif kind == "directory":
            path.mkdir()
        elif kind == "oversized":
            with path.open("wb") as stream:
                stream.write(private.encode())
                stream.truncate(replay.MAX_BYTES + 1)
        selected = directory
        if kind == "missing-directory":
            selected = directory / "absent"
        elif kind == "parent-symlink":
            selected = tmp_path / ("linked-" + phase)
            selected.symlink_to(directory, target_is_directory=True)
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/ci_publish_failure_log.py"),
                "--directory",
                str(selected),
                "--phase",
                phase,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 1
        assert result.stderr == ""
        assert result.stdout == (
            f"Failure transcript unavailable (reason={reason}); original test result is unchanged.\n"
        )
        assert private not in result.stdout
        assert not list(directory.glob("receipt-*.json"))


@pytest.mark.parametrize(
    ("number", "reason"),
    [(errno.EACCES, "UNREADABLE"), (errno.EPERM, "UNREADABLE"), (errno.EIO, "READ_ERROR")],
)
def test_read_errors_use_native_errno_without_private_hooks(
    tmp_path, monkeypatch, capsys, number, reason
):
    class PrivateError(OSError):
        def __str__(self):
            raise AssertionError("must not render private text")

        def __getattribute__(self, name):
            raise AssertionError("must not read private attributes")

    def fail(*_args):
        raise PrivateError(number, "PRIVATE_MESSAGE", "PRIVATE_FILENAME")

    monkeypatch.setattr(replay, "read_log", fail)
    monkeypatch.setattr(sys, "argv", ["publish", "--directory", str(tmp_path), "--phase", "local"])
    assert replay.main() == 1
    assert capsys.readouterr().out == (
        f"Failure transcript unavailable (reason={reason}); original test result is unchanged.\n"
    )

    class PrivateErrno:
        def __hash__(self):
            raise AssertionError("must not hash private errno")

        def __eq__(self, _other):
            raise AssertionError("must not compare private errno")

    number = PrivateErrno()
    assert replay.main() == 1
    assert capsys.readouterr().out == (
        "Failure transcript unavailable (reason=READ_ERROR); original test result is unchanged.\n"
    )


def test_log_growth_beyond_read_limit_has_fixed_reason(tmp_path, monkeypatch, capsys):
    path = tmp_path / "local.log"
    path.write_bytes(b"1234")
    monkeypatch.setattr(replay, "MAX_BYTES", 4)
    original = replay.os.fdopen

    def grow_before_read(*args, **kwargs):
        with path.open("ab") as stream:
            stream.write(b"PRIVATE_CONTENT_CANARY")
        return original(*args, **kwargs)

    monkeypatch.setattr(replay.os, "fdopen", grow_before_read)
    monkeypatch.setattr(sys, "argv", ["publish", "--directory", str(tmp_path), "--phase", "local"])
    assert replay.main() == 1
    assert capsys.readouterr().out == (
        "Failure transcript unavailable (reason=TOO_LARGE); original test result is unchanged.\n"
    )


def test_later_publication_errors_are_not_misreported_as_missing_logs(tmp_path, monkeypatch, capsys):
    (tmp_path / "local.log").write_text("PRIVATE_CONTENT_CANARY")

    def fail(*_args):
        raise FileNotFoundError("PRIVATE_MESSAGE_AND_FILENAME")

    monkeypatch.setattr(replay, "redact", fail)
    monkeypatch.setattr(sys, "argv", ["publish", "--directory", str(tmp_path), "--phase", "local"])
    assert replay.main() == 1
    assert capsys.readouterr().out == (
        "Failure transcript unavailable (reason=PUBLICATION_ERROR); original test result is unchanged.\n"
    )


def test_unknown_reason_and_exception_subclasses_have_fixed_fallback():
    class PrivateFailure(replay._ReadFailure):
        def __getattribute__(self, name):
            raise AssertionError("must not read private attributes")

        def __str__(self):
            raise AssertionError("must not render private text")

    for error in (
        replay._ReadFailure("PRIVATE_CODE"),
        replay._ReadFailure("NOT_FOUND", "PRIVATE_EXTRA"),
        replay._ReadFailure(object()),
        PrivateFailure("NOT_FOUND"),
        RuntimeError("PRIVATE_MESSAGE"),
    ):
        assert replay.failure_reason(error) == "PUBLICATION_ERROR"
