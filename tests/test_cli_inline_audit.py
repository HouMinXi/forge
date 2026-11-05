"""First real inline consumer, with controlled providers and storage only.

No native filesystem qualification or external provider is exercised.
"""

from argparse import Namespace
from types import SimpleNamespace
import hashlib

from code_forge import cli, backend as backend_module, llm_invoke as invoke
from code_forge.errors import CliError
from code_forge.invocation_host import HostAuditSession
from code_forge.llm_invoke import AuditPersistenceError
from code_forge.state import Verdict


import pytest

from code_forge import canary_gen


DIFF = "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


def test_modified_source_callback_precedes_review(monkeypatch):
    events = []
    modified = DIFF + "PRIVATE injected exact source\n"
    monkeypatch.setattr(canary_gen, "generate_canaries", lambda *a, **k: [])
    monkeypatch.setattr(canary_gen, "inject_canaries_into_diff", lambda *a: (modified, []))

    def dispatch(source, **kwargs):
        assert events == [("source", modified)]
        events.append(("review", source))
        raise ValueError("ordinary provider failure")

    monkeypatch.setattr(canary_gen, "dispatch_canary_review", dispatch)
    canary_gen.run_inline_canary(
        DIFF,
        review_provider=lambda _: "",
        source_lookup=lambda _: None,
        source_callback=lambda source: events.append(("source", source)),
    )
    assert events == [("source", modified), ("review", modified)]


def test_source_callback_is_not_called_when_canary_skips():
    events = []
    canary_gen.run_inline_canary(
        "not Python",
        review_provider=lambda _: pytest.fail("unexpected review"),
        source_lookup=lambda _: None,
        source_callback=events.append,
    )
    assert events == []


class FakeHost:
    def __init__(self, **kwargs):
        self.context, self.manifest = kwargs["context"], kwargs["manifest"]
        self.strict = kwargs["strict"]
        self.failed = False
        self.limits = SimpleNamespace(control_timeout_s=0.1)
        self.sends = 0
        self.events = []
        self.finalized = []

    def scope(self, **kwargs):
        self.events.append(("scope", self.manifest, kwargs))
        return self

    def bind_source(self, context, *, manifest):
        self.events.append(("bind", manifest))
        self.context, self.manifest = context, manifest

    def advance_source(self, context, *, manifest, deadline):
        self.events.append(("advance", manifest))
        self.context, self.manifest = context, manifest
        return True

    def safe_summary(self):
        return SimpleNamespace(admitted_sends=self.sends)

    def finalize(self, **kwargs):
        self.finalized.append(kwargs)


@pytest.fixture
def controlled(monkeypatch, tmp_path):
    hosts, calls = [], []
    backend = SimpleNamespace(type="api", format="openai")
    monkeypatch.setattr(backend_module, "resolve_backend", lambda *a, **k: backend)
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=DIFF)
    )

    def create(**kwargs):
        host = FakeHost(**kwargs)
        hosts.append(host)
        return host

    monkeypatch.setattr(HostAuditSession, "from_user_state", create, raising=False)

    def provider(prompt, **kwargs):
        calls.append((prompt, kwargs))
        if "host_audit" in kwargs:
            kwargs["host_audit"].sends += 1
        return SimpleNamespace(content={"mutations": []} if len(calls) == 1 else {"findings": []})

    monkeypatch.setattr(invoke, "llm_invoke", provider)

    def run(**kwargs):
        args = Namespace(canary=True, quiet=True, backend=None, audit_strict=False)
        vars(args).update(kwargs)
        return cli._dispatch_inline_canary("inline", args, {}, [], {}, tmp_path)

    return run, hosts, calls, backend


