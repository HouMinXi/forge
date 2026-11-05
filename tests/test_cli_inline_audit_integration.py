"""Actual CLI inline -> sender -> host/owner, with mock transport/storage.

These tests do not qualify native filesystem durability or contact a provider.
"""

from argparse import Namespace
import json
from types import SimpleNamespace

import pytest

from code_forge import backend as backend_module, canary_gen, cli, invocation_host
from code_forge.llm_invoke import AuditPersistenceError
from code_forge.state import Verdict
from tests import test_invocation_host as host_tests, test_invocation_send_audit as send_tests

MockRecorder = host_tests.MockRecorder
Response, scripted = send_tests.Response, send_tests.scripted
host_setup = host_tests.setup
api_backend = send_tests.backend


DIFF = "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


def wire(content):
    return Response(
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(content),
                            "reasoning_content": "PRIVATE reasoning",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        ).encode()
    )


@pytest.fixture
def real_inline(monkeypatch, tmp_path, host_setup, api_backend):
    make, _ = host_setup
    hosts = []
    monkeypatch.setattr(backend_module, "resolve_backend", lambda *a, **k: api_backend)
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=DIFF)
    )
    monkeypatch.setattr(invocation_host, "maintain_audit_root", lambda *a, **k: None)

    def create(*, context, manifest, strict, environ):
        host, _ = make(
            strict=strict,
            warn=lambda _: print(
                "code-forge: invocation audit unavailable; recording is incomplete", file=cli.sys.stderr
            ),
        )
        host.bind_source(context, manifest=manifest)
        hosts.append(host)
        return host

    monkeypatch.setattr(invocation_host.HostAuditSession, "from_user_state", create, raising=False)

    def run(strict=False, quiet=True):
        return cli._dispatch_inline_canary(
            "inline",
            Namespace(canary=True, audit_strict=strict, backend=None, quiet=quiet),
            {},
            [],
            {},
            tmp_path,
        )

    return run, hosts


def test_real_inline_captures_raw_and_separates_source_epochs(real_inline, monkeypatch, capsys):
    run, hosts = real_inline
    transport = scripted(monkeypatch, [wire({"mutations": []}), wire({"findings": []})])
    assert run(strict=True) is Verdict.UNRELIABLE
    assert len(transport.requests) == 2
    assert len(MockRecorder.instances) == 2
    first, second = MockRecorder.instances
    assert first.context.source_hash != second.context.source_hash
    assert first.manifest["exact_diff_sha256"] != second.manifest["exact_diff_sha256"]
    assert first.started[0]["context"].purpose == "canary_generation"
    assert second.started[0]["context"].purpose == "canary_review"
    raw = b"".join(
        bytes(value) for recorder in MockRecorder.instances for value in recorder.stages.values()
    )
    assert raw.count(b"PRIVATE reasoning") == 2
    assert hosts[0].safe_summary().audit_complete
    assert "PRIVATE reasoning" not in capsys.readouterr().err


@pytest.mark.parametrize("strict", [False, True])
def test_real_storage_failure_default_continues_strict_blocks(real_inline, monkeypatch, capsys, strict):
    run, hosts = real_inline
    monkeypatch.setattr(MockRecorder, "fail", "open")
    monkeypatch.setattr(MockRecorder, "error", OSError("PRIVATE path secret raw"))
    transport = scripted(monkeypatch, [wire({"mutations": []}), wire({"findings": []})])
    if strict:
        with pytest.raises(AuditPersistenceError):
            run(strict=True)
        assert transport.requests == []
    else:
        assert run() is Verdict.UNRELIABLE
        assert len(transport.requests) == 2
    assert hosts[0].safe_summary().audit_complete is False
    output = capsys.readouterr().err
    assert output.count("invocation audit unavailable; recording is incomplete") == 1
    assert "PRIVATE" not in output and "secret" not in output


