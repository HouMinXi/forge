> Published planning snapshot, 2026-10-18. Source statements below retain their
> original dates and acceptance limits; historical "current" claims are not current-main
> verification. Private identity labels and unavailable evidence are explained in the
> [cloud start index](../../README.md). Publication grants no execution admission.

# Phase 59: Judgment and Evidence - Pattern Map

**Mapped:** 2026-10-04
**Source:** canonical `[PRIVATE_CANONICAL_CHECKOUT]`, HEAD `12b735fcefac91685dc231b1cd1434588b194a8e`
**Files classified:** 23 candidate new/modified files
**Analog coverage:** 21 / 23; 13 exact role/data-flow matches, 8 role matches, 2 without an adequate analog
**Status:** planning evidence only; no implementation, product tests, provider/native-engine invocation, independent review or acceptance credit.

FACT means inspected current source/graph. ASSUMPTION means a proposed planner choice. Exact match describes role/data flow, not satisfaction of the new security contract. All source paths below are relative to the canonical root. Proposed filenames are allocation suggestions, not existing interfaces or instructions to create seven separate production modules. The planner should consolidate responsibilities when their consumers and lifetime are identical.

The live requirements remain P59-CLOSE and INV-01..08 in `.planning/REQUIREMENTS.md`. The historical literature snapshot at 3a4a3ac is not current-source evidence. Consolidation `3451e2e63acdcb466559c018b6590cda52626eb5` is an ancestor of current HEAD (measured exit 0). Commit identity differences on old branches do not justify remerging them. Preserve the original calibration closure obligation and 59-B3/B4; this map closes none of them.

## File Classification

| Candidate new/modified file | State | Role | Data flow | Closest current analog | Match |
| --- | --- | --- | --- | --- | --- |
| `src/code_forge/investigation_contracts.py` | proposed/new | model | request-response | `reviewer_json.py:87`, `graph_triage.py:93` | role-match |
| `src/code_forge/investigation_snapshot.py` | proposed/new | service | file-I/O | `context_sources.py:376`; hash-only baseline `snapshot.py:32` | role-match |
| `src/code_forge/investigation_tools.py` | proposed/new | service | request-response | `graph_triage.py:395`, `dead_code.py:285`, `context_sources.py:811` | role-match |
| `src/code_forge/investigation_budget.py` | proposed/new | service | event-driven | no shared reservation/deadline owner; only counter primitive `llm_invoke.py:148` | no-analog |
| `src/code_forge/attempt_journal.py` | proposed/new | service | event-driven, file-I/O | `receipt.py:340`, `snapshot.py:82` | role-match |
| `src/code_forge/investigation_adapter.py` | proposed/new | service | request-response | `llm_invoke.py:2318`, `factories.py:426` | role-match |
| `src/code_forge/investigation_probe.py` | proposed/new | service | batch, file-I/O | `exec_falsify.py:360`, `mcp_jobs.py:140`, `proc.py:54` | role-match |
| `src/code_forge/llm_invoke.py` | existing/modify | service | request-response, streaming | same file: retry/correction/continuation/repair dispatch | exact |
| `src/code_forge/factories.py` | existing/modify | provider | request-response | same file: ordinary/grouped producer | exact |
| `src/code_forge/reviewer_json.py` | existing/modify only if required | utility | transform | same file: strict final-envelope validation | exact |
| `src/code_forge/machine.py` | existing/modify | store/controller | event-driven | same file: acquired/terminal receipt publication | exact |
| `src/code_forge/receipt.py` | existing/modify only if required | utility | file-I/O | same file: attempted/accepted separation | exact |
| `src/code_forge/cli.py` | existing/modify | controller | request-response | same file: ordinary/grouped setup | exact |
| `src/code_forge/mcp_jobs.py` | existing/modify only if required | controller | event-driven | same file: lifetime and terminal results | exact |
| `src/code_forge/mcp_server.py` | existing/modify only if required | controller | request-response | same file: inline/background ownership transfer | exact |
| `tests/test_raw_l1_rejections.py` | proposed/new, historical narrow lane | test | request-response | `test_machine_receipt_gate.py:668`, `test_llm_invoke.py:4624` | exact |
| `tests/test_investigation_snapshot.py` | proposed/new | test | file-I/O | `test_removed_symbol_readers.py:50`, `test_context_sources.py:113` | exact |
| `tests/test_investigation_tools.py` | proposed/new | test | request-response | `test_graph_triage.py:463`, `test_context_sources.py:53` | exact |
| `tests/test_investigation_budget.py` | proposed/new | test | event-driven | no shared multidimensional reservation fixture | no-analog |
| `tests/test_attempt_journal.py` | proposed/new | test | event-driven, file-I/O | `test_machine_receipt_gate.py:668`, `test_llm_invoke.py:4624` | role-match |
| `tests/test_investigation_probe.py` | proposed/new | test | batch | `test_exec_falsify.py`, `test_mcp_jobs.py` | role-match |
| `tests/test_llm_invoke.py` | existing/modify | test | request-response, streaming | same file: scripted format returns and retry counts | exact |
| `tests/test_machine_receipt_gate.py` | existing/modify | test | event-driven | same file: real producer/writer and terminal guards | exact |

