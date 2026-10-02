# SPDX-License-Identifier: Apache-2.0
"""Only identified surviving mutants may extend a completed-round streak."""

import builtins
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.cli import _build_parser, _run_mutation_check
from code_forge.disposition import Disposition
from code_forge.errors import CorruptedStateError, SchemaVersionMismatchError
from code_forge.exit_codes import EXIT_UNRELIABLE
from code_forge.factories import build_l2_runner
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine, TimeoutBreaker, mutation_result_verdict
from code_forge.mutation import launch_detached_mutation
from code_forge.mutation_findings import is_mutation_diagnostic, is_mutation_survivor
from code_forge.state import Mode, State, StateFinding, Verdict, load_state, save_state


def finding(identity="mutant-one", disposition=Disposition.CONFIRMED, source="MUTANT"):
    return StateFinding(
        identity, str(identity), source, disposition, "sample.py", [1, 1], "diagnostic or survivor"
    )


@pytest.fixture
def machine(tmp_path, monkeypatch):
    monkeypatch.delenv("CRG_DB_PATH", raising=False)
    (tmp_path / "sample.py").write_text("value = 1\n")
    forge = tmp_path / ".code-forge"
    forge.mkdir()
    (forge / "gate.yaml").write_text("test:\n  command: [pytest]\n")
    value = StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=ResolvedReview([Path("sample.py")], None, None, "git"),
        source_hash="accounting",
        baseline_spec_repr="owned control",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda registry, files: ([], []),
        max_total_rounds=3,
    )
    value._state.env_manifest = {}
    return value


@pytest.mark.parametrize(
    "identity,expected",
    [
        ("mutant-one", True),
        ("mutant-", False),
        ("other", False),
        ("MUTANT_1", False),
        ("MUTATION_SKIPPED", False),
        ("MUTATION_ERROR", False),
        (None, False),
        (42, False),
    ],
)
@pytest.mark.parametrize(
    "source,disposition",
    [
        ("MUTANT", Disposition.CONFIRMED),
        ("L1", Disposition.CONFIRMED),
        ("MUTANT", Disposition.UNCERTAIN),
        ("MUTANT", Disposition.DISMISSED),
        ("MUTANT", Disposition.STYLE),
        ("MUTANT", Disposition.FIXED),
    ],
)
def test_positive_identity_contract(identity, expected, source, disposition):
    item = finding(identity, disposition, source)
    assert is_mutation_survivor(item) is (
        expected and source == "MUTANT" and disposition == Disposition.CONFIRMED
    )
    assert is_mutation_diagnostic(item) is (
        source == "MUTANT"
        and disposition in (Disposition.CONFIRMED, Disposition.UNCERTAIN)
        and not is_mutation_survivor(item)
    )


def test_actual_missing_engine_is_not_a_survivor(machine, monkeypatch):
    empty = machine.cwd / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    machine.l2_runner = build_l2_runner()
    assert machine.run() != Verdict.PASS
    assert machine._state.consecutive_survivor_rounds == 0
    assert [(f.id, f.disposition) for f in machine._state.findings] == [
        ("MUTATION_SKIPPED", Disposition.CONFIRMED)
    ]
    assert machine._state.infra_errors.count("mutmut not found on PATH") == 3
    assert not any(
        "surviving mutants reported" in e or "demonstrably weak" in e
        for e in machine._state.infra_errors
    )


@pytest.mark.parametrize(
    "identity,disp",
    [
        ("MUTATION_SKIPPED", Disposition.CONFIRMED),
        ("MUTATION_ERROR", Disposition.CONFIRMED),
        ("future-diagnostic", Disposition.CONFIRMED),
        ("MUTATION_SKIPPED", Disposition.DISMISSED),
    ],
)
def test_failed_measurement_breaks_survivor_streak(machine, identity, disp):
    outputs = iter([[finding()], [finding(identity, disp)], [finding()]])
    machine.l2_runner = lambda *a, **kw: (next(outputs), [])
    machine.run()
    assert machine._state.consecutive_survivor_rounds == 1
    assert not any("surviving mutants reported" in e for e in machine._state.infra_errors)


def test_three_completed_observations_still_stop(machine):
    machine.l2_runner = lambda *a, **kw: ([finding()], [])
    assert machine.run() == Verdict.FAIL
    assert machine._state.consecutive_survivor_rounds == 3
    assert any(
        "surviving mutants reported in 3 consecutive rounds" in e for e in machine._state.infra_errors
    )


