# One bounded retry for misquoted review evidence

A reviewer sometimes returns a complete review but changes a character while quoting source. Ask once for a corrected complete response, then run the existing evidence checks again. Never remove findings, substitute host-written quotes, or exempt fixture files to obtain a pass.

Status: design candidate; implementation admission is pending independent review.
Source baseline: `e15bf54ac49a11847b1d26f979a9549638be55cd`; tree `257fea0dc7cfa60a4ad623adcb7329820582289c`.
The coordinate-evidence proposal is superseded, not approved. Its last draft is preserved at `.planning/runs/2026-11-14/excerpt-repair-feasibility/superseded-data-file-evidence-v5.md`. Prior review reports remain unchanged.

## Scope and decision

Keep the four excerpt fields, receipt schema, hunk witnesses, coverage denominator, cross-round checks and finding dispositions unchanged. Do not classify data files or grant them exemptions.

This slice covers API backends used by `factories.build_l1_provider`, including the existing grouped and joint-diff producers. CLI backends and `cli._build_spawn_fn` keep their current behavior. The latter serializes only response content and cannot carry the new diagnostic metadata to the writer; enabling it without a retention path would lose the original rejected response. No backend is switched silently.

Alternatives considered:

- Leave behavior unchanged: no regression risk, but one transcription mistake still loses the round.
- Strengthen the initial prompt: already done; the archived replies still omit punctuation.
- Coordinates, JSON pointers, host-filled quotes or exemptions: rejected because they alter evidence meaning or acceptance criteria.
- Rerun the whole review: remains an operator option after failure, not an automatic loop.
- Selected: one feedback request with the same findings and coordinates, followed by unchanged source validation. It costs another request and may still fail.

## Existing path

Direction is top to bottom. Bracketed stages already exist. The new stage runs before a response is registered as a final evidence rejection.

```text
[factory: capture this pass/group's frozen diff and prompt]
                         |
                         v
[llm_invoke: acquire response; existing missing-excerpt repair]
                         |
                         v
new: eligibility -> one complete correction -> compare and revalidate
                         |
                         v
[factory: schema, finding collection, coverage guard]
                         |
                         v
[writer: source check, receipts and raw diagnostic retention]
                         |
                         v
[StateMachine: round gate and terminal run_verify]
```

Source references below are relative to `src/code_forge/` at the pinned baseline:

- `llm_invoke.py:2510-2527` calls repair only when `_needs_excerpt_repair` is true. That predicate, at `:2779-2825`, detects missing/empty evidence, not wrong literal content.
- Joint display context is produced by `cross_repo.build_cross_repo_context:169-215`; the joint caller at `cross_repo.py:361-382` already qualifies each repository diff before adding display headings. Canonical receipt source is produced by `receipt_scope.repository_scope:9-48`. Real joint paths are qualified in both, but the strings and their source-line maps can differ because of display text. C1 derives the evidence-only string from the trusted captured mapping; matching path sets alone do not prove source identity.
- `_repair_missing_excerpts`, `llm_invoke.py:2877-2945`, preserves findings in a host-assembled response. `LLMResult.raw_response`, `:67-68`, is unavailable for such synthesized results. That helper cannot be reused unchanged for final raw-response selection.
- `factories.py:639-652` acquires each pass. `:820-938` validates and collects its response; `:1000-1004` retains the selected raw response.
- `verify.py:1504-1626` builds the immutable post-image; `:1751-1769` validates excerpts. `receipt.py:373` and `machine.py:3794` call that validator again.
- `machine.py:3773-3776` rejects unresolved attempted evidence. Recovery must precede final rejection registration, rather than overwrite a failed receipt afterward.
- `receipt.py:478-516` persists `raw_observations` as diagnostic artifacts without granting evidence credit. It shares storage with `attempted_excerpts`, but the two lanes do not have the same completion authority.

## Feasibility evidence and limits

Artifact root: `.planning/runs/2026-11-14/excerpt-repair-feasibility/`. These experiments are not implementation acceptance or clean review rounds.