def test_real_template_only_first_send_uses_modified_source(real_inline, monkeypatch):
    run, hosts = real_inline
    monkeypatch.setattr(
        canary_gen,
        "generate_canaries",
        lambda *a, **k: [
            dict(file="canary.py", line=1, code="x = None", original="x = 1", description="bug")
        ],
    )
    transport = scripted(monkeypatch, [wire({"findings": []})])
    assert run(strict=True) is Verdict.UNRELIABLE
    assert len(transport.requests) == len(MockRecorder.instances) == 1
    recorder = MockRecorder.instances[0]
    assert recorder.context.source_hash != cli.compute_source_hash(git_diff=DIFF)
    assert recorder.started[0]["context"].purpose == "canary_review"
    assert hosts[0].safe_summary().audit_complete


def test_actual_run_reaches_inline_and_finalizes_early_return(
    real_inline, monkeypatch, tmp_path, api_backend
):
    _, hosts = real_inline
    from code_forge import user_config, workspace

    monkeypatch.setattr(cli, "is_git_repo", lambda _: False)
    monkeypatch.setattr(cli, "_load_gate_backends", lambda _: ([api_backend], {}))
    monkeypatch.setattr(cli, "_merge_user_into", lambda configs, _: configs)
    monkeypatch.setattr(cli, "_load_trusted_review_lenses", lambda *a: False)
    monkeypatch.setattr(user_config, "load_user_retry", lambda: {})
    monkeypatch.setattr(workspace, "resolve_workspace", lambda *a: tmp_path)
    monkeypatch.setattr(
        canary_gen,
        "generate_canaries",
        lambda *a, **k: [
            dict(file="canary.py", line=1, code="x = None", original="x = 1", description="bug")
        ],
    )
    transport = scripted(monkeypatch, [wire({"findings": []})])
    args = cli._build_parser().parse_args(
        ["review", "--outlet", "inline", "--canary", "--audit-strict", "--quiet"]
    )
    assert cli._run(args, {}, tmp_path) is Verdict.UNRELIABLE
    assert len(transport.requests) == 1
    assert hosts[0].safe_summary().state == "completed"


def test_real_strict_epoch_failure_cannot_fall_back(real_inline, monkeypatch, capsys):
    run, hosts = real_inline
    monkeypatch.setattr(MockRecorder, "fail", "finalize")
    monkeypatch.setattr(MockRecorder, "error", OSError("PRIVATE epoch finalization"))
    transport = scripted(monkeypatch, [wire({"mutations": []}), wire({"findings": []})])
    with pytest.raises(AuditPersistenceError):
        run(strict=True)
    assert len(transport.requests) == 1
    assert hosts[0].safe_summary().audit_complete is False
    output = capsys.readouterr().err
    assert "PRIVATE" not in output and "falling back" not in output


def test_real_cli_factory_selects_managed_roots_and_maintains_before_send(
    monkeypatch, tmp_path, host_setup, api_backend
):
    _, supervisor = host_setup
    state_root = tmp_path / "state"
    v2 = state_root / "code-forge" / "invocations-v2"
    v1 = state_root / "code-forge" / "invocations-v1"
    v2.mkdir(parents=True)
    v1.mkdir()
    hosts, maintained = [], []
    original_factory = invocation_host.HostAuditSession.from_user_state

    def create(**kwargs):
        host = original_factory(
            **kwargs,
            supervisor=supervisor,
            limits=invocation_host.HostAuditLimits(
                batch_bytes=512, capture_buffer_bytes=4096, max_delay_s=0
            ),
        )
        hosts.append(host)
        return host

    def maintain(root, **kwargs):
        assert not MockRecorder.instances
        maintained.append(root)
        return SimpleNamespace(admitted=True, publication_uncertain=False)

    monkeypatch.setattr(invocation_host.HostAuditSession, "from_user_state", create)
    monkeypatch.setattr(invocation_host, "maintain_audit_root", maintain)
    monkeypatch.setattr(backend_module, "resolve_backend", lambda *a, **k: api_backend)
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=DIFF)
    )
    transport = scripted(monkeypatch, [wire({"mutations": []}), wire({"findings": []})])
    args = Namespace(canary=True, audit_strict=True, backend=None, quiet=True)
    try:
        assert (
            cli._dispatch_inline_canary(
                "inline", args, {"XDG_STATE_HOME": str(state_root)}, [], {}, tmp_path
            )
            is Verdict.UNRELIABLE
        )
        assert len(transport.requests) == 2
        assert maintained == [v2, v1]
        assert all(recorder.root == v2 for recorder in MockRecorder.instances)
        assert hosts[0].safe_summary().audit_complete
    finally:
        for host in hosts:
            host.close(primary=RuntimeError("test cleanup"))