FACT: all seven proposed production paths and `tests/test_raw_l1_rejections.py` were absent at inspection. Remaining new test names are proposed, not claimed existing fixtures. Source references to `proc.py`, `verify.py`, `snapshot.py`, `dead_code.py`, `exec_falsify.py`, `cross_repo.py` and generic inference consumers are reuse/qualification dependencies; this map does not require editing them. Any necessary change there must enter the checked plan explicitly.

## Pattern Assignments

### 1. Snapshot and source tools

**Apply to:** proposed snapshot/tool services and their tests. **Strong analog:** `context_sources.py`; supporting graph relationship source `graph_triage.py`.

Imports pattern, `context_sources.py:22-32`:

```python
from __future__ import annotations
import re
import sqlite3
import sys
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Protocol
from .graph_triage import SemAcquisition
```

FACT: project modules generally use relative imports; MCP lifecycle separately uses absolute `code_forge.proc` imports. Keep optional transport/MCP dependencies out of the core snapshot/journal layer.

Existing signatures, `context_sources.py:98-106`, `context_sources.py:376`, `context_sources.py:811-818`:

```python
class ContextSource(Protocol):
    name: str

    def snapshot_sha(self) -> Optional[str]: ...
    def facts(self, changed_files: list[str], diff_text: str) -> list[FactRow]: ...


def _lines_at(self, locations: list[str]) -> tuple[dict, dict]: ...


def gather(
    sources: list[ContextSource],
    changed_files: list[str],
    diff_text: str,
    head_sha: Optional[str],
    allow_unsnapshotted: bool = False,
    on_error: Optional[Callable[[str, str], None]] = None,
) -> GatherResult: ...
```

FACT: the `_lines_at` method belongs to `RemovedSymbolReaders`. Its `locations` come from host-collected grep results, not admitted model tool arguments. It strips ` (test)`, parses `path:line`, caches the entire current file, and returns one source line plus a nearest less-indented def/class header. `None` cache entries trigger reads; zero/negative/out-of-range or non-integer coordinates are omitted; an OSError becomes cached `[]`. Those are existing advisory semantics, not the new reader refusal protocol.

Copy the source-attribution and failure-isolation pattern, `context_sources.py:837-856`:

```python
snap = src.snapshot_sha()
result.snapshot_shas[name] = snap
if snap is not None and not allow_unsnapshotted:
    if head_sha is None or snap != head_sha:
        result.skipped.append(
            "%s: index at %s, review head %s" % (name, (snap or "?")[:12], (head_sha or "unknown")[:12])
        )
        result.skipped_sources.append(name)
        continue
acquisition_started = True
rows = src.facts(changed_files, diff_text)
result.rows.extend(rows)
```

FACT: exceptions are captured separately in `errors`; failures before acquisition also enter `refused_sources`. A source reporting `snapshot_sha=None` runs even when review HEAD is unknown. Empty rendered rows return `""` (`context_sources.py:866-867`). `FactRow` stores sorted tuple maps and returns fresh dicts (`context_sources.py:76-85`). Reuse immutable ownership, not the old unsnapshotted exemption, for INV-01/02.

**Required adaptations, ASSUMPTION:** trusted snapshot builder materializes admitted regular bytes including uncommitted post-image and allowed unchanged callers; read requests specify a bound file ID/path and positive integer range, never a source root. Return snapshot/file identity, exact coordinates and explicit completed/truncated/refused/unavailable status. Freeze finite byte/line/search limits in the plan; reject bool, null, NaN/Infinity and malformed coordinates before dispatch. Deleted/base/cross-repo views need explicit identity; do not reopen live source after initial binding. `snapshot.py:32-46` stores hashes, not file bytes, and its text hash normalizes whitespace (`snapshot.py:165-177`); it is not a byte-fidelity snapshot service.

**Concrete consumers:** CLI outlet-C gathering `cli.py:3424`, ordinary review gathering `cli.py:4036`, `GraphTriageSource.advisory_runner` at `context_sources.py:166`. Reader rows currently feed falsifier context and are intentionally excluded from L1 context rendering (`context_sources.py:885-889`). Preserve that behavior unless a checked plan explicitly changes it.

**Tests to copy:** `_Src` and `_row`, `tests/test_context_sources.py:53-70`; stale/matching/unknown HEAD controls at `113-140`. The tracked private tree fixture at `tests/test_removed_symbol_readers.py:50-81` supplies a removed constructor parameter, production self-read, test caller and ignored comment. Use its data shapes; qualification must also cover symlink swaps, special files, traversal, concurrent source changes and no outside canary bytes.

### 2. Graph callers/callees and typed acquisition

**Apply to:** proposed contracts/tools services. **Strong analog:** typed `SemAcquisition`, `graph_triage.py:93-114`; SQL and relationship navigation are supporting patterns.