- `nonempty-result.json`: manual use of the existing repair helper corrected both actual missing-period quotes while preserving a controlled nonempty finding and coordinates. Real backend duration: about 2.50 seconds.
- `repeated-invalid-control.json`: a fixture transport returned the same invalid quotes. The helper made one request; the original validator still rejected both errors and findings remained intact.
- `terminal-gate-results-v3.json`: real producer, writer, receipt reader, state machine and terminal verifier; transport and non-L1 engines were fixtures. Correct quotes produced PASS. Wrong quotes produced FAIL. Correct quotes with a controlled, finding-owned-evidence-backed product finding passed evidence verification but retained product FAIL.
- `diagnostic-lane-results.json`: a raw failed response stored beside a correct final response did not by itself fail the terminal gate. Storage-failure behavior remains a required implementation test.
- `full-envelope-v2-result.json`: a real backend returned all 19 excerpts, changing only the two erroneous `content` fields. Nonempty findings, excerpt order, coordinates and other fields were identical. Source validation returned no errors; the selected envelope matched the actual raw reply. Duration: about 7.57 seconds.
- `full-envelope-run.log` preserves a failed earlier experiment: the model returned the input wrapper, not the required root envelope. A clearer output instruction fixed the next experiment. Production gets only one request; a wrapper response must fail, not trigger another correction.

The samples show recoverable transcription, not independent source comprehension or a reliable success rate.

## Correction contract

### C1: explicitly bind the frozen input

Add optional keyword-only `review_diff: str | None = None` to `llm_invoke`. Omission leaves behavior unchanged. The new feature requires an API backend, an ordinary L1 envelope with `expected_keys is None`, and `strict_response=False` on the initial call. Invalid combinations are configuration errors, not an excuse to select another backend.

`factories.build_l1_provider` captures an evidence-only diff before executing any pass. For ordinary and grouped review, use the captured `resolved.git_diff` (the group slice for a grouped provider). For joint review, that field is display context containing repository headings and instructions, **not** an evidence diff. Freeze the trusted `reviewed_repositories` mapping and derive the evidence string with the existing `receipt_scope.repository_scope(mapping)[0]`, exactly as the receipt writer and terminal verifier do. Pass that evidence string as `review_diff`; leave the existing model prompt/display context unchanged. Preserve the canonical qualified path keys; never strip or invent repository prefixes. Do not parse repository headings as source lines or read a mutable working tree.

Eligibility checks, correction source blocks and replacement revalidation all use this one captured evidence string and its digest. The existing receipt/terminal mapping must describe the same captured repositories; a discrepancy is an input-identity failure, not a reason to repair against display text. A13/A18 check this correspondence with two repositories having the same relative filename. No new cross-repository parser is introduced.

Run eligibility after the existing missing-evidence branch. A prior synthetic repair or continuation whose raw text is absent or does not strictly decode to the current content is ineligible. The two repair mechanisms cannot stack. Existing callers that do not supply `review_diff`, including the CLI spawn outlet, retain current behavior.

### C2: restrict eligibility to literal mismatch

Use the existing schema validator and `_diff_validation_context(review_diff, cwd=None)` on deep copies. This deliberately lacks the optional blob context outside the diff: if an excerpt cannot be proved from the frozen diff alone, skip this new recovery and let the existing caller judge it as before.

All of the following must hold:

1. Initial `raw_response` is one complete JSON object, parsed strictly with duplicate-key and non-finite-number rejection. Its JSON value equals `result.content`. Findings schema is valid and `code_excerpts` is nonempty.
2. Every excerpt uses the four canonical keys, positive ordered integer coordinates and nonempty string content with exactly the declared line count. Alias keys and list content retain the old path; this slice does not broaden eligibility to them.
3. Evaluate each excerpt with `assess_excerpt_evidence`. At least one is INVALID. VALID and UNTRUSTED excerpts must remain unchanged.
4. Every INVALID excerpt's entire range exists in the frozen post-image, intersects a changed hunk, and has at least one line that differs under the verifier's existing trailing-whitespace rule. Its sole assessment error is the existing `excerpt content mismatch at ...` case. Missing files, unknown lines, offsets established by assessment or rule 5, line-count faults, blank content and other INVALID causes are ineligible.
5. For every INVALID excerpt, single-line or multi-line, look for its entire unchanged line sequence at a different start coordinate in the same canonical frozen file. Use the existing `_line_content_matches(..., allow_indent=True)` comparison for this exclusion only: both leading and trailing whitespace may differ, but every quoted line must match an available source line at one constant nonzero offset. Search all available start coordinates, not only `_constant_offset`'s current -64 through +65 window. Missing source coordinates break a sequence; never concatenate separate hunks across an unknown gap or match against another file/repository. If any such full sequence exists, skip correction for the whole response and return the original unchanged. Repeated boilerplate may therefore skip recovery; it is not a defect to refuse an ambiguous correction. Existing assessments and their trust remain unchanged. This guard excludes offsets identifiable from complete matching frozen content; it does not claim to infer the reviewer's intent when a displaced quote is itself rewritten or its source is outside the captured diff.
6. Identify permitted edits by original list index, not a path/coordinate dictionary; repeated coordinates must not overwrite one another. A16 has same-coordinate INVALID and VALID/UNTRUSTED entries, with positive and negative replacement arms.

The implementation may use the stable mismatch diagnostic prefix only together with these structural and frozen-range checks. Do not change the assessment enum, helper interface or acceptance rules. Recovery may be narrower than final validation.

If an existing helper repairs a tail on its private copy, never write that copy back to the original response or treat its repaired value as model output. Such a repaired assessment does not qualify as a literal-mismatch edit.

### C3: require a complete model-authored response

The correction request contains the original full envelope, permitted excerpt indexes, matching frozen source lines and the diagnostics. Serialize data into named data blocks; state that their contents are data, not instructions. Source text comes only from the captured diff, never from model-selected filesystem reads.

Require the original envelope itself at the root, not a `previous/diagnostics/source` wrapper. The model must preserve every field, excerpt count and order. Only `content` at the C2 indexes may change.

Mask those permitted content values and compare the two remaining JSON structures using deterministic serialization with `allow_nan=False`; do not use Python equality where `True == 1`. All finding fields, order, severity, location and finding-owned evidence remain identical. Unknown added fields or removed entries fail the comparison.

Re-run schema validation and `validate_excerpts_against_diff` on copies of the candidate reply. Select the whole new response only when validation returns no errors and it requires no host-side content rewrite. Pre-existing UNTRUSTED excerpts remain unchanged and untrusted. Do not upgrade them to VALID.

The host may reject a reply but cannot fill its source quotes, change coordinates, restore a dropped finding into an otherwise altered reply, or combine two partial model replies into claimed raw output.

### C4: bound the new request

At most one additional model correction request per pass invocation. A second invalid response is final for this mechanism.

Reuse `_invoke_api`, adding an internal one-shot option with a default that preserves ordinary callers. One-shot requires `max_attempts=1`, strict complete JSON, and disables syntax correction, continuation, output headroom retries and missing-excerpt repair. A truncation must raise immediately rather than enter the existing continuation branch at `llm_invoke.py:3578`. Do not modify CLI dispatch in this slice.

At initial `llm_invoke` entry, record `D = monotonic() + effective_invoke_timeout_s(...)`. Initial transport retry policy remains unchanged. Admit the added request only before D. Its timeout is `min(120, floor(D-now))` seconds; less than one second means skip. Use a local backend copy with that same timeout so `backend.timeout_s` cannot enlarge the remaining caller value. Do not change shared backend configuration.

This bounds admission and the new transport read deadline. It does not claim a universal hard wall-clock bound over existing retries, DNS resolution or blocking operating-system operations. An existing strict job deadline, cancellation or audit failure takes precedence. Propagate cancellation and audit-persistence exceptions; do not convert them into successful fallback results.

