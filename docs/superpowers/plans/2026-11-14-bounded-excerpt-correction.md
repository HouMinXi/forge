# Bounded Excerpt Correction Implementation Plan

> **Execution:** The main session owns design, implementation and acceptance. Use `executing-plans` after this plan's independent admission. Independent review supplies findings, not implementation authority. Keep all implementation stages in one uncommitted delivery candidate until the applicable code-review gate permits a commit.

**Goal:** Recover a strictly eligible literal transcription error with one extra model request, without changing findings, coordinates, evidence standards or the initial prompt.

**Architecture:** Capture the canonical evidence diff at the existing L1 factory. After ordinary acquisition and the old missing-evidence branch, test the complete response against that frozen input; request one complete replacement only for eligible literal errors. A correction-only transport policy bounds reads and refuses redirects. The factory retains bounded diagnostics before admitting replacement findings and excerpts.

**Tech Stack:** Existing Python implementation, standard-library HTTP/threading/JSON facilities, existing `unidiff`, pytest and Ruff. No dependency, shared installation or backend-configuration change.

**Spec:** `docs/superpowers/specs/2026-11-13-data-file-evidence.md`, frozen SHA-256 `8258159a9e2d46f633ff86c67649ba03437fed78da25b0bb1d4fd3a5dd3c3930`. This revision adds the dedicated `correction_observations` lane (C5, C5a, change surface) and A22; the previously admitted content is otherwise unchanged and needs one affected-scope re-check, not a full re-open.

**Status:** Plan candidate; production implementation is not admitted yet. The specification passed its final independent check. This plan must still pass its affected-interface, test-quality and failure-path review.

## Decision summary

The archived failure contains complete excerpts with missing punctuation. Existing missing-excerpt repair does not run for that case. The implementation will add a narrow recovery opportunity rather than relax the verifier. A failed correction still leaves rejected evidence; a successful evidence correction cannot erase a product finding. The earlier optimization is a local WIP checkpoint, not an approved delivery.

Implementation order is top to bottom in the following figure. Boxes are delivery stages, not separate commits.

```text
[baseline + failing factory test]
               | reproduce before changing recovery
               v
[eligibility + one-shot acquisition + byte/deadline bounds]
               | preserve full model reply and cost
               v
[factory ownership + bounded diagnostics + real terminal cases]
               | withdraw real guards and compare baseline
               v
[remote regression + real API measurement + independent code review]
               | three valid clean rounds and finding closure
               v
[one signed delivery commit; publication under the task's authority]
```

## Global constraints

- Worktree: `/home/houminxi/code/forge/.worktrees/runtime-json-contract`; branch `fix/runtime-json-bare-contract`.
- Implementation base: `e15bf54ac49a11847b1d26f979a9549638be55cd`, tree `257fea0dc7cfa60a4ad623adcb7329820582289c`. Integration/review base remains `origin/main` at `903f03f516cd5c25c5ba334d4a1983f6a6bca9a7`, unless a real later update requires reconciliation.
- Production writes: only `src/code_forge/llm_invoke.py`, `src/code_forge/factories.py`, and optional redirect-limit plumbing in `src/code_forge/invocation_send_audit.py`.
- Do not change verifier, reviewer schema, diff/parser/grouping, CLI, cross-repository or redirect/stream implementations. `receipt.py` and `machine.py` receive only the narrow additions named in Task 4: one additive keyword on `write_receipts`, one extra member in the existing `artifact_payloads` concatenation, one snapshot field with its assignment, and one keyword at the single call site. No purity condition, acceptance predicate, lane authority or schema may change. Read and exercise everything else. Stop an affected implementation change if it requires a different acceptance rule.
- Initial prompt and L1 prompt oracle are byte-identical to the implementation base. A separate correction prompt has its own tests; do not recapture the old oracle.
- At most one added correction per pass. Direct calls get one diagnostic slot; a top-level provider has one thread-safe 8 MiB root, reserving 1 MiB before each send and never refunding after dispatch. All grouped children share that root.
- Initial raw, complete serialized request and decoded correction raw are each limited to 128 KiB. Correction wire body and each final persisted diagnostic are each limited to 1 MiB. Overflow metadata is at most 4096 encoded bytes. All are UTF-8 byte counts, not characters or token estimates.
- Compute D at initial `llm_invoke` entry. Correction admission uses `min(120, floor(D-now))` seconds and skips below one second. The response uses one absolute deadline, never renewed. Preserve all existing stricter cancellation, lifecycle and audit failures.
- The correction's local cap is the smaller of the existing effective output cap and 8192 tokens. Keep active cap/ceiling consistent and preserve the known provider maximum guard. Backend configuration is copied, never mutated.
- No office-laptop full-suite or mutation sweep. Remote tests use an operation-owned export and existing interpreter. No new package installation, system service change, credentials in artifacts or automatic publication.