```python
@dataclass(frozen=True)
class SemAcquisition:
    status: Literal[
        "completed",
        "completed_empty",
        "disabled",
        "unavailable",
        "inapplicable",
        "execution_error",
        "timeout",
        "parse_error",
        "schema_error",
        "impact_incomplete",
        "configuration_error",
    ]
    entities: list[dict] = field(default_factory=list)
    diagnostic: str = ""
    capability: Optional[SemCapability] = None
    impact_complete: Optional[bool] = None
```

FACT: `completed` currently includes `disabled` (`graph_triage.py:113-114`); that advisory policy cannot establish an enabled investigation capability.

Full existing interfaces:

```python
# graph_triage.py:395 and 509-513
def _run_graphdb(db_path: str, diff_files: list[str]) -> SemAcquisition: ...
def find_entity_dependents(
    entity_name: str,
    file_path: str,
    repo_root: Path,
) -> list[str]: ...


# dead_code.py:285-289
def _live_callers(
    cursor: sqlite3.Cursor,
    target_name: str,
    module_name: str,
) -> list[LiveCaller]: ...
```

Copy parameter binding and structured failure, `graph_triage.py:415-420`, `445-451`:

```python
cursor.execute(
    "SELECT id, kind, name, qualified_name, file_path, "
    "line_start, line_end FROM nodes WHERE file_path LIKE ?",
    ("%%%s" % file_path,),
)
# Existing exception boundary:
except (sqlite3.Error, OSError) as exc:
    logger.warning("graph.db read error: %s", exc)
    return SemAcquisition("execution_error", diagnostic=str(exc), impact_complete=False)
```

FACT: wildcard suffix matching is existing triage behavior, not an adequate hostile-path/qualified-symbol lookup. INV-02 should use admitted exact identities, host-owned SQL, result limits and a source-bound immutable view. Edges carry `file_path`, `line`, `target_resolution` and `confidence`; preserve unresolved/partial scope and differentiate source function coordinates from call-site coordinates.

**Unsafe direct reuse:** `find_entity_dependents` returns `[]` for absence and SQLite/OSError (`graph_triage.py:554-560`), so it cannot represent unavailable versus no indexed edges. `_live_callers` fetches caller node `line_start` (`dead_code.py:309-315`), then calls `_is_dead_call_site` (`317`); the Python detector reopens live bytes (`dead_code.py:152`). This is not a frozen graph/source query nor necessarily the actual invocation line. Current SQLite helpers use `mode=ro` (`context_sources.py:200`, `graph_triage.py:410,546`); use an immutable coherent bound view for investigation, not a shared read connection that may create sidecars.

**Graph navigation measured here:** bare target `gather` includes `mcp_jobs.py:194,203`, which source shows are `asyncio.gather`, not `context_sources.gather`. Exact-qualified-only lookup also misses bare imported `llm_invoke`, `write_receipts` and factory calls. Index resolution is navigation evidence; verify actual source before treating an edge as a consumer. A static empty edge set never proves no runtime caller.

**Tests:** `tests/test_graph_triage.py:463-494` shows mock SQLite fixtures but its assertion only verifies connection use. For new capability qualification use a real private SQLite database with explicit CALLS/IMPORTS_FROM rows, same-named symbols, unresolved imports, exact `edges.line`, missing/stale/partial index, wildcard-bearing input and immutable read behavior. Keep the shared graph unchanged.

### 3. L1 adapter, strict final parsing and narrow raw repair

**Apply to:** proposed typed adapter/contracts, existing `llm_invoke.py`, `factories.py`, `reviewer_json.py`, and raw/parser tests. **Strong analog:** factory-owned invocation/fold plus existing format transport.

Full current public signature, `llm_invoke.py:1306-1315`:

```python
def llm_invoke(
    prompt: str,
    backend: Optional[BackendConfig] = None,
    timeout_s: Optional[int] = None,
    expected_keys: frozenset[str] | None = None,
    max_attempts: int = 5,
    initial_delay_s: float = 2.0,
    continuation_breaker: "TruncationBreaker | None" = None,
    retry_timeout: bool = False,
) -> LLMResult: ...
```

FACT: `backend=None` refuses before spawn (`1346-1355`); timeout resolution is delegated (`1356`). Expected keys default to the review envelope on API paths; falsifier uses its separate keys. Generic inference also serves contract summaries, daemon/runtime advisories, doctor, tests and falsifier, so keep tools behind an explicit review-only adapter rather than enabling them for every `llm_invoke` caller.

Full factory setup interface, `factories.py:304-323`:

```python
def build_l1_provider(
    engine: str,
    resolved: "ResolvedReview",
    backend=None,
    conventions_digest: str = "",
    post_image: str = "",
    graph_impact_context: str = "",
    contract_spec: str = "",
    focus_spec: str = "",
    manifest_spec: str = "",
    breaker=None,
    max_attempts: int = 5,
    initial_delay_s: float = 2.0,
    continuation_breaker=None,
    split_context: str = "",
    context_sources_text: str = "",
    reviewed_repositories: dict[str, str] | None = None,
    retry_timeout: bool = False,
    pass_stagger_s: float = 0.0,
) -> "_L1Call": ...
def build_grouped_l1_provider(
    engine: str,
    group_specs: list[dict],
    **shared_kwargs,
) -> "Callable": ...
```