Compute the existing effective output cap as `output_ceiling` when positive, otherwise `max_completion_tokens or max_tokens`. The local correction cap is the smaller of that value and 8192 tokens. Set the local `output_ceiling` and active cap field consistently; retain the existing known `output_token_limit` guard. Never raise a configured cap.

The serialized UTF-8 correction request must be at most 128 KiB. On overflow, skip rather than truncate the original envelope or findings. Preserve existing headers, gateway routing and no-cache settings.

### C4a: bound response bodies, including HTTP errors

For the new one-shot request only, pass one absolute read deadline and a host-owned byte budget through `_invoke_api` to the selected `_invoke_openai` / `_invoke_anthropic` / `_invoke_vertex` dispatch and their body readers. Its deadline is no later than `min(D, correction_start + 120)` and is never renewed at an HTTP error branch, SSE event or redirect. Normal initial requests retain their current behavior.

Success bodies and 4xx/5xx/3xx bodies all use bounded chunked reads inside the existing `_read_with_deadline` mechanism. The unaudited `HTTPError.read()` branches at `llm_invoke.py:3880`, `:4065` and `:4287` are not acceptable for one-shot operation. A body-read timeout keeps the observed HTTP status as diagnostic context, marks body text unavailable/incomplete, preserves known usage as known and unknown usage as unknown, and terminates the correction. Do not retry or let a secondary body-read exception erase an active interrupt/audit failure.

To avoid implicit urllib redirect drains escaping these limits, the new correction request refuses all redirects. Unaudited/plain transport uses the existing `_NoRedirect` opener; audited OpenAI passes an optional `max_redirect_hops=0` to `OpenAiSendTransport` and then `open_with_redirects`. Add only this argument plumbing in `invocation_send_audit.py`, with the existing default of 10 retained for every old caller. The refused 3xx body is read once under the same byte/deadline controls. No new redirect request follows. This limits the new recovery opportunity only; the original review reply remains rejected on correction failure. Ordinary initial redirect behavior is unchanged.

Before any correction body bytes reach a parser, SSE line assembly or diagnostic accumulator, wrap the response in a one-shot bounded reader. Cover `read`, `read1`, `readline` and iteration; never delegate an unbounded negative-size read or unlimited readline to the underlying HTTP response. Use chunks of at most 65536 bytes and a single response budget of **1048576 bytes (1 MiB)**, including non-content SSE frames, reasoning, errors and any bytes after DONE that the existing reader consumes. Checking Content-Length alone or the model token cap is insufficient. A one-byte lookahead may be read solely to detect overflow; it is neither decoded nor retained as complete content. Count bytes yielded by the underlying reader only once, before they enter an optional `WireCapture`, so plain, non-strict-audit and strict-audit paths have the same decoding limit. Existing capture/storage quotas remain additional limits, not substitutes.

On response overflow, stop reading and decoding, mark the correction response incomplete and retain only bounded metadata plus an explicitly labelled prefix if it fits C5a. Never parse an over-limit prefix as a complete reply, select it, or claim full raw retention. Keep the original invalid response selected, so the existing evidence gate still refuses it. A19/A20 exercise finite drip and overflow responses at the real HTTP-reader boundary. This is a bound on newly added correction acquisition, not a claim that the pre-existing initial-response path is globally memory-bounded.

### C5: retain raw responses and account for failures

Add a default-empty, `repr=False` host diagnostic field to `LLMResult`, not to model JSON or the receipt schema. Its JSON-serializable records contain the initial raw reply, correction raw reply when available, frozen-diff digest, permitted indexes, whether the replacement was selected, and a bounded reason code. It carries no completion authority.

On success, replace `content` and `raw_response` together with the complete correction reply. On failure, retain the original content and raw reply; the caller's original rejection behavior remains. Do not label reconstructed JSON as exact acquired text.

Factories attach the loop-owned pass name and keep these records on a dedicated host list, `correction_observations`, never on `raw_observations`. The two are different evidence lanes and must stay separate: `machine.py:4107`, `:4137` and `:4218` read a non-empty `producer.raw_observations` as "this round is not a pure evidence-rejection round", so a correction record placed there would silently downgrade `evidence_rejected` to an unproved action, and `state.py:731-737` raises `CorruptedStateError` when that downgrade meets an already earned clean cycle on the same source hash. Persisting the diagnostic and keeping the rejection classification are therefore both required, and only a separate lane satisfies both.