def test_cli_actual_canary_binds_exact_sources_and_finalizes(controlled, monkeypatch):
    run, hosts, calls, _ = controlled
    sources = []
    original = canary_gen.inject_canaries_into_diff

    def inject(*args):
        result = original(*args)
        sources.append(result[0])
        return result

    monkeypatch.setattr(canary_gen, "inject_canaries_into_diff", inject)
    assert run() is Verdict.UNRELIABLE
    assert len(calls) == 2 and len(hosts) == 1
    host = hosts[0]
    assert [event[0] for event in host.events] == ["scope", "advance", "scope"]
    initial = host.events[0][1]
    modified = host.events[1][1]
    assert initial["exact_diff_sha256"] == hashlib.sha256(DIFF.encode()).hexdigest()
    assert initial["diff_bytes"] == len(DIFF.encode())
    assert modified["exact_diff_sha256"] != initial["exact_diff_sha256"]
    assert modified["exact_diff_sha256"] == hashlib.sha256(sources[0].encode()).hexdigest()
    assert modified["diff_bytes"] == len(sources[0].encode())
    assert modified["snapshot_id"] != initial["snapshot_id"]
    assert modified["run_id"] != initial["run_id"]
    assert host.finalized == [dict(outcome="completed", primary=None, maintain=False)]


@pytest.mark.parametrize("which", ["generation", "review"])
def test_typed_error_crosses_all_fallbacks_unchanged(controlled, monkeypatch, capsys, which):
    run, hosts, calls, _ = controlled
    error = AuditPersistenceError()
    cause = OSError("PRIVATE storage path and secret")
    error.__cause__ = cause
    original = invoke.llm_invoke
    attempted = []

    def provider(prompt, **kwargs):
        attempted.append(prompt)
        if which == "generation" or calls:
            raise error
        return original(prompt, **kwargs)

    monkeypatch.setattr(invoke, "llm_invoke", provider)
    with pytest.raises(AuditPersistenceError) as caught:
        run(audit_strict=True)
    assert caught.value is error and caught.value.__cause__ is cause
    assert hosts[0].finalized[0]["primary"] is error
    output = capsys.readouterr().err
    assert "falling back" not in output and "PRIVATE" not in output
    assert len(calls) == (0 if which == "generation" else 1)
    assert len(attempted) == (1 if which == "generation" else 2)


@pytest.mark.parametrize("error_type", [MemoryError, KeyboardInterrupt, SystemExit])
def test_fatal_identity_and_finalization(controlled, monkeypatch, error_type):
    run, hosts, _, _ = controlled
    error = error_type("fatal")

    def provider(*args, **kwargs):
        raise error

    monkeypatch.setattr(invoke, "llm_invoke", provider)
    with pytest.raises(error_type) as caught:
        run()
    assert caught.value is error
    assert hosts[0].finalized[0]["primary"] is error


def test_skipped_diff_stays_lazy(controlled, monkeypatch):
    run, hosts, calls, _ = controlled
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=""))
    assert run() is Verdict.DELEGATED
    assert hosts == calls == []


def test_git_failure_never_binds_known_empty_source(controlled, monkeypatch):
    run, hosts, calls, _ = controlled
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=DIFF)
    )

    def pipeline(**kwargs):
        kwargs["canary_provider"]("forced test send")
        return Verdict.DELEGATED, []

    monkeypatch.setattr(canary_gen, "run_inline_canary", pipeline)
    run()
    assert len(calls) == 1
    assert hosts[0].manifest is None
    assert hosts[0].context.source_hash is hosts[0].context.snapshot_id is None


def test_generation_before_send_failure_binds_without_advancing(controlled, monkeypatch):
    run, hosts, calls, _ = controlled
    original = invoke.llm_invoke

    def provider(prompt, **kwargs):
        if not hosts[0].events[-1][2]["purpose"] == "canary_review":
            raise ValueError("generation failed before transport")
        return original(prompt, **kwargs)

    monkeypatch.setattr(invoke, "llm_invoke", provider)
    run()
    assert [event[0] for event in hosts[0].events] == ["scope", "bind", "scope"]
    assert len(calls) == 1


def test_template_only_pipeline_first_binds_modified_source(controlled, monkeypatch):
    run, hosts, calls, _ = controlled
    monkeypatch.setattr(
        canary_gen,
        "generate_canaries",
        lambda *a, **k: [
            dict(file="canary.py", line=1, code="x = None", original="x = 1", description="bug")
        ],
    )
    run()
    assert len(hosts) == len(calls) == 1
    assert [event[0] for event in hosts[0].events] == ["scope"]
    assert hosts[0].manifest["exact_diff_sha256"] != hashlib.sha256(DIFF.encode()).hexdigest()