FACT: provider return remains `(findings, excerpts, Usage, duration_s)`, with instance-owned `attempted_excerpts`. `engine="stub"` has a trusted setup marker (`factories.py:338-350`); empty diff resets attempted state and returns zeros (`369-372`); API passes run concurrently, CLI passes serially (`424-473`); results fold in trusted qodo/expert/adversarial order (`484`). API duration is parallel wall time (`699-705`), not sum of work durations. The plan must distinguish those metrics.

Copy final acceptance sequence, `factories.py:547-556`:

```python
raw_snapshot = _snapshot_raw_response(response)
try:
    validated = validate_reviewer_json(response)
    require_l1_excerpt_evidence(
        validated, diff_text, reviewed_repositories=reviewed_repositories
    )
    if reviewed_repositories is not None:
        from .receipt_scope import validate_scoped_paths
        validate_scoped_paths(validated, reviewed_repositories)
```

FACT: `validate_reviewer_json(raw: str | dict) -> dict` (`reviewer_json.py:568`) rejects non-object envelopes, malformed fields, bool/non-int/non-positive/reversed coordinates, empty clean evidence and wrong line counts. It mutates data by hoisting/filtering excerpts; capture acquired raw before this. Empty findings alone do not attest clean coverage (`factories.py:629-687`). `require_l1_excerpt_evidence(data: dict, diff_text: str, *, reviewed_repositories: dict[str, str] | None = None) -> None` (`reviewer_json.py:439-450`) is publisher-scope specific; preserve advisory consumers' existing contract.

**Proposed first dialect, ASSUMPTION from parallel research/parent:** explicit non-streaming Chat Completions tool loop using current urllib transport mechanics. Existing `_invoke_openai(prompt: str, backend: BackendConfig, api_key: str, timeout_s: int) -> tuple[str, dict]` (`llm_invoke.py:2318-2323`) only sends one user message (`2334-2337`) and extracts `message.content` (`2402-2408`); it does not return a typed tool request. Reuse URL/header/parameter/error/deadline policy, not the text-only return contract. Preserve complete assistant tool-call/result correlations, explicit refusal, missing content versus tool-only reply, shared reservations and strict final normalization. Selection/conformance remains a checked plan decision; no SDK or provider qualification ran here.

**Narrow raw repair:** final `kind="no_json"` currently raises generic `LLMInvokeError` after fence stripping (`llm_invoke.py:2124,2167-2174`). Factory invoke-failure fold (`factories.py:487-514`) creates INFRA without payload; `_raw_response_data` accepts only parsed dicts (`46-60`). A narrowly admitted final-text carrier and guarded direct-value wrapper can use this existing attempted lane. Whole text means pre-strip whitespace/fences. Preserve retry count/error metadata; successful correction is not made an attempted schema failure. Do not reinterpret a no-text transport error as acquired text.

FACT: the historical addendum requires declared nested JSON compatibility and acyclicity before deepcopy/append, explicit existing NaN/Infinity compatibility, and refusal of unknown objects/cycles/excess recursion without repr/copy. This is not permission to change global accepted JSON semantics. The narrow lane cannot retain recovered rejected replies, failed repair history or all physical attempts and therefore does not close INV-06/07.

**Actual setup callers:** ordinary `cli.py:4121`, grouped `cli.py:4220`, group child factory `factories.py:737`, cross-repo primary `cross_repo.py:419`. Cross-repo supplies `reviewed_repositories` and joint diff; any first-profile refusal there must be explicit, not bypassed. Outlet-C has a separate review path (`cli.py:3435`); preserve unsupported/non-tool behavior until admitted.

**Tests:** correction mock `tests/test_llm_invoke.py:4624-4656` records exactly two changed prompts for completed malformed JSON at `max_attempts=5`; continued EOF case starts at `4658`. Copy their lower-format seam, not a mocked entire adapter, when testing actual parsing. Real factory/machine/writer fixture `tests/test_machine_receipt_gate.py:668-741` sets hostile payload pass labels, host-owned attribution, bounded synthetic source, operation counters and an actual writer. Its receipt assertion (`838-864`) checks identity, status, bytes and zero forbidden operations; healthy public control (`867-881`) checks nine transport calls and write/save/hook order. These tests were inspected, not executed.

### 4. All-attempt journal, accounting and receipt authority

**Apply to:** proposed journal/budget, existing transport/factory/machine/writer and journal tests. **Strong analog:** `receipt.py` for owned attribution and accepted/audit separation; no current complete attempt journal.

Current transport records, `llm_invoke.py:38-59`:

```python
@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    content: Any
    usage: Usage = Usage()
    duration_s: float = 0.0
    is_truncated: bool = False
```

FACT: these defaults cannot distinguish unknown from observed zero. API usage missing/null becomes `{}` (`llm_invoke.py:2407-2408`) then token keys default to zero (`2052-2055`). Key-present null counts can still pass through the unvalidated dataclass. A new journal needs nullable typed counts, observation/completeness scope and finite validation. Do not assign a round total divided by three to physical sends. Cached/reasoning usage needs dialect-specific subset attribution; the existing cached-token comments are not a universal billing contract.