## Grounded execution environment

Local bounded tests: `/home/houminxi/.venvs/forge/bin/python3`; clear `PYTHONPATH` and `PYTHONHOME`, use `-B` and disable pytest's cache provider. This environment currently has pytest 9.1.1, while the repository declares 9.2.0. Its pre-existing configuration warning must remain classified as an environment difference, not silently suppressed or called a new product pass.

The jumpbox's checked interpreters lack `unidiff`; no install is authorized. Use the already available personal Forge test environment on SSH alias `z66`: `/home/houminxi/code/forge/.venv/bin/python`, Python 3.13.12, pytest 9.1.1, pytest-asyncio 1.4.1, unidiff 0.7.5, PyYAML 6.0.3 and jsonschema 4.26.0. `git`, `node`, `npm` and `ruff` are on its checked PATH. This is a personal Forge task, not an office-project workload or cross-machine configuration sync.

Before a remote batch, recheck interpreter imports, free memory/disk and competing jobs. Use one pytest process and sequential injections; do not stop unrelated jobs. Create a unique directory under the remote user's home. Transfer only a bundle of the declared personal Forge baseline plus the current source/test delta and checksums; do not transfer `.env`, configuration, memory, credentials or company repositories. Restore it as an independent temporary Git checkout on a `fix/*` branch, not a copied linked-worktree `.git` pointer. Read back the test tree and module origin before running. Remove only operation-owned temporary trees after logs are archived locally.

## File and interface map

Existing symbols are named below without a prefix. Names marked **new** are additions proposed by this plan, not currently executable assets.

- `llm_invoke.py`: existing `llm_invoke`, `_invoke_api`, `_decode_reply`, `effective_invoke_timeout_s`, `_sum_usage`, `_invoke_openai[_impl]`, `_invoke_anthropic`, `_invoke_vertex`, `_read_with_deadline` and `_read_sse`.
- **New** keyword-only `review_diff: str | None = None` and private `_excerpt_allowance` on `llm_invoke`. With no `review_diff`, preserve old call shapes and behavior; do not pass new default kwargs down to old fake/caller seams unnecessarily.
- **New** `LLMResult.excerpt_diagnostics: tuple[dict, ...]`, default `()`, `repr=False`, `compare=False`. This is host metadata, never a model reply key.
- **New** `_ExcerptCorrectionAllowance`: invocation-owned lock and slot counter. `reserve()` returns a held slot or `None`. The object is created inside each real provider invocation, not once at factory construction and not globally.
- **New** `_literal_excerpt_indexes(content, raw_response, reply_keys, review_diff) -> tuple[int, ...]`: pure eligibility over content and evidence only; it takes no `backend`, so the backend/format gate lives in `_try_literal_excerpt_correction` before this call. Empty tuple means no new request. Strictly decode raw via `_decode_reply(..., strict_response=True)` and compare deterministic JSON encodings to preserve bool/int differences. Validate and assess private deep copies.
- **New** `_try_literal_excerpt_correction(result: LLMResult, *, review_diff: str, backend: BackendConfig, deadline: float, started_at: float, allowance: _ExcerptCorrectionAllowance, audit_scope, reply_keys) -> LLMResult`: receives the initial result, frozen diff, deadline, backend and allowance. It either returns the selected complete raw/content pair plus diagnostics and total usage/time, or the unchanged original pair plus failed-correction diagnostics. Exceptional cancellation/memory/audit/lifecycle stops propagate.
- **New** `_CorrectionReadPolicy` and `_BoundedCorrectionResponse`: one absolute deadline, one per-response wire counter and decoded-text counter. They are used only by the one-shot branch, before capture and decoding.
- **New** `_invoke_api` private keyword options `_one_shot=False` and `_correction_read=None`. One-shot requires `max_attempts=1` and strict complete JSON, and bypasses syntax/continuation/headroom branches rather than trusting retry count alone. It preserves acquired error payloads and observed usage.
- Format dispatch receives optional `_correction_read=None`: `_invoke_openai`, `_invoke_openai_impl`, `_invoke_anthropic` and `_invoke_vertex`. `_read_sse` and `_assemble_sse` in the same file gain the same optional hook; `_read_sse` supplies an assembler closure that passes it through existing audited tail-drain code without changing that module. `_assemble_sse` checks each decoded content delta before append. OpenAI's audited wrapper also passes `max_redirect_hops=0`; `OpenAiSendTransport` adds only optional argument plumbing, defaulting to 10 for existing calls.
- `factories.py`: existing `build_l1_provider`, `_L1Call` and `build_grouped_l1_provider`; **new** keyword-only `excerpt_allowance=None, excerpt_group=None` on private `_L1Call._invoke(self, round_view=None, *, excerpt_allowance=None, excerpt_group=None)`. Its current-invocation fields expose this trusted context to the existing body closure and are cleared on exit — but only the two new fields. `pass_usage`, `attempted_excerpts`, `raw_observations`, `acquisition_failures` and the other existing fields keep baseline semantics (reset at entry, retained after return): `machine.py:2772` reads `pass_usage` off the provider after `_observe_phase`, so clearing it would silently drop per-pass cost rows. Ordinary `__call__` and `call_with_audit` keep their signatures. The grouped body calls each child’s private `_invoke` with the one root and its existing `ReviewGroupScope`. **New** `_bounded_excerpt_observation(record: dict, *, pass_name: str, group_scope: ReviewGroupScope | None) -> dict` performs the writer-shape check.
- New test files: `tests/test_literal_excerpt_correction.py`, `tests/test_literal_excerpt_limits.py`, `tests/test_literal_excerpt_factory.py`. Keep their fixtures small and deterministic. Reuse existing audit fixtures in `tests/test_invocation_send_audit.py` and real machine setup patterns in `tests/test_machine_receipt_gate.py`; do not copy a new machine/verifier implementation.
- Existing regression files remain test inputs. New fixture functions live in the new test module that owns them; share only fixtures genuinely used by multiple new modules. No new generic framework or dependency.