`write_receipts` gains one optional keyword `correction_observations: list[dict] | None = None`. It appends those payloads **after every existing artifact source**, in the fixed order `attempted_excerpts` → `raw_observations` → the conditionally filtered `pending_raw_responses` block at `receipt.py:486-493` → `correction_observations`. Inserting before that pending extension would shift each pending response's ordinal and rename its artifact, because `receipt.py:497-516` numbers the concatenated list with `enumerate`. The keyword defaults to `None` so every current caller keeps its behavior. `machine` snapshots the provider's `correction_observations` the same way it snapshots `raw_observations` and passes the snapshot through; no purity-gate condition is edited. Grouped review merges every child's `correction_observations` into the composite provider the same way it already merges `attempted_excerpts`, `raw_observations` and `pending_raw_responses` at `factories.py:1085-1093`, wrapping each observation in `GroupedReviewAttempt` so group attribution comes from the loop and never from a model field; the composite is the provider `machine` snapshots. Grouped review also includes that wrapper's persisted metadata in the reservation. The initial mismatch is diagnostic, not an unresolved `attempted_excerpts` entry. If the final reply is still rejected, current rejection registration runs normally. Never remove an already registered final rejection.

A writer failure while persisting diagnostics must continue to block completion through the current receipt-write error gate. Correct final evidence does not authorize losing its audit record. Under host send audit, both requests share the same send scope and the existing `excerpt_repair` cause; no new owner or independent allowance is created.

Aggregate usage through `_sum_usage`, retaining unknown fields as unknown. Count observed usage on success, invalid JSON, repeated mismatch and timeouts. On a failed correction, use its observed usage when available; otherwise sum an explicitly unknown `Usage`, not a known zero. Record total wall duration from the initial entry, including eligibility checks and correction. Do not record a failed request as zero cost. Keep diagnostic execution, evidence acceptance and product verdict separate.

Retain the correction's acquired text when returned or carried by `InvalidJSONResponseError` / `_TruncatedResponse`; a timeout with no complete text is explicitly unavailable. The one-shot branch must preserve these exception payloads long enough to record diagnostics and usage. Memory exhaustion, interrupts, lifecycle closure and audit-persistence failure propagate rather than being swallowed by a broad recovery catch.

### C5a: budget additional raw diagnostics locally

These are local byte limits, independent of requested tokens and host capture quotas. Count UTF-8 bytes, not Python characters. Do not serialize large hostile data before checking its bounded raw size.