Success and failure acquisition sites to cover (all source-bound):

| Physical/logical action | Site | Present behavior | Journal requirement |
| --- | --- | --- | --- |
| initial API dispatch/retry | `llm_invoke.py:2038,2046` | per-format content/usage, final LLMResult | start/outcome before/after every observable owned send |
| completed JSON correction | `llm_invoke.py:2148-2165` | replaces prompt; adds initial syntax usage on success (`2278-2283`) | retain both acquired raw replies even if correction succeeds |
| failed excerpt repair | `llm_invoke.py:1659,1666-1667` | returns original data and `Usage(), 0.0` | retain attempted send, original failure, nullable usage/time |
| failed continuation | `llm_invoke.py:1791,1824-1845` | logs last failure; proceeds | separate each failed send/raw/usage and successful sibling |
| successful continuation | `llm_invoke.py:1866-1876` | sums initial truncation plus winning continuation | do not silently omit earlier failed continuations |
| wider retry | `llm_invoke.py:1928,1945-1951,1967-1971` | failure log or only wider-success usage | include initial/continuation/wider ancestry once |
| partial SSE then error | `llm_invoke.py:575,669-675` | content prefix discarded for acceptance | journal exact acquired partial separately from accepted result |
| ordinary pass result/error | `factories.py:484-545` | failures become INFRA; successful usage folded | retain both InvokeResult and error outcomes before parsing/fold |
| grouped pass wrapping | `factories.py:765-767` | attaches host-owned group scope | shared run budget and causal group/pass IDs, no multiplied allowance |

Existing helper signatures: `_repair_missing_excerpts(parsed: dict, prompt: str, backend: BackendConfig, timeout_s: int) -> tuple[dict, Usage, float]` (`llm_invoke.py:1640-1645`); `_continue_truncated(prompt: str, backend: BackendConfig, api_key: str, timeout_s: int, truncated: "_TruncatedResponse", expected_keys: frozenset[str] | None, budget: int = 2, breaker: "TruncationBreaker | None" = None)` (`1725-1734`); `_retry_with_more_headroom(prompt: str, backend: BackendConfig, api_key: str, timeout_s: int, expected_keys: frozenset[str] | None)` (`1889-1895`). Their separate recovery budgets are existing behavior, not a run-level finite accounting boundary.

Cumulative stream usage already replaces the last snapshot (`llm_invoke.py:627-628`); copy replacement semantics only for a declared cumulative dialect. Future controls must prove 2,5,9 -> 9 and repeated 9 -> 9; declared incremental 2,3,4 -> 9. Missing failure usage remains unknown; preserve known subtotals with incomplete coverage.

Host-owned grouped attribution, `reviewer_json.py:86-100`:

```python
@dataclass(frozen=True)
class ReviewGroupScope:
    name: str
    diff_sha256: str
    source_files: tuple[str, ...]


class GroupedReviewAttempt(dict):
    def __init__(self, payload: dict, scope: ReviewGroupScope):
        super().__init__(payload)
        self.group_scope = scope
```

Copy attribution outside payload fields, not a model-supplied run/pass/group name. Factory derives group diff SHA256 from the group's resolved diff and source files from host specs (`factories.py:732-735`). Writer top-level cycle/pass and optional group_scope are host-created (`receipt.py:351-363`); raw wrappers must not become accepted excerpts.

Full existing writer contract, `receipt.py:153-168`:

```python
def write_receipts(
    receipts_dir: Path,
    round_index: int,
    l1_findings: list[StateFinding],
    diff_sha256: str,
    source_files: list[Path],
    cwd: Path,
    diff_files: dict[str, list[int]] | None = None,
    diff_text: str | None = None,
    reviewer_excerpts: list[dict] | None = None,
    manifest: Optional[EnvManifest | ManifestTier | dict[str, Any] | str] = None,
    manifest_tier: Optional[ManifestTier] = None,
    exec_evidence: Optional[dict[str, Any] | str] = None,
    attempted_excerpts: list[dict] | None = None,
    reviewed_repositories: dict[str, str] | None = None,
) -> list[Path]: ...
```

FACT: writes accepted `receipt-cNpM.json`, then attempted JSON beneath `attempted/` (`receipt.py:336-366`). Discovery only loads nonrecursive `receipt-*.json` (`verify.py:288`). `None`/empty attempted list means no attempted artifacts; no automatic attempted pruning is present. `json.dumps` currently permits NaN/Infinity; type/cycle failure is not an OSError. Writer is not an append-only crash-safe journal and creates files directly. `snapshot.py:89-91` offers temp-write/replace precedent, but its shared `.tmp` name and absent fsync are not sufficient for parallel durable attempt starts/outcomes.

