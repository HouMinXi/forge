"""Ordinary consumers with real, admitted native storage and simulated HTTP.

NOT RUN on unsupported hosts. The existing disk_root fixture chooses the
checkout filesystem; admission, host, owner, recorder, and reopening are real.
The HTTP opener/response and synthetic credential are controlled; the fanout
regression also controls post-publication owner-lock timing. These are not
installed-CLI, external-network, crash-recovery, or whole-review tests.
"""

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import threading

import pytest

from code_forge import invocation_audit, invocation_send_audit, llm_invoke
from code_forge.autofix import StubAutoFixer
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.factories import _L1Call, build_l1_provider
from code_forge.falsify_real import RealFalsifier
from code_forge.invocation_host import HostAuditSession
from code_forge.invocation_io import AuditPhase
from code_forge.invocation_owner import InvocationOwner
from code_forge.invocation_owner_stage import _Publication, OwnerCaptureStage, OwnerStageUnavailable
from code_forge.invocation_review import OrdinaryReviewAudit
from code_forge.llm_invoke import Usage
from code_forge.machine import StateMachine
from code_forge.source import compute_source_hash
from code_forge.state import Mode, State

pytest_plugins = ["tests.test_invocation_audit"]

DIFF = (
    "diff --git a/sample.py b/sample.py\n--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
)
PASSES = ("qodo", "expert", "adversarial")
PRIVATE = "NATIVE_PRIVATE_RESPONSE_SENTINEL"
REQUEST_PREFIX = b"POST\0https://provider.invalid/v1/chat/completions\0"


def _resolved():
    return ResolvedReview(
        source_files=[Path("sample.py")], baseline_content=None, git_diff=DIFF, mode_hint="git"
    )


def _provider(monkeypatch):
    monkeypatch.setenv("ORDINARY_NATIVE_TEST_KEY", "synthetic-fixture-value")
    backend = BackendConfig(
        name="ordinary-native",
        type="api",
        format="openai",
        model="fixture",
        base_url="https://provider.invalid/v1",
        api_key_env="ORDINARY_NATIVE_TEST_KEY",
        stream=True,
    )
    call = build_l1_provider("real", _resolved(), backend=backend, max_attempts=1, initial_delay_s=0)
    assert type(call) is _L1Call and call.supports_invocation_audit
    return backend, call


@pytest.fixture(autouse=True)
def one_pass_at_a_time(monkeypatch, default_l1_dispatch):
    """Run the L1 passes one at a time.

    They share the invocation audit owner, whose lock wait is bounded, so
    overlapping them lets a scheduling race decide whether the run under test
    settles -- a property these tests are not about. It asks for
    `default_l1_dispatch` by name so this switch lands after the suite-wide
    deletion of it. The falsify loop needs no switch here: with one candidate it runs
    inline.
    """
    monkeypatch.setenv("FORGE_L1_SERIAL", "1")


def _lane(request):
    body = json.loads(request.data)
    assert body["stream"] is True
    prompt = body["messages"][0]["content"]
    if prompt.startswith("You are a code review verifier."):
        return "falsify"
    for name, role in (
        ("qodo", "structural code reviewer"),
        ("expert", "senior engineer: SOLID"),
        ("adversarial", "adversarial QE"),
    ):
        if "You are a " + role in prompt:
            return name
    raise AssertionError("unexpected ordinary request prompt")


class _Response(io.BytesIO):
    status = 200
    headers = {}

    def __init__(self, raw, *, interrupt_tail=False):
        super().__init__(raw)
        self.interrupt_tail = interrupt_tail

    def read1(self, size=-1):
        block = super().read(size)
        if not block and self.interrupt_tail:
            # DONE and valid content were received, but transport EOF was not.
            raise OSError(PRIVATE)
        return block


@dataclass(frozen=True)
class _Exchange:
    lane: str
    number: int
    request: bytes
    raw: bytes