- Before a new request, require the initial `raw_response` to be at most **131072 bytes (128 KiB)** and the complete request to meet C4's 128 KiB bound. Otherwise skip recovery with zero added requests/copies; retain the existing rejection path. Do not truncate an initial reply to make it eligible.
- A returned correction's complete raw model text may be at most **131072 bytes (128 KiB)**. Even when the total wire body fits 1 MiB, an oversized decoded text cannot be selected. Stop response accumulation at that threshold where the format exposes deltas; non-streaming decoding may hold only the bounded 1 MiB body and a bounded number of derived copies before this check. This is an O(limit) bound, not a claim about an exact process-RSS ceiling.
- One correction diagnostic record, including original and correction text, escaped JSON, fixed metadata, pass/group attribution and the outer persisted artifact wrapper, must fit **1048576 encoded bytes (1 MiB)**. Serialize/check the exact writer shape (`ensure_ascii=True`, current indentation/outer fields) conservatively before appending it to `correction_observations`; do not compare only the raw strings' lengths. Use a single bounded-record builder in factories for this path and cover its persisted output with a writer test.
- Each top-level provider invocation owns a thread-safe **8388608-byte (8 MiB)** allowance for newly added correction diagnostic artifacts, including every record written through `correction_observations`. Reserve one full 1 MiB slot *before* each new request; hold it until the provider invocation ends, without refund after dispatch. The three parallel passes share the allowance. `build_grouped_l1_provider` creates one root allowance and shares it with every child provider rather than granting 8 MiB per group. Independent top-level reviews own independent allowances; pre-existing receipt storage and long-term retention remain outside this new limit.
- `llm_invoke` receives an optional internal host allowance alongside `review_diff`; a direct standalone call with `review_diff` but no provider owner gets only one 1 MiB slot. Factory child invocation plumbing passes the current root allowance, never a model-supplied budget or a persistent globally shared counter. On reservation failure, make zero new requests and return the original reply; do not accumulate an unbounded list of per-skip raw diagnostics.
- If response/decoded-text/serialized-record overflow occurs after dispatch, refuse the replacement. Use at most **4096 encoded bytes** of metadata from the reserved slot: reason, complete=false, observed byte count or a lower bound, source digest and known usage. Raw text is unavailable or an explicitly incomplete bounded prefix, never a purported complete reply. Only successful, fully retained records can accompany a selected correction. The factory-side outer-wrapper check runs before that pass is admitted to finding/excerpt collection. If this final check overflows despite the pre-send reservation, refuse the pass through the existing failure path and record bounded metadata from its slot; do not admit the replacement without its record. Resource refusal leaves the original invalid evidence, or the explicit unresolved pass failure, on a non-PASS path.

This bounds new correction acquisition and new diagnostic artifact payloads for one provider invocation; it does not retroactively cap every old initial-response/rejection artifact, all process memory or total storage over unlimited invocations. No raw diagnostic may bypass the root reservation by using `correction_observations` or any other field. A20/A21 verify unaudited, ordinary-audit, strict-audit, concurrent and grouped cases, plus JSON escaping expansion.

## Change surface and invariants

Planned production files:

- `llm_invoke.py`: optional frozen evidence input and operation-owned allowance, narrow eligibility and invariants, correction-only deadline/byte-bound body reader, one-shot API option, raw-result selection, diagnostics and usage. Thread optional correction-only controls through the three API format functions; ordinary calls retain default behavior.
- `factories.py`: derive the canonical evidence diff, own/share the per-invocation allowance, propagate diagnostics and check their final encoded-size budget. Ordinary, concurrent and grouped paths share those rules.
- `invocation_send_audit.py`: only optional redirect-hop-limit plumbing for the correction request; retain the current default for initial requests. The correction-only bounded response reader lives in `llm_invoke.py` and is applied before audit capture/decoding, not as a looser capture quota.

`receipt.py` receives exactly one additive keyword on `write_receipts` and one extra member in the existing `artifact_payloads` concatenation. `machine.py` receives one snapshot field, its assignment next to the `raw_observations` snapshot, and one keyword at the single `write_receipts` call site. Neither change edits a purity gate, an acceptance predicate, a lane's authority or a schema. Do not change production logic in `verify.py`, `reviewer_json.py`, `diff.py`, `diff_grouping.py`, `cli.py`, `cross_repo.py`, `receipt_scope.py`, `invocation_stream.py`, `invocation_redirect.py`, or any schema. Tests may exercise them. If implementation needs a changed acceptance criterion there, stop that change and revise the design before implementing it.

The initial review prompt and `REVIEW_JSON_CONTRACT` stay byte-identical. Do not recapture the L1 prompt oracle for this slice. Pin the correction prompt in separate tests.

Deviation (recorded after implementation): the post-image block gained one sentence telling the reviewer that a `N: ` prefix is an annotation rather than source. The L1 oracle `l1_prompt_digests_pre_p59.json` is untouched; the separate Outlet C off-path oracle was recaptured and its old digests kept in `superseded_digests`.

## Acceptance

A predicted rejection is a test expectation, not an execution error. Timeout, collection error or zero selected tests cannot satisfy a row.

