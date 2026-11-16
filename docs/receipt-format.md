# Receipt format: `reviewer_response_sha256`

Each accepted pass writes `.code-forge/receipts/receipt-c<cycle>p<pass>.json`. This page documents the field added for #222. It doesn't cover the other fields.

| Field | Type | Present when |
|---|---|---|
| `reviewer_response_sha256` | 64-char lowercase hex | the pass's raw reviewer response was retained (accepted text) |
| `reviewer_response_shingles` | sorted list of 16-char lowercase hex | same as above (#240) |

**Value.** SHA-256 of that cycle's raw reviewer response for the pass, after normalization:
- JSON, bare or inside a ```` ```json ```` fence, is re-serialized with sorted keys, compact separators, and whitespace runs inside every string collapsed to one space.
- Non-JSON text has all whitespace runs collapsed.
- Several responses for one pass (grouped review) are hashed individually, sorted, joined with `\n`, and hashed again.

Reformatting or re-indenting a reused response doesn't change its hash. Changing any word does. The hash is evidence that the answer was produced again, not proof of an independent review.

**Shingles (#240).** The normalized response (as above, so JSON key order and layout don't matter) is casefolded and split into tokens: maximal runs of Unicode word characters (`\w+`). Punctuation, quotes and JSON syntax only separate tokens. Each window of 3 consecutive tokens is one shingle; a response with fewer than 3 tokens is a single shingle, and an empty one has none. Each shingle is stored as the first 16 hex digits of the SHA-256 of its tokens joined by one space, so no response text is kept. A grouped pass stores the union over its responses.

**Use in verify check 7 (hardened path).** Excerpt line-set Jaccard overlap above 0.8 between two of the last cycles still fails, unless:
- every receipt of both cycles carries a valid `reviewer_response_sha256`, **and**
- no hash appears in both cycles, **and**
- no response of one cycle is a near duplicate of any response of the other. A pair is a near duplicate when either holds:
  - shingle Jaccard ≥ 0.9 (two empty sets count as 1.0). This catches a copy with a nonce key, one changed word or a case change.
  - containment ≥ 0.9, where containment = |A ∩ B| / min(|A|, |B|), and the smaller set has at least 20 shingles. This catches a reused answer padded with new text, which lowers Jaccard because the union grows. The 20-shingle guard (about 22 tokens, one short paragraph) keeps a one-line verdict that happens to appear inside a longer answer from counting as reuse. Below the guard, Jaccard alone decides, so nothing is relaxed. At 0.9, up to 10% of the smaller answer can be shared incidentally (a quoted finding title or code line) without being read as reuse.

A small diff whose unfixed finding is honestly re-quoted every cycle therefore passes, provided each cycle's answer is written afresh. Copied, reused or lightly edited answers fail.

**Known trade-off (#240 option 2).** An honest re-review of the same unfixed finding that comes out near-identical (≥ 0.9) can't be told apart from a lightly edited reuse, so it fails check 7. That brings back part of the #222 false positive. Containment makes this stronger. An honest re-review that repeats ≥ 90% of its earlier analysis word for word and then adds new paragraphs also fails, even though the new text makes it a longer, partly fresh answer. Very short responses go the other way: one changed word moves many of their few shingles, so a short edited copy can fall below 0.9 and only exact replay catches it. A receipt missing either field, or with a malformed value, keeps the previous behaviour, so the overlap alone decides. The legacy, non-hardened path is unchanged.

## Phase 7 wave 2b: host provenance and receipt subtypes

Plan authority: `332-host-failure-classification/phase7-plan.md` sections
3 (W2-6), 5 (owner decision), 5.2 (unavailable producer cause), and 5.3.
New receipt findings carry host-written `source` (the StateFinding source)
and `channel`; neither is copied from reviewer JSON. Both are covered by the
existing whole-receipt SHA-256 digest. They only refine display labels: basis
verification, verdicts, `failure_kind`, accounting and clean-credit routing
are unchanged.

| Host observation | Receipt channel | Optional receipt subtype |
| --- | --- | --- |
| Ordinary product or UNTRUSTED reviewer candidate | `product` | absent |
| INFRA product candidate after falsifier failure | `product` | absent |
| Host schema-fail producer, exact ID/fingerprint/sentinel | `gate` | `RECEIPT_SCHEMA` |
| Host incomplete-coverage producer, exact ID/fingerprint/sentinel | `gate` | `RECEIPT_COVERAGE` |
| Host invoke/spawn producer, exact ID/fingerprint/sentinel | `execution` | absent |
| Host-typed mutation infrastructure failure | `execution` | absent |
| Other/unrecognized finding structures | `product` (no trusted infrastructure claim) | absent |

UNTRUSTED reviewer text cannot acquire a host label by imitating an ID, path,
or description. INFRA alone is insufficient: a product candidate can have
that source after falsification failed. The report now retains the host source in its existing `fields.source` slot,
so generic INFRA and UNTRUSTED origins remain distinguishable; INFRA still
does not imply a trusted non-product diagnostic. Existing placement and
counts do not move; producer subtype labels are additive `host_failure_kind`
values, not a new reporting or credit policy.

Receipt gate failures carry display subtypes at the producing boundary:
schema-unavailable with an explicit producer cause, hunk/coverage checks, or literal/excerpt
rejection (`RECEIPT_EVIDENCE`). Untyped failures keep `RECEIPT_INVALID_GATE` (including stored-receipt read/parse errors, whose existing exception combines I/O and schema failures).
These labels may be persisted as optional `StateFinding.receipt_subtype` and
are displayed only beside recognized host producer geometry. Unknown values
do not gain authority or an infrastructure label.

An unavailable rejected payload now records its cause at the producer, before
its exception is discarded: `schema`, `evidence`, or `mixed` across grouped
observations. The per-pass receipt's optional `unavailable_cause` retains that
fact; existing preserve eligibility checks remain unchanged. The mapping is
cleared at every invocation/round and cannot leak across calls.

Old receipts lacking source/channel retain their previous ambiguous reason.
Unknown, partial or inconsistent provenance likewise retains that reason,
never a trusted infrastructure label. Valid provenance distinguishes an
UNTRUSTED candidate, an unverified product candidate, and a host diagnostic
only after the existing basis check already found it unresolved. Old readers
ignore the additional fields. Existing basisless receipts and healthy CI
single-cycle evidence acceptance retain their prior behavior.

Report-reader compatibility is separate from receipt-reader compatibility:
older report parsers have a closed subtype vocabulary and reject the additive
classification block when it contains a new subtype. They retain the existing
legacy report fallback; they do not reinterpret the new subtype as a trusted
status. No report schema version or clean-credit policy is changed. The existing
`fields.source` slot was already supported; old blocks with a null source remain
readable.

## Bounded count-evidence regeneration

Ordinary single-repository API L1 calls can ask once for a new complete response
when a canonical root excerpt has a line-count error beyond the existing
one-line tolerance. The original quote must be an exact prefix of the same
frozen source starting at the declared coordinate. Its full declared range,
and any extra quoted tail, must be known within one hunk. Unknown source,
internal omissions, different literals, displaced or repeated matches, malformed
fields, and other invalid root evidence do not qualify. Already-valid one-line
count differences stay unchanged and never trigger this added request alone.

The declaration remains the target: only the affected `content` strings may
change. Findings, coordinates, excerpt order/count, and all other fields must
remain identical. The selected response is the model's entire new JSON object;
the host does not assemble snippets from two responses or add source characters.
Every selected excerpt must be VALID against the frozen diff without host tail
repair. A regenerated target must also match its granted frozen lines exactly,
with only an optional final LF terminator; ordinary count tolerance cannot
extend that target. The original factory, receipt, coverage, and terminal checks
still apply. A failed or ineligible regeneration leaves the original refusal intact.
This is a recovery strategy, not evidence that a historical rejection was wrong.

Count and literal corrections use one mutually exclusive decision per call,
share the existing allowance, and each spend at most one extra physical request.
There is no continuation, redirect, JSON-correction retry, or recursive recovery
on that request. Existing allowance lifetimes remain unchanged: ordinary
providers allocate eight slots per invocation, while grouped providers share a
root allowance across that provider instance's calls. No exhausted slot is
refunded and no new allowance is created for count recovery.

Correction send admission and body reads share an absolute deadline no later
than the initial call's entry deadline, capped at 120 seconds, with at most 8192
output tokens. Prompt and opener construction consume that original window;
expired preparation sends nothing. Synchronous urllib DNS and response-header
acquisition retain their existing per-socket timeout behavior; this does not
promise a universal wall-clock bound for those phases. Encoded request/response bodies are capped at 1 MiB and retained raw
responses at 128 KiB. Additional conservative eligibility and prompt bounds can
skip recovery rather than spend a request on oversized evidence.

Feedback uses host-computed indexes and declared/actual counts, never exception
strings. Source context comes only from the original frozen scope and goes to
the same configured provider. Both initial and replacement raw responses remain
in the private `correction/` lane with the host-selected strategy and outcome.
That lane confers no completion authority. Observed initial and follow-up token
usage is summed once, including failed attempts; unknown usage stays unknown.

## Provider refusal statistics at strict audit boundaries

Explicit provider refusal metadata stops the affected API acquisition without
JSON, continuation, or headroom retries. Ordinary syntax-correction,
continuation, and headroom refusal paths retain their already-observed recovery
response usage and report elapsed time from the API entry.

Raw API strict-audit failures and pre-network send-deadline controls preserve
the existing primary exception's fields, arguments, and cause before any such
statistical enrichment. Its usage or duration can therefore describe only the
primary observation, or remain unavailable; preceding responses in the same API
call are not folded into those fields. Complete-call statistics after these raw
strict/control exits remain incomplete. Missing observations are not inferred
as zero, and no extra request is made to obtain them.

The separate count/literal outer recovery retains its existing accounting
contract: it can combine an already-returned initial response with follow-up
failure observations while preserving the exception's identity and cause.
That outer aggregation and raw API field preservation are distinct boundaries;
the raw API rule is not a claim of global field immutability across the outer
recovery layer. Evidence acceptance and clean-credit rules remain unchanged.