Shared publication signature: `StateMachine._publish_l1_receipts(self, round_index: int, l1_findings: list[StateFinding], l1_excerpts: list[dict], *, exec_evidence: dict | str | None) -> None` (`machine.py:1504-1511`). Both ordinary (`1639`) and terminal (`1573`) paths use it. The terminal path passes `exec_evidence=None`, preserving current acquisition without borrowing old execution credit. It passes trusted source hash, source files, round/pass excerpts and attempted data to the actual writer (`1519-1532`); OSError becomes a retained receipt error (`1535-1538`). `TimeoutBreaker` persists non-PASS before raising (`1191-1205`). Preserve original failure and healthy siblings when an optional audit sink fails; missing mandatory provenance must still deny authority.

**Budget reuse limit:** `TruncationBreaker` uses one lock around a monotonic shared count (`llm_invoke.py:165-189`). Reuse its atomicity discipline, not its threshold model as an all-resource budget. Proposed run owner must reserve call/tool/bytes/workers before dispatch, use one absolute deadline, define zero remaining as refusal, and reject missing/nonfinite limits rather than default unlimited. Freeze refund/unknown-completion semantics before tests; do not replay crash-unknown work blindly.

### 5. Host-owned data-only recipe and cancellation

**Apply to:** proposed probe and lifecycle/test integration. **Strong analog:** `proc.py` signal decision plus MCP supervised cleanup; historical `exec_falsify.py` supplies output/env primitives only.

Existing process signatures, `proc.py:54,72,98`:

```python
def group_of(proc: Signalable) -> int | None: ...
def terminate_group_or_child(proc: Signalable, pgid: int | None) -> None: ...
def kill_group_or_child(proc: Signalable, pgid: int | None) -> None: ...
```

Copy the ownership-sensitive group branch, `proc.py:110-119`:

```python
if pgid is not None and pgid == proc.pid:
    try:
        os.killpg(pgid, signal.SIGKILL)
        return
    except OSError:
        pass
try:
    proc.kill()
except OSError:
    pass
```

FACT: `group_of` returns None on Windows/OSError. Group signals only target a group whose ID equals the owned child's PID. Caller keeps wait/liveness handling. This avoids self-signalling; it is not a certificate that escaped descendants were killed.

Existing async interfaces, `mcp_jobs.py:87-94,140-143`:

```python
def start_job(
    comm_task: asyncio.Task[Any],
    proc: asyncio.subprocess.Process,
    tempfile_path: str | None = None,
    focus_tempfile_path: str | None = None,
    stderr_log_path: str | None = None,
    max_lifetime_s: float | None = None,
) -> str: ...


async def _terminate_and_reap(
    proc: asyncio.subprocess.Process,
    grace: float = 5.0,
) -> None: ...
```

FACT: None lifetime is legacy-unbounded (`mcp_jobs.py:100-101`); current public transfer passes `cap` (`mcp_server.py:774-780`). Cleanup attempts TERM, bounded wait, KILL, bounded wait (`mcp_jobs.py:158-177`); it returns early if the parent already has returncode (`156-157`). No descendant-empty result is returned. Watchdog captures failed versus completed separately; timeout-race branch discloses lost stdout (`293-308`). Preserve these diagnostic distinctions but add measured owned-domain cleanup for INV-03, rather than equating parent exit with cleanup success.

`exec_falsify.py:360-368` uses a new session, stdin DEVNULL, file stdout/stderr and cleaned env. `_read_file_tail(f: Any, limit: int = 2000) -> str` (`141-149`) bounds the returned tail, not total spool bytes. `_clean_subprocess_env(extra_env: Optional[dict[str, str]] = None) -> dict[str, str]` (`114-138`) is a precedent, not permission to inherit arbitrary env. `_resolve_command` defaults to `python -m pytest` (`438-448`), and copied repository code executes natively. Do not expose that command/runner as the initial data-only experiment.

**Proposed useful recipe, ASSUMPTION from parallel research/parent:** immutable host-owned review-envelope replay of bounded raw attempt plus frozen diff through the actual parser/schema/excerpt checks with `cwd=None`. This observes whether the exact acquired data satisfies existing predicates; it does not execute repository source and cannot attest runtime correctness or a clean review. Inputs are bounded data references owned by the snapshot/journal, not model-chosen code, executable, module, cwd, SQL or env. Worker must load trusted installed code under a pinned origin with no repository import shadowing; stdin/args are data. Recipe ID/version/runtime/input hashes and measured output/failure/time/cleanup belong in the attempt record.

Actual reusable predicate, `verify.py:1040-1058`:

```python
def validate_excerpts_against_diff(
    diff_text: str, excerpts: list[dict], *, cwd: Path | None = None
) -> list[str]:
    if not diff_text or not excerpts:
        return []
    post_image, hunk_map, exempt_files = _diff_validation_context(diff_text, cwd=cwd)
    errors: list[str] = []
    for exc in excerpts:
        assessment = assess_excerpt_evidence(exc, hunk_map, post_image, exempt_files)
        if assessment.status is ExcerptStatus.INVALID:
            errors.append(assessment.diagnostic or "invalid excerpt evidence")
    return errors
```

FACT: `cwd=None` prevents Git blob supplementation (`verify.py:961`); available context is the frozen diff. Empty input/excerpts yields no literal errors, not full clean evidence. The recipe must report that limited result separately and use the real schema/root-evidence checks; it cannot infer PASS from an empty error list. Bounds/CPU/memory/pids/network/env/fds/output/spool/concurrency/cancellation and descendant cleanup still require independent qualification. No qualified recipe supervisor exists in the inspected analogs.