## Task 1: pin the baseline and prove the missing behavior

**Files:** read the admitted spec and baseline production files; create `tests/test_literal_excerpt_factory.py`. Evidence goes to `.planning/runs/<date>/excerpt-correction-implementation/` and the remote operation directory, not the candidate.

- [ ] Record the nine-field task contract, exact baseline, environment and commands. Preserve current untracked spec/plan bytes in the already frozen evidence directories; use explicit staging paths during implementation. Before the later mandatory `git add -A`, move untracked internal drafts out of the candidate or intentionally include only finalized deliverable documentation. Never let logs/probes enter the tree digest.
- [ ] Read both production definitions and their real callers. The code graph is built for `origin/main`; a graph hit in `tests/eval/corpus/base_files/` is a fixture, not the actual `src/code_forge/llm_invoke.py` implementation.
- [ ] Run the existing repair and machine/audit regressions on the declared base in the remote temporary checkout. Keep baseline failures, node IDs and their environment explanation; do not infer pass from import success. If a baseline defect needs code outside this slice, isolate its repair and applicable review rather than hide it.
- [ ] Add the following factory-level red test before adding the recovery implementation. It calls existing production entry points, so the baseline failure must be the missing correction, not an unknown new argument or import error.

```python
def test_factory_recovers_literal_quote_once(monkeypatch):
    import json
    from pathlib import Path
    from code_forge import llm_invoke as invoke
    from code_forge.backend import BackendConfig
    from code_forge.baseline import ResolvedReview
    from code_forge.factories import build_l1_provider

    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n one\n-old.\n+new.\n three\n"
    )
    original = {
        "findings": [],
        "code_excerpts": [
            {"file": "a.py", "start_line": 1, "end_line": 3, "content": "one\nnew!\nthree"}
        ],
    }
    fixed = {
        "findings": [],
        "code_excerpts": [
            {"file": "a.py", "start_line": 1, "end_line": 3, "content": "one\nnew.\nthree"}
        ],
    }
    calls = []

    def transport(prompt, backend, key, timeout_s, **kwargs):
        calls.append(prompt)
        value = original if len(calls) % 2 else fixed
        return json.dumps(value), {"prompt_tokens": 2, "completion_tokens": 3}

    monkeypatch.setenv("FORGE_L1_SERIAL", "1")
    monkeypatch.setenv("EXCERPT_TEST_KEY", "fixture-not-a-credential")
    monkeypatch.setattr(invoke, "_invoke_openai", transport)
    backend = BackendConfig(
        name="literal-test",
        type="api",
        model="fixture",
        format="openai",
        base_url="https://example.invalid",
        api_key_env="EXCERPT_TEST_KEY",
        timeout_s=10,
    )
    provider = build_l1_provider(
        "auto",
        ResolvedReview([Path("a.py")], None, diff, "git"),
        backend=backend,
        max_attempts=1,
        initial_delay_s=0,
    )
    findings, excerpts, usage, _duration = provider()
    assert len(calls) == 6
    assert findings == []
    assert len(excerpts) == 3
    assert all(e["content"] == "one\nnew.\nthree" for e in excerpts)
    assert (usage.input_tokens, usage.output_tokens) == (12, 18)
```

