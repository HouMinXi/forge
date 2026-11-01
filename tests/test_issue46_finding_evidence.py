# SPDX-License-Identifier: Apache-2.0
"""Issue #46: a falsifier CONFIRMED without bound finding evidence must not
silently become an ordinary UNCERTAIN that lets CI PASS."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._outside_gate import outside_gate_root
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import Falsifier
from code_forge.llm_invoke import Usage
from code_forge.machine import StateMachine
from code_forge.reviewer_json import REVIEW_JSON_CONTRACT, _json_to_state_findings
from code_forge.state import Mode, StateFinding
from code_forge.verify import finding_excerpt_binding

DIFF = (
    "diff --git a/calc.py b/calc.py\n"
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def minimum(a, b):\n"
    "-    return min(a, b)\n"
    "+    return max(a, b)\n"
)


class Confirming(Falsifier):
    def falsify(self, finding: StateFinding) -> Disposition:
        return Disposition.CONFIRMED


def _machine(tmp_path, mode=Mode.CI, diff=DIFF):
    return StateMachine(
        mode=mode,
        falsifier=Confirming(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[Path("calc.py")],
            baseline_content=None,
            git_diff=diff,
            mode_hint="git",
        ),
        source_hash="h",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        test_config_root=outside_gate_root(tmp_path),  # #160
        registry={},
        l0_runner=lambda registry, files: ([], []),
    )


ENVELOPE_ONLY = {
    "findings": [
        {"file": "calc.py", "line": 2, "severity": "P1", "description": "minimum returns the maximum"}
    ],
    "code_excerpts": [
        {
            "file": "calc.py",
            "start_line": 1,
            "end_line": 2,
            "content": "def minimum(a, b):\n    return max(a, b)",
        }
    ],
}


def test_contract_requests_finding_owned_excerpt():
    assert '"excerpt"' in REVIEW_JSON_CONTRACT


@pytest.mark.parametrize(
    "excerpt,expected",
    [
        ("    return max(a, b)", True),
        ("def minimum(a, b):\n    return max(a, b)", True),
        ("    return min(a, b)", False),  # pre-image / invented
        ("def minimum(a, b):", False),  # real source, does not cover line 2
        (None, False),
        ("   ", False),
    ],
)
def test_binding(excerpt, expected):
    assert finding_excerpt_binding(DIFF, "calc.py", 2, excerpt) is expected


def test_binding_wrong_file():
    assert finding_excerpt_binding(DIFF, "other.py", 2, "    return max(a, b)") is False


def test_binding_unsupported_without_diff():
    assert finding_excerpt_binding(None, "calc.py", 2, "x") is None


def test_envelope_only_confirmed_does_not_pass_ci(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    m = _machine(tmp_path)
    cands = _json_to_state_findings(ENVELOPE_ONLY, "qodo")
    m.l1_provider = lambda: (cands, ENVELOPE_ONLY["code_excerpts"], Usage(), 0.0)
    result, _ = m._run_l1_phase()
    f = result[0]
    assert f.disposition == Disposition.UNCERTAIN
    assert "returned CONFIRMED" in (f.evidence_downgrade or "")
    assert f not in m._falsify_failed_findings  # not falsifier infra
    assert m._state.rounds_with_falsify_infra == 0
    assert m._state.rounds_with_evidence_incomplete == 1


def test_invalid_owned_excerpt_does_not_pass_ci(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    m = _machine(tmp_path)
    data = {
        **ENVELOPE_ONLY,
        "findings": [{**ENVELOPE_ONLY["findings"][0], "excerpt": "    return min(a, b)"}],
    }
    cands = _json_to_state_findings(data, "qodo")
    m.l1_provider = lambda: (cands, data["code_excerpts"], Usage(), 0.0)
    result, _ = m._run_l1_phase()
    assert result[0].disposition == Disposition.UNCERTAIN
    assert result[0].evidence_downgrade
    assert m._state.rounds_with_evidence_incomplete == 1


def test_bound_confirmed_stays_blocking(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    m = _machine(tmp_path)
    data = {
        **ENVELOPE_ONLY,
        "findings": [{**ENVELOPE_ONLY["findings"][0], "excerpt": "    return max(a, b)"}],
    }
    cands = _json_to_state_findings(data, "qodo")
    m.l1_provider = lambda: (cands, data["code_excerpts"], Usage(), 0.0)
    result, _ = m._run_l1_phase()
    assert result[0].disposition == Disposition.CONFIRMED
    assert not m._falsify_failed_findings
    assert result[0].evidence_downgrade is None
    assert m._state.rounds_with_evidence_incomplete == 0


# --- review follow-ups (PR #101) -------------------------------------------

import json  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
from unittest.mock import patch  # noqa: E402

from code_forge.cross_repo import RepositoryFalsifier  # noqa: E402
from code_forge.machine import TimeoutBreaker  # noqa: E402
from code_forge.factories import build_l1_provider  # noqa: E402
from code_forge.llm_invoke import LLMResult  # noqa: E402
from code_forge.source import compute_source_hash  # noqa: E402
from code_forge.state import State, Verdict, save_state  # noqa: E402
from code_forge.verify import normalize_finding_path, run_verify  # noqa: E402


@pytest.mark.parametrize("path", ["calc.py", "./calc.py", "././calc.py", "b/calc.py", "a/calc.py"])
def test_path_normalization_binds(path):
    assert finding_excerpt_binding(DIFF, path, 2, "    return max(a, b)") is True


def test_absolute_repo_path_binds(tmp_path):
    assert (
        finding_excerpt_binding(DIFF, str(tmp_path / "calc.py"), 2, "    return max(a, b)", cwd=tmp_path)
        is True
    )


def test_absolute_outside_repo_does_not_bind(tmp_path):
    assert (
        finding_excerpt_binding(DIFF, "/elsewhere/calc.py", 2, "    return max(a, b)", cwd=tmp_path)
        is False
    )


def test_normalize_never_guesses_unknown():
    assert normalize_finding_path("b/other.py", {"calc.py": {}}) is None


@pytest.mark.parametrize("line", [0, None, "file"])
def test_file_level_finding_binds_on_hunk_lines(line):
    assert finding_excerpt_binding(DIFF, "calc.py", line, "    return max(a, b)") is True
    assert finding_excerpt_binding(DIFF, "calc.py", line, "    return min(a, b)") is False


def test_file_level_confirmed_from_json_stays_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    m = _machine(tmp_path)
    data = {
        **ENVELOPE_ONLY,
        "findings": [
            {
                "file": "calc.py",
                "severity": "P1",
                "description": "wrong",
                "excerpt": "def minimum(a, b):\n    return max(a, b)",
            }
        ],
    }
    cands = _json_to_state_findings(data, "qodo")
    m.l1_provider = lambda: (cands, data["code_excerpts"], Usage(), 0.0)
    result, _ = m._run_l1_phase()
    assert result[0].line_range == [0, 0]
    assert result[0].disposition == Disposition.CONFIRMED


def test_nfc_nfd_equivalent_quote_binds():
    diff = DIFF.replace("max(a, b)", "max(a, b)  # caf\u00e9")
    assert finding_excerpt_binding(diff, "calc.py", 2, "    return max(a, b)  # cafe\u0301") is True


def test_cross_repo_binds_against_owning_repo_diff(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    sib_diff = DIFF.replace("calc.py", "lib/x.py")
    primary_diff = (
        "diff --git a/main.py b/main.py\n--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n"
    )
    inner = Confirming()
    rf = RepositoryFalsifier(
        inner, {"app@r1": inner, "lib@r2": inner}, {"app@r1": primary_diff, "lib@r2": sib_diff}
    )
    m = _machine(tmp_path, diff=primary_diff)
    m.falsifier = rf

    def mk(fp, file, excerpt):
        return StateFinding(
            id=fp,
            fingerprint=fp,
            source="L1",
            disposition=Disposition.UNCERTAIN,
            file=file,
            line_range=[2, 2],
            description="d",
            excerpt=excerpt,
        )

    good = mk("good", "lib@r2/lib/x.py", "    return max(a, b)")
    wrong_repo = mk("wrong", "app@r1/lib/x.py", "    return max(a, b)")
    m.l1_provider = lambda: ([good, wrong_repo], [], Usage(), 0.0)
    result, _ = m._run_l1_phase()
    assert result[0].disposition == Disposition.CONFIRMED
    assert result[1].disposition == Disposition.UNCERTAIN and result[1].evidence_downgrade
    assert rf.evidence_scope(good) == (sib_diff, "lib/x.py", None)
    assert rf.evidence_scope(mk("u", "nope@r/x.py", "x")) is None


def test_evidence_breaker_has_its_own_message(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    m = _machine(tmp_path)
    m._state.rounds_with_evidence_incomplete = 2
    with pytest.raises(TimeoutBreaker) as caught:
        m._check_evidence_can_still_converge(["fp"])
    assert "host could not bind" in str(caught.value)
    assert "falsifier could not adjudicate" not in str(caught.value)


def test_downgrade_persists_and_verify_refuses(tmp_path):
    directory = tmp_path / ".code-forge"
    (directory / "receipts").mkdir(parents=True)
    (directory / "gate.yaml").write_text("verify:\n  required_cycles: 1\n")
    sha = compute_source_hash(git_diff=DIFF)
    f = StateFinding(
        id="x",
        fingerprint="x",
        source="L1",
        disposition=Disposition.UNCERTAIN,
        file="calc.py",
        line_range=[2, 2],
        description="d",
        evidence_downgrade="falsifier returned CONFIRMED; host downgraded",
    )
    state = State(
        round=1, mode=Mode.CI, source_hash=sha, findings=[f], rounds_with_evidence_incomplete=1
    )
    save_state(state, directory / "state.json")
    disk = json.loads((directory / "state.json").read_text())
    assert disk["rounds_with_evidence_incomplete"] == 1
    assert disk["findings"][0]["evidence_downgrade"]
    result = run_verify(tmp_path, sha, {}, diff_text=DIFF)
    assert not result.passed
    assert "host evidence downgrade" in result.reason


# Full CI runs through the real provider, falsifier policy and verdict gate.

TS = "calc.ts"
CONTENT = "function minimum(a, b) {\n  return Math.max(a, b);\n"
TS_DIFF = (
    "diff --git a/calc.ts b/calc.ts\n--- a/calc.ts\n+++ b/calc.ts\n@@ -1,2 +1,2 @@\n"
    " function minimum(a, b) {\n-  return Math.min(a, b);\n+  return Math.max(a, b);\n"
)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("Real process forbidden"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **kw: pytest.fail("Network forbidden"))
    monkeypatch.setattr("code_forge.git.read_diff_blob", lambda *a, **kw: None)
    monkeypatch.setattr("code_forge.mutation.run_mutation", lambda *a, **kw: ([], []))
    monkeypatch.setattr("code_forge.mutation_dispatch.other_adapter_note", lambda *a, **kw: "note")
    monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", Path)
    monkeypatch.setattr("code_forge.machine.resolve_ledger_root", Path)
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    d = tmp_path / ".code-forge"
    d.mkdir()
    (d / "gate.yaml").write_text('test:\n  command: ["controlled-never-run"]\n')
    (tmp_path / TS).write_text(CONTENT)
    return tmp_path


class Fixed(Falsifier):
    def __init__(self, verdict):
        self.verdict = verdict

    def falsify(self, finding):
        return self.verdict


def _ci_run(tmp_path, payload, verdict):
    resolved = ResolvedReview([Path(TS)], None, TS_DIFF, "git")
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0.0)):
        provider = build_l1_provider("auto", resolved)
        m = StateMachine(
            mode=Mode.CI,
            falsifier=Fixed(verdict),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=resolved,
            source_hash=compute_source_hash(git_diff=TS_DIFF),
            baseline_spec_repr="issue46",
            cwd=tmp_path,
            test_config_root=outside_gate_root(tmp_path),  # #160
            registry={},
            l0_runner=lambda *a: ([], []),
            l1_provider=provider,
            l2_runner=lambda *a, **kw: ([], []),
            e2e_runner=lambda *a: ([], []),
            max_total_rounds=3,
            clean_round_threshold=3,
        )
        m._state.env_manifest = {"tier": "declared"}
        return m.run(), m


EXCERPTS = [{"file": TS, "start_line": 1, "end_line": 2, "content": CONTENT}]
FINDING = {"file": TS, "line": 2, "severity": "P1", "description": "minimum returns max"}


def test_ci_full_run_envelope_only_confirmed_fails(isolated):
    verdict, m = _ci_run(
        isolated, {"findings": [FINDING], "code_excerpts": EXCERPTS}, Disposition.CONFIRMED
    )
    disk = json.loads((isolated / ".code-forge/state.json").read_text())
    assert verdict == Verdict.FAIL
    assert disk["verdict"] == "FAIL" and disk["converged"] is False
    assert any(f.get("evidence_downgrade") for f in disk["findings"])


def test_ci_full_run_bound_confirmed_fails_as_confirmed(isolated):
    owned = {**FINDING, "excerpt": "  return Math.max(a, b);"}
    verdict, m = _ci_run(
        isolated, {"findings": [owned], "code_excerpts": EXCERPTS}, Disposition.CONFIRMED
    )
    disk = json.loads((isolated / ".code-forge/state.json").read_text())
    assert verdict == Verdict.FAIL
    assert any(f["disposition"] == "CONFIRMED" for f in disk["findings"])
    assert not any(f.get("evidence_downgrade") for f in disk["findings"])


def test_ci_full_run_clean_empty_passes(isolated):
    verdict, _ = _ci_run(isolated, {"findings": [], "code_excerpts": EXCERPTS}, Disposition.CONFIRMED)
    assert verdict == Verdict.PASS


def test_ci_full_run_valid_uncertain_keeps_existing_policy(isolated):
    owned = {**FINDING, "excerpt": "  return Math.max(a, b);"}
    verdict, m = _ci_run(
        isolated, {"findings": [owned], "code_excerpts": EXCERPTS}, Disposition.UNCERTAIN
    )
    assert verdict == Verdict.PASS  # ordinary bound UNCERTAIN: unchanged CI policy
    assert m._state.rounds_with_evidence_incomplete == 0


# --- re-review follow-ups (PR #101, 5548242970) ------------------------------

# Hunk at lines 10-11; lines 1-9 are blob context supplied via cwd.
CTX_DIFF = (
    "diff --git a/ctx.py b/ctx.py\n--- a/ctx.py\n+++ b/ctx.py\n@@ -10,2 +10,2 @@\n"
    " def f():\n-    return 1\n+    return 2\n"
)


def test_file_level_quote_must_touch_hunk(monkeypatch):
    lines = ["pass"] * 9 + ["def f():", "    return 2"]
    monkeypatch.setattr(
        "code_forge.verify._diff_validation_context",
        lambda diff, cwd=None: (
            {"ctx.py": {i + 1: t for i, t in enumerate(lines)}},
            {"ctx.py": [{"start": 10, "end": 11}]},
            [],
        ),
    )
    assert finding_excerpt_binding(CTX_DIFF, "ctx.py", 0, "pass") is False  # context only
    assert finding_excerpt_binding(CTX_DIFF, "ctx.py", 0, "pass\ndef f():") is True  # touches hunk
    assert finding_excerpt_binding(CTX_DIFF, "ctx.py", 0, "    return 2") is True


def test_negative_line_is_invalid():
    assert finding_excerpt_binding(DIFF, "calc.py", -1, "    return max(a, b)") is False


def test_nfd_diff_path_binds_nfc_and_nfd_finding_paths():
    nfd = "cafe\u0301.py"
    diff = DIFF.replace("calc.py", nfd)
    assert finding_excerpt_binding(diff, "caf\u00e9.py", 2, "    return max(a, b)") is True
    assert finding_excerpt_binding(diff, nfd, 2, "    return max(a, b)") is True


def test_cross_repo_absolute_path_resolves_against_owning_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    sib_root = tmp_path / "sib"
    sib_root.mkdir()
    sib_diff = DIFF.replace("calc.py", "lib/x.py")
    inner = Confirming()
    rf = RepositoryFalsifier(inner, {"lib@r2": inner}, {"lib@r2": sib_diff}, {"lib@r2": sib_root})
    m = _machine(tmp_path / "primary" if (tmp_path / "primary").mkdir() is None else tmp_path)
    m.falsifier = rf
    f = StateFinding(
        id="a",
        fingerprint="a",
        source="L1",
        disposition=Disposition.UNCERTAIN,
        file="lib@r2/" + str(sib_root / "lib/x.py"),
        line_range=[2, 2],
        description="d",
        excerpt="    return max(a, b)",
    )
    assert rf.evidence_scope(f) == (sib_diff, str(sib_root / "lib/x.py"), sib_root)
    m.l1_provider = lambda: ([f], [], Usage(), 0.0)
    result, _ = m._run_l1_phase()
    assert result[0].disposition == Disposition.CONFIRMED


def _downgraded_state(tmp_path, source_hash, downgrade=True):
    directory = tmp_path / ".code-forge"
    (directory / "receipts").mkdir(parents=True, exist_ok=True)
    (directory / "gate.yaml").write_text("verify:\n  required_cycles: 1\n")
    f = StateFinding(
        id="x",
        fingerprint="x",
        source="L1",
        disposition=Disposition.UNCERTAIN,
        file="calc.py",
        line_range=[2, 2],
        description="d",
        evidence_downgrade="host downgraded" if downgrade else None,
    )
    save_state(
        State(round=1, mode=Mode.CI, source_hash=source_hash, findings=[f]), directory / "state.json"
    )


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"cycles": [1]}, {"require_convergence": False}, {"cycles": [1], "require_convergence": False}],
)
def test_verify_refuses_downgrade_on_every_path(tmp_path, kwargs):
    sha = compute_source_hash(git_diff=DIFF)
    _downgraded_state(tmp_path, sha)
    result = run_verify(tmp_path, sha, {}, diff_text=DIFF, **kwargs)
    assert not result.passed and "host evidence downgrade" in result.reason


def test_verify_downgrade_gate_explicit_outcomes(tmp_path):
    from code_forge.verify import _evidence_downgrade_refusal

    sha = compute_source_hash(git_diff=DIFF)
    assert _evidence_downgrade_refusal(tmp_path, sha) is None  # no state.json
    _downgraded_state(tmp_path, "other-source")
    assert _evidence_downgrade_refusal(tmp_path, sha) is None  # state is for another diff
    _downgraded_state(tmp_path, sha, downgrade=False)
    assert _evidence_downgrade_refusal(tmp_path, sha) is None  # ordinary UNCERTAIN
    (tmp_path / ".code-forge" / "state.json").write_text("{not json")
    bad = _evidence_downgrade_refusal(tmp_path, sha)
    assert bad is not None and not bad.passed and "malformed" in bad.reason  # fail closed
    (tmp_path / ".code-forge" / "state.json").write_text("[]")
    assert not _evidence_downgrade_refusal(tmp_path, sha).passed


# LOCAL multi-round: a downgraded round earns no clean credit, and once later
# rounds no longer report it, it no longer blocks verify.


def _local_run(tmp_path, payloads, verdict, rounds=6):
    resolved = ResolvedReview([Path(TS)], None, TS_DIFF, "git")
    calls = []

    def transport(*a, **kw):
        calls.append(1)
        return LLMResult(payloads((len(calls) - 1) // 3), Usage(), 0.0)

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=transport):
        provider = build_l1_provider("auto", resolved)
        m = StateMachine(
            mode=Mode.LOCAL,
            falsifier=Fixed(verdict),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=resolved,
            source_hash=compute_source_hash(git_diff=TS_DIFF),
            baseline_spec_repr="issue46",
            cwd=tmp_path,
            test_config_root=outside_gate_root(tmp_path),  # #160
            registry={},
            l0_runner=lambda *a: ([], []),
            l1_provider=provider,
            l2_runner=lambda *a, **kw: ([], []),
            e2e_runner=lambda *a: ([], []),
            max_total_rounds=rounds,
            clean_round_threshold=3,
        )
        m._state.env_manifest = {"tier": "declared"}
        try:
            verdict_out = m.run()
        except TimeoutBreaker as exc:
            verdict_out = exc
        return verdict_out, m


def test_local_downgraded_round_earns_no_clean_credit(isolated):
    env = {"findings": [FINDING], "code_excerpts": EXCERPTS}
    out, m = _local_run(isolated, lambda r: env, Disposition.CONFIRMED)
    disk = json.loads((isolated / ".code-forge/state.json").read_text())
    # Existing LOCAL UNCERTAIN policy: HOLD for human disposition, never PASS.
    assert out == Verdict.PENDING
    assert disk["verdict"] == "PENDING" and disk["converged"] is False
    assert disk["consecutive_clean_rounds"] == 0
    assert disk["round_history"][-1]["clean_credit_action"] != "earned"
    assert disk["rounds_with_evidence_incomplete"] == 1
    assert any(f.get("evidence_downgrade") for f in disk["findings"])
    result = run_verify(isolated, compute_source_hash(git_diff=TS_DIFF), {}, diff_text=TS_DIFF)
    assert not result.passed and "host evidence downgrade" in result.reason


def test_local_superseded_downgrade_does_not_block_verify(isolated):
    env = {"findings": [FINDING], "code_excerpts": EXCERPTS}
    clean = {"findings": [], "code_excerpts": EXCERPTS}
    first, _ = _local_run(isolated, lambda r: env, Disposition.CONFIRMED)
    assert first == Verdict.PENDING
    out, m = _local_run(isolated, lambda r: clean, Disposition.CONFIRMED)
    disk = json.loads((isolated / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "PASS" and disk["consecutive_clean_rounds"] >= 3
    assert not any(f.get("evidence_downgrade") for f in disk["findings"])
    from code_forge.verify import _evidence_downgrade_refusal

    assert _evidence_downgrade_refusal(isolated, compute_source_hash(git_diff=TS_DIFF)) is None
    assert out == Verdict.PASS
