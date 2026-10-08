"""FIXVAL refusal and pure-proof regressions; synthetic records are not qualification."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from code_forge import fixval
from code_forge import _gate_pytest as gate
from code_forge.fixval_evidence import (
    EvidenceError,
    Inventory,
    canonical,
    choose_witness,
    new_stage,
    require_green,
    validate_owned_envelope,
    validate_stage,
    validate_test_timeout,
)
from code_forge.state import Verdict, load_state, save_state
from tests.test_fixval_integration import _make_machine, _make_resolved

PATCH = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-answer = 1\n+answer = 2\n"


def inventory(rows=None, code=0, key="g"):
    rows = rows or {
        "tests/test_foo.py::test_value": ("tests/test_foo.py", "passed", "passed", "passed", False)
    }
    return Inventory(rows, code, hashlib.sha256(key.encode()).hexdigest(), 100, "9.1.1")


def envelope(code=0, *, rows=None, files=None, framework=None):
    binding = {
        "parent_pid": 10,
        "fixval": {
            "version": 1,
            "mode": "owned",
            "nonce": "1" * 32,
            "phase": "fixed:0:0",
            "source_root": "/project",
            "caller_start_ticks": 11,
        },
    }
    owner = {
        "capture_version": 1,
        "caller_pid": 10,
        "caller_start_ticks": 11,
        "owner_pid": 20,
        "owner_start_ticks": 21,
        "driver_pid": 30,
        "driver_start_ticks": 31,
        "invocation_nonce": "1" * 32,
        "cleanup_complete": True,
        "timed_out": False,
        "cancelled": False,
        "returncode": code,
    }
    rows = rows or [
        ["tests/test_foo.py::test_value", "passed", "passed" if code == 0 else "failed", "passed"]
    ]
    record = {
        "schema": 1,
        "plugin": gate.PLUGIN,
        "pytest_version": "9.1.1",
        "binding": binding,
        "pid": 30,
        "sessions": 1,
        "collected": True,
        "rows": rows,
        "errors": dict.fromkeys(gate.ERROR_KINDS, 0),
        "collection_count": len(rows),
        "executed_count": sum(r[2] is not None for r in rows),
        "failed_count": sum(r[2] == "failed" for r in rows),
        "exitstatus": code,
        "cmdline_return": code,
        "normal_return": True,
        "unconfigured": True,
        "complete": True,
        "invalid": "",
    }
    result = {
        "schema": "fixval-pytest-v1",
        "record": record,
        "files": files or ["tests/test_foo.py"] * len(rows),
        "framework": framework or [False] * len(rows),
        "producer": {"pid": 30, "start_ticks": 31, "parent_pid": 20, "parent_start_ticks": 21},
    }
    return result, binding, owner


@pytest.mark.parametrize("value", [True, False, None, 0, -1, 86401, 1.5, "900", float("inf")])
def test_timeout_rejects_invalid(value):
    with pytest.raises(ValueError):
        validate_test_timeout(value)


@pytest.mark.parametrize("value", [1, 120, 600, 900, 86400])
def test_timeout_positive_integer_bounds(value):
    assert validate_test_timeout(value) == value


def test_owned_inventory_closes_green_and_red_without_changing_gate_waiver():
    for code in (0, 1):
        record, binding, owner = envelope(code)
        result = validate_owned_envelope(record, binding=binding, ownership=owner, returncode=code)
        assert result.returncode == code
        assert not gate.validate_record(
            record["record"], binding=binding, child_pid=30, returncode=code
        ).valid


@pytest.mark.parametrize(
    "key,value",
    [
        ("owner_pid", 99),
        ("owner_start_ticks", 99),
        ("driver_pid", 99),
        ("driver_start_ticks", 99),
        ("caller_pid", 99),
        ("caller_start_ticks", 99),
        ("invocation_nonce", "2" * 32),
        ("cleanup_complete", False),
        ("timed_out", True),
        ("cancelled", True),
    ],
)
def test_owner_identity_and_cleanup_are_not_test_assertions(key, value):
    record, binding, owner = envelope()
    owner[key] = value
    with pytest.raises(EvidenceError):
        validate_owned_envelope(record, binding=binding, ownership=owner, returncode=0)


@pytest.mark.parametrize("kind", ["internal", "collection", "setup", "teardown"])
def test_harness_errors_never_prove_red(kind):
    record, binding, owner = envelope(1)
    record["record"]["errors"][kind] = 1
    with pytest.raises(EvidenceError):
        validate_owned_envelope(record, binding=binding, ownership=owner, returncode=1)


@pytest.mark.parametrize("file", ["../test_foo.py", "/other/test_foo.py", "tests/../test_foo.py", ""])
def test_collected_file_projection_is_canonical(file):
    record, binding, owner = envelope(files=[file])
    with pytest.raises((EvidenceError, ValueError)):
        validate_owned_envelope(record, binding=binding, ownership=owner, returncode=0)


def test_three_stable_greens_to_same_candidate_call_red():
    greens = [inventory(key=f"g{i}") for i in range(3)]
    red = inventory(
        {"tests/test_foo.py::test_value": ("tests/test_foo.py", "passed", "failed", "passed", False)},
        1,
        "red",
    )
    for green in greens:
        require_green(green, {"tests/test_foo.py"})
    assert choose_witness(greens, red, {"tests/test_foo.py"}) == ("tests/test_foo.py::test_value", 1)
    assert choose_witness(greens, inventory(key="hollow"), {"tests/test_foo.py"}) == (None, 0)


@pytest.mark.parametrize("code", [2, 3, 4, 5, 127, -15])
def test_non_test_red_statuses_refuse(code):
    greens = [inventory(key=f"g{i}") for i in range(3)]
    red = inventory(
        {"tests/test_foo.py::test_value": ("tests/test_foo.py", "passed", "failed", "passed", False)},
        code,
        "red",
    )
    with pytest.raises(EvidenceError):
        choose_witness(greens, red, {"tests/test_foo.py"})


@pytest.mark.parametrize("flagged", ["green", "red", "both"])
def test_xfail_xpass_framework_results_are_not_witnesses(flagged):
    greens = [inventory(key=f"g{i}") for i in range(3)]
    red = inventory(
        {
            "tests/test_foo.py::test_value": (
                "tests/test_foo.py",
                "passed",
                "failed",
                "passed",
                flagged != "green",
            )
        },
        1,
        "red",
    )
    if flagged != "red":
        greens = [
            inventory({n: (*r[:4], True) for n, r in g.rows.items()}, key=f"g{i}")
            for i, g in enumerate(greens)
        ]
    with pytest.raises(EvidenceError):
        choose_witness(greens, red, {"tests/test_foo.py"})


def test_unrelated_or_previously_skipped_failure_cannot_supply_red():
    rows = {
        "tests/test_foo.py::test_value": ("tests/test_foo.py", "passed", "passed", "passed", False),
        "other.py::test_other": ("other.py", "passed", "passed", "passed", False),
    }
    greens = [inventory(rows, key=f"g{i}") for i in range(3)]
    changed = dict(rows)
    changed["other.py::test_other"] = ("other.py", "passed", "failed", "passed", False)
    with pytest.raises(EvidenceError):
        choose_witness(greens, inventory(changed, 1, "red"), {"tests/test_foo.py"})
    skipped = {"tests/test_foo.py::test_value": ("tests/test_foo.py", "skipped", None, "passed", False)}
    with pytest.raises(EvidenceError):
        require_green(inventory(skipped), {"tests/test_foo.py"})


def test_large_complete_inventory_is_not_a_small_envelope_assumption():
    rows = [[f"tests/test_foo.py::test_case[{i}]", "passed", "passed", "passed"] for i in range(12000)]
    record, binding, owner = envelope(rows=rows)
    parsed = validate_owned_envelope(record, binding=binding, ownership=owner, returncode=0)
    assert len(parsed.rows) == 12000
    assert len(canonical(record)) > 512 * 1024


def machine(tmp_path, *, diff=PATCH):
    """Exercise terminal wiring using its existing explicit stub-review exemption."""
    result = _make_machine(tmp_path, resolved=_make_resolved(git_diff=diff))
    result.coverage_l1_active = False
    return result


@pytest.mark.parametrize("config", [None, "test: {}\n", "test:\n  command: []\n"])
def test_applicable_configuration_failure_never_passes(tmp_path, monkeypatch, config):
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    obj = machine(tmp_path)
    gate_file = tmp_path / ".code-forge/gate.yaml"
    if config is None:
        gate_file.unlink()
    else:
        gate_file.write_text(config)
    obj._finalize_local_terminal()
    assert obj._state.verdict == Verdict.FAIL
    assert obj._state.converged is False
    assert obj._state.findings[-1].source == "FIXVAL"
    assert obj._state.infra_errors
    assert load_state(tmp_path / ".code-forge/state.json").verdict == Verdict.FAIL


@pytest.mark.parametrize("exception", ["non_git", "waiver", "no_tests"])
def test_legitimate_exception_precedes_unused_configuration(tmp_path, monkeypatch, exception):
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    obj = machine(tmp_path, diff=None if exception == "non_git" else PATCH)
    if exception == "waiver":
        monkeypatch.setenv("FIXVAL_WAIVER", "explicit controlled waiver")
    if exception == "no_tests":
        obj.resolved_review = _make_resolved(source_files=[Path("src/foo.py")], git_diff=PATCH)
    (tmp_path / ".code-forge/gate.yaml").unlink()
    obj._finalize_local_terminal()
    assert obj._state.verdict == Verdict.PASS
    assert obj._state.fixval_stage["outcome"] == ("WAIVED" if exception == "waiver" else "SKIPPED")


def test_machine_plumbs_timeout_and_refuses_unproven_pass(tmp_path, monkeypatch):
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    obj = machine(tmp_path)
    (tmp_path / ".code-forge/gate.yaml").write_text(
        "test:\n  command: [python3, -m, pytest]\n  timeout_seconds: 900\n"
    )
    with patch(
        "code_forge.fixval.run_fixval",
        return_value=fixval.FixvalResult(fixval.FixvalStatus.PASS, [], []),
    ) as spy:
        obj._finalize_local_terminal()
    assert spy.call_args.kwargs["timeout_seconds"] == 900
    assert obj._state.verdict == Verdict.FAIL
    assert obj._state.fixval_stage["outcome"] == "ERROR"


def test_helper_cannot_accept_naked_process_failure(tmp_path):
    result = fixval._test_reverted_candidate(
        fixval.FixvalCandidate(["tests/test_foo.py"], ["src/foo.py"]),
        [sys.executable, "-c", "raise SystemExit(3)"],
        os.environ.copy(),
        str(tmp_path),
    )
    assert result.status == fixval.FixvalStatus.ERROR


def test_actual_owner_capability_refusal_is_not_a_skipped_success(tmp_path, monkeypatch):
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    child_interface = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    if child_interface.exists():
        pytest.skip("negative case is specific to an unavailable owner capability")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/foo.py").write_text("answer = 2\n")
    result = fixval.run_fixval(
        fixval.FixvalCandidate(["tests/test_foo.py"], ["src/foo.py"]),
        ["python3", "-m", "pytest", "-q"],
        tmp_path,
        "",
        PATCH,
    )
    assert result.status == fixval.FixvalStatus.ERROR
    assert result.findings[0].source == "FIXVAL"
    assert result.infra_errors
    assert "ownership" in result.block_message or "procfs" in result.block_message
    assert (tmp_path / "src/foo.py").read_text() == "answer = 2\n"


def test_state_stage_rejects_unknown_schema(tmp_path):
    obj = machine(tmp_path)
    obj._state.fixval_stage = new_stage("1" * 32, "a" * 64)
    obj._state.fixval_stage["version"] = 99
    with pytest.raises(ValueError):
        save_state(obj._state, tmp_path / ".code-forge/state.json")


def test_unknown_skip_status_cannot_authorize_machine(tmp_path, monkeypatch):
    obj = machine(tmp_path)
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    with patch(
        "code_forge.fixval.run_fixval",
        return_value=fixval.FixvalResult(fixval.FixvalStatus.SKIPPED, [], [], reason="no_tests"),
    ):
        obj._finalize_local_terminal()
    assert obj._state.verdict == Verdict.FAIL


def test_pending_is_not_a_terminal_success_record():
    stage = new_stage("1" * 32, "a" * 64)
    validate_stage(stage)
    stage["outcome"] = "PASS"
    with pytest.raises(EvidenceError):
        validate_stage(stage)


@pytest.mark.integration
@pytest.mark.parametrize(
    "kind",
    [
        "red",
        "hollow",
        "setup",
        "collection",
        "internal",
        "empty",
        "strict_xpass",
        "xfail",
        "unittest_unexpected_success",
    ],
)
def test_real_owned_fixval_transaction(tmp_path, monkeypatch, kind):
    """Actual owner+pytest+Git qualification, unavailable capabilities are explicit."""
    children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    if not children.exists():
        pytest.skip("real owned FIXVAL needs readable Linux procfs task children")
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    source = root / "src/foo.py"
    source.write_text("answer = 1\n")
    for args in (
        ["init", "-q"],
        ["add", "src/foo.py"],
        ["-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "base"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    source.write_text("answer = 2\n")
    body = "from foo import answer\ndef test_value():\n    assert answer == 2\n"
    conftest = ""
    if kind == "hollow":
        body = "def test_value():\n    assert True\n"
    elif kind == "setup":
        body = "import pytest\nfrom foo import answer\n@pytest.fixture(autouse=True)\ndef ready():\n    if answer == 1: raise RuntimeError('setup')\ndef test_value():\n    assert answer == 2\n"
    elif kind == "collection":
        body = "from foo import answer\nif answer == 1: raise RuntimeError('collection')\ndef test_value():\n    assert answer == 2\n"
    elif kind == "internal":
        conftest = "from foo import answer\ndef pytest_sessionstart(session):\n    if answer == 1: raise RuntimeError('internal')\n"
    elif kind == "empty":
        conftest = "from foo import answer\ndef pytest_collection_modifyitems(session, config, items):\n    if answer == 1: items.clear()\n"
    elif kind == "unittest_unexpected_success":
        body = (
            "import unittest\nfrom foo import answer\nclass Case(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertTrue(True)\n"
            "if answer == 1: Case.test_value = unittest.expectedFailure(Case.test_value)\n"
        )
    elif kind in ("strict_xpass", "xfail"):
        conftest = "import pytest\nfrom foo import answer\ndef pytest_collection_modifyitems(items):\n    if answer == 1:\n        for item in items: item.add_marker(pytest.mark.xfail(strict=True))\n"
        if kind == "strict_xpass":
            body = "def test_value():\n    assert True\n"
    (root / "tests/test_foo.py").write_text(body)
    if conftest:
        (root / "tests/conftest.py").write_text(conftest)
        # Test-harness controls belong to the base, not the production reversal.
        subprocess.run(["git", "add", "tests/conftest.py"], cwd=root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "user.name=Test",
                "commit",
                "-qm",
                "base harness",
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )
    subprocess.run(["git", "add", "src/foo.py", "tests"], cwd=root, check=True)
    diff = subprocess.run(
        ["git", "diff", "--cached", "--binary", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    index_before = (root / ".git/index").read_bytes()
    candidate = fixval.FixvalCandidate(["tests/test_foo.py"], ["src/foo.py"])
    result = fixval.run_fixval(
        candidate,
        ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        root,
        "",
        diff,
        timeout_seconds=30,
        overfit_files=[],
    )
    assert source.read_text() == "answer = 2\n"
    assert (root / ".git/index").read_bytes() == index_before
    if kind == "red":
        assert result.status == fixval.FixvalStatus.PASS, result.block_message
        assert result.stage["witness"]["node"] == "tests/test_foo.py::test_value"
        assert len(result.stage["witness"]["green"]) == 3
        validate_stage(result.stage)
    else:
        assert result.status in (fixval.FixvalStatus.BLOCK, fixval.FixvalStatus.ERROR)


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["complete", "timeout", "interrupt"])
def test_real_bounded_owner_cleanup_boundary(tmp_path, mode):
    """Real escaping-descendant cleanup and bounded drain, no synthetic owner."""
    import _thread
    import threading
    import time
    from code_forge._mutation_process import run_owned_command

    children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    if not children.exists():
        pytest.skip("real owned FIXVAL needs readable Linux procfs task children")
    marker = tmp_path / "ready"
    grandchild = "import os,time; os.setsid(); time.sleep(60)"
    script = (
        "import pathlib,subprocess,sys,time\n"
        f"p=subprocess.Popen([sys.executable,'-c',{grandchild!r}])\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))\n"
        "sys.stdout.buffer.write(b'x'*(2*1024*1024)); sys.stdout.flush()\n"
        + ("time.sleep(0.3)\n" if mode == "complete" else "time.sleep(60)\n")
    )
    watcher = None
    stop = threading.Event()
    if mode == "interrupt":

        def interrupt_when_started():
            deadline = time.monotonic() + 10
            while not stop.is_set() and time.monotonic() < deadline:
                if marker.exists():
                    _thread.interrupt_main()
                    return
                time.sleep(0.02)

        watcher = threading.Thread(target=interrupt_when_started, daemon=True)
        watcher.start()
    owner = None
    try:
        if mode == "complete":
            result = run_owned_command(
                [sys.executable, "-c", script],
                timeout=15,
                output_limit_bytes=1024 * 1024,
                invocation_nonce="a" * 32,
            )
            owner = result.ownership
            assert len(result.stdout) + len(result.stderr) <= 1024 * 1024
            assert owner["diagnostic_truncated"] is True
            assert owner["streams"]["stdout"]["bytes"] == 2 * 1024 * 1024
        else:
            with pytest.raises(
                KeyboardInterrupt if mode == "interrupt" else subprocess.TimeoutExpired
            ) as caught:
                run_owned_command(
                    [sys.executable, "-c", script],
                    timeout=1 if mode == "timeout" else 15,
                    output_limit_bytes=1024 * 1024,
                    invocation_nonce="b" * 32,
                )
            owner = caught.value.ownership
        assert owner["cleanup_complete"] is True
        assert not any(row.get("remaining") for row in owner["owned"])
        assert marker.exists()
        assert not Path("/proc/" + marker.read_text()).exists()
    finally:
        stop.set()
        if watcher is not None:
            watcher.join(timeout=2)


@pytest.mark.integration
@pytest.mark.parametrize("shape", ["deletion", "rename", "binary", "restoration"])
def test_real_public_terminal_fixval_topology(tmp_path, monkeypatch, shape):
    """Public terminal/FIXVAL wiring with real Git/owned pytest, no L1 claim."""
    import time
    from code_forge.baseline import ResolvedReview
    from code_forge.diff import get_changed_files
    from code_forge.state import Mode
    from code_forge.machine import StateMachine

    if not Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").exists():
        pytest.skip("real owned FIXVAL needs readable Linux procfs task children")
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    state.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    filename = "src/data.bin" if shape == "binary" else "src/value.py"
    source = repo / filename
    source.write_bytes(b"\x00old" if shape == "binary" else b"value = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--all"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "base",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    if shape == "deletion":
        source.unlink()
        test = "from pathlib import Path\ndef test_changed():\n    assert not Path('src/value.py').exists()\n"
    elif shape == "rename":
        source.rename(repo / "src/renamed.py")
        source = repo / "src/renamed.py"
        test = (
            "from pathlib import Path\ndef test_changed():\n    assert Path('src/renamed.py').exists()\n"
        )
    elif shape == "binary":
        source.write_bytes(b"\x00new")
        test = "from pathlib import Path\ndef test_changed():\n    assert Path('src/data.bin').read_bytes() == b'\\x00new'\n"
    else:
        source.write_bytes(b"value = 2\n")
        source.chmod(0o751)
        marker = tmp_path / "escaped-pid"
        monkeypatch.setenv("FIXVAL_TEST_CHILD_MARKER", str(marker))
        child = "import pathlib,time;time.sleep(1);pathlib.Path('src/value.py').write_text('CORRUPTED');time.sleep(60)"
        test = (
            "import os,pathlib,subprocess,sys\nfrom value import value\n"
            "def test_changed():\n"
            "    if value == 1:\n"
            f"        p=subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True)\n"
            "        pathlib.Path(os.environ['FIXVAL_TEST_CHILD_MARKER']).write_text(str(p.pid))\n"
            "    assert value == 2\n"
        )
    (repo / "tests/test_changed.py").write_text(test)
    subprocess.run(["git", "add", "--all"], cwd=repo, check=True)
    diff = subprocess.run(
        ["git", "diff", "--cached", "--binary", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    before = {
        str(path.relative_to(repo)): (path.read_bytes(), path.stat().st_mode)
        for path in (repo / "src").iterdir()
    }
    index = (repo / ".git/index").read_bytes()
    (state / ".code-forge").mkdir()
    (state / ".code-forge/gate.yaml").write_text(
        "test:\n  command: [python3, -m, pytest, -q, -p, 'no:cacheprovider']\n  timeout_seconds: 30\n"
    )
    machine = StateMachine(
        mode=Mode.LOCAL,
        falsifier=None,
        autofixer=None,
        revert_fn=lambda _: None,
        resolved_review=ResolvedReview([Path(f) for f in get_changed_files(diff)], None, diff, "git"),
        source_hash=hashlib.sha256(diff.encode()).hexdigest(),
        baseline_spec_repr="HEAD..INDEX",
        cwd=state,
        source_root=repo,
        registry={},
        coverage_l1_active=False,
    )
    machine._finalize_local_terminal()
    assert machine._state.verdict == Verdict.PASS, machine._state.infra_errors
    proof = machine._state.fixval_stage
    assert proof["outcome"] == "PASS" and proof["witness"]["file"] == "tests/test_changed.py"
    assert len(proof["witness"]["green"]) == 3
    assert (repo / ".git/index").read_bytes() == index
    # Git does not track empty directories: restoring a deletion may remove src.
    source_dir = repo / "src"
    if shape == "deletion" and not source_dir.exists() and not source_dir.is_symlink():
        assert before == {}
        after = {}
    else:
        after = {
            str(path.relative_to(repo)): (path.read_bytes(), path.stat().st_mode)
            for path in source_dir.iterdir()
        }
    assert after == before
    if shape == "restoration":
        assert marker.exists() and not Path("/proc/" + marker.read_text()).exists()
        time.sleep(1.1)
        assert source.read_bytes() == b"value = 2\n"


@pytest.mark.parametrize(
    "command",
    [
        [sys.executable, "-m", "pytest"],
        [sys.executable, "-c", "raise SystemExit(1)"],
        ["pytest"],
        ["node", "test.js"],
    ],
)
def test_unsupported_legacy_commands_are_explicit_public_refusals(tmp_path, monkeypatch, command):
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    result = fixval.run_fixval(
        fixval.FixvalCandidate(["tests/test_foo.py"], ["src/foo.py"]), command, tmp_path, "", PATCH
    )
    assert result.status == fixval.FixvalStatus.ERROR
    assert result.reason == "execution"
    assert "unsupported direct pytest" in result.block_message


@pytest.mark.parametrize("value", [None, False, 0, 86401])
def test_public_api_rejects_explicit_invalid_timeout(tmp_path, monkeypatch, value):
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    result = fixval.run_fixval(
        fixval.FixvalCandidate(["tests/test_foo.py"], ["src/foo.py"]),
        ["python3", "-m", "pytest"],
        tmp_path,
        "",
        PATCH,
        timeout_seconds=value,
    )
    assert result.status == fixval.FixvalStatus.ERROR
    assert result.reason == "configuration"
    assert "integer" in result.block_message


def test_machine_omits_absent_timeout_instead_of_passing_none(tmp_path, monkeypatch):
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    obj = machine(tmp_path)
    with patch(
        "code_forge.fixval.run_fixval",
        return_value=fixval._execution_error("fixture", "no live qualification"),
    ) as observed:
        obj._finalize_local_terminal()
    assert "timeout_seconds" not in observed.call_args.kwargs


def test_snapshot_binds_candidate_bytes_inode_absence_and_index(tmp_path):
    from code_forge.fixval_evidence import source_snapshot

    (tmp_path / "src").mkdir()
    source = tmp_path / "src/value.py"
    source.write_text("value = 2\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/index").write_bytes(b"index-v1")
    before = source_snapshot(tmp_path, ["src/value.py", "src/absent.py"])
    source.write_text("value = 3\n")
    assert source_snapshot(tmp_path, ["src/value.py", "src/absent.py"]) != before
    stable = source_snapshot(tmp_path, ["src/value.py", "src/absent.py"])
    (tmp_path / ".git/index").write_bytes(b"index-v2")
    after = source_snapshot(tmp_path, ["src/value.py", "src/absent.py"])
    assert after["source_sha256"] == stable["source_sha256"]
    assert after["index_sha256"] != stable["index_sha256"]


def test_snapshot_does_not_follow_candidate_parent_or_index_links(tmp_path):
    from code_forge.fixval_evidence import source_snapshot

    (tmp_path / "outside").mkdir()
    (tmp_path / "src").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(OSError):
        source_snapshot(tmp_path, ["src/value.py"])
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/index").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(EvidenceError):
        source_snapshot(tmp_path, [])


@pytest.mark.parametrize(
    "bad",
    [
        "arbitrary garbage",
        "--- a/src/foo.py\n+++ b/src/foo.py\n@@ broken @@\n-old\n+new\n",
        "--- a/src/foo.py\n+++ b/src/foo.py\n",
        "diff --git a/src/foo.py b/src/foo.py\nold mode 100644\n",
        "garbage\n--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -1 +1 @@\n-old\n+new\n",
    ],
)
def test_malformed_diff_never_becomes_not_applicable(tmp_path, monkeypatch, bad):
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    result = fixval.run_fixval(
        fixval.FixvalCandidate(["tests/test_x.py"], ["src/foo.py"]),
        ["python3", "-m", "pytest"],
        tmp_path,
        "",
        bad,
    )
    assert result.status == fixval.FixvalStatus.ERROR
    assert result.reason == "invalid_patch"


def test_production_restore_identity_is_new_but_content_and_mode_are_exact(tmp_path):
    from code_forge.fixval_evidence import source_snapshot

    source = tmp_path / "value.py"
    source.write_text("value = 2\n")
    before = source_snapshot(tmp_path, ["value.py"])
    patch = "--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    result = fixval._transactional_probe(
        tmp_path, patch, lambda: fixval.FixvalResult(fixval.FixvalStatus.PASS, [], [])
    )
    after = source_snapshot(tmp_path, ["value.py"])
    assert result.status == fixval.FixvalStatus.PASS
    assert after["semantic_sha256"] == before["semantic_sha256"]
    assert after["index_sha256"] == before["index_sha256"]
    assert after["entries"]["value.py"] == result.restored_identities["value.py"]


def test_unittest_twisted_failure_callbacks_record_framework_provenance():
    """Callback unit contract only, not an owned execution qualification."""
    from types import SimpleNamespace

    calls = []

    class FakeCase:
        nodeid = "tests/test_unit.py::Case::test_value"

        def addExpectedFailure(self, *args, **kwargs):
            calls.append(("expected", args, kwargs))
            return "expected-return"

        def addUnexpectedSuccess(self, *args, **kwargs):
            calls.append(("unexpected", args, kwargs))
            return "unexpected-return"

    recorder = SimpleNamespace(framework={}, framework_callbacks=[], refuse=pytest.fail)
    assert gate._install_framework_callbacks(recorder, FakeCase)
    case = FakeCase()
    assert case.addUnexpectedSuccess("testcase", reason="twisted-todo") == "unexpected-return"
    assert case.addExpectedFailure("testcase", "exc") == "expected-return"
    assert recorder.framework == {case.nodeid: True}
    assert calls == [
        ("unexpected", ("testcase",), {"reason": "twisted-todo"}),
        ("expected", ("testcase", "exc"), {}),
    ]
    assert all(getattr(cls, name) is wrapper for cls, name, wrapper in recorder.framework_callbacks)


def test_framework_metadata_does_not_construct_a_lazy_instance():
    from types import SimpleNamespace
    import unittest

    @unittest.expectedFailure
    def example():
        pass

    class Item:
        def __init__(self):
            self._obj = example
            self.parent = SimpleNamespace(_obj=object)

        @property
        def obj(self):
            raise AssertionError("must not access lazy callable metadata")

        @property
        def instance(self):
            raise AssertionError("must not force test construction")

        def get_closest_marker(self, name):
            return None

    assert gate._expected_framework(Item())


def test_framework_metadata_preserves_real_unittest_teardown(tmp_path, request):
    """Real pytest node lifecycle, not an owned execution qualification."""
    from types import SimpleNamespace
    import unittest
    from _pytest.python import Module
    from _pytest.unittest import UnitTestCase, TestCaseFunction

    constructed = []

    class Case(unittest.TestCase):
        def __init__(self, *args, **kwargs):
            constructed.append(args)
            super().__init__(*args, **kwargs)

        def test_value(self):
            pass

    module = Module.from_parent(request.session, path=tmp_path / "test_metadata.py")
    module._obj = SimpleNamespace(Case=Case)
    case = UnitTestCase.from_parent(module, name="Case")
    item = TestCaseFunction.from_parent(case, name="test_value")
    # TestCaseFunction.setup initializes this before the generic fixture setup.
    # Only its real callable cache and teardown are under test here; avoid
    # executing unrelated session autouse fixtures on a synthetic collector.
    item._explicit_tearDown = None
    assert item.obj is not None
    before = len(constructed)
    assert before > 0
    assert not gate._expected_framework(item)
    item.teardown()
    assert item.__dict__.get("_obj") is None
    assert "_instance" not in item.__dict__
    assert not gate._expected_framework(item)
    assert len(constructed) == before
    assert item.__dict__.get("_obj") is None
    assert "_instance" not in item.__dict__


@pytest.mark.parametrize("replacement", [b"changed", b"x" * (1024 * 1024 + 1)])
def test_retained_prefix_replay_rejects_changed_or_oversized_file(tmp_path, replacement):
    from code_forge.fixval_evidence import EvidenceSession

    session = EvidenceSession(tmp_path, ["python3", "-m", "pytest"], [])
    cap = gate.prepare_pytest_capture(
        session.command,
        test_cwd=tmp_path,
        test_env={},
        reporter_path=Path(gate.__file__),
        capture_parent=session.directory,
    )
    record = {
        "status": "error",
        "stdout": b"abc",
        "stderr": b"",
        "ownership": {"streams": {"stdout": {"retained_bytes": 3}, "stderr": {"retained_bytes": 0}}},
    }
    session.captures.append((cap, record, {}))
    try:
        session._persist_phase(cap, record)
        session._replay_retained(cap, record)
        (cap.directory / "stdout.txt").write_bytes(replacement)
        with pytest.raises(ValueError):
            session.replay()
    finally:
        session.close()


def test_missing_closed_record_is_not_a_missing_runner_retry(tmp_path, monkeypatch):
    """Unit boundary failure input; no native owner or test execution claimed."""
    from code_forge import fixval_evidence

    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    monkeypatch.setenv("VIRTUAL_ENV", "/private-venv")
    observed = subprocess.CompletedProcess([], 0, b"", b"")
    observed.ownership = {
        "cleanup_complete": True,
        "streams": {"stdout": {"retained_bytes": 0}, "stderr": {"retained_bytes": 0}},
    }
    with (
        patch.object(fixval_evidence, "run_owned_command", return_value=observed) as owner,
        patch.object(
            fixval_evidence, "read_owned_capture", side_effect=FileNotFoundError("final.json missing")
        ),
        patch.object(fixval, "_transactional_probe") as reverse,
    ):
        result = fixval.run_fixval(
            fixval.FixvalCandidate(["tests/test_foo.py"], ["src/foo.py"]),
            ["python3", "-m", "pytest"],
            tmp_path,
            "",
            PATCH,
        )
    assert result.status == fixval.FixvalStatus.ERROR
    assert "final.json missing" in result.block_message
    assert owner.call_count == 1
    reverse.assert_not_called()


def test_projection_runtime_versions_match_capture_admission():
    from code_forge.fixval_terminal import QUALIFIED_VERSIONS

    assert QUALIFIED_VERSIONS == gate.QUALIFIED_VERSIONS


def test_projection_producer_and_parser_agree_on_synthetic_component_input():
    """Producer-shape unit test; none of these rows are execution authority."""
    from types import SimpleNamespace
    from code_forge.fixval_evidence import EvidenceSession
    from tests.test_fixval_terminal_projection import _phase, COMMAND, NODE, FILE

    session = object.__new__(EvidenceSession)
    session.stage_id = "1" * 32
    session.command = COMMAND
    session.captures = []
    inventories = []
    for index, name in enumerate(["fixed:0:0", "fixed:0:1", "fixed:0:2", "reverted"]):
        phase = _phase(index, name)
        rows = {
            NODE: (FILE, "passed", "passed" if index < 3 else "failed", "passed", False),
            FILE + "::test_other": (FILE, "passed", "passed", "passed", False),
        }
        item = Inventory(
            rows, phase["returncode"], phase["record_sha256"], phase["record_bytes"], "9.1.1"
        )
        inventories.append(item)
        owner = {
            **phase["owner"],
            "env_sha256": phase["env_sha256"],
            "cleanup_complete": True,
            "streams": phase["streams"],
            "diagnostic_truncated": False,
        }
        record = {
            key: phase[key]
            for key in (
                "phase",
                "nonce",
                "timeout",
                "status",
                "duration",
                "returncode",
                "base_env_sha256",
                "retry_eligible",
                "superseded",
            )
        }
        record.update(ownership=owner, inventory=item)
        session.captures.append(
            (SimpleNamespace(command=COMMAND + ["-p", "bootstrap" + str(index)]), record, {})
        )
    session.replay = lambda: {
        "directory": "/retained",
        "identity": [1, 2, 0],
        "files": 28,
        "bytes": 10000,
        "sha256": "9" * 64,
    }
    stage = session.projection(
        source_hash="a" * 64,
        config_hash="b" * 64,
        candidate_hash="c" * 64,
        greens=inventories[:3],
        red=inventories[3],
        witness=NODE,
        witness_count=1,
        outcome="PASS",
        reason="attributable_red",
        restoration="restored",
    )
    validate_stage(stage)
    assert stage["witness"]["red"]["row"] == ["passed", "failed", "passed", False]


@pytest.mark.parametrize(
    "failure_boundary,retention_error",
    [
        ("projection", EvidenceError("missing seals")),
        ("close", EvidenceError("missing seals")),
        ("projection", KeyboardInterrupt()),
        ("close", KeyboardInterrupt()),
        ("projection", SystemExit(3)),
        ("projection", [EvidenceError("missing seals"), KeyboardInterrupt()]),
        ("projection", [EvidenceError("missing seals"), SystemExit(3)]),
    ],
)
def test_transaction_recovery_survives_secondary_proof_failure(
    tmp_path, monkeypatch, retention_error, failure_boundary
):
    """Real Git reversal, injected failure inputs; never claims owned success."""
    from code_forge import fixval_evidence as evidence
    from code_forge._mutation_process import MutationProcessError

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    source = repo / "src/foo.py"
    source.write_text("answer = 2\n")
    (repo / "tests/test_foo.py").write_text("def test_value():\n    assert True\n")
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)

    def boundary(session, env, *, phase, timeout):
        if phase == "reverted":
            assert source.read_text() == "answer = 1\n"
            raise MutationProcessError("injected cleanup incomplete", cleanup_complete=False)
        return inventory(key=phase)

    with (
        patch.object(evidence.EvidenceSession, "execute", boundary),
        patch.object(evidence.EvidenceSession, failure_boundary, side_effect=retention_error),
    ):
        final_error = retention_error[-1] if isinstance(retention_error, list) else retention_error
        if isinstance(final_error, (KeyboardInterrupt, SystemExit)):
            with pytest.raises(type(final_error)) as caught:
                fixval.run_fixval(
                    fixval.FixvalCandidate(["tests/test_foo.py"], ["src/foo.py"]),
                    ["python3", "-m", "pytest"], repo, "", PATCH,
                )
            detail = "\n".join(caught.value.__notes__)
        else:
            result = fixval.run_fixval(
                fixval.FixvalCandidate(["tests/test_foo.py"], ["src/foo.py"]),
                ["python3", "-m", "pytest"], repo, "", PATCH,
            )
            assert result.status == fixval.FixvalStatus.BLOCK
            assert result.reason == "transaction"
            assert result.stage["outcome"] == "BLOCK"
            assert result.findings[0].id == "FIXVAL_TRANSACTION"
            assert any("missing seals" in message for message in result.infra_errors)
            detail = result.findings[0].error
            assert detail in result.infra_errors
    assert source.read_text() == "answer = 1\n"
    assert "injected cleanup incomplete" in detail
    assert "source restoration deferred" in detail
    assert "Recovery: " in detail
    recovery = Path(detail.split("Recovery: ", 1)[1].splitlines()[0])
    assert recovery.exists()


def test_command_envelope_includes_retention_elapsed_time(tmp_path, monkeypatch):
    """Clock/owner unit boundary only, never execution qualification."""
    from code_forge import fixval_evidence as evidence

    session = evidence.EvidenceSession(tmp_path, ["python3", "-m", "pytest"], [])
    now = [100.0]
    observed = subprocess.CompletedProcess([], 0, b"", b"")
    observed.ownership = {}

    def retain(capture, record, *, deadline):
        assert deadline == 131.0
        now[0] = 132.0

    try:
        with (
            patch.object(evidence.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(evidence, "run_owned_command", return_value=observed),
            patch.object(evidence, "read_owned_capture", return_value=inventory()),
            patch.object(session, "_persist_phase", side_effect=retain),
        ):
            with pytest.raises(EvidenceError, match="envelope deadline"):
                session.execute({}, phase="fixed:0:0", timeout=1)
        assert session.phases[0]["duration"] == 32.0
    finally:
        session.close()


@pytest.mark.parametrize("replacement", ["same_length_bytes", "same_bytes_new_inode"])
def test_initial_retained_seal_binds_written_bytes_and_identity(tmp_path, replacement):
    from code_forge.fixval_evidence import EvidenceSession

    session = EvidenceSession(tmp_path, ["python3", "-m", "pytest"], [])
    capture = gate.prepare_pytest_capture(
        session.command, test_cwd=tmp_path, test_env={},
        reporter_path=Path(gate.__file__), capture_parent=session.directory,
    )
    record = {
        "stdout": b"abc", "stderr": b"",
        "ownership": {"streams": {"stdout": {"retained_bytes": 3}, "stderr": {"retained_bytes": 0}}},
    }
    session.captures.append((capture, record, {}))
    original = gate._safe_read

    def replace_before_seal(name, **kwargs):
        if name == "stdout.txt":
            path = capture.directory / name
            if replacement == "same_length_bytes":
                path.write_bytes(b"xyz")
            else:
                other = capture.directory / "replacement"
                other.write_bytes(b"abc")
                other.replace(path)
        return original(name, **kwargs)

    try:
        with patch.object(gate, "_safe_read", side_effect=replace_before_seal):
            with pytest.raises(EvidenceError, match="before sealing"):
                session._persist_phase(capture, record)
    finally:
        session.close()