- [ ] Run that exact test. Baseline must reach its assertions with three initial transport calls rather than six. The factory itself accepts those three excerpts, two of which carry wrong content (`factories._provider` only runs `validate_reviewer_json` and `require_l1_excerpt_evidence`, which never compares excerpt text); the content rejection happens later at `verify.validate_excerpts_against_diff` and the receipt write. Preserve that real failure output and do not describe it as a factory-level rejection, or the implementer will hook a `except ExcerptEvidenceError` branch that never fires. Adapt only constructors to the read baseline if necessary, not the oracle.
- [ ] Add A03 and A10 red cases next: correction returns the original invalid text, and automatic correction is exercised through the real producer/writer/machine. The A10 baseline fails on evidence rejection, not on a fake terminal verdict.

## Task 2: implement eligibility and immutable selection

**Files:** `llm_invoke.py`; new `test_literal_excerpt_correction.py`. Depends on Task 1's concrete red case.

- [ ] Add the default-empty result field and optional input validation without changing omitted-option behavior. Record D once at entry; the old initial request and missing-evidence branch remain unchanged. Gate the whole feature on `backend.type == "api"` with `backend.format in {"openai", "anthropic", "vertex"}` at both the factory binding and the eligibility entry. A CLI backend takes `llm_invoke.py:2460` into `_invoke_cli`, which never receives `_one_shot`/`_correction_read`: it would dispatch a second subprocess with no 1 MiB wire cap, no 128 KiB decoded cap, no zero-redirect policy and no correction deadline. That is a silent resource-bound bypass, so the gate is required by the spec's API-only scope (`spec.md:13`, `:73`) and by A13's CLI control, not an optimization.
- [ ] Add `_literal_excerpt_indexes(content, raw_response, reply_keys, review_diff)`. It is pure and never raises on input shape: absent `review_diff`, `raw_response` that is `None` or not a `str`, `_decode_reply(..., strict_response=True)` raising `TypeError`/`JSONDecodeError`/duplicate-key/non-finite-number, a fenced raw reply, or a decoded value that is not byte-identical to the current `content` all return `()`. `_decode_reply(None, ...)` raises `TypeError` rather than returning a value, so the `None` case is a real branch, not defensive padding. The backend/format gate is checked in `_try_literal_excerpt_correction` before this function is called. Then require canonical four-field nonempty string excerpts, positive true integers, exact line counts, valid findings schema, fully captured ranges and hunk intersection. Assess deep copies with `_diff_validation_context(review_diff, cwd=None)` and `assess_excerpt_evidence`; only the sole literal-mismatch diagnostic qualifies. Prior synthesized repair, absent raw, `repaired_tail`, other INVALID causes, all-VALID or only-UNTRUSTED replies do not. `validate_reviewer_json` accepts a fenced reply, so this strict-decode failure is a real reachable path, not defensive padding: A06 asserts the provider returns the original pair with zero new requests.
- [ ] For each invalid excerpt, enumerate available keys of its same canonical frozen file; compare the complete sequence at one nonzero offset with existing `_line_content_matches(..., allow_indent=True)`. Gaps break matching. Never expand a sparse min..max range, join hunk fragments or inspect another repository/file. Any whole-sequence alternate placement skips the whole correction.
- [ ] Keep permitted positions as original list indexes. Build the correction prompt with a JSON-serialized untrusted-data section containing the entire previous envelope, diagnostic coordinates and only the frozen lines for those indexes. Require root `findings`/`code_excerpts`, not the input wrapper. Do not change the initial prompt or synthesize repaired output.
- [ ] Compare strict replacement JSON by masking only those indexed `content` values on private copies and serializing with sorted keys and `allow_nan=False`; require everything else identical. Re-run the unchanged reviewer and source validators on copies, reject any normalization/tail repair that would be needed to claim literal acceptance. Select the acquired correction raw and its decoded content together only after all conditions pass.
- [ ] Add and run A01/A02/A04/A05/A06/A09/A13/A14/A16/A17/A18 tests. Use actual archived missing-period source lines from the tracked prompt fixture, not `.planning` input at test runtime. Assert the malformed text actually differs; retain 17 unaffected entries and a controlled nonempty finding. Use `neW.` or a non-tail character error for positive controls so the old tail repair cannot supply the expected answer. A13 runs the real CLI backend path with a controlled spawn/transport and asserts zero added invocations and byte-identical old results.

