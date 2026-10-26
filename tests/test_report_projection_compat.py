"""Phase 1 compat + paired fault-free oracle (Forge #332 slice §5 / §6.4)."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from code_forge import cli, llm_invoke, mcp_server
from code_forge.errors import CorruptedStateError, SchemaVersionMismatchError
from code_forge.falsify import StubFalsifier
from code_forge.report_projection import classify_report_records
from code_forge.state import (
    Disposition,
    FindingDiagnosticKind,
    Mode,
    State,
    StateFinding,
    load_state,
    save_state,
)
from tests.test_cli_integration import (
    _git_init_repo,
    _write_measured_change,
    _write_measured_gate,
    _write_py_file,
    _write_tools_yaml,
)
from tests.test_resume_receipt_provenance import (
    CONTENT,
    VALID,
    _attach_falsifier_candidate,
    _machine,
)

_DURATION_KEYS = frozenset({"durationSeconds", "duration_s", "total_duration_s"})


def _strip(o):
    if isinstance(o, dict):
        return {k: ("<duration>" if k in _DURATION_KEYS else _strip(v)) for k, v in sorted(o.items())}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def _mask(text: str, w) -> str:
    for r in sorted({str(w), os.path.realpath(w)}, key=len, reverse=True):
        text = text.replace(r, "<W>")
    return text


def _extract_sarif_json(stdout: str) -> str:
    """Return the SARIF JSON document from CI stdout (pure JSON preferred)."""
    text = stdout.strip()
    if not text:
        raise AssertionError("CI stdout empty; expected SARIF JSON")
    if text[0] == "{":
        return text
    # Harness noise before JSON: take from first object brace.
    idx = text.find("{")
    assert idx >= 0, stdout[:200]
    return text[idx:]


def stable_view(code, stdout, stderr, state_text, w) -> dict:
    summary = [l for l in stderr.splitlines() if l.startswith("code-forge: ") and " findings=" in l]
    assert len(summary) == 1, summary
    sarif_text = _extract_sarif_json(stdout)
    p = mcp_server._sarif_projection_with_status(sarif_text)
    view = {
        "exit": code,
        "sarif": _strip(json.loads(sarif_text)),
        "state": _strip(json.loads(state_text)),
        "summary": summary[0],
        "mcp": (
            p[0],
            p[3],
            None if p[1] is None else len(p[1]),
            None if p[2] is None else len(p[2]),
        ),
    }
    return json.loads(_mask(json.dumps(view, sort_keys=True), w))


def _paths_differ(a, b, prefix="$"):
    if type(a) is not type(b):
        return [prefix]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _paths_differ(a.get(k, "<absent>"), b.get(k, "<absent>"), f"{prefix}.{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [prefix + ".length"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _paths_differ(x, y, f"{prefix}[{i}]")
        return out
    return [] if a == b else [prefix]


def _product_finding(**kw):
    defaults = dict(
        id="p1",
        fingerprint="pf1",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="a.py",
        line_range=[1, 2],
        description="d",
    )
    defaults.update(kw)
    return StateFinding(**defaults)


# ---------------------------------------------------------------------------
# Real-file loader reactions (classifier not involved for strict rejects)
# ---------------------------------------------------------------------------

def test_unknown_additive_field_real_file(tmp_path):
    path = tmp_path / "state.json"
    save_state(State(findings=[_product_finding()]), path)
    doc = json.loads(path.read_text())
    doc["findings"][0]["unknown_wave1_field"] = {"x": 1}
    path.write_text(json.dumps(doc))
    loaded = load_state(path)
    assert loaded is not None
    proj = classify_report_records(loaded)
    assert proj.classification_complete is True
    assert len(proj.product) == 1


def test_unknown_diagnostic_kind_real_file_via_emit_ci_output(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    save_state(State(findings=[_product_finding()]), path)
    doc = json.loads(path.read_text())
    doc["findings"][0]["diagnostic_kind"] = "not-a-real-kind"
    path.write_text(json.dumps(doc))
    called = {"n": 0}

    def sentinel(*a, **k):
        called["n"] += 1
        raise AssertionError("classifier must not be called")

    monkeypatch.setattr("code_forge.report_projection.classify_report_records", sentinel)
    stdout, stderr = io.StringIO(), io.StringIO()
    with pytest.raises(Exception):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            cli._emit_ci_output(path, {})
    assert called["n"] == 0


def test_schema_version_mismatch_real_file(tmp_path):
    path = tmp_path / "state.json"
    save_state(State(findings=[]), path)
    doc = json.loads(path.read_text())
    doc["schema_version"] = 999
    path.write_text(json.dumps(doc))
    with pytest.raises(SchemaVersionMismatchError):
        load_state(path)


def test_unparseable_state_real_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(CorruptedStateError):
        load_state(path)


def test_old_writer_rewrite_drops_unknown_fields_without_restoring_trust(tmp_path):
    path = tmp_path / "state.json"
    save_state(State(findings=[_product_finding()]), path)
    doc = json.loads(path.read_text())
    doc["findings"][0]["future_trust_flag"] = True
    path.write_text(json.dumps(doc))
    loaded = load_state(path)
    assert loaded is not None
    assert not hasattr(loaded.findings[0], "future_trust_flag") or not getattr(
        loaded.findings[0], "future_trust_flag", False
    )
    # rewrite via save_state drops unknown
    save_state(loaded, path)
    rewritten = json.loads(path.read_text())
    assert "future_trust_flag" not in rewritten["findings"][0]
    proj = classify_report_records(loaded)
    assert proj.classification_complete is True
    # no new trust semantics
    assert all(not hasattr(r, "trusted") for r in proj.product)


def test_stable_view_detects_listed_field_difference(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    # Minimal synthetic SARIF/state/summary for stable_view self-test
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "fp1",
                        "level": "error",
                        "message": {"text": "m"},
                        "properties": {"source": "L1", "error": None},
                    }
                ],
                "properties": {"tokenCost": {"durationSeconds": 1.5}},
            }
        ],
    }
    state = {
        "verdict": "FAIL",
        "findings": [
            {
                "id": "p1",
                "fingerprint": "fp1",
                "source": "L1",
                "disposition": "CONFIRMED",
                "error": None,
                "provider_failure": {"duration_s": 0.1, "kind": "x"},
            }
        ],
        "cost": {"total_duration_s": 9.0, "per_pass": [{"duration_s": 0.2}]},
    }
    stdout = json.dumps(sarif)
    stderr = "code-forge: FAIL findings=1 confirmed=1\n"
    state_text = json.dumps(state)
    base = stable_view(1, stdout, stderr, state_text, w)

    # change listed path: result level
    sarif2 = copy.deepcopy(sarif)
    sarif2["runs"][0]["results"][0]["level"] = "warning"
    v2 = stable_view(1, json.dumps(sarif2), stderr, state_text, w)
    assert base != v2

    # change fingerprint (and thus compared)
    sarif3 = copy.deepcopy(sarif)
    sarif3["runs"][0]["results"][0]["ruleId"] = "other"
    v3 = stable_view(1, json.dumps(sarif3), stderr, state_text, w)
    assert base != v3

    # excluded duration paths keep equality
    sarif4 = copy.deepcopy(sarif)
    sarif4["runs"][0]["properties"]["tokenCost"]["durationSeconds"] = 99.0
    state4 = copy.deepcopy(state)
    state4["cost"]["total_duration_s"] = 100.0
    state4["cost"]["per_pass"][0]["duration_s"] = 50.0
    state4["findings"][0]["provider_failure"]["duration_s"] = 7.0
    v4 = stable_view(1, json.dumps(sarif4), stderr, json.dumps(state4), w)
    assert base == v4


# ---------------------------------------------------------------------------
# Paired fault-free oracle (§6.4)
# ---------------------------------------------------------------------------

def _build_snap(snap: Path, scenario: str):
    snap.mkdir()
    if scenario == "ci_pass_stub":
        _git_init_repo(snap)
        _write_tools_yaml(snap)
        _write_py_file(snap)
        _write_measured_gate(snap)
        subprocess.run(["git", "add", "-A"], cwd=snap, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=snap, capture_output=True, check=True)
        _write_measured_change(snap)
    else:
        (snap / "control.ts").write_text(CONTENT)
        (snap / ".code-forge").mkdir()
        (snap / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")


def _adversarial(kind):
    return lambda machine, prompt: (
        llm_invoke.LLMInvokeError("capacity", kind=kind, retryable=False)
        if "adversarial" in prompt.rsplit("You are a ", 1)[-1]
        else VALID
    )


def _procs_under(w: Path):
    hits = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if cwd == str(w) or cwd.startswith(str(w) + "/"):
            hits.append(int(pid))
    return hits


def _assert_anchors(scenario, code, stdout, stderr, state_obj):
    sarif_text = _extract_sarif_json(stdout)
    sarif = json.loads(sarif_text)
    run = sarif["runs"][0]
    results = run["results"]
    # Legacy format_summary line (findings=); §5 classification line is separate.
    summary_lines = [l for l in stderr.splitlines() if l.startswith("code-forge: ") and " findings=" in l]
    assert len(summary_lines) == 1, summary_lines
    summary = summary_lines[0]
    class_lines = [l for l in stderr.splitlines() if l.startswith("code-forge: classification")]
    assert class_lines, "IC-05 §5 classification line missing on included CI stderr"
    count, _findings, _audit, status = mcp_server._sarif_projection_with_status(sarif_text)
    verdict = state_obj["verdict"]
    if scenario == "ci_pass_stub":
        assert (code, verdict, len(results)) == (0, "PASS", 0)
        assert summary.startswith("code-forge: PASS findings=0 confirmed=0 ")
        assert (count, status) == (0, None)
    elif scenario == "ci_fail_capacity":
        assert (code, verdict, results) == (1, "FAIL", [])
        assert "FAIL findings=0 confirmed=0" in summary
        assert "provider_capacity=1" in summary
        assert "capacity_incomplete=1" in summary
        assert "passes=2/3" in summary
        assert run["properties"]["providerDiagnostics"]
        assert count is None and status.startswith("unknown: capacity-incomplete")
    elif scenario == "ci_fail_conn":
        assert (code, verdict, len(results)) == (1, "FAIL", 2)
        assert "FAIL findings=2 confirmed=2" in summary
        assert "infra=2" in summary
        assert "passes=2/3" in summary
        assert "providerDiagnostics" not in run.get("properties", {})
        assert (count, status) == (2, None)
    elif scenario == "ci_uncertain_candidate":
        assert (code, verdict, len(results)) == (0, "PASS", 2)
        assert "PASS findings=2 confirmed=0 uncertain=1 dismissed=1" in summary
        assert any(
            f["id"] == "l1-qodo-new-falsifier-candidate" and f["disposition"] == "UNCERTAIN"
            for f in state_obj["findings"]
        )
        assert (count, status) == (2, None)
    else:
        raise AssertionError(scenario)


def _run_side(tmp: Path, snap: Path, scenario: str, monkeypatch, label: str, sentinel: bool, fixture: Path):
    w = tmp / "w"
    assert not w.exists()
    shutil.copytree(snap, w, symlinks=True)
    calls = {"n": 0}
    stdout, stderr = io.StringIO(), io.StringIO()
    with monkeypatch.context() as m:
        m.chdir(w)
        if scenario != "ci_pass_stub":
            m.setattr(
                "code_forge.manifest.extract_manifest",
                lambda root: type(
                    "Manifest",
                    (),
                    {"to_dict": lambda self: {}, "to_prompt_block": lambda self: ""},
                )(),
            )
        if sentinel:
            # Phase 4 Entry (IC-03 include routes) calls classify inside
            # _emit_ci_output. Count calls but do not raise — B remains a
            # paired fault-free re-run, not a "classifier forbidden" probe.
            import code_forge.report_projection as _rp

            _real_classify = _rp.classify_report_records

            def _count_classify(*a, **k):
                calls["n"] += 1
                return _real_classify(*a, **k)

            m.setattr("code_forge.report_projection.classify_report_records", _count_classify)
        if scenario == "ci_pass_stub":
            # Self-contained main-worktree bypass: independent repro may not
            # inherit conftest's FORGE_SKIP_WORKTREE_CHECK into _run(env=os.environ).
            m.setenv("FORGE_ALLOW_MAIN", "1")
            m.setattr(
                sys,
                "argv",
                [
                    "code-forge",
                    "--falsification-engine",
                    "stub",
                    "--mode",
                    "ci",
                    "--allow-main",
                    "a.py",
                ],
            )
            m.setattr("code_forge.outlet_resolver.resolve_outlet", lambda *a, **kw: "subprocess")
        else:
            m.setattr(sys, "argv", ["code-forge", "review"])

            def fake_run(args, env, cwd):
                if scenario == "ci_uncertain_candidate":
                    machine = _machine(w, m, rounds=1)
                    _attach_falsifier_candidate(machine)
                    machine.falsifier = StubFalsifier(fixture)
                else:
                    kind = "truncated" if scenario == "ci_fail_capacity" else "conn"
                    machine = _machine(w, m, rounds=1, payload=_adversarial(kind))
                machine.mode = Mode.CI
                verdict = machine.run()
                cli._emit_ci_output(w / ".code-forge" / "state.json", {})
                return verdict

            m.setattr(cli, "_run", fake_run)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main()
    assert Path.cwd() != w and not str(Path.cwd()).startswith(str(w) + "/")
    waited = 0.0
    while _procs_under(w) and waited < 120:
        time.sleep(0.5)
        waited += 0.5
    still = _procs_under(w)
    assert not still, f"leaked process: {still}"
    state_path = w / ".code-forge" / "state.json"
    state_text = state_path.read_text()
    state_obj = json.loads(state_text)
    out_s, err_s = stdout.getvalue(), stderr.getvalue()
    _assert_anchors(scenario, code, out_s, err_s, state_obj)
    view = stable_view(code, out_s, err_s, state_text, w)
    shutil.copytree(w, tmp / f"{label}_after", symlinks=True)
    shutil.rmtree(w)
    return {
        "exit": code,
        "stdout": out_s,
        "stderr": err_s,
        "state_text": state_text,
        "view": view,
        "sentinel_calls": calls["n"],
        "quiesce_wait_s": waited,
    }


@pytest.mark.parametrize(
    "scenario",
    ["ci_pass_stub", "ci_fail_capacity", "ci_fail_conn", "ci_uncertain_candidate"],
)
def test_paired_fault_free_oracle(tmp_path, monkeypatch, scenario):
    snap = tmp_path / "snap"
    _build_snap(snap, scenario)
    fixture = tmp_path / "falsifier.json"
    fixture.write_text(json.dumps({"default": "UNCERTAIN"}))
    sides = {}
    for label, use_sentinel in (("A", False), ("B", True), ("Aprime", False)):
        sides[label] = _run_side(tmp_path, snap, scenario, monkeypatch, label, use_sentinel, fixture)
    # Included CI emits via Entry wiring: classify runs on A/B/Aprime.
    # B still matches A/Aprime (fault-free); call count is informational.
    assert sides["B"]["sentinel_calls"] >= 1
    assert sides["A"]["view"] == sides["B"]["view"] == sides["Aprime"]["view"], _paths_differ(
        sides["A"]["view"], sides["B"]["view"]
    )

    # §6.4.6: classify on A_after state, fault-free and with per-record fault
    a_state_path = tmp_path / "A_after" / ".code-forge" / "state.json"
    if a_state_path.exists():
        loaded = load_state(a_state_path)
        if loaded is not None and loaded.findings:
            proj_ok = classify_report_records(copy.deepcopy(loaded))
            import code_forge.report_projection as rp

            target = loaded.findings[0]
            real = rp._build_record

            def boom(index, f, shape, selector, diags):
                if f is target:
                    raise RuntimeError("inj")
                return real(index, f, shape, selector, diags)

            with monkeypatch.context() as m:
                m.setattr(rp, "_build_record", boom)
                proj_fault = classify_report_records(loaded)
            ok_map = {
                r.index: r
                for r in proj_ok.product
                + proj_ok.unresolved_product_candidates
                + proj_ok.execution
                + proj_ok.gate
            }
            for r in (
                proj_fault.product
                + proj_fault.unresolved_product_candidates
                + proj_fault.execution
                + proj_fault.gate
            ):
                assert r == ok_map[r.index]
