"""Exercise the standalone verifier over actual commits and pytest records."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys

import pytest
from test_packet import OBSERVER, PACKET, _announced, _digest
from test_packet import project as project

CHECK = PACKET.with_name("inspect_change.py")
SRC = PACKET.parents[3] / "src"


def git(root, *args):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        check=True,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    ).stdout.strip()


@pytest.fixture
def pair(project):
    base = git(project, "rev-parse", "HEAD")
    path = project / "mod.py"
    path.write_text(path.read_text().replace("return VALUE", "return VALUE + 0"))
    git(project, "commit", "-qam", "change one line")
    return project, base, git(project, "rev-parse", "HEAD")


def run_case(pair, name, *, exit_code=0, assertion_failure=False):
    root, base, candidate = pair
    node = f"test_mod.py::{name}"
    run_id = secrets.token_hex(16)
    packet = root.parent / f"{run_id}.packet.json"
    record = root.parent / f"{run_id}.run.json"
    env = dict(
        os.environ,
        PYTHONPATH=str(SRC),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
        FORGE_E2E_RECORD=str(record),
        FORGE_E2E_EXECUTION_ID=run_id,
    )
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    producer = subprocess.run(
        [
            sys.executable,
            str(PACKET),
            "--root",
            str(root),
            "--source",
            "mod.py",
            "--node",
            node,
            "--file",
            "mod.py",
            "--line",
            "5",
            "--run-id",
            run_id,
            "--output",
            str(packet),
            "--",
            node,
            "-p",
            "code_forge.e2e_record",
            "--rootdir",
            str(root),
            "--confcutdir",
            str(root),
            "-q",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert producer.returncode == exit_code, producer.stdout + producer.stderr
    if assertion_failure:
        assert "test_mod.py:5: AssertionError" in producer.stdout
    args = [
        "--root",
        str(root),
        "--base",
        base,
        "--candidate",
        candidate,
        "--record",
        str(record),
        "--record-sha256",
        _digest(record),
        "--packet",
        str(packet),
        "--expect-packet-sha256",
        _announced(producer)["sha256"],
        "--expect-run-id",
        run_id,
        "--expect-node",
        node,
        "--expect-target-file",
        "mod.py",
        "--expect-line",
        "5",
        "--expect-observer-sha256",
        _digest(OBSERVER),
        "--expect-hits",
        "1",
        "--expect-call",
        "failed" if exit_code else "passed",
        "--expect-exit",
        str(exit_code),
    ]

    def check(*overrides):
        result = subprocess.run(
            [sys.executable, str(CHECK), *args, *overrides],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert not result.stderr
        return result.returncode, json.loads(result.stdout)

    return check


def test_hit_is_bound_but_does_not_establish_the_assertion(pair):
    check = run_case(pair, "test_hits_the_line")
    code, report = check()
    assert code == 0
    assert (report["file"], report["execution"], report["changed_line"], report["assertion"]) == (
        "present",
        "bound",
        "hit",
        "unproven",
    )


def test_green_but_unrelated_is_never_positive_evidence(pair):
    check = run_case(pair, "test_misses_the_line")
    code, report = check()
    assert code == 1 and report["changed_line"] == "not_hit"
    assert report["execution"] == "bound"
    assert "hits" in report["reason"]
    code, report = check("--expect-hits", "0")
    assert code == 0 and report["execution"] == "bound" and report["changed_line"] == "not_hit"


def test_wrong_execution_unmodified_line_and_stale_checkout_are_refused(pair):
    check = run_case(pair, "test_hits_the_line")
    for flags, reason in [
        (("--expect-run-id", secrets.token_hex(16)), "wrong_execution"),
        (("--expect-line", "1"), "not an added or replaced line"),
        (("--record-sha256", "0" * 64), "execution record digest"),
    ]:
        code, report = check(*flags)
        assert code == 1 and report["changed_line"] == "unproven" and reason in report["reason"]
    git(pair[0], "checkout", "--detach", pair[1])
    code, report = check()
    assert code == 1 and "clean Git root" in report["reason"]


def test_the_unchanged_fixture_assertion_detects_wrong_behavior_and_recovers(pair):
    root, base, candidate = pair
    assert run_case(pair, "test_hits_the_line")()[0] == 0
    source = root / "mod.py"
    test_hash = _digest(root / "test_mod.py")
    original = source.read_bytes()
    source.write_bytes(original.replace(b"return VALUE + 0", b"return VALUE + 1"))
    git(root, "commit", "-qam", "controlled wrong result")
    changed = (root, base, git(root, "rev-parse", "HEAD"))
    assert run_case(changed, "test_hits_the_line", exit_code=1, assertion_failure=True)()[0] == 0
    git(root, "checkout", "--detach", candidate)
    assert source.read_bytes() == original
    assert _digest(root / "test_mod.py") == test_hash
    assert run_case(pair, "test_hits_the_line")()[0] == 0


@pytest.mark.parametrize(("setup", "exit_code"), [(False, 0), (True, 1)])
def test_a_skip_or_setup_error_does_not_prove_execution(pair, setup, exit_code):
    root, base, _ = pair
    test = root / "test_mod.py"
    body = (
        "\n@pytest.fixture(autouse=True)\ndef broken():\n    raise RuntimeError('setup')\n"
        if setup
        else "\npytestmark = pytest.mark.skip(reason='unexecuted')\n"
    )
    test.write_text(test.read_text() + "\nimport pytest\n" + body)
    git(root, "commit", "-qam", "exercise a test without a call phase")
    absent_call = (root, base, git(root, "rev-parse", "HEAD"))
    code, report = run_case(absent_call, "test_hits_the_line", exit_code=exit_code)()
    assert code == 1
    assert report["file"] == "present" and report["execution"] == "unproven"
    assert report["changed_line"] == "unproven"
