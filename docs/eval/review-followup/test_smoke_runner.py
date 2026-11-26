"""Use local journals and fake senders only; no real model or credentials."""

import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from code_forge.eval.review_followup import smoke_runner
from code_forge.eval.review_followup.fixtures import InputError
from test_attempt_plan import gate, pinned


def setup_pair():
    rows = pinned(["storage"])
    return gate(["storage"], rows), tuple(body for _, body in rows)


def invoke(root, send):
    admission, bodies = setup_pair()
    return smoke_runner.run_smoke_once(
        journal_dir=root, run_id="paired-1", gate=admission, request_bodies=bodies, send_once=send
    )


def journal(root):
    (path,) = tuple(root.glob("*.jsonl"))
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def test_lost_reply_consumes_slot_and_fresh_invocation_sends_nothing(tmp_path):
    seen = []

    def lost_reply(body):
        # The sender observes the body only after its reservation is committed.
        assert journal(tmp_path)[-1] == {
            "event": "reserved",
            "attempt": 1,
            "state": "unknown_outcome",
        }
        seen.append(body)
        raise TimeoutError("reply lost after request")

    with pytest.raises(TimeoutError, match="reply lost"):
        invoke(tmp_path, lost_reply)
    before = journal(tmp_path)
    assert [e["event"] for e in before] == ["started", "reserved"]
    assert len(before[0]["slots"]) == 6
    with pytest.raises(InputError, match="smoke-run-already-started"):
        invoke(tmp_path, seen.append)
    assert len(seen) == 1
    assert journal(tmp_path) == before


def test_concurrent_launch_cannot_reenter_the_same_run(tmp_path):
    entered, release = Event(), Event()
    seen = []

    def wait_on_first(body):
        if not seen:
            entered.set()
            if not release.wait(3):
                raise AssertionError("other runner did not finish admission")
        seen.append(body)
        return True

    with ThreadPoolExecutor() as pool:
        first = pool.submit(invoke, tmp_path, wait_on_first)
        try:
            assert entered.wait(3)
            with pytest.raises(InputError, match="smoke-run-already-started"):
                invoke(tmp_path, seen.append)
        finally:
            release.set()
        rows = first.result(timeout=3)
    assert len(seen) == 6
    assert all(row.state == "completed" for row in rows)


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_sync_failure_before_dispatch_never_sends_and_preserves_marker(tmp_path, monkeypatch, fail_at):
    real_sync = smoke_runner.os.fsync
    calls = []

    def interrupted_sync(fd):
        calls.append(fd)
        if len(calls) == fail_at:
            raise OSError("test sync failure")
        real_sync(fd)

    monkeypatch.setattr(smoke_runner.os, "fsync", interrupted_sync)
    seen = []
    with pytest.raises(OSError, match="test sync failure"):
        invoke(tmp_path, seen.append)
    with pytest.raises(InputError, match="smoke-run-already-started"):
        invoke(tmp_path, seen.append)
    assert seen == []


def test_late_drift_is_checked_before_any_send_or_marker(tmp_path):
    admission, bodies = setup_pair()
    seen = []
    with pytest.raises(InputError, match="attempt-body-drift"):
        smoke_runner.run_smoke_once(
            journal_dir=tmp_path,
            run_id="paired-1",
            gate=admission,
            request_bodies=(*bodies[:-1], b"different"),
            send_once=seen.append,
        )
    assert not seen and not list(tmp_path.iterdir())
    assert all(row.state == "not_started" for row in admission.rows)


def test_known_reply_failure_is_counted_once_and_does_not_replace_its_slot(tmp_path):
    seen = []

    def known_result(body):
        seen.append(body)
        return len(seen) != 1

    rows = invoke(tmp_path, known_result)
    assert [r.state for r in rows] == ["failed", *["completed"] * 5]
    assert len(set(seen)) == 6
    assert [e["attempt"] for e in journal(tmp_path) if e["event"] == "reserved"] == list(range(1, 7))
    assert journal(tmp_path)[2]["state"] == "failed"


def _crash_after_send(root):
    def lose_process(body):
        (root / "sender-observed").write_bytes(body)
        os._exit(19)

    invoke(root, lose_process)


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="POSIX process")
def test_process_loss_after_send_blocks_a_new_process_with_same_id(tmp_path):
    ctx = multiprocessing.get_context("fork")
    worker = ctx.Process(target=_crash_after_send, args=(tmp_path,))
    worker.start()
    try:
        worker.join(10)
        assert worker.exitcode == 19
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join()
        worker.close()
    assert (tmp_path / "sender-observed").read_bytes() == pinned(["storage"])[0][1]
    events = journal(tmp_path)
    assert [event["event"] for event in events] == ["started", "reserved"]
    assert events[-1]["state"] == "unknown_outcome"
    with pytest.raises(InputError, match="smoke-run-already-started"):
        invoke(tmp_path, lambda _: pytest.fail("restarted run sent another request"))


def test_result_sync_failure_leaves_unknown_and_blocks_restart(tmp_path, monkeypatch):
    real_sync = smoke_runner.os.fsync
    syncs = 0
    seen = []
    admission, bodies = setup_pair()

    def interrupt_terminal_sync(fd):
        nonlocal syncs
        syncs += 1
        if syncs == 4:
            # Header, directory, and first reservation were already synced.
            assert len(seen) == 1
            raise OSError("terminal sync failed")
        real_sync(fd)

    def succeeded(body):
        seen.append(body)
        return True

    monkeypatch.setattr(smoke_runner.os, "fsync", interrupt_terminal_sync)
    with pytest.raises(OSError, match="terminal sync failed"):
        smoke_runner.run_smoke_once(
            journal_dir=tmp_path,
            run_id="paired-1",
            gate=admission,
            request_bodies=bodies,
            send_once=succeeded,
        )
    assert [row.state for row in admission.rows] == ["unknown_outcome", *["not_started"] * 5]
    # A flushed terminal line may be readable despite fsync failing. Do not
    # use its presence or absence to assert durability.
    with pytest.raises(InputError, match="smoke-run-already-started"):
        invoke(tmp_path, succeeded)
    assert seen == [bodies[0]]


@pytest.mark.parametrize("invalid_reply", [1, None])
def test_malformed_sender_return_never_publishes_terminal_state(tmp_path, invalid_reply):
    admission, bodies = setup_pair()
    seen = []

    def malformed(body):
        seen.append(body)
        return invalid_reply

    with pytest.raises(InputError, match=r"attempt_plan\.success"):
        smoke_runner.run_smoke_once(
            journal_dir=tmp_path,
            run_id="paired-1",
            gate=admission,
            request_bodies=bodies,
            send_once=malformed,
        )
    assert [event["event"] for event in journal(tmp_path)] == ["started", "reserved"]
    assert [row.state for row in admission.rows] == ["unknown_outcome", *["not_started"] * 5]
    with pytest.raises(InputError, match="smoke-run-already-started"):
        invoke(tmp_path, malformed)
    assert seen == [bodies[0]]