| ID | Input or fault | Required outcome |
|---|---|---|
| A01 | The two archived missing-period excerpts | One correction, no source/schema errors, both raw replies retained within local budgets |
| A02 | Nonempty findings, finding-owned evidence and 17 other correct excerpts | Correct full reply leaves all nonpermitted fields identical |
| A03 | Correction still contains the same errors | No further request; original evidence reaches the existing rejection path |
| A04 | Drop finding, change severity/coordinates/order, add field, substitute bool for int | Reject replacement |
| A05 | Valid reply or only UNTRUSTED indentation | Zero new requests; old result unchanged |
| A06 | Missing excerpts, truncated JSON, unknown file/line, noncanonical fields | No new literal-repair path; existing repair or rejection behavior unchanged; single-line and multi-line offset ambiguity is tested in A17 |
| A07 | One-shot receives 429, invalid JSON, truncation or timeout | At most one added model request; no continuation/failover; observed usage retained; HTTP body deadline/overflow tested in A19/A20 |
| A08 | D expired, cancellation or strict-audit failure | No added network entry, or propagate active cancellation/audit error |
| A09 | Request above 128 KiB; quoted delimiter/instruction in evidence | Oversize skips; untrusted data cannot pass changed findings or invalid source through the gate |
| A10 | Correct final reply through writer, reader, state machine and terminal verifier | Receipts completed; evidence accepted; product verdict still obeys existing finding rules |
| A11 | Correct evidence with a controlled product finding | Finding retained and independently processed; evidence recovery alone cannot produce product PASS |
| A12 | Receipt or raw diagnostic write failure | No completed/PASS delivery; failure remains traceable |
| A13 | Serial/concurrent API passes, grouped and canonical joint evidence diff; CLI controls | Correct pass/group/source binding and root allowance sharing; CLI has zero new requests and unchanged behavior |
| A14 | Working candidate changes during correction | Validate only the captured evidence diff; existing source-drift checks remain effective |
| A15 | Automatic recovery during a real full Forge LOCAL run | Both raw replies, extra usage and duration retained; unchanged negative controls reject |
| A16 | Two excerpts at the same file/start/end: index 0 INVALID and index 1 VALID (repeat with index 1 UNTRUSTED) | Changing only index 0 may pass; changing index 1 as well, including only tolerated trailing whitespace, must reject. Inject a coordinate-set mask in place of the index mask and observe this negative fail |
| A17 | Single-line `new.` at range 1-1 when it belongs at line 2; two-hunk multi-line sequences displaced by +100 and -100, both exact and leading/trailing-whitespace variants | Each complete sequence matches another contiguous frozen range: zero new requests, original reply unchanged. Positive controls `neW.` at 2-2 and a two-line non-tail character error match no other range and remain INVALID without tail repair. A sequence crossing unknown coordinates, partial block, or identical text only in another file/repository must not trigger this offset guard. Inject the old single-line-only guard and the old bounded-window search separately; both must expose the multi-line negative cases |
| A18 | Two repositories share `a.py`; display context includes `## Repo:` after a hunk; forged quote reaches that heading | Correction eligibility, writer and terminal use canonical `repository_scope` digest/paths. A heading is never source; zero correction for the forged range. A real same-path mismatch in one qualified repository may recover without touching its sibling |
| A19 | For each API format, finite 429 and 500 bodies drip below socket-idle timeout past the correction deadline; plain and applicable audit modes | Body reading exits on the single absolute deadline, no retry, HTTP status retained as context, body incomplete, usage unknown unless observed. Test refused 3xx similarly and assert zero redirect follow-ups. Underlying body calls are bounded reads, never `read()` without a size |
| A20 | Correction wire body exceeds 1 MiB, including a no-newline SSE frame, reasoning/non-content bytes or HTTP-error body; decoded raw model text exceeds 128 KiB inside an in-limit wire envelope | No parser/line accumulator receives more than allowed; at most one-byte overflow lookahead; stop without draining unlimited tail. Original invalid evidence stays selected, incomplete metadata at most 4096 encoded bytes, no completed correction/PASS. Cover plain, ordinary-audit and strict-audit modes where supported |
| A21 | JSON escaping expands the diagnostic above 1 MiB; three parallel passes and more than eight grouped correction candidates | A record exceeding final encoded size is not appended or selected. Root allowance is shared and reserved before sends: at most eight slots/requests per top-level invocation, no refund after dispatch; skipped candidates issue zero sends and add no unbounded raw diagnostics. Actual persisted new diagnostic bytes do not exceed 8 MiB |
| A22 | Zero-findings round where one pass dispatched a correction and another pass still has rejected evidence, on a source hash that already holds an earned clean cycle | `clean_credit_action` stays `evidence_rejected`, its `evidence_rejections` row is written, and the earned window still validates. The correction record persists only on `correction_observations`; `producer.raw_observations` stays empty. Injecting that record into `raw_observations` instead must make this row fail |