class _Transport:
    def __init__(self, monkeypatch, *, partial_falsifier=False):
        self.partial_falsifier = partial_falsifier
        self.exchanges = []
        self.counts = {}
        self.lock = threading.Lock()
        monkeypatch.setattr(llm_invoke.urllib.request, "urlopen", self.forbidden)
        monkeypatch.setattr(invocation_send_audit.urllib.request, "build_opener", lambda *_: self)

    @staticmethod
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected unaudited network route")

    def open(self, request, *, timeout):
        assert timeout > 0
        # Freeze the method and target independently of the production digest.
        assert request.get_method() == "POST"
        assert request.full_url == "https://provider.invalid/v1/chat/completions"
        lane = _lane(request)
        with self.lock:
            number = self.counts[lane] = self.counts.get(lane, 0) + 1
        if lane == "falsify":
            content = {"verdict": "DISMISSED", "reasoning": "The change is intentional."}
            tokens = 4
        else:
            tokens = PASSES.index(lane) + 1
            content = {
                "findings": [
                    dict(
                        file="sample.py",
                        line=1,
                        severity="P2",
                        description=f"{lane} candidate",
                        excerpt="x = 2",
                    )
                ]
                if not self.partial_falsifier or lane == "qodo"
                else [],
                "code_excerpts": [dict(file="sample.py", start_line=1, end_line=1, content="x = 2")],
            }
        frame = {
            "choices": [
                {
                    "delta": {
                        "content": json.dumps(content),
                        "reasoning_content": PRIVATE,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": tokens, "completion_tokens": tokens + 1},
        }
        raw = (
            b"data: "
            + json.dumps(frame).encode()
            + b"\n\ndata: [DONE]\n\n"
            + f"opaque-tail:{lane}:{number}".encode()
        )
        with self.lock:
            self.exchanges.append(_Exchange(lane, number, request.data, raw))
        return _Response(
            raw, interrupt_tail=self.partial_falsifier and lane == "falsify" and number == 1
        )


def _native_recorder(review, disk_root):
    host = review._host
    assert type(host) is HostAuditSession
    assert host._root == disk_root / "code-forge" / "invocations-v2"
    assert type(host._owner) is InvocationOwner, "actual supported-filesystem admission is required"
    assert len(host._owner._epochs) == 1
    recorder = host._owner._epochs[0].recorder
    assert type(recorder) is invocation_audit.AttemptRecorder
    return recorder


def _journal(run):
    """Decode committed framing independently of the recorder's replay code."""
    data = (run / "journal").read_bytes()
    rows, offset = [], 0
    while offset < len(data):
        length = int(data[offset : offset + 8], 16)
        assert data[offset + 8 : offset + 9] == b" "
        end = offset + 9 + length
        assert data[end : end + 1] == b"\n"
        rows.append(json.loads(data[offset + 9 : end]))
        offset = end + 1
    assert offset == len(data)
    assert rows and all(row["schema_version"] == 2 for row in rows)
    return rows


def _reopen(review, recorder, disk_root):
    assert _native_recorder(review, disk_root) is recorder
    live = recorder.scoped_snapshot()
    root, run_id = recorder.root, live.run_id
    replay = invocation_audit.AttemptRecorder.reopen_scoped(root, run_id)
    assert replay == live
    run = root / run_id
    assert (run / "admission").is_file() and (run / "final").is_file()
    assert replay.input_manifest == dict(
        schema_version=1,
        run_id=run_id,
        snapshot_id=replay.attempts[0].context.snapshot_id,
        mode="diff",
        legacy_source_hash=compute_source_hash(git_diff=DIFF),
        exact_diff_sha256=hashlib.sha256(DIFF.encode()).hexdigest(),
        diff_bytes=len(DIFF.encode()),
        files=(),
        repositories=(),
    )
    return replay, run, _journal(run)


def _assert_raw(run, ref, raw, *, partial=False):
    assert type(ref) is invocation_audit.RawRef and ref.layer == "wire"
    assert ref.partial is partial
    assert ref.original_bytes == (None if partial else len(raw))
    assert ref.retained_bytes == len(raw)
    assert ref.sha256 == hashlib.sha256(raw).hexdigest()
    assert (run / ("raw-" + ref.artifact_id)).read_bytes() == raw
    assert PRIVATE.encode() in raw and b"opaque-tail:" in raw


def test_native_three_pass_rounds_reopen_exact_raw(disk_root, monkeypatch, capsys):
    transport = _Transport(monkeypatch)
    _, call = _provider(monkeypatch)
    review = OrdinaryReviewAudit(source=DIFF, environ={"XDG_STATE_HOME": str(disk_root)})
    earlier, later = review.round(2), review.round(7)
    recorder = None
    try:
        for view in (later, earlier, earlier):
            findings, excerpts, usage, _ = call.call_with_audit(view)
            assert [item.description for item in findings] == [
                f"[{name}] {name} candidate" for name in PASSES
            ]
            assert [item["pass_name"] for item in excerpts] == list(PASSES)
            assert usage == Usage(6, 9)
            assert call.reviewer_responses == dict.fromkeys(PASSES, 1)
            current = _native_recorder(review, disk_root)
            assert recorder is None or current is recorder
            recorder = current
    finally:
        review.finalize()

    replay, run, rows = _reopen(review, recorder, disk_root)
    assert replay.audit_complete and replay.state == "completed" and not replay.faults
    assert review._host.safe_summary().audit_complete
    assert transport.counts == dict.fromkeys(PASSES, 3)
    assert len(replay.attempts) == len(transport.exchanges) == 9
    assert len({attempt.logical_id for attempt in replay.attempts}) == 9
    assert len({attempt.context.snapshot_id for attempt in replay.attempts}) == 1
    starts = {row["attempt_id"]: row for row in rows if row["event"] == "start"}
    expected = {hashlib.sha256(item.raw).hexdigest(): item for item in transport.exchanges}
    assert len(expected) == 9
    for attempt in replay.attempts:
        assert len(attempt.raw_refs) == 1
        ref = attempt.raw_refs[0]
        exchange = expected.pop(ref.sha256)
        _assert_raw(run, ref, exchange.raw)
        context = attempt.context
        assert context.run_id == replay.run_id
        assert context.source_hash == compute_source_hash(git_diff=DIFF)
        assert context.round_index == (7 if exchange.number == 1 else 2)
        assert context.pass_name == exchange.lane and context.purpose == "review"
        assert context.group_id is context.group_diff_sha256 is context.parent_logical_id is None
        assert attempt.cause == "initial" and attempt.parent_id is None
        assert attempt.outcome == "completed" and attempt.dispatch_state == "entered_api_send"
        start = starts[attempt.attempt_id]
        assert start["request_digest"] == hashlib.sha256(REQUEST_PREFIX + exchange.request).hexdigest()
        assert start["request_bytes"] == len(exchange.request)
    assert not expected
    assert replay.summary["observed_api_send_count"] == 9
    # Aggregated consumer usage does not invent trustworthy wire-frame credit.
    assert replay.summary["usage"]["input_tokens"]["total"] is None
    output = capsys.readouterr()
    assert PRIVATE not in output.err + output.out


def test_native_machine_partial_falsifier_stays_incomplete(disk_root, monkeypatch, capsys):
    transport = _Transport(monkeypatch, partial_falsifier=True)
    backend, call = _provider(monkeypatch)
    review = OrdinaryReviewAudit(source=DIFF, environ={"XDG_STATE_HOME": str(disk_root)})
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=RealFalsifier(backend=backend, diff_text=DIFF),
        autofixer=StubAutoFixer(),
        revert_fn=lambda _: None,
        resolved_review=_resolved(),
        source_hash=compute_source_hash(git_diff=DIFF),
        baseline_spec_repr="fixture",
        cwd=disk_root.parent,
        registry={},
        l1_provider=call,
        invocation_audit=review,
    )
    try:
        machine._state = State(round=6)
        findings, _ = machine._run_l1_phase()
        assert len(findings) == 1 and findings[0].source == "L1"
        assert findings[0].disposition is Disposition.DISMISSED
        recorder = _native_recorder(review, disk_root)
        assert len(recorder.scoped_snapshot().attempts) == 4
        assert review._host.failed
        # Default-mode recording failure cannot stop later provider work or
        # silently open a replacement recorder/source epoch on a later round.
        machine._state.round = 9
        again, _ = machine._run_l1_phase()
        assert len(again) == 1 and again[0].disposition is Disposition.DISMISSED
        assert _native_recorder(review, disk_root) is recorder
        assert len(recorder.scoped_snapshot().attempts) == 4
    finally:
        review.finalize()

    replay, run, rows = _reopen(review, recorder, disk_root)
    assert replay.state == "incomplete" and not replay.audit_complete
    assert not review._host.safe_summary().audit_complete
    assert transport.counts == dict.fromkeys((*PASSES, "falsify"), 2)
    assert len(replay.attempts) == 4
    assert len({attempt.logical_id for attempt in replay.attempts}) == 4
    assert replay.summary["observed_api_send_count"] == 4
    assert {attempt.context.round_index for attempt in replay.attempts} == {6}
    assert len({attempt.context.snapshot_id for attempt in replay.attempts}) == 1
    falsifiers = [attempt for attempt in replay.attempts if attempt.context.purpose == "falsify"]
    assert len(falsifiers) == 1
    failed = falsifiers[0]
    assert failed.context.pass_name is None and failed.context.group_id is None
    assert len(failed.raw_refs) == 1
    first = {item.lane: item for item in transport.exchanges if item.number == 1}
    starts = {row["attempt_id"]: row for row in rows if row["event"] == "start"}
    for attempt in replay.attempts:
        lane = attempt.context.pass_name or "falsify"
        assert attempt.context.source_hash == compute_source_hash(git_diff=DIFF)
        assert attempt.context.run_id == replay.run_id
        assert attempt.context.purpose == ("falsify" if lane == "falsify" else "review")
        assert len(attempt.raw_refs) == 1
        _assert_raw(run, attempt.raw_refs[0], first[lane].raw, partial=lane == "falsify")
        start = starts[attempt.attempt_id]
        assert (
            start["request_digest"] == hashlib.sha256(REQUEST_PREFIX + first[lane].request).hexdigest()
        )
        assert start["request_bytes"] == len(first[lane].request)
    terminal = [row for row in rows if row["event"] == "run_final"]
    assert len(terminal) == 1 and terminal[0]["audit_complete"] is False
    assert terminal[0]["outcome"] == "incomplete"
    output = capsys.readouterr()
    assert output.err.count("invocation audit unavailable; recording is incomplete") == 1
    assert PRIVATE not in output.err + output.out


@pytest.mark.parametrize("contended", [False, True], ids=["healthy", "post-adopt-contention"])
def test_native_parallel_fanout_preserves_publication_boundary(
    disk_root, monkeypatch, capsys, contended
):
    """Real three-pass fanout; only HTTP and post-publication lock timing are controlled.

    A durable ADOPT is not owner-visible credit when the bounded owner lock
    cannot be reacquired. This does not diagnose an unrelated CI empty-raw run.
    """
    # Override this module's serial fixture, not the production lock budget.
    monkeypatch.delenv("FORGE_L1_SERIAL", raising=False)
    transport = _Transport(monkeypatch)
    barrier = threading.Barrier(3)
    real_open = transport.open
    threads = set()

    def open_response(request, *, timeout):
        response = real_open(request, timeout=timeout)
        with transport.lock:
            threads.add(threading.get_ident())
        barrier.wait(5)
        return response

    monkeypatch.setattr(transport, "open", open_response)
    _, call = _provider(monkeypatch)
    review = OrdinaryReviewAudit(source=DIFF, environ={"XDG_STATE_HOME": str(disk_root)})
    release, locked = threading.Event(), threading.Event()
    holders, released, evidence, refusals = [], [], [], []
    real_publish, real_adopt = _Publication.audit_io, OwnerCaptureStage.adopt

    def publish(command, phase, payload):
        ack = real_publish(command, phase, payload)
        if contended and phase is AuditPhase.ADOPT and not evidence:
            stage = command.stage
            assert ack.admitted and ack.ref is not None
            run = stage._recorder.root / stage._handle.identity.run_id
            raw = (run / ("raw-" + ack.ref.artifact_id)).read_bytes()
            _assert_raw(run, ack.ref, raw)
            evidence.append((stage, ack.ref, raw))

            def hold():
                if not stage._owner._mutex.acquire(timeout=2):
                    return
                try:
                    locked.set()
                    # Watchdog only. Failure must reach the test thread, not be
                    # reduced to a background-thread assertion warning.
                    released.append(release.wait(5))
                finally:
                    stage._owner._mutex.release()

            holder = threading.Thread(target=hold)
            holders.append(holder)
            holder.start()
            assert locked.wait(2)
        return ack

    def adopt(stage, **kwargs):
        try:
            return real_adopt(stage, **kwargs)
        except OwnerStageUnavailable as error:
            if evidence and stage is evidence[0][0]:
                record = stage._owner._leases[id(stage._lease)]
                refusals.append(
                    (str(error), list(record.refs), dict(record.captures), stage._owner._refused)
                )
            raise
        finally:
            if evidence and stage is evidence[0][0]:
                release.set()

    monkeypatch.setattr(_Publication, "audit_io", publish)
    monkeypatch.setattr(OwnerCaptureStage, "adopt", adopt)
    try:
        findings, excerpts, usage, _ = call.call_with_audit(review.round(1))
        # Default warning mode preserves the provider consumer and fixed fold.
        assert [item.description for item in findings] == [
            f"[{name}] {name} candidate" for name in PASSES
        ]
        assert [item["pass_name"] for item in excerpts] == list(PASSES)
        assert usage == Usage(6, 9)
        assert call.reviewer_responses == dict.fromkeys(PASSES, 1)
        recorder = _native_recorder(review, disk_root)
    finally:
        release.set()
        for holder in holders:
            holder.join(3)
            assert not holder.is_alive()
        review.finalize()

    assert len(threads) == 3
    assert transport.counts == dict.fromkeys(PASSES, 1)
    assert len(transport.exchanges) == 3
    live = recorder.scoped_snapshot()
    replay = invocation_audit.AttemptRecorder.reopen_scoped(recorder.root, live.run_id)
    run = recorder.root / replay.run_id
    refs = [ref for attempt in replay.attempts for ref in attempt.raw_refs]
    expected = {hashlib.sha256(item.raw).hexdigest(): item.raw for item in transport.exchanges}
    assert len(expected) == 3
    for ref in refs:
        _assert_raw(run, ref, expected[ref.sha256])
    if contended:
        assert len(evidence) == 1 and released == [True]
        assert refusals == [("owner stage lock unavailable", [], {}, True)]
        stage, ref, raw = evidence[0]
        assert stage._adopted is None
        assert refs.count(ref) == 1
        assert (run / ("raw-" + ref.artifact_id)).read_bytes() == raw
        assert not review._host.safe_summary().audit_complete
        assert replay.state in {"active", "incomplete"}
        # An active prefix can have audit_complete=True: it describes the
        # retained prefix, not a successful host return or finalized run.
        if replay.state == "active":
            assert not (run / "final").exists()
        else:
            assert not replay.audit_complete
    else:
        assert not evidence and not holders and not refusals
        assert len(refs) == 3 and len({ref.sha256 for ref in refs}) == 3
        assert replay == live and replay.audit_complete and replay.state == "completed"
        assert review._host.safe_summary().audit_complete
        assert (run / "final").is_file()
    output = capsys.readouterr()
    assert PRIVATE not in output.err + output.out
    assert output.err.count("invocation audit unavailable; recording is incomplete") == int(contended)
