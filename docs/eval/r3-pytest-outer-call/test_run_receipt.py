"""Exercise the producer and independent verifier through their commands."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).with_name("run_receipt.py")
SOURCE = "arithmetic.py"
TEST = "test_arithmetic.py"
NODE = f"{TEST}::test_total"
RUN = "0123456789abcdef" * 2
GOOD = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from arithmetic import total

def test_total(subtests):
    with subtests.test("actual call"):
        assert total(2, 2) == 4
"""
SETUP_FAILURE = """
import pytest

@pytest.fixture
def setup(subtests):
    with subtests.test("only setup"):
        assert 2 + 2 == 4
    raise RuntimeError("constructed setup error")

def test_total(setup):
    raise AssertionError("body should not be entered")
"""
INTERRUPTED = """
def test_total():
    raise KeyboardInterrupt
"""
EARLY_GREEN_EXIT = """
import pytest

def test_total(subtests):
    with subtests.test("before an explicit early green exit"):
        assert 2 + 2 == 4
    pytest.exit("constructed early exit", returncode=0)
"""
TEARDOWN_FAILURE = """
import pytest

@pytest.fixture
def setup():
    yield
    raise RuntimeError("constructed teardown error")

def test_total(setup):
    assert 2 + 2 == 4
"""
DIRTY_AFTER = """
from pathlib import Path

def test_total():
    Path("arithmetic.py").write_text("changed during pytest")
"""
PASS_SKIP = """
import pytest

@pytest.mark.skip(reason="not executed")
def test_total():
    pass
"""


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], check=True, capture_output=True, timeout=30).stdout


def build(tmp_path, body=GOOD):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / SOURCE).write_text("def total(a, b):\n    return a + b\n", encoding="utf-8")
    (root / TEST).write_text(body, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "--", SOURCE, TEST)
    git(
        root,
        "-c",
        "user.name=Receipt Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "constructed receipt fixture",
    )
    return root


def invoke(*args):
    return subprocess.run(
        [sys.executable, "-B", str(TOOL), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def produce(root, output, node=NODE):
    return invoke(
        "produce",
        "--root",
        root,
        "--output",
        output,
        "--source",
        SOURCE,
        "--node",
        node,
        "--run-id",
        RUN,
    )


def verify(root, output, **override):
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    expected = {
        "source": SOURCE,
        "node": NODE,
        "run-id": RUN,
        "commit": commit,
        "source-sha256": hashlib.sha256(git(root, "show", f"{commit}:{SOURCE}")).hexdigest(),
        "test-sha256": hashlib.sha256(git(root, "show", f"{commit}:{TEST}")).hexdigest(),
    }
    expected.update(override)
    flags = [part for key, value in expected.items() for part in (f"--{key}", value)]
    return invoke("verify", output, "--root", root, *flags)


def test_round_trip_rejects_wrong_independent_expectations_and_bad_documents(tmp_path, monkeypatch):
    root = build(tmp_path)
    output = tmp_path / "accepted.json"
    made = produce(root, output)
    assert made.returncode == 0, made.stderr + made.stdout
    result = verify(root, output)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "verified one ordinary passing pytest call\n"
    full = output.read_bytes()
    stale = produce(root, output)
    assert stale.returncode == 1 and "already exists" in stale.stderr
    assert output.read_bytes() == full
    for override, field in (
        ({"run-id": "c" * 32}, "run_id"),
        ({"commit": "a" * 40}, "snapshot"),
        ({"source-sha256": "b" * 64}, "snapshot"),
        ({"test-sha256": "d" * 64}, "snapshot"),
        ({"node": f"{TEST}::test_unrelated"}, "node"),
    ):
        rejected = verify(root, output, **override)
        assert rejected.returncode == 1 and field in rejected.stderr
    data = json.loads(full)
    for altered, field in (
        (dict(data, pytest="9.1.0"), "pytest"),
        (dict(data, phases=data["phases"][::2]), "phases"),
        (dict(data, pytest_exit=2), "pytest_exit"),
        (dict(data, pytest_exit=False), "integer"),
    ):
        output.write_text(json.dumps(altered), encoding="utf-8")
        rejected = verify(root, output)
        assert rejected.returncode == 1 and field in rejected.stderr
    output.write_bytes(b'{"profile":"unexpected",' + full[1:])
    assert "duplicate JSON key" in verify(root, output).stderr
    monkeypatch.setenv("PYTHONINTMAXSTRDIGITS", "4300")
    output.write_bytes(full.replace(b'"pytest_exit": 0', b'"pytest_exit": ' + b"1" * 4400))
    result = verify(root, output)
    assert result.returncode == 1
    assert result.stderr == "receipt refused: unreadable receipt JSON\n"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (SETUP_FAILURE, "not complete"),
        (INTERRUPTED, "not complete"),
        (EARLY_GREEN_EXIT, "not complete with one ordinary passing node (pytest exit 0)"),
        (TEARDOWN_FAILURE, "not complete"),
        (PASS_SKIP, "not complete"),
        (DIRTY_AFTER, "not clean"),
    ],
    ids=["setup-subtest", "interrupt", "early-green-exit", "teardown", "skip", "dirty-during-run"],
)
def test_producer_refuses_non_evidence(tmp_path, body, reason):
    root = build(tmp_path, body)
    output = tmp_path / "refused.json"
    result = produce(root, output)
    assert result.returncode == 1 and reason in result.stderr, result.stderr + result.stdout
    assert not output.exists()