@pytest.mark.parametrize("strict", [False, True])
def test_swallowed_pipeline_error_cannot_suppress_strict_finalization(
    real_inline, monkeypatch, capsys, strict
):
    run, hosts = real_inline
    monkeypatch.setattr(MockRecorder, "fail", "finalize")
    monkeypatch.setattr(MockRecorder, "error", OSError("PRIVATE finalization secret"))
    transport = scripted(monkeypatch, [wire({"mutations": 1})])
    if strict:
        with pytest.raises(AuditPersistenceError):
            run(strict=True)
    else:
        assert run() is Verdict.DELEGATED
    assert len(transport.requests) == 1
    assert hosts[0].safe_summary().audit_complete is False
    output = capsys.readouterr().err
    assert "PRIVATE" not in output
    assert output.count("invocation audit unavailable; recording is incomplete") == 1


@pytest.mark.parametrize("stage", ["generation", "review"])
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("quiet", [False, True])
def test_real_http_body_is_private_in_inline_fallbacks(
    real_inline, monkeypatch, capsys, stage, strict, quiet
):
    run, hosts = real_inline
    secret = b"PRIVATE_REASONING_SENTINEL bearer TOKEN_SENTINEL /private/secret/path"
    error = send_tests.http_error(status=400, raw=secret)
    responses = (
        [error, wire({"findings": []})] if stage == "generation" else [wire({"mutations": []}), error]
    )
    transport = scripted(monkeypatch, responses)
    expected = Verdict.UNRELIABLE if stage == "generation" else Verdict.DELEGATED
    assert run(strict=strict, quiet=quiet) is expected
    assert len(transport.requests) == 2
    captured = b"".join(
        bytes(value) for recorder in MockRecorder.instances for value in recorder.stages.values()
    )
    assert secret in captured
    output = capsys.readouterr().err
    assert all(
        value not in output
        for value in ("PRIVATE_REASONING_SENTINEL", "TOKEN_SENTINEL", "/private/secret/path")
    )
    assert f"canary {stage} failed" in output
    assert hosts[0].safe_summary().audit_complete


def test_active_typed_primary_survives_real_finalization_failure(real_inline, monkeypatch, capsys):
    from code_forge import llm_invoke

    run, hosts = real_inline
    original = llm_invoke.llm_invoke
    error = AuditPersistenceError()
    cause = OSError("PRIVATE active cause token")
    error.__cause__ = cause
    calls = []

    def provider(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            MockRecorder.fail = "finalize"
            MockRecorder.error = OSError("PRIVATE secondary finalization")
            raise error
        return original(*args, **kwargs)

    monkeypatch.setattr(llm_invoke, "llm_invoke", provider)
    transport = scripted(monkeypatch, [wire({"mutations": []})])
    with pytest.raises(AuditPersistenceError) as caught:
        run(strict=True)
    assert caught.value is error and caught.value.__cause__ is cause
    assert len(transport.requests) == 1
    assert hosts[0].safe_summary().audit_complete is False
    assert "PRIVATE" not in capsys.readouterr().err
