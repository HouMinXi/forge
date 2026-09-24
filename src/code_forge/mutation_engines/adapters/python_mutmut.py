"""mutmut adapter (spec Table: native source of truth = per-source .meta
files plus pytest plugin events).

Status rules that override mutmut's native labels:

- native exit 3 is labelled killed upstream but is pytest's internal
  error: it maps to runtime_error, never killed;
- native exit 1 (killed) is accepted only with plugin phase evidence of
  at least one executed assertion failure and zero harness errors;
- native exit 0 (survived) is accepted only with plugin evidence of at
  least one executed test and no failures or harness errors;
- a missing .meta for any selected source is a missing completion
  signal: the run goes on hold, never a zero-mutant pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from code_forge.mutation_engines import isolate, state
from code_forge.mutation_engines.adapters.base import (
    CapabilityReport,
    CapabilityState,
    ExecutionContext,
    InputSnapshot,
    MutationAdapter,
)
from code_forge.mutation_engines.pytest_report_plugin import PLUGIN_ID
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
from code_forge.mutation_engines.targets import TargetSelection, _glob_to_regex

ADAPTER_ID = "python-mutmut"
ADAPTER_VERSION = "1"
SUPPORTED_MUTMUT_VERSION = "3.8.0"

_PLUGIN_MODULE = "code_forge.mutation_engines.pytest_report_plugin"
_PLUGIN_SRC_ROOT = str(Path(__file__).resolve().parents[3])
_FORGE_SRC_BIND = "/opt/forge-src"

_NO_TESTS_CODES = frozenset({5, 33})
_TIMEOUT_CODES = frozenset({36, 24, -24})


class AdapterError(Exception):
    """Internal adapter failure (recorded, never raised across run())."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact(run_directory: Path, name: str, payload: bytes) -> ArtifactReference:
    state.publish(run_directory, name, payload)
    return ArtifactReference(
        relative_run_path="results/" + name,
        digest=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
    )


def _source_digest(workspace: Path, src: Path) -> str:
    path = workspace / src
    try:
        return _sha256_file(path)
    except OSError:
        return ""


def _native_label(exit_code: int | None) -> str:
    if exit_code is None:
        return "not checked"
    return {
        0: "survived",
        1: "killed",
        2: "interrupted",
        3: "killed",
        34: "skipped",
        35: "suspicious",
        37: "caught by type check",
    }.get(exit_code, "suspicious")


def _load_event(events_dir: Path, run_id: str, mutant: str | None) -> dict | None:
    name = "%s__%s.json" % (
        _safe_component(run_id),
        _safe_component(mutant) if mutant else "baseline",
    )
    path = events_dir / name
    try:
        with open(path, "rb") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("plugin") != PLUGIN_ID:
        return None
    return data


def _safe_component(value: str) -> str:
    import re

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return safe[:128] or "unknown"


def _event_identity_ok(event: dict, run_id: str, mutant: str | None) -> bool:
    return (
        event.get("final") is True
        and event.get("run_id") == run_id
        and (event.get("mutant_id") or None) == mutant
    )


def _harness_errors(event: dict) -> int:
    return int(event.get("setup_errors", 0)) + int(event.get("teardown_errors", 0)) + int(
        event.get("collection_errors", 0)
    ) + int(event.get("internal_errors", 0))


def _event_proves_killed(event: dict | None, run_id: str, mutant: str) -> bool:
    if event is None or not _event_identity_ok(event, run_id, mutant):
        return False
    return int(event.get("failed_assertions", 0)) >= 1 and _harness_errors(event) == 0


def _event_proves_survived(event: dict | None, run_id: str, mutant: str) -> bool:
    if event is None or not _event_identity_ok(event, run_id, mutant):
        return False
    return (
        int(event.get("executed", 0)) >= 1
        and int(event.get("failed_assertions", 0)) == 0
        and _harness_errors(event) == 0
    )


def _map_outcome(
    exit_code: int | None, event: dict | None, run_id: str, mutant: str
) -> NormalizedStatus:
    """Translate a native exit code plus plugin evidence (spec status rules)."""
    if exit_code is None:
        return NormalizedStatus.PENDING
    if exit_code == 3:
        # pytest internal error: never killed, whatever mutmut labels it.
        return NormalizedStatus.RUNTIME_ERROR
    if exit_code == 1:
        return (
            NormalizedStatus.KILLED
            if _event_proves_killed(event, run_id, mutant)
            else NormalizedStatus.UNKNOWN
        )
    if exit_code == 0:
        return (
            NormalizedStatus.SURVIVED
            if _event_proves_survived(event, run_id, mutant)
            else NormalizedStatus.UNKNOWN
        )
    if exit_code in _NO_TESTS_CODES:
        return NormalizedStatus.NO_COVERAGE
    if exit_code in _TIMEOUT_CODES:
        return NormalizedStatus.TIMED_OUT
    if exit_code == 34:
        return NormalizedStatus.IGNORED
    if exit_code == 37:
        return NormalizedStatus.NONVIABLE
    if exit_code == 2:
        return NormalizedStatus.RUNTIME_ERROR
    return NormalizedStatus.UNKNOWN


