"""Patch-corpus adapter.

Corpus entries are data, not a generator. Each selected entry is applied
once to a fresh copy of its source, then the entry's pytest node is the
oracle. A failed assertion without harness errors is killed. A clean
executed test is survived. Anything else stays unverified.

The command must be the approved Python, ``-m``, ``pytest``, then only
documented selection flags. The adapter appends the selector and owns
the report plugin.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from code_forge.mutation_engines.adapters.base import (
    CapabilityReport,
    CapabilityState,
    ExecutionContext,
    InputSnapshot,
    MutationAdapter,
)
from code_forge.mutation_engines.adapters.python_mutmut import (
    _PLUGIN_MODULE,
    AdapterError,
    _artifact,
    _event_identity_ok,
    _event_proves_killed,
    _event_proves_survived,
    _harness_errors,
    _load_event,
    _safe_component,
)
from code_forge.mutation_engines.corpus import (
    Corpus,
    CorpusEntry,
    CorpusError,
    check_old_byte_occurrence,
    compute_source_digest,
    load_corpus,
)
from code_forge.mutation_engines.schemas import (
    ArtifactReference,
    BaselineRecord,
    BaselineState,
    Cleanup,
    CleanupState,
    CommandReceipt,
    Generation,
    InfrastructureError,
    Inventory,
    InventoryManifestEntry,
    NormalizedStatus,
    Outcome,
    RunIdentity,
    RunState,
    TargetDeclaration,
    TargetResult,
)
from code_forge.mutation_engines.targets import TargetSelection

ADAPTER_ID = "patch-corpus"
ADAPTER_VERSION = "1"

_ALLOWED_FLAGS = frozenset({"-q", "-qq", "-v", "-vv", "--tb=short", "--tb=line", "-m"})


class PatchCorpusAdapter:
    """Apply reviewed corpus entries and score them with pytest."""

    id = ADAPTER_ID

    def probe(
        self, target: TargetDeclaration, context: ExecutionContext
    ) -> CapabilityReport:
        if target.adapter != ADAPTER_ID:
            return CapabilityReport(
                state=CapabilityState.UNAVAILABLE_ISOLATION,
                resolved_tool_version=None,
                evidence=(),
                errors=(
                    InfrastructureError(
                        code="adapter-mismatch",
                        phase="probe",
                        target_id=target.id,
                        message="target adapter %r is not %s" % (target.adapter, ADAPTER_ID),
                        retryable=False,
                        evidence_refs=(),
                    ),
                ),
            )
        grammar = _command_grammar_error(target.command, context.approved_python)
        if grammar is not None:
            return CapabilityReport(
                state=CapabilityState.MISSING_DEPENDENCY,
                resolved_tool_version=None,
                evidence=(),
                errors=(
                    InfrastructureError(
                        code="command-grammar",
                        phase="probe",
                        target_id=target.id,
                        message=grammar,
                        retryable=False,
                        evidence_refs=(),
                    ),
                ),
            )
        return CapabilityReport(
            state=CapabilityState.AVAILABLE,
            resolved_tool_version="pytest",
            evidence=(),
            errors=(),
        )

    def run(
        self,
        target: TargetDeclaration,
        selection: TargetSelection,
        snapshot: InputSnapshot,
        context: ExecutionContext,
    ) -> TargetResult:
        grammar = _command_grammar_error(target.command, context.approved_python)
        if grammar is not None:
            raise AdapterError(grammar)
        if target.corpus is None:
            raise AdapterError("patch-corpus target %r has no corpus path" % target.id)

        workspace = Path(tempfile.mkdtemp(prefix="forge-corpus-"))
        events_dir = workspace / "events"
        events_dir.mkdir()
        run_directory = Path(context.state_root) / "runs" / context.run_id
        run_directory.mkdir(parents=True, exist_ok=True)
        receipts: list[CommandReceipt] = []
        try:
            self._materialize(snapshot, workspace)
            corpus, corpus_artifact = self._load(target, workspace, run_directory)
            selected = _entries_for_selection(corpus, selection)
            outside = [entry.id for entry in corpus.entries if entry not in selected]
            baseline_state, baseline_receipt, baseline_event = self._run_selector(
                target, workspace, events_dir, context, None, "baseline"
            )
            receipts.append(baseline_receipt)
            baseline_count = 0
            if baseline_event is not None and _event_identity_ok(
                baseline_event, context.run_id, None
            ):
                baseline_count = int(baseline_event.get("executed", 0))
            baseline = BaselineRecord(
                state=baseline_state,
                test_count=baseline_count,
                command_receipt=baseline_receipt,
                native_evidence=(corpus_artifact,),
            )
            if baseline_state is not BaselineState.PASSED:
                return self._short(
                    target, context, baseline, corpus_artifact, receipts,
                    "baseline-not-passed",
                )
            if not selected:
                coverage = _artifact(
                    run_directory,
                    "corpus-coverage.json",
                    _coverage_payload([], outside),
                )
                return self._short(
                    target, context, baseline, coverage, receipts,
                    "corpus-limited",
                    extra_artifacts=(corpus_artifact, coverage),
                )

            outcomes: list[Outcome] = []
            artifacts: list[ArtifactReference] = [corpus_artifact]
            for entry in selected:
                outcome, event_artifact, receipt = self._score_entry(
                    entry, target, workspace, events_dir, context, run_directory
                )
                outcomes.append(outcome)
                receipts.append(receipt)
                if event_artifact is not None:
                    artifacts.append(event_artifact)
            coverage = _artifact(
                run_directory,
                "corpus-coverage.json",
                _coverage_payload(selected, outside),
            )
            artifacts.append(coverage)
            manifest = tuple(
                InventoryManifestEntry(
                    mutant_id=entry.id,
                    source_path=entry.source,
                    source_digest=entry.source_digest,
                    operator=entry.operator,
                    selected=entry in selected,
                    exclusion=None,
                )
                for entry in corpus.entries
            )
            return TargetResult(
                identity=_identity(target, context),
                target_id=target.id,
                adapter_id=ADAPTER_ID,
                adapter_version=ADAPTER_VERSION,
                tool_version="pytest",
                run_state=RunState.COMPLETE,
                reason_code="corpus-limited",
                baseline=baseline,
                generation=Generation(
                    inventory_artifact=corpus_artifact,
                    completion_evidence=coverage,
                    extractor_version=ADAPTER_VERSION,
                ),
                inventory=Inventory(
                    generated=len(corpus.entries),
                    selected=len(selected),
                    excluded=len(outside),
                    completed=len(outcomes),
                    manifest=manifest,
                ),
                outcomes=tuple(outcomes),
                native_artifacts=tuple(artifacts),
                command_receipts=tuple(receipts),
                infrastructure_errors=(),
                cleanup=Cleanup(
                    state=CleanupState.COMPLETE,
                    owned_group_empty=True,
                    owned_mounts_removed=True,
                    errors=(),
                ),
            )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _short(
        self,
        target: TargetDeclaration,
        context: ExecutionContext,
        baseline: BaselineRecord,
        corpus_artifact: ArtifactReference,
        receipts: list[CommandReceipt],
        reason: str,
        extra_artifacts: tuple[ArtifactReference, ...] = (),
    ) -> TargetResult:
        empty = Inventory(0, 0, 0, 0, ())
        return TargetResult(
            identity=_identity(target, context),
            target_id=target.id,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            tool_version="pytest",
            run_state=RunState.COMPLETE,
            reason_code=reason,
            baseline=baseline,
            generation=Generation(
                inventory_artifact=corpus_artifact,
                completion_evidence=corpus_artifact,
                extractor_version=ADAPTER_VERSION,
            ),
            inventory=empty,
            outcomes=(),
            native_artifacts=extra_artifacts or (corpus_artifact,),
            command_receipts=tuple(receipts),
            infrastructure_errors=(),
            cleanup=Cleanup(
                state=CleanupState.COMPLETE,
                owned_group_empty=True,
                owned_mounts_removed=True,
                errors=(),
            ),
        )

    def _load(
        self,
        target: TargetDeclaration,
        workspace: Path,
        run_directory: Path,
    ) -> tuple[Corpus, ArtifactReference]:
        if target.corpus is None:
            raise AdapterError("patch-corpus target %r has no corpus path" % target.id)
        path = workspace / target.corpus
        if not path.is_file():
            raise AdapterError("corpus file missing: %s" % target.corpus)
        raw = path.read_bytes()
        try:
            corpus = load_corpus(raw)
        except CorpusError as exc:
            raise AdapterError("corpus rejected: %s" % exc) from exc
        if corpus.target_id != target.id:
            raise AdapterError(
                "corpus target_id %r does not match target %r"
                % (corpus.target_id, target.id)
            )
        return corpus, _artifact(run_directory, "corpus.json", raw)

    def _score_entry(
        self,
        entry: CorpusEntry,
        target: TargetDeclaration,
        workspace: Path,
        events_dir: Path,
        context: ExecutionContext,
        run_directory: Path,
    ) -> tuple[Outcome, ArtifactReference | None, CommandReceipt]:
        source_path = workspace / entry.source
        original = source_path.read_bytes()
        applied = _apply_entry(original, entry)
        source_path.write_bytes(applied)
        try:
            state, receipt, event = self._run_selector(
                target, workspace, events_dir, context, entry, entry.id
            )
        finally:
            source_path.write_bytes(original)
        status = _status_for(state, event, context.run_id, entry.id)
        event_artifact = None
        payload = _event_bytes(events_dir, context.run_id, entry.id)
        if payload != b"{}":
            event_artifact = _artifact(
                run_directory,
                "event-%s.json" % _safe_component(entry.id),
                payload,
            )
        return (
            Outcome(
                mutant_id=entry.id,
                source_path=entry.source,
                source_digest=entry.source_digest,
                location=entry.source,
                operator=entry.operator,
                native_status=state.value,
                normalized_status=status,
                test_evidence=(event_artifact,) if event_artifact else (),
                native_evidence=(),
            ),
            event_artifact,
            receipt,
        )

    def _run_selector(
        self,
        target: TargetDeclaration,
        workspace: Path,
        events_dir: Path,
        context: ExecutionContext,
        entry: CorpusEntry | None,
        label: str,
    ) -> tuple[BaselineState, CommandReceipt, dict | None]:
        from code_forge.mutation_engines.adapters.python_mutmut import MutmutAdapter

        argv = _pytest_argv(target.command, context, entry)
        runner = MutmutAdapter()
        # Baseline and one corpus entry do not share a budget. A mutant
        # that runs for the whole baseline allowance hides a hang.
        timeout = (
            target.budget.baseline_seconds
            if entry is None
            else target.budget.mutant_seconds
        )
        code, timed_out, receipt = runner._run_sandboxed(
            context,
            argv,
            timeout,
            workspace,
            label + "-" + context.run_id,
            target.id,
            mutant_id=None if entry is None else entry.id,
        )
        mutant = None if entry is None else entry.id
        event = _load_event(events_dir, context.run_id, mutant)
        if timed_out or event is None or not _event_identity_ok(event, context.run_id, mutant):
            return BaselineState.UNKNOWN, receipt, event
        if int(event.get("executed", 0)) == 0:
            return BaselineState.EMPTY, receipt, event
        if _harness_errors(event) > 0:
            return BaselineState.UNSTABLE, receipt, event
        if int(event.get("failed_assertions", 0)) > 0:
            return BaselineState.FAILED, receipt, event
        if code != 0:
            return BaselineState.UNKNOWN, receipt, event
        return BaselineState.PASSED, receipt, event

    @staticmethod
    def _materialize(snapshot: InputSnapshot, workspace: Path) -> None:
        root = Path(snapshot.root)
        for entry in snapshot.files:
            destination = workspace / entry.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = root / entry.path
            if entry.symlink_target is not None:
                continue
            data = source.read_bytes()
            destination.write_bytes(data)
            os.chmod(destination, entry.mode & 0o777)


def _command_grammar_error(command: tuple[str, ...], approved_python: str) -> str | None:
    if len(command) < 3 or command[1] != "-m" or command[2] != "pytest":
        return "patch-corpus command must be <python> -m pytest [flags], got %r" % (command,)
    if os.path.basename(command[0]) != os.path.basename(approved_python):
        return (
            "patch-corpus command python %r is not the approved %r"
            % (command[0], approved_python)
        )
    for flag in command[3:]:
        if flag not in _ALLOWED_FLAGS and not flag.startswith("--tb="):
            return "patch-corpus command flag %r is not documented" % (flag,)
    return None


def _pytest_argv(
    command: tuple[str, ...],
    context: ExecutionContext,
    entry: CorpusEntry | None,
) -> tuple[str, ...]:
    argv = (
        context.approved_python,
        "-m",
        "pytest",
        "-p",
        _PLUGIN_MODULE,
        "-p",
        "no:cacheprovider",
    ) + command[3:]
    if entry is not None:
        argv = argv + (entry.test_selector,)
    return argv


def _entries_for_selection(corpus: Corpus, selection: TargetSelection) -> list[CorpusEntry]:
    if selection.granularity == "full" or not selection.files:
        return list(corpus.entries)
    wanted = set(selection.files)
    return [entry for entry in corpus.entries if entry.source in wanted]


def _apply_entry(original: bytes, entry: CorpusEntry) -> bytes:
    if compute_source_digest(original) != entry.source_digest:
        raise AdapterError(
            "entry %r digest does not match source %r" % (entry.id, entry.source)
        )
    count = check_old_byte_occurrence(original, entry)
    if count != 1:
        raise AdapterError(
            "entry %r old text occurs %d times in %r" % (entry.id, count, entry.source)
        )
    old = entry.old.encode("utf-8")
    new = entry.new.encode("utf-8")
    return original.replace(old, new, 1)


def _status_for(
    state: BaselineState, event: dict | None, run_id: str, mutant: str
) -> NormalizedStatus:
    if state is BaselineState.FAILED and _event_proves_killed(event, run_id, mutant):
        return NormalizedStatus.KILLED
    if state is BaselineState.PASSED and _event_proves_survived(event, run_id, mutant):
        return NormalizedStatus.SURVIVED
    return NormalizedStatus.UNKNOWN


def _event_bytes(events_dir: Path, run_id: str, mutant: str) -> bytes:
    name = "%s__%s.json" % (run_id, _safe_component(mutant))
    path = events_dir / name
    if not path.is_file():
        return b"{}"
    return path.read_bytes()


def _coverage_payload(selected: list[CorpusEntry], outside: list[str]) -> bytes:
    import json

    body = {
        "coverage": "corpus-limited",
        "selected": [entry.id for entry in selected],
        "outside_selection": outside,
    }
    return json.dumps(body, sort_keys=True).encode("utf-8")


def _identity(target: TargetDeclaration, context: ExecutionContext) -> RunIdentity:
    return RunIdentity(
        run_id=context.run_id,
        reviewed_source_id=context.run_id,
        input_manifest_digest=context.config_digest,
        selection_digest=context.config_digest,
        config_digest=context.config_digest,
        execution_policy_digest=context.execution_policy_digest,
        toolchain_fingerprint=context.toolchain_fingerprint,
        target_id=target.id,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        tool_version="pytest",
    )


def make_adapter() -> MutationAdapter:
    return PatchCorpusAdapter()