**Existing test leads:** `tests/test_exec_falsify.py` has environment identity, timeout descendant cleanup, secret stripping and output flood cases; `tests/test_mcp_jobs.py:984,1012,1073` has reap timeout, real process-group kill and new-session controls. These are native execution tests and were not run here. Reuse their ownership/observation shapes in isolated recipe controls; do not treat their historical success as qualification of the new worker.

## Shared Patterns and Reuse Hazards

| Concern | Reuse | Adapt or reject |
| --- | --- | --- |
| Identity | frozen dataclasses, host pass ordering, GroupedReviewAttempt scope | untrusted tool/raw fields never select run/pass/group authority |
| Validation | strict final object and excerpt evidence chain | tool-only content is an intermediate event, not an empty final review |
| Graph | bound parameters, typed SemAcquisition errors | live dead-code reads, suffix LIKE, list-only failure API, mode=ro shared view |
| Raw | pre-validation snapshot and attempted subdirectory | deepcopy before nested admission; final-only audit cannot replace attempt journal |
| Usage | dialect-specific raw fields and last cumulative stream value | default zeros, lost failed attempts, cost estimates without known coverage |
| Time/concurrency | monotonic durations, ordinary API wall time, shared counter lock | fresh timeout per send, future cancellation as worker termination, per-pass full budgets |
| Publication | shared normal/terminal writer and nonrecursive receipt discovery | direct writes or serializer failures as proof of durable journal success |
| Process | new session, child/group signal discrimination, bounded wait | parent exit or KILL request as proof of descendant cleanup |
| Experiment authority | frozen-diff literal predicate with cwd=None | empty literal errors as clean-review credit; arbitrary pytest/native runner |

No HTTP authentication middleware analog is relevant here. Backend credentials remain transport-only and must not enter recipe env, data previews or raw metadata. Log module style follows `logging.getLogger(__name__)`; operator progress uses existing `progress.emit`. Do not replace exact stored raw with a safe preview/hash, and do not publish raw artifacts through accepted-receipt discovery.

## Source-Bound Defect Leads

These are current source observations and future test hypotheses, not runtime-confirmed product defects or independent review findings. Parent owns issue-stream adjudication/publication. No live calls or product control runs were performed.

| Lead | Exact source evidence | Observable risk / distinguishing control |
| --- | --- | --- |
| final rejected text absent | `llm_invoke.py:2124,2167-2174`; `factories.py:487-514,46-60` | Replay fenced malformed terminal reply and direct non-dict values through actual factory/machine/writer; require exact pre-strip artifact without accepted excerpts. |
| failed repair hidden in totals | `llm_invoke.py:1666-1667` | Script success lacking excerpts then failed repair; owned sends must count two, known first usage retained, failed usage unknown rather than zero. |
| failed continuations/wider recovery omitted | `llm_invoke.py:1824-1845,1866-1876,1967-1971` | Script initial truncation, failed continuation with usage, successful continuation or wider retry; retain every attempt once and reconcile known subtotals. |
| graph errors look empty | `graph_triage.py:554-560` | Broken private SQLite and genuinely empty private graph must have different typed outcomes. |
| relationship location is function start | `dead_code.py:309-317` | Private graph caller def at line10 with actual edge at line20 under a dead guard: distinguish definition line from invocation line and bound dead-code view. |
| current reader lacks frozen authority | `context_sources.py:293-294,395-402` | Race source content/symlink after snapshot creation; new reader must return frozen bytes or refusal, never newly opened outside data. |
| graph index ambiguity | actual bare `gather` edges at `mcp_jobs.py:194,203` resolve in source to asyncio | Caller qualification must reject bare-name false bindings; qualified-only search must not silently omit imported consumers. |
| cleanup not measured after parent exit | `mcp_jobs.py:156-157,175-177` | Owned fixture parent exits with lingering child; require observed owned domain empty, separate cleanup-failed from original exit/timeout. |

These leads supplement the already scoped narrow raw-rejection lane. They do not authorize repairing unrelated modules during that three-file delivery.

## Small Plan Slices

ASSUMPTION: slices are dependency guidance within existing phase59, not execution admission or additional milestones.

1. Recover P59-CLOSE evidence in parallel with contract design. Preserve calibration 18/20 original gate versus historical13/20, B3 capped arm/canary and B4 matched report; use the dedicated acceptance-recovery work rather than copying historical branch code.
2. Independently admit the narrow final-raw lane on current base if desired: only `llm_invoke.py`, `factories.py`, new raw tests; guarded declared-value compatibility before copy; actual factory/machine/writer controls and exact-site negative cuts. Do not widen it into the journal.
3. Freeze owned run/attempt/tool IDs, observable boundary, source/capability view, raw retention and nullable usage, finite numeric budgets and partial verdict policy. Implement shared reservation/journal before enabling new tools. Three real consumers are L1 acquisition, CLI/MCP operator diagnosis and receipt/verifier/evaluation reconstruction.
4. Implement one snapshot read/relationship vertical slice through both ordinary/grouped factories and the selected adapter. Denied/stale/incomplete work cannot attest clean results; preserve explicit unsupported paths for other consumers.
5. Qualify and enable the actual fixed recipe: known-answer wrong-literal versus source-bound correct-literal data, malformed/empty/hostile input, N/N+1 limits, output flood, cancellation and observed cleanup. Predicate observation has no runtime or landing authority.
6. Public CLI/MCP and machine/receipt qualification: retain terminal siblings, failed sink states and source-safe recovery. Perform exact-site defect injection, executed-line coverage, independent reviews and exact-source dual-host adoption only under the checked implementation plan. Phase60 comparative calls remain separate.

