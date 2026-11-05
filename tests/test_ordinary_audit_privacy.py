"""Consumer/terminal projection controls; no native storage or provider claim."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_forge import cli, factories, llm_invoke as invoke
from code_forge.baseline import ResolvedReview
from code_forge.errors import CliError
from code_forge.invocation_review import OrdinaryReviewAudit
from code_forge.state import State, save_state


SECRET = "PRIVATE_DIAGNOSTIC_SENTINEL"
DIFF = "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


class _Scopes:
    def scope(self, **_kwargs):
        return SimpleNamespace(strict_failed=False)


@pytest.mark.parametrize("audited", [False, True], ids=["legacy", "selected"])
@pytest.mark.parametrize(
    "kind", ["unknown", "typed", "runtime", "schema"], ids=["unknown", "typed", "runtime", "schema"]
)
def test_actual_factory_projects_errors_without_changing_private_objects(
    monkeypatch, tmp_path, capsys, audited, kind
):
    errors = []

    def fail(_prompt, **_kwargs):
        if kind == "schema":
            return invoke.LLMResult(content={"findings": SECRET}, usage=invoke.Usage())
        if kind == "runtime":
            error = RuntimeError(SECRET)
        else:
            error = invoke.LLMInvokeError(
                SECRET,
                kind=SECRET if kind == "unknown" else "bad_body",
                stderr=SECRET,
                exit_code=503,
                duration_s=0.2,
                usage=invoke.Usage(3, 2, 0),
                observed_usage=invoke.Usage(3, 2, 0),
            )
        error.__cause__ = LookupError(SECRET + " cause")
        errors.append(error)
        raise error

    monkeypatch.setattr(invoke, "llm_invoke", fail)
    source = ResolvedReview(
        source_files=[Path("sample.py")], baseline_content=None, git_diff=DIFF, mode_hint="git"
    )
    backend = SimpleNamespace(type="api", format="openai", name="fixture")
    call = factories.build_l1_provider("real", source, backend=backend)
    audit = OrdinaryReviewAudit(source=DIFF, environ={})
    monkeypatch.setattr(audit, "_open_host", _Scopes)
    result = call.call_with_audit(audit.round(4)) if audited else call()
    findings = result[0]
    assert len(findings) == 3
    assert all(finding.source == "INFRA" for finding in findings)
    state_path = tmp_path / "state.json"
    save_state(State(findings=findings), state_path)
    public = capsys.readouterr().err + state_path.read_text()
    if kind != "schema":
        assert (SECRET in public) is not audited
        assert len(errors) == 3
        for error in errors:
            assert SECRET in str(error) and SECRET in str(error.__cause__)
        if kind != "runtime":
            assert result[2].input_tokens == 9 and result[2].output_tokens == 6
            for finding in findings:
                metadata = finding.provider_failure
                assert metadata["exit_code"] == 503 and metadata["duration_s"] == 0.2
                assert metadata["stderr"] == (None if audited else SECRET)
                assert metadata["kind"] == (
                    None if audited and kind == "unknown" else "bad_body" if kind == "typed" else SECRET
                )
    elif audited:
        assert SECRET not in public
        # Existing private rejection payloads remain available to their writer.
        assert SECRET in json.dumps(call.attempted_excerpts + call.raw_observations)


def _terminal_error(kind):
    if kind == "provider":
        return invoke.LLMInvokeError(SECRET, stderr=SECRET)
    if kind == "cli":
        return CliError(SECRET, remediation=SECRET)
    if kind == "audit":
        return invoke.AuditPersistenceError()
    if kind == "exit":
        return SystemExit(SECRET)
    if kind == "cancel":
        return asyncio.CancelledError(SECRET)
    if kind == "interrupt":
        return KeyboardInterrupt(SECRET)
    return RuntimeError(SECRET)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("provider", 2),
        ("cli", 2),
        ("audit", 1),
        ("exit", 2),
        ("cancel", 1),
        ("interrupt", 130),
        ("runtime", 1),
    ],
    ids=["provider", "cli", "audit", "exit", "cancel", "interrupt", "runtime"],
)
def test_main_private_terminal_keeps_exit_category_without_rendering_chain(
    monkeypatch, capsys, kind, expected
):
    error = _terminal_error(kind)
    cause = LookupError(SECRET + " cause")
    error.__cause__ = cause
    error.__context__ = ValueError(SECRET + " context")
    original_context = error.__context__

    def fail(_args, *, env, cwd, audit_diagnostics):
        audit_diagnostics.note(error)
        raise error

    monkeypatch.setattr(cli, "_run", fail)
    monkeypatch.setattr(cli.sys, "argv", ["code-forge", "review"])
    if kind == "interrupt":
        with pytest.raises(SystemExit) as caught:
            cli.main()
        assert caught.value.code == expected
    else:
        assert cli.main() == expected
    output = capsys.readouterr()
    assert SECRET not in output.err + output.out
    assert "Traceback" not in output.err
    assert error.__cause__ is cause and error.__context__ is original_context


def test_unrelated_terminal_error_does_not_inherit_private_marker(monkeypatch, capsys):
    private = RuntimeError(SECRET)
    unrelated = RuntimeError("ordinary unrelated failure")

    def fail(_args, *, env, cwd, audit_diagnostics):
        audit_diagnostics.note(private)
        raise unrelated

    monkeypatch.setattr(cli, "_run", fail)
    monkeypatch.setattr(cli.sys, "argv", ["code-forge", "review"])
    assert cli.main() == 1
    output = capsys.readouterr().err
    assert "ordinary unrelated failure" in output and "Traceback" in output
    assert SECRET not in output
