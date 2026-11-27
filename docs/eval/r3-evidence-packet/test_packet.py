"""One packet binds identity, execution and hits to the same pytest run."""

import hashlib
import json
import os
import re
import runpy
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

PACKET = Path(__file__).with_name("packet.py")
VERIFY = Path(__file__).with_name("verify.py")
OBSERVER = PACKET.parent.parent / "r3-target-line" / "target_hits.py"

MODULE = """\
VALUE = 1


def hit():
    return VALUE


def other():
    return 0
"""

TESTS = """\
import mod


def test_hits_the_line():
    assert mod.hit() == 1


def test_misses_the_line():
    assert mod.other() == 0
"""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _line_of(source: str, needle: str) -> int:
    for number, text in enumerate(source.splitlines(), start=1):
        if text.strip() == needle:
            return number
    raise AssertionError(f"{needle} not found")


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "mod.py").write_text(MODULE, encoding="utf-8")
    (root / "test_mod.py").write_text(TESTS, encoding="utf-8")
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="Project Owner",
        GIT_AUTHOR_EMAIL="owner@example.invalid",
        GIT_COMMITTER_NAME="Project Owner",
        GIT_COMMITTER_EMAIL="owner@example.invalid",
    )
    for command in (["init", "-q"], ["add", "."], ["commit", "-qm", "sample"]):
        subprocess.run(["git", "-C", str(root), *command], check=True, capture_output=True, env=env)
    return root


def _run(
    root: Path,
    node: str,
    *extra: str,
    source: str = "mod.py",
    pytest_node: str | None = None,
):
    output = root.parent / f"packet-{secrets.token_hex(4)}.json"
    command = [
        sys.executable,
        str(PACKET),
        "--root",
        str(root),
        "--source",
        source,
        "--node",
        node,
        "--file",
        "mod.py",
        "--line",
        str(_line_of(MODULE, "return VALUE")),
        "--run-id",
        secrets.token_hex(16),
        "--output",
        str(output),
        "--",
        pytest_node or node,
        *extra,
    ]
    env = dict(
        os.environ,
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONPATH=str(root),
    )
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    return result, output


def _announced(result) -> dict:
    line = [row for row in result.stdout.splitlines() if row.startswith("forge-r3-packet ")]
    assert len(line) == 1, result.stdout
    return dict(re.findall(r"(\w+)=([0-9a-f]+)", line[0]))


def _expectations(
    root: Path,
    node: str,
    run_id: str,
    digest: str,
    *,
    call: str = "passed",
    hits: int = 1,
    exit_code: int = 0,
) -> list[str]:
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return [
        "--expect-run-id",
        run_id,
        "--expect-commit",
        commit,
        "--expect-source-sha256",
        _digest(root / "mod.py"),
        "--expect-test-sha256",
        _digest(root / node.split("::", 1)[0]),
        "--expect-observer-sha256",
        _digest(OBSERVER),
        "--expect-target-file",
        "mod.py",
        "--expect-line",
        str(_line_of(MODULE, "return VALUE")),
        "--expect-node",
        node,
        "--expect-call",
        call,
        "--expect-exit",
        str(exit_code),
        "--expect-hits",
        str(hits),
        "--expect-packet-sha256",
        digest,
    ]


def _verify(packet: Path, args: list[str]):
    return subprocess.run(
        [sys.executable, str(VERIFY), str(packet), *args],
        capture_output=True,
        text=True,
    )


def test_a_node_in_a_subdirectory_verifies(project):
    nested = project / "tests"
    nested.mkdir()
    (nested / "test_mod.py").write_text(TESTS, encoding="utf-8")
    node = "tests/test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    announced = _announced(result)
    check = _verify(packet, _expectations(project, node, announced["run_id"], announced["sha256"]))
    assert check.returncode == 0, check.stderr
    assert "verified one bound pytest execution" in check.stdout