def test_unsupported_default_warns_once_without_changing_provider_calls(controlled, capsys):
    run, hosts, calls, backend = controlled
    backend.format = "anthropic"
    assert run() is Verdict.UNRELIABLE
    assert len(calls) == 2 and hosts == []
    assert all("host_audit" not in kwargs for _, kwargs in calls)
    output = capsys.readouterr().err
    assert output.count("invocation audit unavailable for this backend") == 1
    assert "PRIVATE" not in output and "https://" not in output


def test_unsupported_strict_refuses_before_provider(controlled):
    run, hosts, calls, backend = controlled
    backend.format = "anthropic"
    with pytest.raises(CliError, match="inline canary OpenAI API"):
        run(audit_strict=True)
    assert hosts == calls == []


@pytest.mark.parametrize("outlet", ["subprocess", "subagent", "cli", "sampling"])
def test_other_outlets_cannot_silently_ignore_explicit_strict(outlet):
    with pytest.raises(CliError, match="only the inline canary"):
        cli._dispatch_inline_canary(outlet, Namespace(audit_strict=True), {}, [], {}, ".")


def test_strict_requires_canary():
    with pytest.raises(CliError, match="requires inline canary"):
        cli._dispatch_inline_canary("inline", Namespace(audit_strict=True), {}, [], {}, ".")


def test_parser_strict_default_and_opt_in():
    parser = cli._build_parser()
    assert not parser.parse_args(["review"]).audit_strict
    assert parser.parse_args(["review", "--audit-strict"]).audit_strict


def test_cli_terminal_strict_error_does_not_print_private_cause(monkeypatch, capsys):
    error = AuditPersistenceError()
    error.__cause__ = OSError("PRIVATE /private/path Authorization: secret raw reasoning")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "_run", fail)
    monkeypatch.setattr(cli, "_review_terminal_exit", lambda value: value)
    monkeypatch.setattr(cli.sys, "argv", ["code-forge", "review", "--audit-strict"])
    assert cli.main() == cli.EXIT_FAIL
    output = capsys.readouterr().err
    assert "strict review blocked" in output
    assert all(
        value not in output
        for value in ("PRIVATE", "/private/path", "Authorization", "reasoning", "Traceback")
    )


def test_existing_non_audit_cli_error_still_degrades(controlled, monkeypatch, capsys):
    run, hosts, calls, _ = controlled

    def pipeline(**kwargs):
        raise CliError("ordinary legacy canary error")

    monkeypatch.setattr(canary_gen, "run_inline_canary", pipeline)
    assert run() is Verdict.DELEGATED
    assert hosts == calls == []
    assert "falling back to DELEGATED" in capsys.readouterr().err


def test_identical_source_does_not_advance_or_mint_new_scope_identity(controlled):
    _, hosts, _, backend = controlled
    audit = cli._InlineCanaryAudit(backend, source=DIFF, strict=False, environ={})
    audit.scope("canary_generation")
    hosts[0].sends = 1
    audit.bind_review_source(DIFF)
    assert [event[0] for event in hosts[0].events] == ["scope"]


def test_disabled_host_does_not_rebind_after_generation_failure(controlled):
    _, hosts, _, backend = controlled
    audit = cli._InlineCanaryAudit(backend, source=DIFF, strict=False, environ={})
    audit.scope("canary_generation")
    hosts[0].failed = True
    audit.bind_review_source(DIFF + "changed")
    assert [event[0] for event in hosts[0].events] == ["scope"]


def test_other_canary_callers_keep_existing_dispatch_diagnostic(monkeypatch, capsys):
    monkeypatch.setattr(canary_gen, "generate_canaries", lambda *a, **k: [])
    monkeypatch.setattr(canary_gen, "inject_canaries_into_diff", lambda *a: (DIFF, []))

    def provider(*args, **kwargs):
        raise ValueError("legacy diagnostic marker")

    result, _ = canary_gen.run_inline_canary(
        DIFF, review_provider=provider, source_lookup=lambda _: None
    )
    assert result is Verdict.DELEGATED
    assert "legacy diagnostic marker" in capsys.readouterr().err