## Task 3: one-shot dispatch and bounded acquisition

**Files:** `llm_invoke.py`, optional argument plumbing in `invocation_send_audit.py`; new `test_literal_excerpt_limits.py`. The ordinary request path remains a regression control.

- [ ] Resolve the correction timeout/cap on a local backend copy. Build the actual per-format request and check its encoded size before network entry, not just the user prompt size. Request/input oversize or less than one second remaining skips with zero sends and no raw-copy diagnostic growth.
- [ ] Fix the correction call's kwargs explicitly rather than "same route plus strict": `strict_response=True`, `l1_evidence_required=False`, `max_attempts=1`, `initial_delay_s=0`, plus the frozen `review_diff` and allowance. `llm_invoke.py:2445-2446` raises `ValueError` when `strict_response and l1_evidence_required`, and the initial L1 call sets `l1_evidence_required=_requires_l1_excerpts(diff_text, ...)` — true on exactly the diffs where correction is meaningful. Reusing the initial kwargs therefore fails before any request and would surface as INFRA through `_run_pass`. Add a regression asserting an excerpt-bearing diff plus the correction call does not raise.
- [ ] Add one-shot branches in `_invoke_api` before truncation continuation, headroom and syntax retry. A one-shot parse failure returns the acquired text through `InvalidJSONResponseError`; truncation preserves `_TruncatedResponse` payload/usage, and neither path recurses through `llm_invoke`.
- [ ] `_BoundedCorrectionResponse` wraps the opened success or `HTTPError` response before `WireCapture` or SSE parsing. Implement `read`, `read1`, `readline`, iterator and context/close delegation. Underlying reads always have a nonnegative size at most 65536, including the one-byte overflow lookahead. Count raw wire once; buffer only within the single response limit; refuse over-limit input before yielding it for decoding. Preserve the response status/header/socket properties needed by existing deadline/capture helpers.
- [ ] Nonstream reads and finite error-body reads run via `_read_with_deadline` with the same supplied absolute deadline. Streaming consumes the same wrapper. Where a format exposes content deltas, stop accumulation at the decoded UTF-8 text threshold; for nonstream input, check immediately after bounded body parsing before making derived diagnostic copies.
- [ ] Use existing `_NoRedirect` for plain correction calls. Thread optional zero hop limit through the audited OpenAI wrapper and `OpenAiSendTransport`; its old default stays ten. Do not modify `_NoRedirect` or `open_with_redirects`. Keep refused 3xx status and read its body once under the policy.
- [ ] Preserve interrupt, `MemoryError`, typed lifecycle/deadline/audit stops; a secondary HTTP-body read error must not overwrite an active primary exception. Retain observed HTTP status and explicit body-incomplete state on correction failure. On the correction path **all three** `except BaseException` handlers in the same HTTPError block must become `except Exception` with `KeyboardInterrupt`, `SystemExit` and `MemoryError` re-raised explicitly: `llm_invoke.py:3876-3879` (`_audit_transport.response_capture`), `:3884-3885` (plain deadline read) and `:3891-3894` (captured body). The capture handler is easy to miss and is the audited-mode window: a cancel arriving there is silently turned into `wire_capture = None`, after which the code keeps reading the error body. A08 therefore needs two cases, one cancelling inside `response_capture` and one during the body read, each asserting the original exception propagates and no further read happens.
- [ ] Run A07/A08/A19/A20 for all three formats and supported plain/ordinary-audit/strict-audit modes. Fake finite body streams are deterministic unit fixtures; also run one loopback HTTP server through the real opener/reader, with read-call sizes and physical request counts observed. Cover 429, 500, refused 302, no-newline SSE, reasoning-only bytes, exact limit, limit plus one and a valid small-body positive control.

## Task 4: provider ownership, costs and durable diagnostic shape

**Files:** `llm_invoke.py`, `factories.py`, one additive keyword in `receipt.py`, one snapshot and pass-through in `machine.py`; new factory and limits tests. No schema change and no purity-gate edit.

