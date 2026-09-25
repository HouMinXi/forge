"""Stryker adapter for JavaScript and TypeScript.

Native source of truth is the json reporter's mutation.json plus the
event-recorder pair under events/: onMutationTestingPlanReady and
onMutationTestReportReady. Plan mutant count must equal the report
mutant count. Baseline comes from the vitest JSON report, not from
Stryker's dry run.

Killed, Survived, Timeout and NoCoverage map onto the same-named
normalized members. Anything else is unknown. A killed verdict still
needs the tested-mutant event to name the test that killed it.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from code_forge.mutation_engines.adapters.base import (
    CapabilityReport,
    CapabilityState,
    ExecutionContext,
    InputSnapshot,
)
from code_forge.mutation_engines.adapters.python_mutmut import (
    MutmutAdapter,
    _artifact,
    _sha256_file,
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
from code_forge.mutation_engines.state import run_dir
from code_forge.mutation_engines.targets import TargetSelection

ADAPTER_ID = "js-stryker"
ADAPTER_VERSION = "1"
SUPPORTED_STRYKER = "10.0.0"
SUPPORTED_VITEST = "5.0.1"

_STATUS = {
    "Killed": NormalizedStatus.KILLED,
    "Survived": NormalizedStatus.SURVIVED,
    "Timeout": NormalizedStatus.TIMED_OUT,
    "NoCoverage": NormalizedStatus.NO_COVERAGE,
}

_PLAN_EVENT = "onMutationTestingPlanReady"
_REPORT_EVENT = "onMutationTestReportReady"
_TESTED_EVENT = "onMutantTested"


class StrykerError(Exception):
    """The Stryker run cannot be scored."""


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _event_files(events_dir: Path, method: str) -> list[Path]:
    return sorted(events_dir.glob("*-%s.json" % method))


def _package_version(node_modules: Path, name: str) -> str | None:
    path = node_modules / name / "package.json"
    data = _read_json(path)
    if data is None:
        return None
    version = data.get("version")
    return version if isinstance(version, str) and version else None


def _mutants_of(report: dict) -> list[dict]:
    files = report.get("files")
    if not isinstance(files, dict):
        return []
    mutants: list[dict] = []
    for body in files.values():
        if not isinstance(body, dict):
            continue
        listed = body.get("mutants")
        if isinstance(listed, list):
            mutants.extend(item for item in listed if isinstance(item, dict))
    return mutants


def _plan_ids(plan_event: dict) -> list[str]:
    plans = plan_event.get("mutantPlans")
    if not isinstance(plans, list):
        return []
    ids: list[str] = []
    for item in plans:
        if not isinstance(item, dict):
            continue
        mutant = item.get("mutant")
        if isinstance(mutant, dict) and mutant.get("id") is not None:
            ids.append(str(mutant["id"]))
    return ids


def map_stryker_status(native: str) -> NormalizedStatus:
    return _STATUS.get(native, NormalizedStatus.UNKNOWN)


def killed_has_test(tested: dict | None) -> bool:
    if not isinstance(tested, dict):
        return False
    killers = tested.get("killedBy")
    return isinstance(killers, list) and any(isinstance(item, str) and item for item in killers)


def baseline_from_vitest(report: dict | None) -> tuple[BaselineState, int]:
    """Vitest JSON reporter: success plus executed tests, not discovery."""
    if not isinstance(report, dict):
        return BaselineState.UNKNOWN, 0
    try:
        total = int(report.get("numTotalTests", 0))
        failed = int(report.get("numFailedTests", 0))
        passed = int(report.get("numPassedTests", 0))
    except (TypeError, ValueError):
        return BaselineState.UNKNOWN, 0
    if total <= 0:
        return BaselineState.EMPTY, 0
    if failed > 0 or passed <= 0 or report.get("success") is not True:
        return BaselineState.FAILED, passed
    return BaselineState.PASSED, passed


def reconcile(
    plan_event: dict | None, report: dict | None, report_events: int
) -> tuple[bool, str]:
    """Plan count, report count and the report event must agree.

    The event recorder writes one onMutationTestReportReady file. Zero
    or more than one means the run did not finish one report.
    """
    if plan_event is None or report is None or report_events != 1:
        return False, "missing plan event or mutation report"
    plan_count = len(_plan_ids(plan_event))
    report_count = len(_mutants_of(report))
    if plan_count == 0 or plan_count != report_count:
        return False, "plan count %d != report count %d" % (plan_count, report_count)
    return True, "plan-matches-report"


class StrykerAdapter:
    """JavaScript and TypeScript adapter driving a local Stryker binary."""

    id = ADAPTER_ID

    def probe(
        self, target: TargetDeclaration, context: ExecutionContext
    ) -> CapabilityReport:
        errors: list[InfrastructureError] = []
        node = context.approved_node
        if not node or not os.path.isfile(node):
            errors.append(
                InfrastructureError(
                    code="missing-node",
                    phase="probe",
                    target_id=target.id,
                    message="approved node binary is missing",
                    retryable=False,
                    evidence_refs=(),
                )
            )
            return CapabilityReport(
                state=CapabilityState.MISSING_DEPENDENCY,
                resolved_tool_version=None,
                evidence=(),
                errors=tuple(errors),
            )
        modules = context.extra_node_paths[0] if context.extra_node_paths else ""
        stryker = _package_version(Path(modules), "@stryker-mutator/core") if modules else None
        vitest = _package_version(Path(modules), "vitest") if modules else None
        if stryker != SUPPORTED_STRYKER or vitest != SUPPORTED_VITEST:
            errors.append(
                InfrastructureError(
                    code="unsupported-toolchain",
                    phase="probe",
                    target_id=target.id,
                    message="need stryker %s and vitest %s, got %r and %r"
                    % (SUPPORTED_STRYKER, SUPPORTED_VITEST, stryker, vitest),
                    retryable=False,
                    evidence_refs=(),
                )
            )
            return CapabilityReport(
                state=CapabilityState.UNSUPPORTED_VERSION,
                resolved_tool_version=stryker,
                evidence=(),
                errors=tuple(errors),
            )
        return CapabilityReport(
            state=CapabilityState.AVAILABLE,
            resolved_tool_version=stryker,
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
        directory = run_dir(context.state_root, context.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="forge-stryker-", dir=str(directory)))
        try:
            return self._run(target, selection, snapshot, context, directory, workspace)
        finally:
            if os.environ.get("FORGE_KEEP_WORKSPACE") != "1":
                shutil.rmtree(workspace, ignore_errors=True)

    def _run(
        self,
        target: TargetDeclaration,
        selection: TargetSelection,
        snapshot: InputSnapshot,
        context: ExecutionContext,
        directory: Path,
        workspace: Path,
    ) -> TargetResult:
        runner = MutmutAdapter()
        runner._materialize(snapshot, workspace)
        self._link_modules(workspace, context)
        self._write_config(workspace)
        identity = self._identity(snapshot, context, target)
        events = workspace / "native-events"
        vitest_out = workspace / "vitest-baseline.json"

        baseline_argv = (
            context.approved_node,
            "node_modules/vitest/vitest.mjs",
            "run",
            "--reporter=json",
            "--outputFile=/workspace/vitest-baseline.json",
        )
        code, timed_out, baseline_receipt = runner._run_sandboxed(
            context,
            baseline_argv,
            target.budget.baseline_seconds,
            workspace,
            "vitest-" + context.run_id,
            target.id,
            extra_binds=self._runtime_binds(context),
            extra_rw_binds=self._vite_bind(workspace),
        )
        vitest = _read_json(vitest_out)
        baseline_state, test_count = baseline_from_vitest(vitest)
        if timed_out or code != 0:
            baseline_state = BaselineState.UNKNOWN if timed_out else BaselineState.FAILED
        baseline_bytes = vitest_out.read_bytes() if vitest_out.is_file() else b"{}"
        baseline_ref = _artifact(directory, "vitest-baseline.json", baseline_bytes)
        baseline = BaselineRecord(
            state=baseline_state,
            test_count=test_count,
            command_receipt=baseline_receipt,
            native_evidence=(baseline_ref,),
        )
        if baseline_state is not BaselineState.PASSED:
            return self._result(
                identity, target, baseline, (), (), (baseline_receipt,), (),
                "baseline-not-passed", RunState.COMPLETE, directory,
            )

        stryker_argv = (
            context.approved_node,
            "node_modules/@stryker-mutator/core/bin/stryker.js",
            "run",
            "stryker.config.json",
        )
        _code, stryker_timed_out, stryker_receipt = runner._run_sandboxed(
            context,
            stryker_argv,
            target.budget.mutant_seconds,
            workspace,
            "stryker-" + context.run_id,
            target.id,
            extra_binds=self._runtime_binds(context),
            extra_rw_binds=self._vite_bind(workspace),
        )
        report_path = workspace / "mutation.json"
        report = _read_json(report_path)
        plan_files = _event_files(events, _PLAN_EVENT)
        report_files = _event_files(events, _REPORT_EVENT)
        plan_event = _read_json(plan_files[0]) if len(plan_files) == 1 else None
        matched, reason = reconcile(plan_event, report, len(report_files))
        report_ref = _artifact(
            directory,
            "mutation.json",
            report_path.read_bytes() if report_path.is_file() else b"{}",
        )
        plan_ref = _artifact(
            directory,
            "plan-event.json",
            plan_files[0].read_bytes() if plan_files else b"{}",
        )
        if stryker_timed_out or not matched or report is None:
            message = "stryker timed out" if stryker_timed_out else reason
            error = InfrastructureError(
                code="incomplete-stryker",
                phase="parse",
                target_id=target.id,
                message=message,
                retryable=bool(stryker_timed_out),
                evidence_refs=(report_ref, plan_ref),
            )
            return self._result(
                identity, target, baseline, (), (report_ref, plan_ref),
                (baseline_receipt, stryker_receipt), (error,),
                "incomplete-evidence", RunState.INCOMPLETE, directory,
            )

        tested = {
            str(body.get("id")): body
            for path in _event_files(events, _TESTED_EVENT)
            if (body := _read_json(path)) is not None and body.get("id") is not None
        }
        outcomes: list[Outcome] = []
        manifest: list[InventoryManifestEntry] = []
        for mutant in _mutants_of(report):
            native = str(mutant.get("status", ""))
            normalized = map_stryker_status(native)
            mutant_id = str(mutant.get("id"))
            if normalized is NormalizedStatus.KILLED and not killed_has_test(tested.get(mutant_id)):
                normalized = NormalizedStatus.UNKNOWN
            location = mutant.get("location") or {}
            start = location.get("start") if isinstance(location, dict) else None
            loc = ""
            if isinstance(start, dict):
                loc = "%s:%s" % (start.get("line", ""), start.get("column", ""))
            source = next(iter(report.get("files", {})), "")
            outcomes.append(
                Outcome(
                    mutant_id=mutant_id,
                    source_path=str(source),
                    source_digest=_sha256_file(workspace / source) if source else "",
                    location=loc,
                    operator=str(mutant.get("mutatorName", "")),
                    native_status=native,
                    normalized_status=normalized,
                    test_evidence=(),
                    native_evidence=(report_ref,),
                )
            )
            manifest.append(
                InventoryManifestEntry(
                    mutant_id=mutant_id,
                    source_path=str(source),
                    source_digest=outcomes[-1].source_digest,
                    operator=str(mutant.get("mutatorName", "")),
                    selected=True,
                    exclusion=None,
                )
            )
        generated = len(manifest)
        inventory = Inventory(
            generated=generated,
            selected=generated,
            excluded=0,
            completed=generated,
            manifest=tuple(manifest),
        )
        return self._result(
            identity, target, baseline, tuple(outcomes), (report_ref, plan_ref),
            (baseline_receipt, stryker_receipt), (), "complete", RunState.COMPLETE,
            directory, inventory,
        )

    def _runtime_binds(self, context: ExecutionContext) -> tuple[tuple[str, str], ...]:
        """Bind node and its modules inside the sandbox, not as host paths.

        A workspace symlink to a host absolute path does not resolve after
        bwrap. Vite also writes .vite-temp under node_modules, so the
        modules tree is bound read-only at a sandbox path and the workspace
        symlink points there. Writes still fail; the temp dir is created
        on the writable workspace side by a directory bind, below.
        """
        if not context.approved_node or not os.path.isfile(context.approved_node):
            raise StrykerError("approved node binary is missing")
        if not context.extra_node_paths:
            raise StrykerError("no node_modules path in the execution context")
        modules = context.extra_node_paths[0]
        if not os.path.isdir(modules):
            raise StrykerError("node_modules path is not a directory: %s" % modules)
        node_real = os.path.realpath(context.approved_node)
        node_home = str(Path(node_real).parent.parent)
        binds = [(modules, "/opt/node_modules"), (node_home, node_home)]
        link = os.path.dirname(context.approved_node)
        if link != node_home and os.path.isdir(link):
            binds.append((link, link))
        return tuple(binds)

    def _link_modules(self, workspace: Path, context: ExecutionContext) -> None:
        self._runtime_binds(context)
        os.symlink("/opt/node_modules", workspace / "node_modules")
        # Vite writes a temp bundle next to node_modules. The modules tree
        # is read-only, so give it a writable directory on the workspace.
        temp = workspace / ".vite-temp"
        temp.mkdir()
        # The bind that covers the read-only path is added in _runtime_binds
        # only after the workspace exists, so record it on the context copy
        # the caller already passes through extra_binds. Nothing to do here
        # beyond creating the directory; the bind is declared below.
        del temp

    def _vite_bind(self, workspace: Path) -> tuple[tuple[str, str], ...]:
        temp = workspace / ".vite-temp"
        temp.mkdir(exist_ok=True)
        return ((str(temp), "/opt/node_modules/.vite-temp"),)

    def _write_config(self, workspace: Path) -> None:
        config = {
            "mutate": ["src/*.js", "src/*.ts"],
            "testRunner": "vitest",
            "plugins": ["@stryker-mutator/vitest-runner"],
            "reporters": ["json", "event-recorder"],
            "eventReporter": {"baseDir": "native-events"},
            "jsonReporter": {"fileName": "mutation.json"},
            "concurrency": 1,
            "coverageAnalysis": "perTest",
            "timeoutMS": 10000,
            "vitest": {"configFile": "vitest.config.mjs"},
            "incremental": False,
        }
        (workspace / "stryker.config.json").write_text(json.dumps(config), encoding="utf-8")

    def _identity(
        self, snapshot: InputSnapshot, context: ExecutionContext, target: TargetDeclaration
    ) -> RunIdentity:
        return RunIdentity(
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
            tool_version=SUPPORTED_STRYKER,
        )

    def _result(
        self,
        identity: RunIdentity,
        target: TargetDeclaration,
        baseline: BaselineRecord,
        outcomes: tuple[Outcome, ...],
        artifacts: tuple[ArtifactReference, ...],
        receipts: tuple[CommandReceipt, ...],
        errors: tuple[InfrastructureError, ...],
        reason: str,
        run_state: RunState,
        directory: Path,
        inventory: Inventory | None = None,
    ) -> TargetResult:
        empty = artifacts[0] if artifacts else _artifact(directory, "empty.json", b"{}")
        if inventory is None:
            inventory = Inventory(generated=0, selected=0, excluded=0, completed=0, manifest=())
        return TargetResult(
            identity=identity,
            target_id=target.id,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            tool_version=SUPPORTED_STRYKER,
            run_state=run_state,
            reason_code=reason,
            baseline=baseline,
            generation=Generation(
                inventory_artifact=empty,
                completion_evidence=empty,
                extractor_version=ADAPTER_VERSION,
            ),
            inventory=inventory,
            outcomes=outcomes,
            native_artifacts=artifacts,
            command_receipts=receipts,
            infrastructure_errors=errors,
            cleanup=Cleanup(
                state=CleanupState.COMPLETE,
                owned_group_empty=True,
                owned_mounts_removed=True,
                errors=(),
            ),
        )


def make_adapter() -> StrykerAdapter:
    return StrykerAdapter()