def test_unknown_custom_identity_remains_blocking(machine):
    machine.l2_runner = lambda *a, **kw: ([finding("custom-survivor")], [])
    assert machine.run() != Verdict.PASS
    assert machine._state.findings[0].disposition == Disposition.CONFIRMED
    assert machine._state.consecutive_survivor_rounds == 0


def test_interrupted_round_drops_persisted_streak(machine):
    state_path = machine.cwd / ".code-forge/state.json"
    save_state(
        State(source_hash="accounting", consecutive_survivor_rounds=2, env_manifest={}), state_path
    )
    machine.l2_runner = lambda *a, **kw: ([finding("MUTATION_SKIPPED")], [])

    def abort(_round):
        raise RuntimeError("interrupted after persisted snapshot")

    machine.post_round_hook = abort
    with pytest.raises(RuntimeError, match="interrupted after persisted"):
        machine.run()
    loaded = load_state(state_path)
    assert loaded.consecutive_survivor_rounds == 0
    assert loaded.round_history[-1]["l2_fingerprints"] == ["MUTATION_SKIPPED"]
    machine.post_round_hook = None
    machine.max_total_rounds = 1
    machine.l2_runner = lambda *a, **kw: ([finding()], [])
    machine.run()
    assert machine._state.consecutive_survivor_rounds == 1


def test_receipt_refusal_cannot_retain_a_streak(machine, monkeypatch):
    machine._state.consecutive_survivor_rounds = 2
    machine.l2_runner = lambda *a, **kw: ([finding()], [])
    monkeypatch.setattr(machine, "_receipt_gate_round_errors", lambda: ["controlled receipt refusal"])
    assert machine.run() == Verdict.FAIL
    assert load_state(machine.cwd / ".code-forge/state.json").consecutive_survivor_rounds == 0


@pytest.mark.parametrize("interruption", [RuntimeError, KeyboardInterrupt])
def test_first_round_operation_cannot_inherit_completed_state(machine, monkeypatch, interruption):
    state_path = machine.cwd / ".code-forge/state.json"
    save_state(
        State(
            source_hash="accounting",
            consecutive_survivor_rounds=2,
            verdict=Verdict.PASS,
            converged=True,
            env_manifest={},
        ),
        state_path,
    )
    execute = machine._execute_round

    def abort(_round):
        raise interruption("interrupted before the first round operation")

    monkeypatch.setattr(machine, "_execute_round", abort)
    with pytest.raises(interruption, match="before the first round operation"):
        machine.run()
    loaded = load_state(state_path)
    assert loaded.verdict == Verdict.PENDING
    assert loaded.converged is False
    assert loaded.consecutive_survivor_rounds == 0
    monkeypatch.setattr(machine, "_execute_round", execute)
    machine.max_total_rounds = 1
    machine.l2_runner = lambda *a, **kw: ([finding()], [])
    machine.run()
    assert machine._state.consecutive_survivor_rounds == 1


def test_pre_round_writer_refusal_preserves_disk_and_propagates(machine, monkeypatch):
    state_path = machine.cwd / ".code-forge/state.json"
    save_state(
        State(
            source_hash="accounting",
            consecutive_survivor_rounds=2,
            verdict=Verdict.PASS,
            converged=True,
            env_manifest={},
        ),
        state_path,
    )
    original = state_path.read_bytes()

    def refuse():
        raise PermissionError("controlled state writer refusal")

    monkeypatch.setattr(machine, "_persist_state", refuse)
    with pytest.raises(PermissionError, match="controlled state writer refusal"):
        machine.run()
    assert state_path.read_bytes() == original
    loaded = load_state(state_path)
    assert loaded.verdict == Verdict.PASS
    assert loaded.converged is True
    assert loaded.consecutive_survivor_rounds == 2


def legacy_state(tmp_path, counter=2):
    path = tmp_path / "state.json"
    save_state(State(consecutive_survivor_rounds=counter, source_hash="accounting"), path)
    data = json.loads(path.read_text())
    del data["mutation_survivor_counter_version"]
    data["round_history"] = [
        {"l2_fingerprints": ["mutant:one"], "dispositions": {"mutant:one": "CONFIRMED"}}
    ]
    original = json.dumps(data, indent=4).encode()
    path.write_bytes(original)
    return path, original


def archive_path(path, original):
    return path.with_name(f"{path.name}.legacy-survivors-{hashlib.sha256(original).hexdigest()}.json")