- [ ] Bind the frozen input, then call the helper. In `build_l1_provider` construction, derive the evidence-only diff once per C1: ordinary and grouped use the captured `resolved.git_diff`; joint uses `receipt_scope.repository_scope(reviewed_repositories)[0]`, never the display context. Pin its digest. Pass it as `review_diff` only when `backend.type == "api"` and the format is in the supported set; CLI and `backend is None` pass `None`. Then, in `_provider`, after `llm_invoke` returns and **before** `validate_reviewer_json`/excerpt collection, call `_try_literal_excerpt_correction` with that diff and the allowance, and continue with whichever result it returns. The helper must receive the *entry* clock, never a fresh one: add two more additive host fields to `LLMResult`, `entry_started_at` and `entry_deadline`, filled at the `llm_invoke` entry that records `D` per C3, and read them from the returned result. A helper that recomputes `monotonic() + 120` would give itself a new window after the initial call already consumed part of the budget and would violate A19; assert in A19 that the correction deadline is the entry value minus elapsed time, and that a run where the initial call already passed `D` sends nothing. Without this bullet the rest of the mechanism is inert: the red test in Task 1 stays at `assert 3 == 6` after every other task is implemented.
- [ ] Allocate the ordinary root when `_provider` is invoked, just before pass execution. For grouped review, allocate once inside `_composite` and pass that object to each child invocation. A private `_L1Call` invocation parameter may carry that object; never keep a spent root across separate provider calls. Three threads reserve on the same lock; direct `llm_invoke` without a host root gets one slot.
- [ ] Reserve a full slot before sending. Keep it until the invocation ends, with no dispatch refund. Reservation refusal returns the original pair without adding per-skip raw diagnostics. Eight slots bound all new records; existing audit quotas remain independent stricter restrictions.
- [ ] Add a bounded diagnostic record for each dispatched correction. Check original raw before copying, correction raw before serializing and the encoded record before appending. Include selected flag, source digest, indexes, bounded reason, initial/correction text where complete, usage availability and duration. Overflow uses only the bounded incomplete metadata record; never call a prefix complete.
- [ ] Before a pass enters finding/excerpt collection, `_bounded_excerpt_observation` attaches the loop-owned pass and group context and measures the exact current writer envelope with `ensure_ascii=True` and indentation. Reuse `GroupedReviewAttempt`; do not let a model field choose scope. As the unaudited factory does not receive the eventual round number, budget a conservative cycle field at the interpreter's finite integer-string conversion ceiling; reject replacement if that ceiling is disabled. This may reject a large diagnostic earlier than the writer but does not grant more storage. The size-probe wrapper is not persisted evidence. Test with the real writer, including a long cycle representation and escaped/grouped payloads.
- [ ] If this last outer check overflows, append only bounded metadata and route that pass through its existing invocation-failure path before admitting its corrected findings/excerpts. Carry observed usage and duration into that failure. Do not drop only the diagnostic while keeping corrected evidence.
- [ ] Write the record to a dedicated lane. Append to a new `_L1Call.correction_observations` list, reset at the same entry point as `raw_observations`; never to `raw_observations`, never to `attempted_excerpts`. Add the single additive keyword `correction_observations: list[dict] | None = None` to `receipt.write_receipts`, and append its payloads **after every existing artifact source** in this exact order: `attempted_excerpts` → `raw_observations` → the conditionally filtered `pending_raw_responses` extension at `receipt.py:486-493` → `correction_observations`. Appending before that pending extension shifts every pending artifact's `enumerate` ordinal (`receipt.py:497-516`) and renames files that already exist on disk. The `None` default keeps every current caller identical.
- [ ] Merge the lane across grouped children. `build_grouped_l1_provider` must forward each child's `correction_observations` into the composite the same way `factories.py:1085-1093` already forwards `attempted_excerpts`, `raw_observations` and `pending_raw_responses`, wrapping each entry in `GroupedReviewAttempt(observation, scope)` so group attribution comes from the loop rather than the model. Without that merge a child's dispatched correction is silently dropped: the composite's list stays empty, `machine` snapshots empty, and the writer persists nothing even though the request happened and a slot was consumed. In `machine`, snapshot `getattr(self.l1_provider, "correction_observations", None)` beside the existing `raw_observations` snapshot and pass that snapshot at the single `write_receipts` call site. **Do not touch** the three purity conditions at `machine.py:4107`, `:4137`, `:4218` — the whole point of the separate lane is that they never see this record. Let the existing final-rejection registration handle still-invalid evidence; never remove an existing rejection. A write failure still blocks completion through the existing receipt-write error gate.
- [ ] Prove the lane separation instead of asserting it. A22 runs the combined shape — one pass dispatched a correction, another pass has empty findings with rejected evidence, and the same source hash already holds an earned clean cycle — through the real `StateMachine`, and asserts `clean_credit_action == "evidence_rejected"`, that the `evidence_rejections` row is written, that the earned window still validates, and that `producer.raw_observations` stayed empty. Then inject the record into `raw_observations` instead and confirm that same test fails: without that negative arm the lane choice is decoration. The earlier diagnostic-lane probe returned PASS and never entered this path, so it is not evidence about this shape.
- [ ] Aggregate usage with `_sum_usage`. Failure without observed usage contributes unknown fields, not a known zero. Total duration is from the original entry through eligibility/correction. Both accepted and refused corrections retain observed costs.
- [ ] Run A02/A07/A08/A12/A13/A16/A18/A21/A22. Concurrent and more-than-eight-group tests must count actual correction sends and serialize the real writer output, not merely inspect the allowance counter. Two separate top-level calls must each start fresh; one grouped run must not reset at child boundaries. The A13 CLI control asserts zero added invocations and byte-identical results through the real CLI branch, not just a `backend.type` predicate check. Two writer controls are required: (a) a mixed round carrying `attempted_excerpts`, `raw_observations`, one pending response that qualifies for the extension, and a correction record — compare every pre-existing artifact path and payload against the same round without the new lane, and assert the correction lands at the next free ordinal; (b) a correction-only round where the new lane starts at index 0. For grouping, at least one child dispatches a correction and the test follows composite → machine snapshot → writer, asserting the persisted payload carries that child's group metadata; keep the shared-root eight-slot assertion in the same test.

