"""Real v2 emitter and _run cleanup with isolated acquisition/mock storage.

No installed CLI, external provider or native durability qualification.
"""

import asyncio
import io

import pytest

from code_forge import cli
from code_forge.invocation_review import OrdinaryReviewAudit
from code_forge.state import save_state, Verdict
from tests.test_report_baseline_v2 import fixture_state
from tests import test_ordinary_audit_integration as ordinary_tests

api_backend = ordinary_tests.api_backend
host_setup = ordinary_tests.host_setup
isolated_cli = ordinary_tests.isolated_cli
ordinary_host = ordinary_tests.ordinary_host
MockRecorder = ordinary_tests.MockRecorder
RoutedTransport = ordinary_tests.RoutedTransport

# Capture before the imported isolated fixture substitutes its emitter seam.
REAL_EMITTER = cli._emit_ci_output


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
@pytest.mark.parametrize("primary_type", [OSError, KeyboardInterrupt, asyncio.CancelledError])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_real_v2_write_failure_survives_run_finalization(
    isolated_cli, ordinary_host, monkeypatch, stream_name, primary_type, cleanup_failure
):
    args, env, cwd = isolated_cli
    args.report_version = "v2"
    primary = primary_type("synthetic-write-failure")
    secondary = OSError("synthetic-cleanup-failure")
    marker = RuntimeError("original-cause")
    primary.__cause__ = marker
    events, reviews, finalized, cleanup_calls = [], [], [], []
    transport = RoutedTransport(monkeypatch)

    class Stream(io.StringIO):
        armed = False

        def write(self, text):
            if self.armed:
                events.append((stream_name, text))
                raise primary
            return super().write(text)

    failing = Stream()
    other = io.StringIO()
    monkeypatch.setattr(cli.sys, stream_name, failing)
    monkeypatch.setattr(cli.sys, "stderr" if stream_name == "stdout" else "stdout", other)
    monkeypatch.setattr(cli, "_emit_ci_output", REAL_EMITTER)
    original_finalize = OrdinaryReviewAudit.finalize
    original_recorder_finalize = MockRecorder.finalize

    def observe_finalize(review, entering=None):
        finalized.append((review, entering))
        return original_finalize(review, entering)

    def recorder_finalize(recorder, **kwargs):
        cleanup_calls.append(kwargs)
        if cleanup_failure:
            raise secondary
        return original_recorder_finalize(recorder, **kwargs)

    def pipeline(**kwargs):
        review = kwargs["invocation_audit"]
        reviews.append(review)
        kwargs["l1_provider"].call_with_audit(review.round(0))
        save_state(fixture_state("mutation"), kwargs["state_path"])
        failing.armed = True
        return Verdict.FAIL

    monkeypatch.setattr(OrdinaryReviewAudit, "finalize", observe_finalize)
    monkeypatch.setattr(MockRecorder, "finalize", recorder_finalize)
    monkeypatch.setattr(cli, "_run_hold_loop", pipeline)
    with pytest.raises(primary_type) as caught:
        cli._run(args, env, cwd)
    assert caught.value is primary and primary.__cause__ is marker
    assert finalized == [(reviews[0], primary)]
    assert reviews[0]._closed
    assert len(transport.requests) == 3 and len(cleanup_calls) == 1
    assert events
    if stream_name == "stdout":
        assert '"forge-report-classification/v2"' in events[0][1]
    else:
        assert "report-v2 execution-diagnostics" in events[0][1]
        assert '"forge-report-classification/v2"' in other.getvalue()
    assert "synthetic-cleanup-failure" not in other.getvalue()
