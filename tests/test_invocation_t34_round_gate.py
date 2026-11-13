"""Controlled ordinary LOCAL round death, with native audit and real verifier.

HTTP responses and L0/L2/E2E are synthetic. No power-loss or installed CLI claim.
"""

import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

import pytest

pytest_plugins = ["tests.test_invocation_audit"]


def _raw_fixture():
    from tests import _review_harness as h

    return json.dumps(
        {
            "choices": [{"message": {"content": json.dumps(h._healthy())}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 9},
        }
    ).encode()


def _write(root, prior_pass, mode):
    import io
    import threading

    from code_forge import invocation_send_audit, llm_invoke
    from code_forge.backend import BackendConfig
    from code_forge.factories import build_l1_provider
    from code_forge.invocation_review import OrdinaryReviewAudit
    from code_forge.state import Verdict
    from tests import _review_harness as h

    root.mkdir()
    patch = pytest.MonkeyPatch()
    forbidden_events = []

    def tripwire(event, args):
        if event.startswith(
            ("socket.", "subprocess.Popen", "os.system", "os.fork", "os.posix_spawn", "os.exec")
        ):
            forbidden_events.append(event)
            raise h._ForbiddenExternalOperation("unexpected external operation: " + event)

    # Register before the harness hook so its log cannot hide in generator GC.
    sys.addaudithook(tripwire)
    workspace = h.offline_workspace.__wrapped__(root, patch)
    next(workspace)
    original = llm_invoke._invoke_api
    if prior_pass:
        prior, _ = h._new_machine(root, patch, rounds=3)
        assert prior.run() is Verdict.PASS and h._verify(root).passed
    machine, _ = h._new_machine(root, patch)
    patch.delenv("FORGE_L1_SERIAL", raising=False)
    patch.setenv("T34_SYNTHETIC_KEY", "fixture-not-a-credential")
    backend = BackendConfig(
        name="t34",
        type="api",
        format="openai",
        model="fixture",
        base_url="https://fixture.invalid/v1",
        api_key_env="T34_SYNTHETIC_KEY",
        stream=False,
        timeout_s=30,
    )
    machine.l1_provider = build_l1_provider(
        "real", machine.resolved_review, backend=backend, max_attempts=1, initial_delay_s=0
    )
    review = OrdinaryReviewAudit(source=h.DIFF, environ={"XDG_STATE_HOME": str(root / "audit")})
    machine.invocation_audit = review
    if mode == "omit-state":
        patch.setattr(machine, "_persist_state", lambda: None)
    barrier, release = threading.Barrier(3), threading.Event()
    raw = _raw_fixture()

    class Response(io.BytesIO):
        status = 200
        headers = {}
        read1 = io.BytesIO.read

    class Transport:
        def open(self, request, *, timeout):
            assert request.full_url == "https://fixture.invalid/v1/chat/completions"
            prompt = json.loads(request.data)["messages"][0]["content"]
            role = h._reviewer_role(prompt)
            barrier.wait(10)
            if role != "qodo":
                assert release.wait(30)
            return Response(raw)

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected network")

    patch.setattr(llm_invoke.urllib.request, "urlopen", forbidden)
    patch.setattr(invocation_send_audit.urllib.request, "build_opener", lambda *_: Transport())

    def invoke(prompt, *args, **kwargs):
        result = original(prompt, *args, **kwargs)
        if h._reviewer_role(prompt) == "qodo":
            assert result.usage.input_tokens == 7 and result.usage.output_tokens == 9
            owner = review._host._owner
            recorder = owner._epochs[0].recorder
            snap = recorder.scoped_snapshot()
            qodo = next(a for a in snap.attempts if a.context.pass_name == "qodo")
            assert qodo.outcome == "completed" and qodo.raw_refs
            assert forbidden_events == []
            print(
                json.dumps(
                    {
                        "run_id": snap.run_id,
                        "root": str(recorder.root),
                        "attempt": qodo.attempt_id,
                    }
                ),
                flush=True,
            )
            assert sys.stdin.readline() == "finish\n"
            release.set()
        return result

    patch.setattr(llm_invoke, "_invoke_api", invoke)
    assert machine.run() is (Verdict.PASS if prior_pass else Verdict.ESCALATED)
    review.finalize()
    assert forbidden_events == []
    workspace.close()


def _read(root, identity, prior_pass, mode):
    from code_forge.invocation_audit import AttemptRecorder
    from code_forge.state import Verdict, load_state
    from tests import _review_harness as h

    state = load_state(root / ".code-forge" / "state.json")
    if mode == "omit-state":
        assert prior_pass and state.verdict is Verdict.PASS and state.converged
        assert h._verify(root).passed, "omission must expose stale PASS"
    elif mode == "sigkill":
        assert state.verdict is Verdict.PENDING and not state.converged, "state-oracle"
        assert state.round_history[-1]["clean_credit_action"] == "unavailable"
        assert state.consecutive_clean_rounds == (3 if prior_pass else 0)
        assert not h._verify(root).passed
        with pytest.raises(ValueError, match="PENDING verdict on a LOCAL run"):
            h._report(root)
    else:
        assert state.verdict is (Verdict.PASS if prior_pass else Verdict.ESCALATED)
        assert state.round_history[-1]["clean_credit_action"] == "earned"
        assert h._verify(root).passed is prior_pass
    replay = AttemptRecorder.reopen_scoped(Path(identity["root"]), identity["run_id"])
    qodo = next(a for a in replay.attempts if a.attempt_id == identity["attempt"])
    assert qodo.outcome == "completed"
    assert qodo.context.round_index == (3 if prior_pass else 0)
    from tests.test_ordinary_audit_native import _journal

    rows = _journal(Path(identity["root"]) / identity["run_id"])
    finish = next(r for r in rows if r["event"] == "finish" and r["attempt_id"] == qodo.attempt_id)
    assert finish["error_class"] is None
    ref = qodo.raw_refs[0]
    assert (
        Path(identity["root"]) / identity["run_id"] / ("raw-" + ref.artifact_id)
    ).read_bytes() == _raw_fixture()
    assert len(replay.attempts) == 3
    assert sorted(a.outcome for a in replay.attempts) == (
        ["completed"] * 3 if mode == "finish" else ["completed", "unknown", "unknown"]
    )
    print("verified", flush=True)


@pytest.fixture
def product_root():
    # Same filesystem as the checkout, but outside every Git working tree.
    with tempfile.TemporaryDirectory(
        prefix="forge-t34-round-", dir=Path(__file__).resolve().parents[2]
    ) as directory:
        yield Path(directory) / "product"


@pytest.mark.parametrize(
    "prior_pass,mode",
    [(False, "sigkill"), (True, "sigkill"), (False, "finish"), (True, "finish"), (True, "omit-state")],
)
def test_round_death_cannot_reuse_prior_pass(product_root, prior_pass, mode):
    from tests.test_invocation_t34_process_recovery import _ready_line

    module = "tests.test_invocation_t34_round_gate"
    root = product_root
    child = subprocess.Popen(
        [sys.executable, "-B", "-m", module, "write", str(root), str(int(prior_pass)), mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        identity = json.loads(_ready_line(child.stdout.fileno()))
        if mode != "finish":
            child.kill()
        out, err = child.communicate(b"finish\n" if mode == "finish" else None, timeout=10)
        assert child.returncode == (0 if mode == "finish" else -signal.SIGKILL) and not out, err.decode()
        assert b"Exception ignored" not in err and b"Traceback" not in err
        reader = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                module,
                "read",
                str(root),
                json.dumps(identity),
                str(int(prior_pass)),
                mode,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert reader.returncode == 0, reader.stderr
        assert reader.stdout == "verified\n" and not reader.stderr
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


if __name__ == "__main__":
    if sys.argv[1] == "write":
        _write(Path(sys.argv[2]), bool(int(sys.argv[3])), sys.argv[4])
    else:
        _read(Path(sys.argv[2]), json.loads(sys.argv[3]), bool(int(sys.argv[4])), sys.argv[5])