## Task 5: real chain, regression, defects and measurement

**Files:** new test modules; existing production consumers read-only. Evidence and injection drivers stay outside the candidate.

- [ ] Complete A10/A11/A12 with real `build_l1_provider` → `llm_invoke` → fixture format transport → receipt writer/reader → `StateMachine` → `run_verify`. Correct quotes pass evidence; a controlled product finding remains product failure; a diagnostic-path-only filesystem `OSError` prevents terminal completion. Persist state and both raw artifacts and inspect their bytes. A fixture transport or stub non-L1 engine is identified, not called a real model review.
- [ ] For every new test family, withdraw the real production fix in an exported no-`.git` copy, run the same selected tests to an assertion FAIL, restore the exact bytes and run to PASS. Record source commit/tree, function/line, stub boundary, selected node IDs, raw failure and restored pass. Collection errors, missing imports, zero-selected, timeout or unrelated rejection are not a killed defect.
- [ ] Required injection sites (each names the Task 4 bullet that implements it; the injection list does not define the design): new factory `review_diff` binding; automatic post-acquisition recovery call; finding/index invariant guard; whole-sequence offset guard; one-shot retry/continuation guard; HTTP-error deadline path; response and decoded-text limiter; shared-root propagation; failed-cost merge; bounded writer observation and diagnostic persistence; the dedicated diagnostic lane (the `receipt` keyword, the `machine` snapshot and pass-through, and the untouched purity conditions). Inject old single-line-only and old -64..65 window separately for A17. No local sweep and no fake standalone bug/fix implementation.
- [ ] Run changed-module py_compile with `PYTHONPYCACHEPREFIX` outside the source tree and Ruff. Then run the new test modules plus existing excerpt repair, strict response/retry, send audit, factory/group/joint, machine receipt, terminal verify and prompt-byte tests remotely. Reconcile collected vs executed node IDs and exclusions. Compare any failures to the identical-command base run, isolate true pre-existing defects, and do not call environment failures PASS.

```bash
env -u PYTHONPATH -u PYTHONHOME "$PY" -B -m pytest -p no:cacheprovider \
  tests/test_literal_excerpt_correction.py \
  tests/test_literal_excerpt_limits.py \
  tests/test_literal_excerpt_factory.py -q
env -u PYTHONPATH -u PYTHONHOME "$PY" -B -m pytest -p no:cacheprovider \
  tests/test_excerpt_repair_contract.py tests/test_excerpt_repair_boundaries.py \
  tests/test_excerpt_repair_prompt_contract.py tests/test_l1_evidence_recovery.py \
  tests/test_invocation_send_audit.py tests/test_factories.py \
  tests/test_machine_receipt_gate.py tests/test_ordinary_audit_integration.py \
  tests/test_l1_prompt_byte_identity.py -q
```

- [ ] Run the broader existing test suite on the remote environment with one process and explicit timeout, archiving full output and baseline comparison. Recheck available memory before each batch; do not start a second test or injection batch on the same budget.
- [ ] Exercise the real configured model path once with a bounded correction case, preserving initial raw, full replacement raw, cost, duration and validator results. Credentials come only from the existing `pass` reference at launch and never enter a script, URL, log or snapshot. Do not change the default backend or runtime installation to test the candidate.
- [ ] Freeze one combined candidate and evaluate a predeclared six-route sample using the user-approved agnes entries as equivalent traffic routes, sequentially. Use the same input/candidate/settings, no hidden rerun of failed routes, one bounded full LOCAL invocation per route. Count initial acceptance, correction activated/recovered/rejected, final product status and extra usage/duration. A route with no eligible error is not evidence of recovery. Do not promise a target success rate from the earlier two-case experiment.