First implement failing A01/A03/A10 tests for the new wiring. Withdraw the actual production fix and observe FAIL, then restore it and observe PASS. Separately inject acceptance of changed findings, changed coordinates, repeated mismatch, nested retry, omitted cost and swallowed write failure. Check selection counts and import health before interpreting an injection. For the new revision, also withdraw the canonical evidence-source selection (A18), whole-sequence same-file offset guard (A17), index-based mask (A16), deadline-aware HTTP-error read (A19), local byte limiter and root allowance sharing (A20/A21), and the dedicated diagnostic lane (A22). Read the failed assertion, not just the test exit code: collection errors and earlier unrelated rejection are not proof of the intended guard.

Existing feasibility probes cover parts of A01/A02/A03/A10/A11, not the automatic wiring plus strict host recording plus request limit. They do not admit implementation as finished.

Heavy regression and mutation sweeps run on an existing remote test environment. The office laptop runs only bounded targeted probes. Missing remote dependencies require environment reconciliation, not an unapproved install.

## Measurement and release

Freeze one exact implementation tree, input set and review settings. Use the six user-approved agnes routes as equivalent traffic entries. Report initial acceptance, correction activation, recovered acceptance, final rejection, extra input/output tokens, duration and changes to product findings. Include failed attempts; do not infer stability from the few live probes.

The earlier optimization commit is a local WIP checkpoint, not a three-round-approved delivery. The final combined deliverable needs its applicable tests, real defect withdrawal, findings closure and three clean independent code-review rounds before publication.

## Literature and existing implementations

All four requested search channels were attempted. Exa, Tavily and Firecrawl returned results. Context7 returned HTTP 401; it was recorded, not silently substituted. Primary extracts and channel status are at the experiment root as `literature-primary.json` and `literature-channel-status.json`.

- Stechly et al., *GPT-4 Doesn't Know It's Wrong: An Analysis of Iterative Prompting for Reasoning Problems*, <https://arxiv.org/abs/2310.12397>. It distinguishes self-critique from external correct feedback. Another model attempt is not proof; this design keeps deterministic source validation authoritative.
- Madaan et al., *Self-Refine: Iterative Refinement with Self-Feedback*, <https://arxiv.org/abs/2303.17651>. It supports studying refinement, not guaranteed recovery or unbounded retries for this workload.
- Instructor official retry documentation, <https://python.useinstructor.com/concepts/retrying/>. It separates validation retries from transport retries and includes exact-source quotation validation. Do not copy its `max_retries` meaning as a physical-send bound.
- Pydantic AI output and retry documentation, <https://ai.pydantic.dev/output/> and <https://ai.pydantic.dev/retries/>. Output validators can return feedback; layered retry budgets multiply. This is the reason for an explicit one-shot correction.
- Repository metadata read through W1: `567-labs/instructor`, 14112 stars, pushed `2026-11-14T02:00:31Z`; `pydantic/pydantic-ai`, 21199 stars, pushed `2026-11-14T01:52:22Z`. These are active implementation references, not quality endorsements. Neither dependency is added.
- This repository already has `_repair_missing_excerpts`, `_sum_usage`, strict parsing, per-send audit and raw diagnostic storage. Reuse those capabilities rather than add another generic retry framework. The missing behavior is present-but-wrong evidence, exact full-reply selection and an explicit one-shot limit.