class MutmutAdapter:
    """Python adapter driving mutmut under the isolation supervisor."""

    id = ADAPTER_ID

    # -- probe -------------------------------------------------------------

    def probe(
        self, target: TargetDeclaration, context: ExecutionContext
    ) -> CapabilityReport:
        errors: list[InfrastructureError] = []
        evidence: list[str] = []
        python = shutil.which(context.approved_python) or (
            context.approved_python if os.path.exists(context.approved_python) else None
        )
        if python is None:
            errors.append(
                InfrastructureError(
                    code="missing-python",
                    phase="probe",
                    target_id=target.id,
                    message="approved python %r not found" % context.approved_python,
                    retryable=False,
                    evidence_refs=(),
                )
            )
            return CapabilityReport(
                state=CapabilityState.MISSING_DEPENDENCY,
                resolved_tool_version=None,
                evidence=tuple(evidence),
                errors=tuple(errors),
            )
        try:
            out = subprocess.run(
                [python, "-c", "import mutmut; print(mutmut.__version__)"],
                capture_output=True,
                text=True,
                timeout=30,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            out = None
            errors.append(
                InfrastructureError(
                    code="probe-exec-failed",
                    phase="probe",
                    target_id=target.id,
                    message="mutmut version probe failed: %s" % exc,
                    retryable=True,
                    evidence_refs=(),
                )
            )
        version = None
        if out is not None and out.returncode == 0:
            version = out.stdout.strip() or None
        if version is None:
            if not any(e.code == "probe-exec-failed" for e in errors):
                errors.append(
                    InfrastructureError(
                        code="missing-mutmut",
                        phase="probe",
                        target_id=target.id,
                        message="mutmut not importable by %s" % python,
                        retryable=False,
                        evidence_refs=(),
                    )
                )
            return CapabilityReport(
                state=CapabilityState.MISSING_DEPENDENCY,
                resolved_tool_version=None,
                evidence=tuple(evidence),
                errors=tuple(errors),
            )
        evidence.append("mutmut " + version)
        if version != SUPPORTED_MUTMUT_VERSION:
            return CapabilityReport(
                state=CapabilityState.UNSUPPORTED_VERSION,
                resolved_tool_version=version,
                evidence=tuple(evidence),
                errors=tuple(errors),
            )
        try:
            isolate.verify_isolation_support(context.cgroup_root)
        except isolate.IsolationUnavailable as exc:
            errors.append(
                InfrastructureError(
                    code="isolation-unavailable",
                    phase="probe",
                    target_id=target.id,
                    message=str(exc),
                    retryable=False,
                    evidence_refs=(),
                )
            )
            return CapabilityReport(
                state=CapabilityState.UNAVAILABLE_ISOLATION,
                resolved_tool_version=version,
                evidence=tuple(evidence),
                errors=tuple(errors),
            )
        return CapabilityReport(
            state=CapabilityState.AVAILABLE,
            resolved_tool_version=version,
            evidence=tuple(evidence),
            errors=tuple(errors),
        )

    # -- run ---------------------------------------------------------------

    def run(
        self,
        target: TargetDeclaration,
        selection: TargetSelection,
        snapshot: InputSnapshot,
        context: ExecutionContext,
    ) -> TargetResult:
        run_directory = state.run_dir(context.state_root, context.run_id)
        run_directory.mkdir(parents=True, exist_ok=True)
        receipts: list[CommandReceipt] = []
        artifacts: list[ArtifactReference] = []
        infra_errors: list[InfrastructureError] = []
        identity = RunIdentity(
            run_id=context.run_id,
            reviewed_source_id=snapshot.reviewed_source_id,
            input_manifest_digest=snapshot.manifest_digest,
            selection_digest=snapshot.selection_digest,
            config_digest=context.config_digest,
            execution_policy_digest=context.execution_policy_digest,
            toolchain_fingerprint=context.toolchain_fingerprint,
            target_id=target.id,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            tool_version=SUPPORTED_MUTMUT_VERSION,
        )

        workspace = Path(tempfile.mkdtemp(prefix="forge-mutmut-"))
        events_dir = workspace / "events"
        events_dir.mkdir()
        try:
            self._materialize(snapshot, workspace)
            sources = self._selected_sources(target, snapshot, workspace)
            self._write_mutmut_config(target, workspace, sources)

            baseline, baseline_receipt = self._run_baseline(
                target, selection, workspace, events_dir, context
            )
            receipts.append(baseline_receipt)
            baseline_event_ref = _artifact(
                run_directory,
                "events__baseline.json",
                (events_dir / ("%s__baseline.json" % _safe_component(context.run_id))).read_bytes()
                if (events_dir / ("%s__baseline.json" % _safe_component(context.run_id))).exists()
                else b"{}",
            )
            baseline_record = BaselineRecord(
                state=baseline,
                test_count=self._baseline_test_count(events_dir, context.run_id),
                command_receipt=baseline_receipt,
                native_evidence=(baseline_event_ref,),
            )

            if baseline is not BaselineState.PASSED:
                return TargetResult(
                    identity=identity,
                    target_id=target.id,
                    adapter_id=ADAPTER_ID,
                    adapter_version=ADAPTER_VERSION,
                    tool_version=SUPPORTED_MUTMUT_VERSION,
                    run_state=RunState.COMPLETE,
                    reason_code="baseline-not-passed",
                    baseline=baseline_record,
                    generation=Generation(
                        inventory_artifact=baseline_event_ref,
                        completion_evidence=baseline_event_ref,
                        extractor_version=ADAPTER_VERSION,
                    ),
                    inventory=Inventory(
                        generated=0, selected=0, excluded=0, completed=0, manifest=()
                    ),
                    outcomes=(),
                    native_artifacts=tuple(artifacts),
                    command_receipts=tuple(receipts),
                    infrastructure_errors=tuple(infra_errors),
                    cleanup=Cleanup(
                        state=CleanupState.COMPLETE,
                        owned_group_empty=True,
                        owned_mounts_removed=True,
                        errors=(),
                    ),
                )

            mutmut_receipt = self._run_mutmut(workspace, events_dir, context, target)
            receipts.append(mutmut_receipt)

            meta_by_source, missing_meta = self._collect_meta(workspace, sources)
            meta_bundle = _artifact(
                run_directory,
                "mutmut-meta.json",
                json.dumps(
                    {str(src): meta for src, meta in meta_by_source.items()},
                    sort_keys=True,
                ).encode("utf-8"),
            )
            artifacts.append(meta_bundle)

            outcomes, event_artifacts = self._build_outcomes(
                meta_by_source, events_dir, context.run_id, run_directory, workspace
            )
            artifacts.extend(event_artifacts)

            generated = sum(len(m["exit_code_by_key"]) for m in meta_by_source.values())
            completed = sum(
                1
                for m in meta_by_source.values()
                for code in m["exit_code_by_key"].values()
                if code is not None
            )
            manifest = tuple(
                InventoryManifestEntry(
                    mutant_id=key,
                    source_path=str(src),
                    source_digest=_source_digest(workspace, src),
                    operator="mutmut",
                    selected=True,
                    exclusion=None,
                )
                for src, meta in meta_by_source.items()
                for key in meta["exit_code_by_key"]
            )
            if missing_meta:
                infra_errors.append(
                    InfrastructureError(
                        code="missing-completion",
                        phase="parse",
                        target_id=target.id,
                        message="no .meta for selected sources: %s"
                        % ", ".join(sorted(str(s) for s in missing_meta)),
                        retryable=True,
                        evidence_refs=(meta_bundle,),
                    )
                )
            return TargetResult(
                identity=identity,
                target_id=target.id,
                adapter_id=ADAPTER_ID,
                adapter_version=ADAPTER_VERSION,
                tool_version=SUPPORTED_MUTMUT_VERSION,
                run_state=RunState.INCOMPLETE if missing_meta else RunState.COMPLETE,
                reason_code="missing-completion" if missing_meta else "complete",
                baseline=baseline_record,
                generation=Generation(
                    inventory_artifact=meta_bundle,
                    completion_evidence=meta_bundle,
                    extractor_version=ADAPTER_VERSION,
                ),
                inventory=Inventory(
                    generated=generated,
                    selected=generated,
                    excluded=0,
                    completed=completed,
                    manifest=manifest,
                ),
                outcomes=tuple(outcomes),
                native_artifacts=tuple(artifacts),
                command_receipts=tuple(receipts),
                infrastructure_errors=tuple(infra_errors),
                cleanup=Cleanup(
                    state=CleanupState.COMPLETE,
                    owned_group_empty=True,
                    owned_mounts_removed=True,
                    errors=(),
                ),
            )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    # -- internals ---------------------------------------------------------

    def _materialize(self, snapshot: InputSnapshot, workspace: Path) -> None:
        for entry in snapshot.files:
            destination = workspace / entry.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if entry.symlink_target is not None:
                target = Path(entry.symlink_target)
                if target.is_absolute() or ".." in target.parts:
                    raise AdapterError(
                        "snapshot symlink escapes workspace: %r -> %r"
                        % (entry.path, entry.symlink_target)
                    )
                os.symlink(entry.symlink_target, destination)
                continue
            source = Path(snapshot.root) / entry.path
            digest = _sha256_file(source)
            if digest != entry.digest:
                raise AdapterError(
                    "snapshot digest mismatch for %r: manifest %s, file %s"
                    % (entry.path, entry.digest, digest)
                )
            shutil.copyfile(source, destination)
            os.chmod(destination, entry.mode & 0o777)

    def _selected_sources(
        self, target: TargetDeclaration, snapshot: InputSnapshot, workspace: Path
    ) -> list[Path]:
        patterns = [_glob_to_regex(p) for p in target.sources]
        selected: list[Path] = []
        for entry in snapshot.files:
            if entry.symlink_target is not None:
                continue
            if any(pattern.match(entry.path) for pattern in patterns):
                selected.append(Path(entry.path))
        if not selected:
            raise AdapterError(
                "no selected sources for target %r in snapshot" % target.id
            )
        return sorted(selected)

    def _write_mutmut_config(
        self, target: TargetDeclaration, workspace: Path, sources: list[Path]
    ) -> None:
        source_args = ",".join(str(s) for s in sources)
        test_selection = " ".join(target.tests)
        (workspace / "setup.cfg").write_text(
            "[mutmut]\n"
            "source_paths=%s\n"
            "pytest_add_cli_args=-p %s\n"
            "pytest_add_cli_args_test_selection=%s\n"
            % (source_args, _PLUGIN_MODULE, test_selection)
        )

    def _sandbox_env(self, context: ExecutionContext) -> tuple[tuple[str, str], ...]:
        python_paths = [_FORGE_SRC_BIND] + [
            "/opt/extra-%d" % i for i in range(len(context.extra_python_paths))
        ]
        return (
            ("PATH", "/usr/bin:/bin"),
            ("HOME", "/workspace"),
            ("PYTHONPATH", ":".join(python_paths)),
            ("FORGE_MUTATION_EVENTS_DIR", "/workspace/events"),
            ("FORGE_MUTATION_RUN_ID", context.run_id),
        )

    def _extra_binds(self, context: ExecutionContext) -> tuple[tuple[str, str], ...]:
        binds = [(_PLUGIN_SRC_ROOT, _FORGE_SRC_BIND)]
        binds += [
            (host, "/opt/extra-%d" % i)
            for i, host in enumerate(context.extra_python_paths)
        ]
        return tuple(binds)

    def _run_sandboxed(
        self,
        context: ExecutionContext,
        argv: tuple[str, ...],
        timeout: int,
        workspace: Path,
        receipt_id: str,
        target_id: str,
    ) -> tuple[int, bool, CommandReceipt]:
        spec = isolate.SandboxSpec(
            run_id=context.run_id + "-" + receipt_id[:24],
            command=argv,
            cwd="/workspace",
            memory_mb=context.memory_mb,
            pids=context.pids,
            workspace_mb=context.workspace_mb,
            env=self._sandbox_env(context),
            workspace_host=str(workspace),
            extra_ro_binds=self._extra_binds(context),
        )
        supervisor = isolate.Supervisor(spec, context.cgroup_root)
        thread = isolate.SupervisorThread(supervisor)
        started = _utcnow()
        thread.start()
        timed_out = False
        try:
            thread.wait_started(timeout=60)
            try:
                code = supervisor.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                code = -1
        except BaseException:
            supervisor.teardown()
            thread.join(timeout=30)
            raise
        memory_peak = supervisor.read_resource_peak("memory.peak")
        supervisor.teardown()
        thread.join(timeout=30)
        python_path = shutil.which(context.approved_python) or context.approved_python
        receipt = CommandReceipt(
            id=receipt_id,
            run_id=context.run_id,
            target_id=target_id,
            executable_digest=(
                _sha256_file(Path(python_path))
                if os.path.exists(python_path)
                else _sha256_text(context.approved_python)
            ),
            argv=argv,
            started_at=started,
            finished_at=_utcnow(),
            exit_code=code,
            signal=None,
            timeout=timed_out,
            applied_limits={
                "memory_mb": context.memory_mb,
                "pids": context.pids,
                "workspace_mb": context.workspace_mb,
            },
            resource_events={"memory_peak_bytes": memory_peak},
            evidence_refs=(),
        )
        return code, timed_out, receipt

    def _run_baseline(
        self,
        target: TargetDeclaration,
        selection: TargetSelection,
        workspace: Path,
        events_dir: Path,
        context: ExecutionContext,
    ) -> tuple[BaselineState, CommandReceipt]:
        argv = (
            context.approved_python,
            "-m",
            "pytest",
            "-p",
            _PLUGIN_MODULE,
            "-q",
            "-p",
            "no:cacheprovider",
        ) + tuple(target.tests)
        code, timed_out, receipt = self._run_sandboxed(
            context, argv, target.budget.baseline_seconds, workspace,
            "baseline-" + context.run_id, target.id,
        )
        event = _load_event(events_dir, context.run_id, None)
        if timed_out:
            return BaselineState.UNKNOWN, receipt
        if event is None or not _event_identity_ok(event, context.run_id, None):
            return BaselineState.UNKNOWN, receipt
        executed = int(event.get("executed", 0))
        if executed == 0:
            return BaselineState.EMPTY, receipt
        if _harness_errors(event) > 0:
            return BaselineState.UNSTABLE, receipt
        if int(event.get("failed_assertions", 0)) > 0:
            return BaselineState.FAILED, receipt
        if code != 0:
            return BaselineState.UNKNOWN, receipt
        return BaselineState.PASSED, receipt

    def _baseline_test_count(self, events_dir: Path, run_id: str) -> int:
        event = _load_event(events_dir, run_id, None)
        if event is None or not _event_identity_ok(event, run_id, None):
            return 0
        return int(event.get("executed", 0))

    def _run_mutmut(
        self,
        workspace: Path,
        events_dir: Path,
        context: ExecutionContext,
        target: TargetDeclaration,
    ) -> CommandReceipt:
        argv = (context.approved_python, "-m", "mutmut", "run")
        _code, _timed_out, receipt = self._run_sandboxed(
            context, argv, target.budget.total_seconds, workspace,
            "mutmut-" + context.run_id, target.id,
        )
        return receipt

    @staticmethod
    def _collect_meta(
        workspace: Path, sources: list[Path]
    ) -> tuple[dict[Path, dict], list[Path]]:
        meta_by_source: dict[Path, dict] = {}
        missing: list[Path] = []
        for src in sources:
            meta_path = workspace / "mutants" / (str(src) + ".meta")
            if not meta_path.exists():
                missing.append(src)
                continue
            try:
                with open(meta_path, "rb") as handle:
                    meta = json.load(handle)
            except (OSError, ValueError):
                missing.append(src)
                continue
            if not isinstance(meta, dict) or not isinstance(
                meta.get("exit_code_by_key"), dict
            ):
                missing.append(src)
                continue
            meta_by_source[src] = meta
        return meta_by_source, missing

    @staticmethod
    def _build_outcomes(
        meta_by_source: dict[Path, dict],
        events_dir: Path,
        run_id: str,
        run_directory: Path,
        workspace: Path,
    ) -> tuple[list[Outcome], list[ArtifactReference]]:
        outcomes: list[Outcome] = []
        artifacts: list[ArtifactReference] = []
        for src, meta in meta_by_source.items():
            for key, exit_code in meta["exit_code_by_key"].items():
                event = _load_event(events_dir, run_id, key)
                event_ref: tuple[ArtifactReference, ...] = ()
                event_name = "%s__%s.json" % (
                    _safe_component(run_id),
                    _safe_component(key),
                )
                event_path = events_dir / event_name
                if event_path.exists():
                    ref = _artifact(
                        run_directory,
                        "events__" + event_name,
                        event_path.read_bytes(),
                    )
                    artifacts.append(ref)
                    event_ref = (ref,)
                outcomes.append(
                    Outcome(
                        mutant_id=key,
                        source_path=str(src),
                        source_digest=_source_digest(workspace, src),
                        location=key,
                        operator="mutmut",
                        native_status=_native_label(exit_code),
                        normalized_status=_map_outcome(exit_code, event, run_id, key),
                        test_evidence=event_ref,
                        native_evidence=(),
                    )
                )
        return outcomes, artifacts

def make_adapter() -> MutationAdapter:
    return MutmutAdapter()