@pytest.mark.parametrize("counter", [0, 2, 3])
def test_legacy_reset_is_read_only_and_preserves_original_on_save(tmp_path, counter):
    path, original = legacy_state(tmp_path, counter)
    loaded = load_state(path)
    assert loaded.consecutive_survivor_rounds == 0
    assert path.read_bytes() == original
    archive = archive_path(path, original)
    assert not archive.exists()
    if counter:
        assert loaded.survivor_counter_migration == {
            "previous_count": counter,
            "reason": "legacy counter lacks surviving-mutant accounting provenance",
            "source_sha256": hashlib.sha256(original).hexdigest(),
        }
    save_state(loaded, path)
    if counter:
        assert archive.exists()
        assert archive.read_bytes() == original
        assert archive.stat().st_mode & 0o777 == 0o600
    else:
        assert not archive.exists()
    loaded.consecutive_survivor_rounds = 1
    save_state(loaded, path)
    assert load_state(path).consecutive_survivor_rounds == 1
    assert load_state(path).survivor_counter_migration == loaded.survivor_counter_migration


def test_exact_existing_archive_is_reused_without_overwrite(tmp_path):
    path, original = legacy_state(tmp_path)
    archive = archive_path(path, original)
    archive.write_bytes(original)
    inode = archive.stat().st_ino
    save_state(load_state(path), path)
    assert archive.read_bytes() == original
    assert archive.stat().st_ino == inode


@pytest.mark.parametrize(
    "kind", ["different", "symlink", "directory", "fifo", "unsupported", "permission"]
)
def test_archive_failure_preserves_old_state_before_temporary_write(tmp_path, monkeypatch, kind):
    path, original = legacy_state(tmp_path)
    loaded = load_state(path)
    archive = archive_path(path, original)
    if kind == "different":
        archive.write_bytes(b"unrelated evidence")
    elif kind == "symlink":
        target = tmp_path / "foreign.json"
        target.write_bytes(original)
        archive.symlink_to(target)
    elif kind == "directory":
        archive.mkdir()
    elif kind == "fifo":
        os.mkfifo(archive)
    elif kind == "unsupported":
        archive.write_bytes(original)
        monkeypatch.delattr(os, "O_NOFOLLOW")
    else:

        def denied(name, flags, *args, **kwargs):
            assert Path(name) == archive
            raise PermissionError("cannot retain original evidence")

        monkeypatch.setattr(os, "open", denied)
    with pytest.raises((OSError, CorruptedStateError)):
        save_state(loaded, path)
    assert path.read_bytes() == original
    assert not path.with_suffix(".tmp").exists()
    assert loaded._legacy_survivor_state == original


@pytest.mark.parametrize(
    "key,value",
    [
        ("consecutive_survivor_rounds", True),
        ("consecutive_survivor_rounds", -1),
        ("consecutive_survivor_rounds", "2"),
        ("consecutive_survivor_rounds", None),
        ("mutation_survivor_counter_version", True),
        ("mutation_survivor_counter_version", 2),
        ("mutation_survivor_counter_version", None),
        ("mutation_survivor_counter_version", "1"),
    ],
)
def test_invalid_accounting_data_is_refused_without_migration(tmp_path, key, value):
    path, _ = legacy_state(tmp_path)
    data = json.loads(path.read_text())
    data[key] = value
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    with pytest.raises(CorruptedStateError):
        load_state(path)
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.legacy-survivors-*.json")) == []


@pytest.mark.parametrize("kind", ["schema", "cache", "enum"])
def test_original_schema_and_cache_refusals_precede_migration(tmp_path, kind):
    path, _ = legacy_state(tmp_path)
    data = json.loads(path.read_text())
    data["consecutive_survivor_rounds"] = "malformed counter"
    if kind == "schema":
        data["schema_version"] = -1
    elif kind == "cache":
        data["dispositions"] = {"missing": "CONFIRMED"}
    else:
        data["mode"] = "invalid mode"
    path.write_text(json.dumps(data))
    with pytest.raises((CorruptedStateError, SchemaVersionMismatchError)) as caught:
        load_state(path)
    assert "survivor counter" not in str(caught.value)