## Acceptance index and completion evidence

The spec remains the single owner of outcomes. This index assigns each row to a task and required observation, without redefining the criteria.

- A01/A02 → Tasks 1/2/4: real archived punctuation shape; complete envelope, nonempty finding and unaffected entries.
- A03/A04/A05/A06 → Tasks 1/2: failed replacement, invariant violations and old behavior/no-send controls.
- A07/A08 → Tasks 3/4: physical send counts, cancellation/error/usage propagation.
- A09 → Tasks 2/3: untrusted instructions and real encoded-request oversize.
- A10/A11/A12 → Task 5: actual consumer-chain verdicts and diagnostic write failure.
- A13/A14 → Tasks 2/4: API-only gate with CLI zero-new-request control, serial/concurrent passes, grouped and canonical joint binding, captured-source drift.
- A15 → Task 5: a full real LOCAL run; deterministic forced correction and actual model review remain distinct evidence.
- A16/A17/A18 → Tasks 2/4/5: index-vs-coordinate negative, whole-sequence ambiguity and joint pseudo-source rejection, each with a reachable positive control.
- A19/A20/A21 → Tasks 3/4/5: deadline/bytes, physical readers, actual persisted envelope and shared invocation budget.
- A22 → Tasks 4/5: dedicated diagnostic lane; rejection classification and earned-window proof through the real machine, with the `raw_observations` injection as the failing negative arm.

## Task 6: independent code review and delivery

- [ ] Confirm scope, new non-ASCII lines, dead imports/variables and per-modified-line test coverage. Reconcile all A01–A22 with this run's original evidence. A pending or blocked row remains pending or blocked.
- [ ] Clean or move untracked drafts/probes out of the candidate; retain restorable evidence outside it. Run `git status --porcelain`, then the required `git add -A && git write-tree`; archive the full combined diff against `origin/main`. Review the whole original optimization plus new recovery, not just the incremental diff against the WIP commit.
- [ ] Use the requested Forge LOCAL path from this checkout, with source-origin verification and logs outside the tree. The MCP refresh remains deferred. Obtain three valid clean independent rounds and close any P3 batch under the governing rules. A single real PASS, infrastructure failure or WIP commit is not round credit. Genuine product fixes reset credit as required.
- [ ] Compare the staged delivery tree to the final reviewed tree. Only after all gates, amend the local WIP into one signed human-written delivery commit using local identity and `-s -S`. Verify tree, author/trailer and signature; do not publish the WIP.
- [ ] Update the user with exact code/test/review status. Publish the personal feature branch and immediately create/update its request only when this task's publication authority applies; no main write, force push, merge or runtime deployment is implied by plan admission.

## Literature and contract-evidence reuse

The admitted spec's literature section and raw experiment directory cover this unchanged mechanism. Exa, Tavily and Firecrawl results are retained; Context7's 401 is retained as a failed channel. Reuse is mechanism-specific, not a new claim that all four succeeded.

- Stechly et al., <https://arxiv.org/abs/2310.12397>, and Madaan et al., <https://arxiv.org/abs/2303.17651>: deterministic external feedback, not unconstrained self-critique or guaranteed repair.
- Instructor <https://python.useinstructor.com/concepts/retrying/> and Pydantic AI <https://ai.pydantic.dev/retries/>: distinguish validation attempts and physical sends; no dependency is added. The spec recorded 14112 and 21199 repository stars respectively, observed 2026-11-14; adoption context only.
- Local reuse: `_decode_reply` supplies complete strict parsing; `_sum_usage` preserves unknown fields; receipt/terminal validators retain evidence authority; `open_with_redirects(max_hops=0)` supplies refusal without implicit drain. Source and adverse paths were read on the declared baseline, not inferred from comments.
- Plan-only choices rechecked: `_L1Call` can carry per-invocation context without changing the machine's returned tuple; group metadata is the existing out-of-band wrapper; the writer's cycle field is not available to unaudited factory calls, so the encoded-size check must use a finite conservative bound or refuse replacement. The plan must be independently checked at this seam before implementation.
- Existing remote imports and versions above were actually queried. No remote tests, dependency installs, automatic correction, resource caps or six-route measurements have been executed by writing this plan.
