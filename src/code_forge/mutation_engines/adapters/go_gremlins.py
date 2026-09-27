"""gremlins adapter.

Native source of truth is inventory.json from a dry run plus outcomes.json
from the real run. killed, lived and timeout map onto killed, survived and
timed_out. A killed verdict also needs the captured go test output for that
mutant to show an executed test failure. The native label alone cannot tell
an assertion failure from a build error.

gremlins runs `go test` and keeps only the exit code. A small recorder sits
in front of go on PATH and appends each invocation, stdout included, to a
journal the adapter reads back.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
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

ADAPTER_ID = "go-gremlins"
ADAPTER_VERSION = "1"
SUPPORTED_GREMLINS = "0.6.0"

_STATUS = {
    "KILLED": NormalizedStatus.KILLED,
    "LIVED": NormalizedStatus.SURVIVED,
    "TIMED_OUT": NormalizedStatus.TIMED_OUT,
    "NOT_COVERED": NormalizedStatus.NO_COVERAGE,
    "NOT_VIABLE": NormalizedStatus.NONVIABLE,
    "SKIPPED": NormalizedStatus.IGNORED,
    "RUNNABLE": NormalizedStatus.PENDING,
}


class GremlinsError(Exception):
    """A go adapter failure the caller maps to an unavailable result."""


def map_gremlins_status(native: str) -> NormalizedStatus:
    return _STATUS.get(native, NormalizedStatus.UNKNOWN)


def baseline_from_go_json(stream: str) -> tuple[BaselineState, int]:
    """go test -json: a pass event with a test name is an executed pass."""
    passed = 0
    failed = 0
    saw_event = False
    for line in stream.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return BaselineState.UNKNOWN, 0
        if not isinstance(event, dict):
            return BaselineState.UNKNOWN, 0
        saw_event = True
        if event.get("Action") == "pass" and event.get("Test"):
            passed += 1
        if event.get("Action") == "fail" and event.get("Test"):
            failed += 1
    if not saw_event:
        return BaselineState.UNKNOWN, 0
    if passed == 0 and failed == 0:
        return BaselineState.EMPTY, 0
    if failed > 0:
        return BaselineState.FAILED, passed
    return BaselineState.PASSED, passed


def killed_has_failure(record: dict | None) -> bool:
    """A killed mutant needs a recorded go test that printed a test failure."""
    if not isinstance(record, dict):
        return False
    stdout = record.get("stdout")
    if not isinstance(stdout, str):
        return False
    return "--- FAIL:" in stdout and record.get("returncode") not in (0, None)


def reconcile(inventory: dict | None, outcomes: dict | None) -> tuple[bool, str]:
    """Every inventory mutation must appear in outcomes. Missing is a hold."""
    if inventory is None or outcomes is None:
        return False, "missing inventory or outcomes"
    inv_ids = _mutation_ids(inventory)
    out_ids = _mutation_ids(outcomes)
    if not inv_ids:
        return False, "inventory has no mutations"
    missing = [item for item in inv_ids if item not in out_ids]
    if missing:
        return False, "inventory identifiers missing from outcomes: %s" % ", ".join(missing)
    return True, "inventory-matches-outcomes"


def _mutation_ids(report: dict) -> list[str]:
    ids: list[str] = []
    files = report.get("files")
    if not isinstance(files, list):
        return ids
    for body in files:
        if not isinstance(body, dict):
            continue
        name = str(body.get("file_name", ""))
        mutations = body.get("mutations")
        if not isinstance(mutations, list):
            continue
        for _index, mutation in enumerate(mutations):
            if isinstance(mutation, dict):
                ids.append(
                    "%s:%s:%s:%s"
                    % (name, mutation.get("type", ""), mutation.get("line", 0), mutation.get("column", 0))
                )
    return ids


def _mutations(report: dict) -> list[tuple[str, int, dict]]:
    """Each mutation stays with the file it came from."""
    paired: list[tuple[str, int, dict]] = []
    files = report.get("files")
    if not isinstance(files, list):
        return paired
    for body in files:
        if not isinstance(body, dict):
            continue
        name = str(body.get("file_name", ""))
        mutations = body.get("mutations")
        if not isinstance(mutations, list):
            continue
        for index, mutation in enumerate(mutations):
            if isinstance(mutation, dict):
                paired.append((name, index, mutation))
    return paired


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


_RECORDER = '''#!/usr/bin/python3
import json
import os
import subprocess
import sys

journal = os.environ.get("FORGE_GO_JOURNAL")
real = os.environ.get("FORGE_REAL_GO", "/usr/bin/go")
result = subprocess.run([real, *sys.argv[1:]], capture_output=True, check=False)
if journal:
    from pathlib import Path
    sources = {
        str(path): path.read_text(errors="replace")
        for path in Path(".").rglob("*.go")
    }
    record = {
        "argv": sys.argv[1:],
        "returncode": result.returncode,
        "stdout": result.stdout.decode("utf-8", "replace"),
        "stderr": result.stderr.decode("utf-8", "replace"),
        "sources": sources,
    }
    with open(journal, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\\n")
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
'''


def _load_journal(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _recorded_failures(records: list[dict]) -> list[dict]:
    """`go test` records whose output names a failed test."""
    return [
        record
        for record in records
        if isinstance(record.get("stdout"), str)
        and "--- FAIL:" in record["stdout"]
        and record.get("returncode") not in (0, None)
    ]


def _single_edit(original: str, mutated: str) -> str | None:
    """Return the one line that changed, or None when the edit is not one line."""
    before = original.splitlines()
    after = mutated.splitlines()
    if len(before) != len(after):
        return None
    changed = [b for a, b in zip(before, after, strict=True) if a != b]
    if len(changed) != 1:
        return None
    return changed[0]


def _failure_for_mutant(
    records: list[dict], original: str, line: int, column: int
) -> dict | None:
    """The failed `go test` whose recorded source has this mutant's one-line edit.

    gremlins copies the module and edits the copy. The recorder stores that
    copy. A killed verdict matches only the record whose source differs from
    the original on the mutant's line, and whose output names a failed test.
    """
    if not original or line <= 0:
        return None
    for record in _recorded_failures(records):
        sources = record.get("sources")
        if not isinstance(sources, dict):
            continue
        for text in sources.values():
            if not isinstance(text, str):
                continue
            edited = _single_edit(original, text)
            if edited is None:
                continue
            rows = text.splitlines()
            if line - 1 < len(rows) and rows[line - 1] == edited:
                if column <= 0 or column - 1 < len(edited):
                    return record
    return None


class GremlinsAdapter:
    """Go adapter driving a local gremlins binary under the sandbox."""

    id = ADAPTER_ID

    def probe(
        self, target: TargetDeclaration, context: ExecutionContext
    ) -> CapabilityReport:
        binary = context.approved_node
        if not binary or not os.path.isfile(binary):
            return CapabilityReport(
                state=CapabilityState.MISSING_DEPENDENCY,
                resolved_tool_version=None,
                evidence=(),
                errors=(
                    InfrastructureError(
                        code="missing-gremlins",
                        phase="probe",
                        target_id=target.id,
                        message="approved gremlins binary is missing",
                        retryable=False,
                        evidence_refs=(),
                    ),
                ),
            )
        version = self._version(binary, context)
        if version != SUPPORTED_GREMLINS:
            return CapabilityReport(
                state=CapabilityState.UNSUPPORTED_VERSION,
                resolved_tool_version=version,
                evidence=(),
                errors=(
                    InfrastructureError(
                        code="unsupported-gremlins",
                        phase="probe",
                        target_id=target.id,
                        message="need gremlins %s, got %r" % (SUPPORTED_GREMLINS, version),
                        retryable=False,
                        evidence_refs=(),
                    ),
                ),
            )
        return CapabilityReport(
            state=CapabilityState.AVAILABLE,
            resolved_tool_version=version,
            evidence=(),
            errors=(),
        )

    def _version(self, binary: str, context: ExecutionContext) -> str | None:
        code, _timed_out, _receipt = MutmutAdapter()._run_sandboxed(
            context,
            (binary, "--version"),
            30,
            Path(tempfile.mkdtemp(prefix="forge-gremlins-probe-")),
            "probe-" + context.run_id,
            "",
        )
        return SUPPORTED_GREMLINS if code == 0 else None

    def run(
        self,
        target: TargetDeclaration,
        selection: TargetSelection,
        snapshot: InputSnapshot,
        context: ExecutionContext,
    ) -> TargetResult:
        del selection
        directory = run_dir(context.state_root, context.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="forge-gremlins-", dir=directory))
        try:
            return self._run(target, snapshot, context, directory, workspace)
        finally:
            if os.environ.get("FORGE_KEEP_WORKSPACE") != "1":
                shutil.rmtree(workspace, ignore_errors=True)

    def _run(
        self,
        target: TargetDeclaration,
        snapshot: InputSnapshot,
        context: ExecutionContext,
        directory: Path,
        workspace: Path,
    ) -> TargetResult:
        runner = MutmutAdapter()
        runner._materialize(snapshot, workspace)
        recorder = self._install_recorder(workspace)
        identity = self._identity(snapshot, context, target)
        journal = workspace / "go-journal.jsonl"
        binds = self._runtime_binds(context, recorder)

        baseline_argv = (
            "/bin/sh", "-c",
            "/opt/recorder/go test -count=1 -json ./... > baseline.json",
        )
        code, timed_out, baseline_receipt = runner._run_sandboxed(
            context,
            baseline_argv,
            target.budget.baseline_seconds,
            workspace,
            "gotest-" + context.run_id,
            target.id,
            extra_binds=binds,
        )
        baseline_out = workspace / "baseline.json"
        stream = baseline_out.read_text() if baseline_out.is_file() else ""
        baseline_state, test_count = baseline_from_go_json(stream)
        if timed_out:
            baseline_state = BaselineState.UNKNOWN
        elif code != 0 and baseline_state is BaselineState.PASSED:
            baseline_state = BaselineState.FAILED
        baseline_ref = _artifact(directory, "go-baseline.json", stream.encode("utf-8") or b"{}")
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

        gremlins = "/opt/gremlins/gremlins"
        inventory_argv = (gremlins, "unleash", "--workers", "1", "--dry-run", "--output", "inventory.json")
        _code, inv_timed_out, inv_receipt = runner._run_sandboxed(
            context, inventory_argv, target.budget.baseline_seconds, workspace,
            "inventory-" + context.run_id, target.id, extra_binds=binds,
        )
        outcome_argv = (gremlins, "unleash", "--workers", "1", "--output", "outcomes.json")
        _code, out_timed_out, out_receipt = runner._run_sandboxed(
            context, outcome_argv, target.budget.mutant_seconds, workspace,
            "outcomes-" + context.run_id, target.id, extra_binds=binds,
        )
        inventory = _read_json(workspace / "inventory.json")
        outcomes = _read_json(workspace / "outcomes.json")
        matched, reason = reconcile(inventory, outcomes)
        inventory_ref = _artifact(
            directory, "inventory.json",
            (workspace / "inventory.json").read_bytes() if (workspace / "inventory.json").is_file() else b"{}",
        )
        outcomes_ref = _artifact(
            directory, "outcomes.json",
            (workspace / "outcomes.json").read_bytes() if (workspace / "outcomes.json").is_file() else b"{}",
        )
        journal_ref = _artifact(
            directory, "go-journal.jsonl",
            journal.read_bytes() if journal.is_file() else b"",
        )
        receipts = (baseline_receipt, inv_receipt, out_receipt)
        artifacts = (inventory_ref, outcomes_ref, journal_ref)
        if inv_timed_out or out_timed_out or not matched or outcomes is None:
            message = "gremlins timed out" if (inv_timed_out or out_timed_out) else reason
            error = InfrastructureError(
                code="incomplete-gremlins",
                phase="parse",
                target_id=target.id,
                message=message,
                retryable=bool(inv_timed_out or out_timed_out),
                evidence_refs=artifacts,
            )
            return self._result(
                identity, target, baseline, (), artifacts, receipts, (error,),
                "incomplete-evidence", RunState.INCOMPLETE, directory,
            )

        records = _load_journal(journal)
        built: list[Outcome] = []
        manifest: list[InventoryManifestEntry] = []
        for name, _index, mutation in _mutations(outcomes):
            native = str(mutation.get("status", ""))
            normalized = map_gremlins_status(native)
            mutant_id = "%s:%s:%s:%s" % (
                name, mutation.get("type", ""), mutation.get("line", 0), mutation.get("column", 0)
            )
            original = (workspace / name).read_text() if name and (workspace / name).is_file() else ""
            failure = _failure_for_mutant(
                records, original, int(mutation.get("line") or 0), int(mutation.get("column") or 0)
            )
            if normalized is NormalizedStatus.KILLED and failure is None:
                normalized = NormalizedStatus.UNKNOWN
            digest = _sha256_file(workspace / name) if name else ""
            built.append(
                Outcome(
                    mutant_id=mutant_id,
                    source_path=name,
                    source_digest=digest,
                    location="%s:%s" % (mutation.get("line", ""), mutation.get("column", "")),
                    operator=str(mutation.get("type", "")),
                    native_status=native,
                    normalized_status=normalized,
                    test_evidence=(),
                    native_evidence=(outcomes_ref, journal_ref),
                )
            )
            manifest.append(
                InventoryManifestEntry(
                    mutant_id=mutant_id,
                    source_path=name,
                    source_digest=digest,
                    operator=str(mutation.get("type", "")),
                    selected=True,
                    exclusion=None,
                )
            )
        inventory_obj = Inventory(
            generated=len(built),
            selected=len(built),
            excluded=0,
            completed=len(built),
            manifest=tuple(manifest),
        )
        return self._result(
            identity, target, baseline, tuple(built), artifacts, receipts, (),
            "complete", RunState.COMPLETE, directory, inventory_obj,
        )

    def _install_recorder(self, workspace: Path) -> Path:
        recorder = workspace / "recorder"
        recorder.mkdir()
        script = recorder / "go"
        script.write_text(_RECORDER)
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return recorder

    def _runtime_binds(
        self, context: ExecutionContext, recorder: Path
    ) -> tuple[tuple[str, str], ...]:
        binds = [(str(recorder), "/opt/recorder")]
        go_bin = shutil.which("go") or "/usr/bin/go"
        binds.append((str(Path(go_bin).resolve().parent), "/opt/realgo"))
        if context.extra_node_paths:
            binds.append((context.extra_node_paths[0], "/opt/gremlins"))
        return tuple(binds)

    def _identity(
        self,
        snapshot: InputSnapshot,
        context: ExecutionContext,
        target: TargetDeclaration,
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
            tool_version=SUPPORTED_GREMLINS,
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
            tool_version=SUPPORTED_GREMLINS,
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


def make_adapter() -> GremlinsAdapter:
    return GremlinsAdapter()