def test_producer_requires_clean_tracked_input_and_outside_output(tmp_path):
    root = build(tmp_path)
    output = tmp_path / "refused.json"
    assert "outside the worktree" in produce(root, root / "receipt.json").stderr
    assert "not complete" in produce(root, output, "test_arithmetic.py::test_absent").stderr
    (root / SOURCE).write_text("uncommitted\n", encoding="utf-8")
    result = produce(root, output)
    assert result.returncode == 1 and "not clean" in result.stderr
    assert not output.exists()


def test_verifier_refuses_in_tree_and_symlink_receipts(tmp_path):
    root = build(tmp_path)
    output = tmp_path / "accepted.json"
    made = produce(root, output)
    assert made.returncode == 0, made.stderr + made.stdout
    inside = root / "receipt.json"
    inside.write_bytes(output.read_bytes())
    direct_alias = tmp_path / "external-link.json"
    direct_alias.symlink_to(output)
    outside_to_inside = tmp_path / "external-link"
    outside_to_inside.symlink_to(root, target_is_directory=True)
    inside_to_outside = root / "internal-link"
    inside_to_outside.symlink_to(tmp_path, target_is_directory=True)
    for path in (inside, direct_alias, outside_to_inside / inside.name, inside_to_outside / output.name):
        result = verify(root, path)
        assert result.returncode == 1 and "outside the worktree" in result.stderr, result
    assert verify(root, output).returncode == 0


def test_producer_does_not_fetch_missing_promisor_blob(tmp_path):
    root = build(tmp_path)
    remote = tmp_path / "promisor.git"
    git(root, "clone", "--bare", "--no-local", root, remote)
    git(remote, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(root, "remote", "add", "origin", remote.as_uri())
    git(root, "config", "remote.origin.promisor", "true")
    git(root, "config", "core.repositoryformatversion", "1")
    git(root, "config", "extensions.partialClone", "origin")
    blob = git(root, "rev-parse", f"HEAD:{SOURCE}").decode().strip()
    object_path = root / ".git" / "objects" / blob[:2] / blob[2:]
    object_path.unlink()
    output = tmp_path / "refused.json"

    result = produce(root, output)
    assert result.returncode == 1 and "git show failed" in result.stderr, result
    assert not output.exists()
    assert not object_path.exists()
