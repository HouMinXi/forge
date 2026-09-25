"""cargo-mutants adapter.

Native source of truth is mutants.json plus outcomes.json under the owned
output directory, with one log per mutant. caught, missed, timeout and
unviable map onto killed, survived, timed_out and nonviable. A killed
verdict also needs the mutant's own log to show an executed test failure.
The native label alone is not enough.

Baseline status comes from `cargo test` output, not from the mutation run.
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

ADAPTER_ID = "rust-cargo-mutants"
ADAPTER_VERSION = "1"
SUPPORTED_CARGO_MUTANTS = "27.1.0"

_STATUS = {
    "CaughtMutant": NormalizedStatus.KILLED,
    "MissedMutant": NormalizedStatus.SURVIVED,
    "Timeout": NormalizedStatus.TIMED_OUT,
    "Unviable": NormalizedStatus.NONVIABLE,
}


class CargoMutantsError(Exception):
    """The cargo-mutants adapter cannot produce a trustworthy result."""


def _read_json(path: Path) -> dict | list | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if isinstance(value, (dict, list)):
        return value
    return None


def map_cargo_status(native: str) -> NormalizedStatus:
    return _STATUS.get(native, NormalizedStatus.UNKNOWN)


def killed_has_failure(log_text: str | None) -> bool:
    """A caught mutant needs its own log to show an executed test failure."""
    if not log_text:
        return False
    return "FAILED" in log_text or "panicked at" in log_text


def baseline_from_cargo(output: str | None, returncode: int) -> tuple[BaselineState, int]:
    """Read `cargo test` text. A passing run names how many tests passed."""
    if output is None:
        return BaselineState.UNKNOWN, 0
    passed = 0
    failed = 0
    saw = False
    for line in output.splitlines():
        text = line.strip()
        if not text.startswith("test result:"):
            continue
        saw = True
        parts = text.split()
        for index, part in enumerate(parts):
            if part == "passed;" and index:
                try:
                    passed += int(parts[index - 1])
                except ValueError:
                    return BaselineState.UNKNOWN, 0
            if part == "failed;" and index:
                try:
                    failed += int(parts[index - 1])
                except ValueError:
                    return BaselineState.UNKNOWN, 0
    if not saw:
        return BaselineState.UNKNOWN, 0
    if failed > 0 or returncode != 0:
        return BaselineState.FAILED, passed
    if passed <= 0:
        return BaselineState.EMPTY, 0
    return BaselineState.PASSED, passed


def reconcile(mutants: list | None, outcomes: dict | None) -> tuple[bool, str]:
    """Every listed mutant needs one outcome, and the summary has to add up."""
    if not isinstance(mutants, list) or not isinstance(outcomes, dict):
        return False, "missing mutants.json or outcomes.json"
    listed = outcomes.get("outcomes")
    if not isinstance(listed, list):
        return False, "outcomes has no list"
    mutant_outcomes = [item for item in listed if _scenario_name(item) != "Baseline"]
    if len(mutants) != len(mutant_outcomes):
        return False, "mutants %d != outcomes %d" % (len(mutants), len(mutant_outcomes))
    if int(outcomes.get("total_mutants", -1)) != len(mutants):
        return False, "summary total does not match the mutant list"
    return True, "inventory-matches-outcomes"


def _scenario_name(outcome: dict) -> str:
    scenario = outcome.get("scenario")
    if isinstance(scenario, str):
        return scenario
    if isinstance(scenario, dict) and "Mutant" in scenario:
        return "Mutant"
    return ""


def _mutant_key(body: dict) -> str:
    span = body.get("span") or {}
    start = span.get("start") or {}
    return "%s:%s:%s:%s:%s" % (
        body.get("file", ""),
        body.get("genre", ""),
        start.get("line", 0),
        start.get("column", 0),
        body.get("replacement", ""),
    )


def _contained(root: Path, relative: str) -> Path | None:
    """A path stays inside root. Traversal and absolute paths are refused."""
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        return None
    path = root / relative
    if not path.is_file():
        return None
    return path


class CargoMutantsAdapter:
    """Rust adapter driving a local cargo-mutants binary."""

    id = ADAPTER_ID

    def probe(
        self, target: TargetDeclaration, context: ExecutionContext
    ) -> CapabilityReport:
        errors: list[InfrastructureError] = []
        tool = context.extra_node_paths[0] if context.extra_node_paths else ""
        if not tool or not os.path.isfile(tool):
            errors.append(
                InfrastructureError(
                    code="missing-cargo-mutants",
                    phase="probe",
                    target_id=target.id,
                    message="cargo-mutants binary is missing",
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
        version = self._version(tool, context)
        if version != SUPPORTED_CARGO_MUTANTS:
            errors.append(
                InfrastructureError(
                    code="unsupported-toolchain",
                    phase="probe",
                    target_id=target.id,
                    message="need cargo-mutants %s, got %r" % (SUPPORTED_CARGO_MUTANTS, version),
                    retryable=False,
                    evidence_refs=(),
                )
            )
            return CapabilityReport(
                state=CapabilityState.UNSUPPORTED_VERSION,
                resolved_tool_version=version,
                evidence=(),
                errors=tuple(errors),
            )
        return CapabilityReport(
            state=CapabilityState.AVAILABLE,
            resolved_tool_version=version,
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
        workspace = Path(tempfile.mkdtemp(prefix="forge-rust-", dir=str(directory)))
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
        del selection
        runner = MutmutAdapter()
        runner._materialize(snapshot, workspace)
        identity = self._identity(snapshot, context, target)
        binds = self._runtime_binds(context)

        baseline_out = workspace / "baseline.txt"
        baseline_argv = ("/bin/sh", "-c", "/opt/cargo/cargo test --offline > baseline.txt 2>&1")
        code, timed_out, baseline_receipt = runner._run_sandboxed(
            context, baseline_argv, target.budget.baseline_seconds, workspace,
            "cargo-" + context.run_id, target.id, extra_binds=binds,
        )
        output = baseline_out.read_text() if baseline_out.is_file() else ""
        baseline_state, test_count = baseline_from_cargo(output or None, code)
        if timed_out:
            baseline_state = BaselineState.UNKNOWN
        baseline_ref = _artifact(directory, "cargo-baseline.txt", output.encode())
        baseline = BaselineRecord(
            state=baseline_state,
            test_count=test_count,
            command_receipt=baseline_receipt,
            native_evidence=(baseline_ref,),
        )
        if baseline_state is not BaselineState.PASSED:
            return self._result(
                identity, target, baseline, (), (baseline_ref,), (baseline_receipt,), (),
                "baseline-not-passed", RunState.COMPLETE, directory,
            )

        mutants_argv = (
            "/opt/cargo-mutants/cargo-mutants", "mutants", "--no-config", "--jobs", "1",
            "--timeout", "20", "--build-timeout", "30", "--all-logs", "--output", "results",
        )
        _code, mutants_timed_out, mutants_receipt = runner._run_sandboxed(
            context, mutants_argv, target.budget.mutant_seconds, workspace,
            "mutants-" + context.run_id, target.id, extra_binds=binds,
        )
        owned = workspace / "results" / "mutants.out"
        mutants = _read_json(owned / "mutants.json")
        outcomes_doc = _read_json(owned / "outcomes.json")
        matched, reason = reconcile(
            mutants if isinstance(mutants, list) else None,
            outcomes_doc if isinstance(outcomes_doc, dict) else None,
        )
        artifacts = (
            _artifact(directory, "mutants.json", (owned / "mutants.json").read_bytes() if (owned / "mutants.json").is_file() else b"{}"),
            _artifact(directory, "outcomes.json", (owned / "outcomes.json").read_bytes() if (owned / "outcomes.json").is_file() else b"{}"),
        )
        if mutants_timed_out or not matched:
            message = "cargo-mutants timed out" if mutants_timed_out else reason
            error = InfrastructureError(
                code="incomplete-cargo-mutants",
                phase="parse",
                target_id=target.id,
                message=message,
                retryable=bool(mutants_timed_out),
                evidence_refs=artifacts,
            )
            return self._result(
                identity, target, baseline, (), artifacts,
                (baseline_receipt, mutants_receipt), (error,),
                "incomplete-evidence", RunState.INCOMPLETE, directory,
            )

        assert isinstance(outcomes_doc, dict)
        built = self._outcomes(directory, owned, outcomes_doc, artifacts[1])
        inventory = Inventory(
            generated=len(built), selected=len(built), excluded=0, completed=len(built),
            manifest=tuple(
                InventoryManifestEntry(
                    mutant_id=item.mutant_id,
                    source_path=item.source_path,
                    source_digest=item.source_digest,
                    operator=item.operator,
                    selected=True,
                    exclusion=None,
                )
                for item in built
            ),
        )
        return self._result(
            identity, target, baseline, tuple(built), artifacts,
            (baseline_receipt, mutants_receipt), (), "complete", RunState.COMPLETE,
            directory, inventory,
        )

    def _outcomes(
        self,
        directory: Path,
        owned: Path,
        outcomes_doc: dict,
        outcomes_ref: ArtifactReference,
    ) -> list[Outcome]:
        built: list[Outcome] = []
        for item in outcomes_doc.get("outcomes", []):
            if not isinstance(item, dict) or _scenario_name(item) != "Mutant":
                continue
            body = item["scenario"]["Mutant"]
            native = str(item.get("summary", ""))
            normalized = map_cargo_status(native)
            log_rel = str(item.get("log_path") or "")
            log_path = _contained(owned, log_rel)
            log_text = log_path.read_text(errors="replace") if log_path is not None else ""
            if normalized is NormalizedStatus.KILLED and not killed_has_failure(log_text):
                normalized = NormalizedStatus.UNKNOWN
            source = str(body.get("file", ""))
            source_file = _contained(owned.parent.parent, source)
            built.append(
                Outcome(
                    mutant_id=_mutant_key(body),
                    source_path=source,
                    source_digest=_sha256_file(source_file) if source_file is not None else "",
                    location="%s:%s" % (
                        (body.get("span") or {}).get("start", {}).get("line", ""),
                        (body.get("span") or {}).get("start", {}).get("column", ""),
                    ),
                    operator=str(body.get("genre", "")),
                    native_status=native,
                    normalized_status=normalized,
                    test_evidence=(
                        _artifact(
                            directory,
                            "log-%s.txt" % _mutant_key(body).replace("/", "_").replace(":", "-"),
                            log_text.encode(),
                        ),
                    ) if log_text else (),
                    native_evidence=(outcomes_ref,),
                )
            )
        return built

    def _version(self, tool: str, context: ExecutionContext) -> str | None:
        del context
        import subprocess
        try:
            out = subprocess.run(
                [tool, "mutants", "--version"],
                capture_output=True, text=True, timeout=20, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        text = (out.stdout or "").strip().split()
        return text[-1] if text else None

    def _runtime_binds(self, context: ExecutionContext) -> tuple[tuple[str, str], ...]:
        binds: list[tuple[str, str]] = []
        cargo = shutil.which("cargo")
        if cargo:
            binds.append((str(Path(cargo).resolve().parent), "/opt/cargo"))
        if context.extra_node_paths:
            tool = Path(context.extra_node_paths[0])
            binds.append((str(tool.parent), "/opt/cargo-mutants"))
        rustup = Path.home() / ".rustup"
        if rustup.is_dir():
            binds.append((str(rustup), "/opt/rustup"))
        cargo_home = Path.home() / ".cargo"
        if cargo_home.is_dir():
            binds.append((str(cargo_home), "/opt/cargo-home"))
        return tuple(binds)

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
            tool_version=SUPPORTED_CARGO_MUTANTS,
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
            tool_version=SUPPORTED_CARGO_MUTANTS,
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


def make_adapter() -> CargoMutantsAdapter:
    return CargoMutantsAdapter()
