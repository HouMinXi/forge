# SPDX-License-Identifier: Apache-2.0
"""Recorded timeout evidence, using physical files and a substituted command seam.

These are producer/consumer contract tests, not native-engine qualification.
Expected statuses are explicit fixture inputs, independent of product mappings.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_forge import mutation
from code_forge.disposition import Disposition
from code_forge.machine import mutation_result_verdict
from code_forge.mutation_findings import is_mutation_diagnostic, is_mutation_survivor
from code_forge.state import Verdict


TIMEOUT_CODES = (36, -24, 24, 152, 255)
REFUSED_CODES = (
    None,
    True,
    False,
    36.0,
    "36",
    900,
    5,
    33,
    34,
    35,
    2,
    -11,
    -9,
)
REFUSED_IDS = (
    "null",
    "true",
    "false",
    "float",
    "string",
    "unknown",
    "no-tests-5",
    "no-tests-33",
    "skipped",
    "suspicious",
    "interrupted",
    "segfault-11",
    "segfault-9",
)
SOURCE = b"def allows(n):\n    return n > 1\n"
CONFIG = b"[unrelated]\nvalue=preserve\n"
BASELINE = [sys.executable, "-m", "pytest"]


def _identity(index):
    return f"calc.x_allows__mutmut_{index}"


def _write_evidence(root, codes, statuses):
    assert len(codes) == len(statuses)
    mirror = root / "mutants/src/calc.py"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    generated = "\n".join(
        f"def x_allows__mutmut_{i}(n):\n    return n >= {i}\n" for i in range(1, len(codes) + 1)
    ).encode("utf-8")
    metadata = json.dumps(
        {"exit_code_by_key": {_identity(i): code for i, code in enumerate(codes, 1)}}
    ).encode("utf-8")
    results = "".join(f"    {_identity(i)}: {status}\n" for i, status in enumerate(statuses, 1)).encode(
        "utf-8"
    )
    mirror.write_bytes(generated)
    mirror.with_name("calc.py.meta").write_bytes(metadata)
    # Keep physical raw evidence outside the workspace-owned mirror cleanup.
    artifacts = root / "recorded"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "calc.py").write_bytes(generated)
    (artifacts / "calc.py.meta").write_bytes(metadata)
    (artifacts / "results.txt").write_bytes(results)
    return {_identity(i): status for i, status in enumerate(statuses, 1)}


def _install_executor(root, monkeypatch, codes, statuses, fault=None):
    (root / "src").mkdir(exist_ok=True)
    (root / "src/calc.py").write_bytes(SOURCE)
    (root / "setup.cfg").write_bytes(CONFIG)
    stages = []

    def execute(argv, **kwargs):
        assert kwargs["cwd"] == str(root)
        if argv == BASELINE:
            stages.append("baseline")
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv[0] == "recorded-mutmut"
        stage = argv[1]
        stages.append(stage)
        if stage == "run":
            assert argv == ["recorded-mutmut", "run", "--max-children", "1"]
            assert "only_mutate=src/calc.py\n" in (root / "setup.cfg").read_text()
            if fault == "run-nonzero":
                return subprocess.CompletedProcess(argv, 2, "", "RUN_REFUSED")
            if fault == "run-timeout":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            _write_evidence(root, codes, statuses)
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert stage == "results" and argv == ["recorded-mutmut", "results", "--all", "true"]
        if fault == "results-nonzero":
            return subprocess.CompletedProcess(argv, 2, "", "RESULTS_REFUSED")
        if fault == "results-timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        result_file = root / "recorded/results.txt"
        output = result_file.read_text(encoding="utf-8")
        lines = output.splitlines(keepends=True)
        if fault == "missing":
            output = "".join(lines[1:])
        elif fault == "extra":
            output += f"    {_identity(99)}: killed\n"
        elif fault == "contradiction":
            output = output.replace(": timeout", ": killed")
        elif fault == "duplicate":
            output += lines[0]
        elif fault == "malformed":
            output += "broken record\n"
        elif fault == "unknown-status":
            output = output.replace(": timeout", ": alien")
        elif fault == "empty":
            output = ""
        result_file.write_text(output, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, result_file.read_text(encoding="utf-8"), "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    monkeypatch.setattr(mutation, "_resolve_mutmut_invocation", lambda *_a, **_k: ["recorded-mutmut"])
    return stages


def _run(root):
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/calc.py"],
        BASELINE,
        cwd=root,
        max_children=1,
        _evidence=evidence,
    )
    return findings, infra, evidence


def _assert_restored(root):
    assert (root / "src/calc.py").read_bytes() == SOURCE
    assert (root / "setup.cfg").read_bytes() == CONFIG
    assert not (root / "mutants").exists()


def _notes(findings):
    return [item for item in findings if item.id.startswith("mutation-timeout-")]


def _assert_notes(findings, identities):
    notes = _notes(findings)
    assert len(notes) == len(identities)
    for note, identity in zip(notes, identities, strict=True):
        assert note.id == f"mutation-timeout-{identity}"
        assert note.fingerprint == f"mutation-timeout:{identity}"
        assert note.source == "MUTANT" and note.disposition is Disposition.DISMISSED
        assert note.file == "" and note.line_range == []
        assert note.description == f"mutant timed out: {identity}"
        assert not is_mutation_survivor(note) and not is_mutation_diagnostic(note)


@pytest.mark.parametrize("code", TIMEOUT_CODES)
def test_inventory_accepts_each_recorded_timeout_code(tmp_path, code):
    expected = _write_evidence(tmp_path, (code, 1, 0), ("timeout", "killed", "survived"))
    inventory, digests = mutation._mutation_inventory(str(tmp_path), ["src/calc.py"])
    assert inventory == expected
    assert inventory[_identity(1)] == "timeout"
    assert digests == {
        "src/calc.py": hashlib.sha256((tmp_path / "recorded/calc.py.meta").read_bytes()).hexdigest()
    }


@pytest.mark.parametrize("code", TIMEOUT_CODES)
def test_recorded_timeout_note_preserves_completed_mixed_inventory(tmp_path, monkeypatch, code):
    stages = _install_executor(tmp_path, monkeypatch, (code, 1, 0), ("timeout", "killed", "survived"))
    findings, infra, evidence = _run(tmp_path)
    assert stages == ["baseline", "baseline", "baseline", "run", "results"]
    assert infra == [] and evidence["infra_errors"] == [] and evidence["skipped"] == []
    assert evidence["baseline_passed"] is True and evidence["completed_measurement"] is True
    assert evidence["inventory"] == {
        _identity(1): "timeout",
        _identity(2): "killed",
        _identity(3): "survived",
    }
    assert evidence["metadata_sha256"] == {
        "src/calc.py": hashlib.sha256((tmp_path / "recorded/calc.py.meta").read_bytes()).hexdigest()
    }
    assert (
        evidence["results_sha256"]
        == hashlib.sha256((tmp_path / "recorded/results.txt").read_bytes()).hexdigest()
    )
    assert len(findings) == 2
    survivor = findings[0]
    assert survivor.id == f"mutant-{_identity(3)}"
    assert survivor.fingerprint == f"mutant:{_identity(3)}"
    assert survivor.source == "MUTANT" and survivor.disposition is Disposition.CONFIRMED
    assert survivor.file == "" and survivor.line_range == [0, 0]
    assert survivor.description == f"mutant survived: {_identity(3)}"
    _assert_notes(findings, [_identity(1)])
    _assert_restored(tmp_path)


@pytest.mark.parametrize(
    "codes,statuses,timeout_ids,survivor_ids",
    [
        ((36,), ("timeout",), (1,), ()),
        ((36, -24, 0), ("timeout", "timeout", "survived"), (1, 2), (3,)),
        ((1, 3, 37, 0), ("killed", "killed", "caught by type check", "survived"), (), (4,)),
    ],
    ids=["timeout-only", "multiple-timeouts", "no-timeouts"],
)
def test_timeout_note_cardinality_and_non_timeout_controls(
    tmp_path,
    monkeypatch,
    codes,
    statuses,
    timeout_ids,
    survivor_ids,
):
    _install_executor(tmp_path, monkeypatch, codes, statuses)
    findings, infra, evidence = _run(tmp_path)
    _assert_notes(findings, [_identity(i) for i in timeout_ids])
    assert [f.id for f in findings if is_mutation_survivor(f)] == [
        f"mutant-{_identity(i)}" for i in survivor_ids
    ]
    assert len(findings) == len(timeout_ids) + len(survivor_ids)
    assert not infra and evidence["completed_measurement"] is True
    assert evidence["baseline_passed"] is True
    assert evidence["inventory"] == {_identity(i): s for i, s in enumerate(statuses, 1)}
    _assert_restored(tmp_path)


@pytest.mark.parametrize("bad_code", REFUSED_CODES, ids=REFUSED_IDS)
def test_timeout_does_not_sanitize_refused_metadata(tmp_path, monkeypatch, bad_code):
    direct = tmp_path / "direct"
    _write_evidence(direct, (36, bad_code), ("timeout", "not checked"))
    with pytest.raises(ValueError):
        mutation._mutation_inventory(str(direct), ["src/calc.py"])
    stages = _install_executor(tmp_path, monkeypatch, (36, bad_code), ("timeout", "not checked"))
    findings, infra, evidence = _run(tmp_path)
    assert stages == ["baseline", "baseline", "baseline", "run"]
    assert infra and findings[0].id == "MUTATION_ERROR" and _notes(findings) == []
    assert evidence["completed_measurement"] is False and evidence["baseline_passed"] is False
    assert mutation._mutation_outcome(findings, infra, evidence)["status"] == "error"
    _assert_restored(tmp_path)


@pytest.mark.parametrize("damage", ["identity", "duplicate-json", "meta-link", "meta-directory"])
def test_timeout_inventory_keeps_identity_and_regular_file_guards(tmp_path, damage):
    _write_evidence(tmp_path, (36, 1), ("timeout", "killed"))
    meta = tmp_path / "mutants/src/calc.py.meta"
    if damage == "identity":
        meta.write_text(json.dumps({"exit_code_by_key": {_identity(1): 36}}))
    elif damage == "duplicate-json":
        meta.write_text('{"exit_code_by_key":{},' + meta.read_text()[1:])
    elif damage == "meta-link":
        meta.unlink()
        meta.symlink_to(tmp_path / "recorded/calc.py.meta")
    else:
        meta.unlink()
        meta.mkdir()
    with pytest.raises((ValueError, OSError)):
        mutation._mutation_inventory(str(tmp_path), ["src/calc.py"])


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "extra",
        "contradiction",
        "duplicate",
        "malformed",
        "unknown-status",
        "empty",
        "run-nonzero",
        "run-timeout",
        "results-nonzero",
        "results-timeout",
    ],
)
def test_timeout_notes_require_complete_command_and_transport_proof(tmp_path, monkeypatch, fault):
    stages = _install_executor(
        tmp_path,
        monkeypatch,
        (36, 1, 0),
        ("timeout", "killed", "survived"),
        fault,
    )
    findings, infra, evidence = _run(tmp_path)
    assert _notes(findings) == []
    assert not any(is_mutation_survivor(f) for f in findings)
    assert evidence["completed_measurement"] is False and evidence["baseline_passed"] is False
    if fault.endswith("timeout"):
        assert findings[0].id == "MUTATION_SKIPPED" and infra == []
    else:
        assert findings[0].id == "MUTATION_ERROR" and infra
    assert stages == ["baseline", "baseline", "baseline", "run"] + (
        [] if fault.startswith("run-") else ["results"]
    )
    if not fault.startswith("run-"):
        assert evidence["inventory"][_identity(1)] == "timeout"
    assert mutation._mutation_outcome(findings, infra, evidence)["status"] == "error"
    _assert_restored(tmp_path)


@pytest.mark.parametrize("mixed", [False, True], ids=["timeout-only", "mixed"])
def test_late_cleanup_failure_invalidates_proof_without_erasing_notes(tmp_path, monkeypatch, mixed):
    codes = (36, 1, 0) if mixed else (36,)
    statuses = ("timeout", "killed", "survived") if mixed else ("timeout",)
    _install_executor(tmp_path, monkeypatch, codes, statuses)
    original_release = mutation.MutationWorkspace.release

    def release(workspace):
        original_release(workspace)
        raise mutation.MutationWorkspaceError("RECORDED_TIMEOUT_RELEASE_FAILURE")

    monkeypatch.setattr(mutation.MutationWorkspace, "release", release)
    findings, infra, evidence = _run(tmp_path)
    _assert_notes(findings, [_identity(1)])
    assert findings[-1].id == "MUTATION_ERROR"
    assert infra == ["RECORDED_TIMEOUT_RELEASE_FAILURE"]
    assert evidence["completed_measurement"] is False and evidence["baseline_passed"] is False
    assert evidence["inventory"][_identity(1)] == "timeout"
    outcome = mutation._mutation_outcome(findings, infra, evidence)
    assert outcome["status"] == "error" and outcome["baseline_passed"] is False
    assert "RECORDED_TIMEOUT_RELEASE_FAILURE" in outcome["message"]
    _assert_restored(tmp_path)


@pytest.mark.parametrize("mixed", [False, True], ids=["timeout-only", "mixed"])
def test_timeout_outcome_and_source_cli_keep_existing_survivor_policy(
    tmp_path,
    monkeypatch,
    capsys,
    mixed,
):
    from code_forge.cli import EXIT_FAIL, EXIT_PASS, main

    codes = (36, 1, 0) if mixed else (36,)
    statuses = ("timeout", "killed", "survived") if mixed else ("timeout",)
    _install_executor(tmp_path, monkeypatch, codes, statuses)
    findings, infra, evidence = _run(tmp_path)
    _assert_notes(findings, [_identity(1)])
    outcome = mutation._mutation_outcome(findings, infra, evidence)
    assert outcome["status"] == "done" and outcome["baseline_passed"] is True
    assert outcome["inventory"] == {_identity(i): s for i, s in enumerate(statuses, 1)}
    assert outcome["survivors"] == ([f"mutant-{_identity(3)}"] if mixed else [])
    assert outcome["skipped"] == [] and outcome["infra_errors"] == [] and outcome["message"] == ""
    assert mutation_result_verdict(outcome) is (Verdict.FAIL if mixed else None)
    diff_file = tmp_path / "input.diff"
    diff_file.write_text(
        "diff --git a/src/calc.py b/src/calc.py\n--- a/src/calc.py\n+++ b/src/calc.py\n"
        "@@ -1 +1 @@\n-def allows(n): return n > 1\n+def allows(n): return n >= 1\n"
    )
    monkeypatch.setattr(mutation, "run_mutation", lambda **_kwargs: (findings, infra))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["code-forge", "mutation-check", "--diff", str(diff_file)])
    capsys.readouterr()
    assert main() == (EXIT_FAIL if mixed else EXIT_PASS)
    output = capsys.readouterr()
    if mixed:
        assert "1 survivor(s) found" in output.err
        assert f"mutant survived: {_identity(3)}" in output.err
        assert "PASS" not in output.err
    else:
        assert output.err == "code-forge: mutation-check: PASS\n"
    assert "mutant timed out" not in output.err + output.out
    assert "all killed" not in (output.err + output.out).lower()
    _assert_restored(tmp_path)


@pytest.mark.parametrize("mixed", [False, True], ids=["timeout-only", "mixed"])
def test_generated_detached_carrier_retains_raw_timeout_inventory(
    tmp_path,
    monkeypatch,
    run_detached_payload,
    mixed,
):
    codes = (36, 1, 0) if mixed else (36,)
    statuses = ("timeout", "killed", "survived") if mixed else ("timeout",)
    stages = _install_executor(tmp_path, monkeypatch, codes, statuses)
    scripts = []

    def capture(argv, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert argv[:2] == [sys.executable, "-c"]
        scripts.append(argv[2])
        return SimpleNamespace(pid=123, wait=lambda timeout: 0)

    monkeypatch.setattr(mutation.subprocess, "Popen", capture)
    result_path = tmp_path / ".code-forge/mutation-result.json"
    assert mutation.launch_detached_mutation(
        ["src/calc.py"],
        BASELINE,
        tmp_path,
        result_path,
        max_children=1,
    )
    assert len(scripts) == 1
    selected_src = str(Path(mutation.__file__).resolve().parent.parent)
    assert f"sys.path.insert(0, {selected_src!r})" in scripts[0]
    # Existing fixture strips the verified fork prologue, not carrier logic.
    run_detached_payload(scripts[0])
    payload = json.loads(result_path.read_text())
    assert stages == ["baseline", "baseline", "baseline", "run", "results"]
    assert payload["status"] == "done" and payload["baseline_passed"] is True
    assert payload["inventory"] == {_identity(i): s for i, s in enumerate(statuses, 1)}
    assert payload["survivors"] == ([f"mutant-{_identity(3)}"] if mixed else [])
    assert payload["skipped"] == [] and payload["infra_errors"] == [] and payload["message"] == ""
    assert mutation_result_verdict(payload) is (Verdict.FAIL if mixed else None)
    assert "mutant timed out" not in result_path.read_text()
    _assert_restored(tmp_path)