def test_one_run_carries_identity_hits_and_the_call_outcome(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    assert body["schema"] == "forge-r3-evidence-v1"
    assert body["pytest_exit"] == 0
    assert body["collected"] == [node]
    assert body["hits"] == {node: 1}
    assert ["call", "passed"] in body["phases"][node]
    assert body["identity"]["commit"]
    assert body["identity"]["source"]["sha256"] == _digest(project / "mod.py")
    assert body["identity"]["observer"]["sha256"] == _digest(OBSERVER)


def test_the_announced_digest_verifies_the_matching_packet(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    announced = _announced(result)
    check = _verify(packet, _expectations(project, node, announced["run_id"], announced["sha256"]))
    assert check.returncode == 0, check.stderr
    assert "verified one bound pytest execution" in check.stdout


def test_a_run_identity_from_another_execution_is_refused(project):
    node = "test_mod.py::test_hits_the_line"
    first, packet = _run(project, node)
    second, _ = _run(project, node)
    assert first.returncode == 0 and second.returncode == 0
    other_run = _announced(second)["run_id"]
    first_digest = _announced(first)["sha256"]
    check = _verify(packet, _expectations(project, node, other_run, first_digest))
    assert check.returncode == 1
    assert "run identity" in check.stderr


def test_a_packet_edited_after_the_run_is_refused(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    announced = _announced(result)
    body = json.loads(packet.read_text(encoding="utf-8"))
    body["hits"][node] = 99
    packet.write_text(json.dumps(body), encoding="utf-8")
    check = _verify(packet, _expectations(project, node, announced["run_id"], announced["sha256"]))
    assert check.returncode == 1
    assert "packet digest" in check.stderr


def _rehashed(body: dict) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("verify", VERIFY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body["packet_sha256"] = module.digest_of(body)


def _assign(body: dict, path: tuple[str, ...], value: object) -> None:
    holder = body
    for key in path[:-1]:
        holder = holder[key]
    holder[path[-1]] = value


def test_a_test_record_for_another_file_is_refused_even_when_the_digest_holds(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    announced = _announced(result)
    body = json.loads(packet.read_text(encoding="utf-8"))
    body["identity"]["test"]["path"] = "test_other.py"
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    check = _verify(packet, _expectations(project, node, announced["run_id"], body["packet_sha256"]))
    assert check.returncode == 1
    assert "node does not name a node in the traced test file" in check.stderr
    assert "verified" not in check.stdout


def test_a_node_from_another_file_is_refused_even_when_the_digest_holds(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    announced = _announced(result)
    body = json.loads(packet.read_text(encoding="utf-8"))
    body["identity"]["node"] = "test_other.py::test_in_the_other_file"
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    check = _verify(packet, _expectations(project, node, announced["run_id"], body["packet_sha256"]))
    assert check.returncode == 1
    assert "node does not name a node in the traced test file" in check.stderr
    assert "verified" not in check.stdout


@pytest.mark.parametrize(
    ("path", "value", "refusal"),
    [
        pytest.param(
            ("identity", "source"),
            "not-a-record",
            "source digest does not match",
            id="source",
        ),
        pytest.param(
            ("collected",),
            "test_mod.py::test_hits_the_line",
            "node was not collected in this run",
            id="collected-not-list",
        ),
        pytest.param(
            ("collected",),
            ["test_mod.py::test_other_node"],
            "node was not collected in this run",
            id="collected-not-member",
        ),
    ],
)
def test_a_malformed_record_is_refused_without_a_traceback(project, path, value, refusal):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    announced = _announced(result)
    body = json.loads(packet.read_text(encoding="utf-8"))
    _assign(body, path, value)
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    check = _verify(packet, _expectations(project, node, announced["run_id"], body["packet_sha256"]))
    assert check.returncode == 1
    assert check.stderr == f"packet refused: {refusal}\n"
    assert "Traceback" not in check.stderr
    assert check.stdout == ""


@pytest.mark.parametrize(
    ("malformed", "refusal"),
    [
        pytest.param(
            {"phases": {"test_mod.py::test_hits_the_line": 1}},
            "phase entry is not a when/outcome pair",
            id="phase-container",
        ),
        pytest.param(
            {"phases": {"test_mod.py::test_hits_the_line": ["xx"]}},
            "phase entry is not a when/outcome pair",
            id="phase-entry-type",
        ),
        pytest.param(
            {"phases": {"test_mod.py::test_hits_the_line": [["call"]]}},
            "phase entry is not a when/outcome pair",
            id="phase-entry-length",
        ),
        pytest.param({"hits": [1]}, "hits is not a record", id="hits"),
    ],
)
def test_a_malformed_execution_record_is_refused_without_a_traceback(project, malformed, refusal):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    announced = _announced(result)
    body = json.loads(packet.read_text(encoding="utf-8"))
    body.update(malformed)
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    check = _verify(packet, _expectations(project, node, announced["run_id"], body["packet_sha256"]))
    assert check.returncode == 1
    assert check.stderr == f"packet refused: {refusal}\n"
    assert "Traceback" not in check.stderr
    assert check.stdout == ""


@pytest.mark.parametrize(
    ("path", "value", "refusal"),
    [
        pytest.param(
            ("hits", "test_mod.py::test_hits_the_line"),
            True,
            "target hits are not a number",
            id="hit-count",
        ),
        pytest.param(
            ("hits", "test_mod.py::test_hits_the_line"),
            1.0,
            "target hits are not a number",
            id="hit-count-float",
        ),
        pytest.param(("pytest_exit",), False, "pytest exit is not a number", id="pytest-exit"),
        pytest.param(("pytest_exit",), 0.0, "pytest exit is not a number", id="pytest-exit-float"),
        pytest.param(
            ("identity", "target", "line"), 5.0, "target does not match", id="target-line-float"
        ),
    ],
)
def test_a_non_integer_execution_field_is_refused_without_a_traceback(project, path, value, refusal):
    # A JSON boolean or float is not an integer count, exit status or line number.
    # Python's equality would otherwise accept `true` for 1, `false` for 0 and
    # `5.0` for a line of 5. A boolean is not a useful line witness: it cannot
    # equal the traced line of 5, so the base verifier already refuses it. The
    # float is the witness that distinguishes the new guard from the old comparison.
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    announced = _announced(result)
    body = json.loads(packet.read_text(encoding="utf-8"))
    _assign(body, path, value)
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    check = _verify(packet, _expectations(project, node, announced["run_id"], body["packet_sha256"]))
    assert check.returncode == 1
    assert check.stderr == f"packet refused: {refusal}\n"
    assert "Traceback" not in check.stderr
    assert check.stdout == ""


def test_a_source_that_differs_from_the_target_is_refused_by_the_verifier(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    announced = _announced(result)
    body = json.loads(packet.read_text(encoding="utf-8"))
    body["identity"]["source"]["path"] = "other.py"
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    check = _verify(packet, _expectations(project, node, announced["run_id"], body["packet_sha256"]))
    assert check.returncode == 1
    assert "does not describe the traced target" in check.stderr
    assert "verified" not in check.stdout


def test_an_explicit_zero_hit_count_verifies_when_zero_is_expected(project):
    node = "test_mod.py::test_misses_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    assert body["hits"] == {node: 0}
    announced = _announced(result)
    check = _verify(
        packet,
        _expectations(project, node, announced["run_id"], announced["sha256"], hits=0),
    )
    assert check.returncode == 0, check.stderr
    assert check.stdout == "verified one bound pytest execution\n"
    assert check.stderr == ""


@pytest.mark.parametrize(
    "hits",
    [
        pytest.param(None, id="missing-table"),
        pytest.param({}, id="empty-table"),
        pytest.param({"test_mod.py::test_other_node": 0}, id="other-node-only"),
    ],
)
def test_a_missing_node_hit_count_is_refused_when_zero_is_expected(project, hits):
    node = "test_mod.py::test_misses_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    assert body["hits"] == {node: 0}
    announced = _announced(result)
    if hits is None:
        body.pop("hits")
    else:
        body["hits"] = hits
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    check = _verify(
        packet,
        _expectations(project, node, announced["run_id"], body["packet_sha256"], hits=0),
    )
    assert check.returncode == 1
    assert check.stderr == "packet refused: target hits are not a number\n"
    assert check.stdout == ""


def test_a_green_run_that_missed_the_target_is_refused(project):
    node = "test_mod.py::test_misses_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    announced = _announced(result)
    check = _verify(
        packet,
        _expectations(project, node, announced["run_id"], announced["sha256"]),
    )
    assert check.returncode == 1
    assert "target hits do not match" in check.stderr


def test_a_collected_node_that_never_ran_is_refused(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node, "--collect-only")
    assert result.returncode == 0, result.stderr
    announced = _announced(result)
    check = _verify(packet, _expectations(project, node, announced["run_id"], announced["sha256"]))
    assert check.returncode == 1
    assert "no call phase" in check.stderr


def test_another_commit_is_refused(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    announced = _announced(result)
    args = _expectations(project, node, announced["run_id"], announced["sha256"])
    args[args.index("--expect-commit") + 1] = "0" * 40
    check = _verify(packet, args)
    assert check.returncode == 1
    assert "commit" in check.stderr


def test_a_source_that_is_not_the_traced_file_is_refused(project):
    (project / "other.py").write_text(MODULE, encoding="utf-8")
    result, packet = _run(project, "test_mod.py::test_hits_the_line", source="other.py")
    assert result.returncode == 2
    assert "resolve to the same file" in result.stderr
    assert not packet.exists()


def test_a_passing_call_beside_a_failing_node_is_refused(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    announced = _announced(result)
    body = json.loads(packet.read_text(encoding="utf-8"))
    body["collected"].append("test_other.py::test_fails")
    body["phases"]["test_other.py::test_fails"] = [["call", "failed"]]
    body["pytest_exit"] = 1
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    check = _verify(
        packet,
        _expectations(
            project,
            node,
            announced["run_id"],
            body["packet_sha256"],
            call="failed",
            exit_code=1,
        ),
    )
    assert check.returncode == 1
    assert "collected nodes do not all belong to the traced test file" in check.stderr
    assert "verified" not in check.stdout


def test_a_missing_digest_or_hit_count_is_refused(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    announced = _announced(result)
    full = _expectations(project, node, announced["run_id"], announced["sha256"])
    for flag in ("--expect-packet-sha256", "--expect-hits"):
        trimmed = list(full)
        start = trimmed.index(flag)
        del trimmed[start : start + 2]
        check = _verify(packet, trimmed)
        assert check.returncode != 0, flag
        assert "verified one bound pytest execution" not in check.stdout, flag


XFAILED = """\
import pytest

import mod


@pytest.mark.xfail(reason="expected to fail, but it passes")
def test_marks_as_xpass():
    assert mod.hit() == 1
"""

DRIFTING = """\
from pathlib import Path

import mod


def test_edits_the_source_after_hitting_the_line():
    assert mod.hit() == 1
    target = Path(__file__).with_name("mod.py")
    target.write_text(target.read_text() + "\\n# drift\\n", encoding="utf-8")
"""


def test_an_xpass_does_not_count_as_a_plain_pass(project):
    (project / "test_xfail.py").write_text(XFAILED, encoding="utf-8")
    node = "test_xfail.py::test_marks_as_xpass"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    assert ["call", "xpassed"] in body["phases"][node]
    announced = _announced(result)
    check = _verify(packet, _expectations(project, node, announced["run_id"], announced["sha256"]))
    assert check.returncode == 1
    assert "node call outcome does not match" in check.stderr


def test_a_tree_that_changes_during_the_run_is_refused(project):
    (project / "test_drift.py").write_text(DRIFTING, encoding="utf-8")
    result, packet = _run(project, "test_drift.py::test_edits_the_source_after_hitting_the_line")
    assert result.returncode == 2
    assert "changed during the run" in result.stderr
    assert not packet.exists()


def test_a_test_file_changed_before_the_run_keeps_its_own_digest(project):
    test_file = project / "test_mod.py"
    test_file.write_text(TESTS + "\n# prepared before the run\n", encoding="utf-8")
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    assert body["identity"]["test"]["sha256"] == _digest(test_file)
    announced = _announced(result)
    check = _verify(packet, _expectations(project, node, announced["run_id"], announced["sha256"]))
    assert check.returncode == 0, check.stderr


STRICT_XPASS = """\
import pytest

import mod


@pytest.mark.xfail(strict=True, reason="expected to fail, but it passes")
def test_marks_as_strict_xpass():
    assert mod.hit() == 1
"""


def test_a_strict_xpass_is_not_a_plain_failure(project):
    (project / "test_strict.py").write_text(STRICT_XPASS, encoding="utf-8")
    node = "test_strict.py::test_marks_as_strict_xpass"
    result, packet = _run(project, node)
    assert result.returncode == 1, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    assert ["call", "xpassed"] in body["phases"][node]
    announced = _announced(result)
    check = _verify(
        packet,
        _expectations(
            project,
            node,
            announced["run_id"],
            announced["sha256"],
            call="failed",
            exit_code=1,
        ),
    )
    assert check.returncode == 1
    assert "node call outcome does not match" in check.stderr


INACTIVE_XFAIL = """\
import sys

import pytest

import mod


@pytest.mark.xfail(sys.version_info < (3, 0), reason="only on py2")
def test_fails_under_an_inactive_marker():
    assert mod.hit() == 0
"""


def test_a_plain_failure_under_an_inactive_xfail_stays_a_failure(project):
    (project / "test_inactive.py").write_text(INACTIVE_XFAIL, encoding="utf-8")
    node = "test_inactive.py::test_fails_under_an_inactive_marker"
    result, packet = _run(project, node)
    assert result.returncode == 1, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    assert ["call", "failed"] in body["phases"][node]


RAISES_MISMATCH = """\
import pytest

import mod


@pytest.mark.xfail(raises=ValueError, reason="expects ValueError")
def test_raises_a_different_type():
    mod.hit()
    raise TypeError("not a ValueError")
"""


def test_a_raises_mismatch_stays_a_failure(project):
    (project / "test_raises.py").write_text(RAISES_MISMATCH, encoding="utf-8")
    node = "test_raises.py::test_raises_a_different_type"
    result, packet = _run(project, node)
    assert result.returncode == 1, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    assert ["call", "failed"] in body["phases"][node]


OTHER_FILE = """\
import mod


def test_in_the_other_file():
    assert mod.hit() == 1
"""


def test_the_recorded_node_must_live_in_the_hashed_test_file(project):
    (project / "test_other.py").write_text(OTHER_FILE, encoding="utf-8")
    claimed = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, claimed, pytest_node="test_other.py::test_in_the_other_file")
    assert result.returncode == 2
    assert "does not name a node in" in result.stderr
    assert not packet.exists()


def test_the_producer_refuses_a_repeated_target(project):
    node = "test_mod.py::test_hits_the_line"
    run, packet = _run(project, node, "--keep-duplicates", "test_mod.py", pytest_node="test_mod.py")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "target node was collected more than once" in run.stderr
    assert not packet.exists()
    assert "forge-r3-packet" not in run.stdout


@pytest.mark.parametrize("repeat", ["collection", "call"])
def test_the_verifier_refuses_ambiguous_target_identity(project, repeat):
    node = "test_mod.py::test_hits_the_line"
    run, packet = _run(project, node)
    assert run.returncode == 0, run.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    if repeat == "collection":
        body["collected"].append(node)
        reason = "target node was collected more than once"
    else:
        body["phases"][node].append(["call", "failed"])
        reason = "target node has more than one call report"
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    check = _verify(
        packet,
        _expectations(project, node, body["identity"]["run_id"], body["packet_sha256"]),
    )
    assert check.returncode == 1, check.stdout + check.stderr
    assert reason in check.stderr
    assert "verified" not in check.stdout


@pytest.mark.parametrize(
    ("raw", "diagnostic"),
    [(b"\xff", "packet is not valid UTF-8"), (b"{", "packet is not valid JSON")],
)
def test_a_malformed_packet_file_is_refused_without_a_traceback(project, tmp_path, raw, diagnostic):
    node = "test_mod.py::test_hits_the_line"
    packet = tmp_path / "malformed.json"
    packet.write_bytes(raw)
    check = _verify(packet, _expectations(project, node, "a" * 32, "b" * 64))
    assert check.returncode == 1
    assert check.stdout == ""
    assert check.stderr == f"packet refused: {diagnostic}\n"


def test_a_json_conversion_error_is_refused(tmp_path, monkeypatch):
    verifier = runpy.run_path(str(VERIFY))
    packet = tmp_path / "packet.json"
    packet.write_text("{}", encoding="utf-8")

    def refuse_numeric_conversion(_text):
        raise ValueError("numeric limit")

    monkeypatch.setattr(json, "loads", refuse_numeric_conversion)
    with pytest.raises(verifier["Refused"], match="^packet contains an unsupported JSON value$"):
        verifier["load"](packet)


def test_an_unreadable_packet_file_is_refused(project, tmp_path):
    node = "test_mod.py::test_hits_the_line"
    packet = tmp_path / "packet.json"
    packet.write_text("{}", encoding="utf-8")
    packet.chmod(0)
    try:
        check = _verify(packet, _expectations(project, node, "a" * 32, "b" * 64))
    finally:
        packet.chmod(0o644)
    assert check.returncode == 1
    assert check.stdout == ""
    assert check.stderr == "packet refused: packet cannot be read\n"


def test_an_out_of_range_number_is_refused_by_the_real_parser(tmp_path):
    verifier = runpy.run_path(str(VERIFY))
    packet = tmp_path / "packet.json"
    packet.write_text("1" + "0" * 4300, encoding="utf-8")
    with pytest.raises(verifier["Refused"], match="^packet contains an unsupported JSON value$"):
        verifier["load"](packet)


def test_a_packet_in_an_unsearchable_directory_is_refused(tmp_path):
    verifier = runpy.run_path(str(VERIFY))
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    packet = hidden / "packet.json"
    packet.write_text("{}", encoding="utf-8")
    hidden.chmod(0)
    try:
        with pytest.raises(verifier["Refused"], match="^packet (cannot be read|is not a regular file)$"):
            verifier["load"](packet)
    finally:
        hidden.chmod(0o755)


def test_a_packet_past_the_size_limit_is_refused_before_it_is_read(tmp_path, monkeypatch):
    verifier = runpy.run_path(str(VERIFY))
    packet = tmp_path / "packet.json"
    packet.write_text("{}", encoding="utf-8")
    verifier["load"].__globals__["MAX_PACKET_BYTES"] = 0

    def fail_if_read(self, *args, **kwargs):
        raise AssertionError("the packet was read before the size check")

    monkeypatch.setattr(verifier["Path"], "read_text", fail_if_read)
    with pytest.raises(verifier["Refused"], match="^packet exceeds size limit$"):
        verifier["load"](packet)


def test_a_node_mismatch_refuses_without_echoing_the_name(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    leaked = "secret-node-name"
    body["identity"]["node"] = leaked + "::nope"
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    announced = _announced(result)
    check = _verify(packet, _expectations(project, node, announced["run_id"], body["packet_sha256"]))
    assert check.returncode == 1
    assert leaked not in check.stderr
    assert check.stdout == ""


def test_a_different_node_in_the_same_file_is_refused(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    body["identity"]["node"] = "test_mod.py::test_some_other_node"
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    announced = _announced(result)
    check = _verify(packet, _expectations(project, node, announced["run_id"], body["packet_sha256"]))
    assert check.returncode == 1
    assert "node does not name a node in the traced test file" in check.stderr


def test_a_node_mismatch_does_not_echo_the_test_path(project):
    node = "test_mod.py::test_hits_the_line"
    result, packet = _run(project, node)
    assert result.returncode == 0, result.stderr
    body = json.loads(packet.read_text(encoding="utf-8"))
    leaked = "secret-test-path"
    body["identity"]["node"] = leaked + "::nope"
    body["identity"]["test"]["path"] = leaked
    _rehashed(body)
    packet.write_text(json.dumps(body), encoding="utf-8")
    announced = _announced(result)
    check = _verify(packet, _expectations(project, node, announced["run_id"], body["packet_sha256"]))
    assert check.returncode == 1
    assert leaked not in check.stderr