## No Analog Found

| Candidate | Reason | Planner action |
| --- | --- | --- |
| `investigation_budget.py` | monotonic truncation count exists, but no shared finite multidimensional pre-dispatch reservation/deadline service | specify full signatures, finite value sources, zero/refund/cancel/unknown-completion behavior and contender tests before implementation |
| `test_investigation_budget.py` | no existing fixture proves atomic competing reservations across passes/groups/retries/tools | create meaningful N/N+1 and concurrent contender controls; exact-site omission must fail |

An all-attempt crash-safe journal, snapshot-safe opener, typed tool conversation controller and qualified data-only recipe supervisor also lack complete behavioral analogs. The listed role matches provide primitives only. Do not copy a native executor or list-returning graph helper wholesale to fill those gaps.

## Metadata and Embedded Pattern Evidence

**Analog search scope:** immutable canonical SQLite nodes/edges, targeted `src/code_forge` and current tests, phase context/requirements/roadmap/reconciliation, accepted synthesis, historical narrow repair inputs.
**Graph files bound:** 726 physical File nodes; all current bytes hashed, none missing, zero hash mismatches. No shared graph or Registry writes/refresh.
**Graph metadata:** schema13, branch main, `git_head_sha=12b735fcefac91685dc231b1cd1434588b194a8e`. Metadata `last_updated=2026-10-03T02:40:47` is not used as freshness proof; matching current file hashes are the checked binding.
**Tool limitation:** filesystem Read/Write tools were unavailable. Read-only command tools supplied source; apply_patch wrote only this PATTERNS.md. Evidence is embedded here to honor the mapper's single-output-file rule. No secondary ledger or standalone evidence script was created.
**Instruction discovery:** canonical AGENTS.md/RTK.md and local `.Codex/skills`/`.agents/skills` were absent; canonical CLAUDE.md and home AGENTS.md loaded. RTK fallback `[PRIVATE_HOME]/.claude/RTK.md` loaded. Parent's supplied refreshed graph/fetched base were verified, not refreshed/fetched by this mapper.
**Read failure disclosure:** one numbered-range helper requested source line178 of snapshot.py although splitlines had177 entries; it exited1 after printing all real lines. No source data or test result was synthesized from that nonexistent line. Some oversized initial tool displays were truncated; targeted source/graph extraction supplied the specific cited contracts. No acceptance FAIL/PASS flip occurred; product tests were not run.

Verbatim binding output from read-only `/usr/bin/python3` SHA256 validation of all File nodes:

```json
{
  "head": "12b735fcefac91685dc231b1cd1434588b194a8e",
  "graph_head": "12b735fcefac91685dc231b1cd1434588b194a8e",
  "physical_file_nodes": 726,
  "missing": [],
  "hash_mismatches": []
}
```

Verbatim additional command outcomes: `git status --short` emitted no output; `git merge-base --is-ancestor 3451e2e63acdcb466559c018b6590cda52626eb5 HEAD` exited0. SQLite was opened only with `file:.code-review-graph/graph.db?immutable=1`, `uri=True`.

Current principal source SHA256 bindings:

```text
b4655c23c0925ccfc3572d9e5b5cb5b73b893b6c7e0c55e0f97c0e2e603341fa  src/code_forge/context_sources.py
361355cb544089ca3a71e23ca389f9d7ff21011ced5ff890481d442ae52a6a00  src/code_forge/graph_triage.py
945abaaace799faa9d9fb5eb0dc85020236158150264c2bab2f9fd16d31e289a  src/code_forge/llm_invoke.py
d0b5cc9f21895dedc5fc8934995091451636608f313ca5e73866160c890017fe  src/code_forge/factories.py
804910d31f9967c458fd9767a03923ad5098c56afe8bbf3a601eda1f4bb7ee69  src/code_forge/receipt.py
803b06ac226ef5504aceff8be1678094de6880472ae65e1b5a5faac8996ce795  src/code_forge/verify.py
1b011eec804ebf62b8086d98b5ca882400c19450bda6a80f4a0e9791f0f5a4ad  src/code_forge/proc.py
822bf42e8f882b59d779e8f536deba592a353736dcf9709d56987a893fdfe294  src/code_forge/mcp_jobs.py
```

Fresh plan execution must rebind current HEAD, these source files, graph view and then-current phase inputs; none of this document transfers historical implementation/review credit.