@pytest.mark.parametrize(
    "identity,expected",
    [
        ("mutant-one", 1),
        ("mutant-", 2),
        ("custom-survivor", 2),
        ("MUTATION_SKIPPED", 2),
        ("MUTATION_ERROR", EXIT_UNRELIABLE),
    ],
)
def test_real_cli_wrapper_refuses_unmeasured_results(tmp_path, capsys, identity, expected):
    diff = tmp_path / "change.diff"
    diff.write_text(
        "diff --git a/sample.py b/sample.py\n--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    args = _build_parser().parse_args(["mutation-check", "--diff", str(diff)])
    with patch("code_forge.mutation.run_mutation", return_value=([finding(identity)], [])):
        assert _run_mutation_check(args, tmp_path) == expected
    assert "PASS" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "identity,status,survivors",
    [
        ("mutant-one", "done", ["mutant-one"]),
        ("mutant-", "error", []),
        ("MUTATION_SKIPPED", "error", []),
        ("custom-survivor", "error", []),
        ("MUTATION_ERROR", "error", []),
    ],
)
@pytest.mark.parametrize("fallback_import", [False, True])
def test_owned_detached_payload_retains_diagnostics(
    tmp_path, monkeypatch, run_detached_payload, identity, status, survivors, fallback_import
):
    result = tmp_path / "result.json"

    class Child:
        pid = 123

        def wait(self, timeout):
            return 0

    def capture(argv, **kwargs):
        original_cwd = Path.cwd()
        original_import = builtins.__import__
        failed = []

        def import_once(name, *a, **kw):
            if name == "code_forge.mutation" and not failed:
                failed.append(name)
                raise ImportError("force owned installed-layout fallback")
            return original_import(name, *a, **kw)

        if fallback_import:
            monkeypatch.setattr(builtins, "__import__", import_once)
        try:
            run_detached_payload(argv[2])
        finally:
            os.chdir(original_cwd)
            monkeypatch.setattr(builtins, "__import__", original_import)
        assert bool(failed) == fallback_import
        return Child()

    monkeypatch.setattr("subprocess.Popen", capture)
    monkeypatch.setattr("code_forge.mutation.run_mutation", lambda **kw: ([finding(identity)], []))
    assert launch_detached_mutation(["sample.py"], ["pytest"], tmp_path, result)
    data = json.loads(result.read_text())
    assert data["status"] == status
    assert data["survivors"] == survivors
    if status == "error":
        assert "diagnostic or survivor" in data["message"]
    assert mutation_result_verdict(data) is (Verdict.FAIL if survivors else None)


def test_l1_breaker_before_measurement_clears_the_streak(machine):
    from code_forge.llm_invoke import Usage

    state_path = machine.cwd / ".code-forge/state.json"
    save_state(
        State(
            source_hash="accounting",
            consecutive_survivor_rounds=2,
            rounds_with_failed_pass=2,
            env_manifest={},
        ),
        state_path,
    )
    incomplete = finding("l1-qodo-incomplete-coverage", source="INFRA")
    machine.l1_provider = lambda: ([incomplete], [], Usage(), 0.0)
    calls = []
    machine.l2_runner = lambda *a, **kw: (calls.append("l2") or [], [])
    with pytest.raises(TimeoutBreaker, match="pass that did not complete"):
        machine.run()
    assert load_state(state_path).consecutive_survivor_rounds == 0
    assert calls == []


def test_versioned_completed_counter_continues_normally(machine):
    state_path = machine.cwd / ".code-forge/state.json"
    save_state(
        State(source_hash="accounting", consecutive_survivor_rounds=2, env_manifest={}), state_path
    )
    machine.max_total_rounds = 1
    machine.l2_runner = lambda *a, **kw: ([finding()], [])
    assert machine.run() == Verdict.FAIL
    assert machine._state.consecutive_survivor_rounds == 3


def test_legacy_completed_history_requires_fresh_observations(machine):
    path, original = legacy_state(machine.cwd / ".code-forge")
    machine.max_total_rounds = 1
    machine.l2_runner = lambda *a, **kw: ([finding()], [])
    machine.run()
    assert machine._state.consecutive_survivor_rounds == 1
    assert archive_path(path, original).exists()
    assert archive_path(path, original).read_bytes() == original


def test_cli_preserves_quiet_inapplicable_skip(tmp_path, capsys):
    diff = tmp_path / "change.diff"
    diff.write_text(
        "diff --git a/sample.py b/sample.py\n--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    args = _build_parser().parse_args(["mutation-check", "--diff", str(diff)])
    skipped = finding("MUTATION_SKIPPED", Disposition.DISMISSED)
    skipped.fingerprint = "mutation-tests-only"
    with patch("code_forge.mutation.run_mutation", return_value=([skipped], [])):
        assert _run_mutation_check(args, tmp_path) == 0
    assert "SKIP" in capsys.readouterr().err
