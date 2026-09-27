# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the mutation_engines.schemas module.

Covers: schema round-trip, normalized status enumeration, target
declaration validation, worker config validation, budget validation,
and rejection of malformed, duplicate and oversized declarations.
"""
from __future__ import annotations


import pytest

from code_forge.mutation_engines.schemas import (
    AggregateDecision,
    ArtifactReference,
    BaselineState,
    Budget,
    CleanupState,
    InfrastructureError,
    NormalizedStatus,
    RunState,
    TargetDeclaration,
    WorkerConfig,
    valid_identifier,
)


# -- Identifier validation ---------------------------------------------------

class TestIdentifierValidation:
    def test_valid_simple(self):
        assert valid_identifier("abc")

    def test_valid_with_digits_hyphens_underscores(self):
        assert valid_identifier("python-core")
        assert valid_identifier("go_core")
        assert valid_identifier("a0123456789")

    def test_valid_max_length(self):
        assert valid_identifier("a" + "b" * 63)

    def test_reject_empty(self):
        assert not valid_identifier("")

    def test_reject_starts_with_digit(self):
        assert not valid_identifier("1abc")

    def test_reject_starts_with_hyphen(self):
        assert not valid_identifier("-abc")

    def test_reject_uppercase(self):
        assert not valid_identifier("Python")

    def test_reject_too_long(self):
        assert not valid_identifier("a" + "b" * 64)

    def test_reject_special_chars(self):
        assert not valid_identifier("a.b")
        assert not valid_identifier("a/b")
        assert not valid_identifier("a b")


# -- NormalizedStatus ---------------------------------------------------------

class TestNormalizedStatus:
    def test_nine_members(self):
        members = list(NormalizedStatus)
        assert len(members) == 9

    def test_values_lowercase(self):
        expected = {
            "killed", "survived", "no_coverage", "nonviable",
            "timed_out", "runtime_error", "ignored", "pending", "unknown",
        }
        assert {s.value for s in NormalizedStatus} == expected

    def test_string_enum(self):
        assert NormalizedStatus.KILLED == "killed"
        assert NormalizedStatus.SURVIVED.value == "survived"


# -- Budget -------------------------------------------------------------------

def _valid_budget_dict():
    return {
        "total_seconds": 600,
        "baseline_seconds": 120,
        "mutant_seconds": 60,
        "concurrency": 2,
        "memory_mb": 4096,
        "processes": 128,
        "workspace_mb": 1024,
        "evidence_mb": 64,
    }


class TestBudget:
    def test_round_trip(self):
        d = _valid_budget_dict()
        b = Budget.from_dict(d)
        assert b.to_dict() == d

    def test_reject_missing_key(self):
        d = _valid_budget_dict()
        del d["memory_mb"]
        with pytest.raises(ValueError, match="missing required keys"):
            Budget.from_dict(d)

    def test_reject_extra_key(self):
        d = _valid_budget_dict()
        d["extra_field"] = 1
        with pytest.raises(ValueError, match="unknown keys"):
            Budget.from_dict(d)

    def test_reject_zero_value(self):
        d = _valid_budget_dict()
        d["concurrency"] = 0
        with pytest.raises(ValueError, match="positive integer"):
            Budget.from_dict(d)

    def test_reject_negative_value(self):
        d = _valid_budget_dict()
        d["memory_mb"] = -1
        with pytest.raises(ValueError, match="positive integer"):
            Budget.from_dict(d)

    def test_reject_boolean_value(self):
        d = _valid_budget_dict()
        d["processes"] = True
        with pytest.raises(ValueError, match="positive integer"):
            Budget.from_dict(d)

    def test_reject_string_value(self):
        d = _valid_budget_dict()
        d["evidence_mb"] = "64"
        with pytest.raises(ValueError, match="positive integer"):
            Budget.from_dict(d)

    def test_reject_baseline_exceeds_total(self):
        d = _valid_budget_dict()
        d["baseline_seconds"] = 700
        with pytest.raises(ValueError, match="baseline_seconds.*exceeds total_seconds"):
            Budget.from_dict(d)

    def test_reject_mutant_exceeds_total(self):
        d = _valid_budget_dict()
        d["mutant_seconds"] = 700
        with pytest.raises(ValueError, match="mutant_seconds.*exceeds total_seconds"):
            Budget.from_dict(d)

    def test_reject_non_dict(self):
        with pytest.raises(TypeError, match="budget must be a mapping"):
            Budget.from_dict([1, 2, 3])


# -- TargetDeclaration --------------------------------------------------------

def _valid_target_dict(**overrides):
    d = {
        "id": "python-core",
        "adapter": "mutmut",
        "root": ".",
        "sources": ["src/**/*.py"],
        "tests": ["tests/**"],
        "inputs": ["pyproject.toml", "setup.cfg"],
        "oracle": "pytest",
        "command": ["python", "-m", "pytest", "tests", "-q"],
        "execution_profile": "linux-isolated",
        "environment": "python-core-v1",
        "budget": _valid_budget_dict(),
    }
    d.update(overrides)
    return d


class TestTargetDeclaration:
    def test_round_trip(self):
        d = _valid_target_dict()
        td = TargetDeclaration.from_dict(d)
        assert td.to_dict() == d

    def test_round_trip_with_engine_config(self):
        d = _valid_target_dict(engine_config="pyproject.toml")
        td = TargetDeclaration.from_dict(d)
        assert td.to_dict() == d
        assert td.engine_config == "pyproject.toml"

    def test_round_trip_corpus_adapter(self):
        d = _valid_target_dict(
            adapter="patch-corpus",
            corpus=".code-forge/corpora/shell-config.json",
        )
        td = TargetDeclaration.from_dict(d)
        assert td.to_dict() == d

    def test_reject_missing_required_key(self):
        d = _valid_target_dict()
        del d["oracle"]
        with pytest.raises(ValueError, match="missing required keys"):
            TargetDeclaration.from_dict(d)

    def test_reject_unknown_key(self):
        d = _valid_target_dict(unknown_field="x")
        with pytest.raises(ValueError, match="unknown keys"):
            TargetDeclaration.from_dict(d)

    def test_reject_invalid_id(self):
        d = _valid_target_dict(id="Python-Core")
        with pytest.raises(ValueError, match="target id must match"):
            TargetDeclaration.from_dict(d)

    def test_reject_empty_sources(self):
        d = _valid_target_dict(sources=[])
        with pytest.raises(ValueError, match="sources must be nonempty"):
            TargetDeclaration.from_dict(d)

    def test_reject_empty_tests(self):
        d = _valid_target_dict(tests=[])
        with pytest.raises(ValueError, match="tests must be nonempty"):
            TargetDeclaration.from_dict(d)

    def test_accept_empty_inputs(self):
        d = _valid_target_dict(inputs=[])
        td = TargetDeclaration.from_dict(d)
        assert td.inputs == ()

    def test_reject_empty_command(self):
        d = _valid_target_dict(command=[])
        with pytest.raises(ValueError, match="command must be nonempty"):
            TargetDeclaration.from_dict(d)

    def test_reject_absolute_source_pattern(self):
        d = _valid_target_dict(sources=["/absolute/path/*.py"])
        with pytest.raises(ValueError, match="absolute pattern"):
            TargetDeclaration.from_dict(d)

    def test_reject_traversal_in_test_pattern(self):
        d = _valid_target_dict(tests=["../outside/**"])
        with pytest.raises(ValueError, match="traversal"):
            TargetDeclaration.from_dict(d)

    def test_reject_absolute_input_path(self):
        d = _valid_target_dict(inputs=["/etc/passwd"])
        with pytest.raises(ValueError, match="absolute path"):
            TargetDeclaration.from_dict(d)

    def test_reject_traversal_in_input(self):
        d = _valid_target_dict(inputs=["../outside/file"])
        with pytest.raises(ValueError, match="traversal"):
            TargetDeclaration.from_dict(d)

    def test_reject_corpus_for_native_adapter(self):
        d = _valid_target_dict(corpus="some/path.json")
        with pytest.raises(ValueError, match="corpus is forbidden"):
            TargetDeclaration.from_dict(d)

    def test_reject_missing_corpus_for_patch_corpus(self):
        d = _valid_target_dict(adapter="patch-corpus")
        with pytest.raises(ValueError, match="corpus is required"):
            TargetDeclaration.from_dict(d)

    def test_reject_non_dict_input(self):
        with pytest.raises(TypeError, match="target declaration must be a mapping"):
            TargetDeclaration.from_dict([1, 2, 3])

    def test_reject_non_list_sources(self):
        d = _valid_target_dict(sources="src/**/*.py")
        with pytest.raises(TypeError, match="sources must be a list"):
            TargetDeclaration.from_dict(d)

    def test_reject_non_string_command_entries(self):
        d = _valid_target_dict(command=["python", 42])
        with pytest.raises(TypeError, match="command entries must be strings"):
            TargetDeclaration.from_dict(d)

    def test_reject_traversal_in_root(self):
        d = _valid_target_dict(root="../outside")
        with pytest.raises(ValueError, match="traversal"):
            TargetDeclaration.from_dict(d)

    def test_reject_drive_letter_root(self):
        d = _valid_target_dict(root="C:/windows")
        with pytest.raises(ValueError, match="drive-letter"):
            TargetDeclaration.from_dict(d)

    def test_reject_lowercase_drive_letter_root(self):
        d = _valid_target_dict(root="c:/windows")
        with pytest.raises(ValueError, match="drive-letter"):
            TargetDeclaration.from_dict(d)

    def test_reject_drive_letter_source_pattern(self):
        d = _valid_target_dict(sources=["C:/abs/*.py"])
        with pytest.raises(ValueError, match="drive-letter"):
            TargetDeclaration.from_dict(d)

    def test_reject_absolute_root(self):
        d = _valid_target_dict(root="/absolute")
        with pytest.raises(ValueError, match="absolute path"):
            TargetDeclaration.from_dict(d)


# -- WorkerConfig ------------------------------------------------------------

def _valid_worker_dict(**overrides):
    d = {
        "schema_version": 1,
        "id": "worker-1",
        "concurrency": 1,
        "swap_mb": 0,
        "memory_mb": 8192,
        "pids": 512,
        "supervisor_memory_mb": 256,
        "supervisor_pids": 32,
        "delegated_cgroup_root": "/sys/fs/cgroup/forge",
        "state_root": "/var/lib/code-forge/mutation",
    }
    d.update(overrides)
    return d


class TestWorkerConfig:
    def test_round_trip(self):
        d = _valid_worker_dict()
        wc = WorkerConfig.from_dict(d)
        assert wc.to_dict() == d

    def test_reject_wrong_schema_version(self):
        d = _valid_worker_dict(schema_version=2)
        with pytest.raises(ValueError, match="schema_version must be 1"):
            WorkerConfig.from_dict(d)

    def test_reject_invalid_id(self):
        d = _valid_worker_dict(id="Worker 1")
        with pytest.raises(ValueError, match="valid identifier"):
            WorkerConfig.from_dict(d)

    def test_reject_nonone_concurrency(self):
        d = _valid_worker_dict(concurrency=4)
        with pytest.raises(ValueError, match="concurrency must be 1"):
            WorkerConfig.from_dict(d)

    def test_reject_nonzero_swap(self):
        d = _valid_worker_dict(swap_mb=100)
        with pytest.raises(ValueError, match="swap_mb must be 0"):
            WorkerConfig.from_dict(d)

    def test_reject_zero_memory(self):
        d = _valid_worker_dict(memory_mb=0)
        with pytest.raises(ValueError, match="positive integer"):
            WorkerConfig.from_dict(d)

    def test_reject_negative_pids(self):
        d = _valid_worker_dict(pids=-1)
        with pytest.raises(ValueError, match="positive integer"):
            WorkerConfig.from_dict(d)

    def test_reject_relative_cgroup_root(self):
        d = _valid_worker_dict(delegated_cgroup_root="relative/path")
        with pytest.raises(ValueError, match="absolute path"):
            WorkerConfig.from_dict(d)

    def test_reject_relative_state_root(self):
        d = _valid_worker_dict(state_root="relative/path")
        with pytest.raises(ValueError, match="absolute path"):
            WorkerConfig.from_dict(d)

    def test_reject_missing_key(self):
        d = _valid_worker_dict()
        del d["state_root"]
        with pytest.raises(ValueError, match="missing required keys"):
            WorkerConfig.from_dict(d)

    def test_reject_extra_key(self):
        d = _valid_worker_dict(extra="x")
        with pytest.raises(ValueError, match="unknown keys"):
            WorkerConfig.from_dict(d)

    def test_reject_boolean_memory(self):
        d = _valid_worker_dict(memory_mb=True)
        with pytest.raises(ValueError, match="positive integer"):
            WorkerConfig.from_dict(d)


# -- WorkerConfig admission --------------------------------------------------

class TestWorkerAdmission:
    def _worker(self, **kw):
        return WorkerConfig.from_dict(_valid_worker_dict(**kw))

    def _target(self, **kw):
        return TargetDeclaration.from_dict(_valid_target_dict(**kw))

    def test_admission_passes(self):
        w = self._worker(memory_mb=8192, pids=512,
                         supervisor_memory_mb=256, supervisor_pids=32)
        t = self._target()  # budget.memory_mb=4096, budget.processes=128
        w.check_admission([t])  # 4096+256 <= 8192, 128+32 <= 512

    def test_admission_rejects_memory(self):
        w = self._worker(memory_mb=4000, supervisor_memory_mb=256)
        t = self._target()  # budget.memory_mb=4096
        with pytest.raises(ValueError, match="admission.*memory"):
            w.check_admission([t])

    def test_admission_rejects_pids(self):
        w = self._worker(pids=100, supervisor_pids=32)
        t = self._target()  # budget.processes=128
        with pytest.raises(ValueError, match="admission.*processes"):
            w.check_admission([t])

    def test_admission_empty_targets(self):
        w = self._worker()
        w.check_admission([])  # should not raise


# -- ArtifactReference / InfrastructureError ---------------------------------

class TestArtifactReference:
    def test_round_trip(self):
        ar = ArtifactReference(
            relative_run_path="report.json",
            digest="abc123",
            bytes=1024,
        )
        d = ar.to_dict()
        assert d["relative_run_path"] == "report.json"
        assert d["digest"] == "abc123"
        assert d["bytes"] == 1024

    def test_reject_negative_bytes(self):
        with pytest.raises(ValueError, match="nonnegative"):
            ArtifactReference(
                relative_run_path="x", digest="d", bytes=-1
            )


class TestInfrastructureError:
    def test_valid_phases(self):
        for phase in ("resolve", "snapshot", "probe", "baseline",
                      "mutation", "parse", "cleanup"):
            ie = InfrastructureError(
                code="test_err",
                phase=phase,
                target_id=None,
                message="test",
                retryable=False,
                evidence_refs=(),
            )
            assert ie.phase == phase

    def test_reject_invalid_phase(self):
        with pytest.raises(ValueError, match="phase must be one of"):
            InfrastructureError(
                code="e", phase="invalid", target_id=None,
                message="m", retryable=False, evidence_refs=(),
            )

    def test_reject_empty_code(self):
        with pytest.raises(ValueError, match="code must be nonempty"):
            InfrastructureError(
                code="", phase="resolve", target_id=None,
                message="m", retryable=False, evidence_refs=(),
            )

    def test_reject_oversized_message(self):
        with pytest.raises(ValueError, match="exceeds 4096"):
            InfrastructureError(
                code="e", phase="resolve", target_id=None,
                message="x" * 4097, retryable=False, evidence_refs=(),
            )


# -- Enum checks --------------------------------------------------------------

class TestEnums:
    def test_run_state_values(self):
        assert set(RunState) == {
            RunState.COMPLETE, RunState.INCOMPLETE, RunState.UNAVAILABLE,
            RunState.CANCELLED, RunState.ERROR, RunState.INAPPLICABLE,
        }

    def test_aggregate_decision_values(self):
        assert set(AggregateDecision) == {
            AggregateDecision.PASS, AggregateDecision.FAIL,
            AggregateDecision.HOLD, AggregateDecision.NOT_APPLICABLE,
        }

    def test_baseline_state_values(self):
        assert set(BaselineState) == {
            BaselineState.PASSED, BaselineState.FAILED,
            BaselineState.EMPTY, BaselineState.UNSTABLE,
            BaselineState.UNKNOWN,
        }

    def test_cleanup_state_values(self):
        assert set(CleanupState) == {
            CleanupState.COMPLETE, CleanupState.INCOMPLETE,
            CleanupState.PENDING,
        }


class TestPatternBackslashRejection:
    def test_sources_reject_backslash(self):
        d = _valid_target_dict(sources=[r"src\**\*.py"])
        with pytest.raises(ValueError, match="backslash"):
            TargetDeclaration.from_dict(d)

    def test_tests_reject_backslash(self):
        d = _valid_target_dict(tests=[r"tests\sub\**"])
        with pytest.raises(ValueError, match="backslash"):
            TargetDeclaration.from_dict(d)


class TestDirectConstructionGuards:
    def test_direct_construction_rejects_list_command(self):
        """from_dict coerces lists to tuples, but direct construction does
        not; the tuple-of-strings check in __post_init__ must stay
        reachable for that path."""
        import dataclasses

        target = TargetDeclaration.from_dict(_valid_target_dict())
        with pytest.raises(ValueError, match="tuple of strings"):
            dataclasses.replace(target, command=["python"])
