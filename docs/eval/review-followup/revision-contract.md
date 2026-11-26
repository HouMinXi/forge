# Revision follow-up: offline record boundary (C1–C2)

Part of [Forge #574](https://github.com/HouMinXi/forge/issues/574). This
contract pins record identity and gives the later validator and adjudicator
independent expected results. This slice imports records only. Import success
does not admit evidence, establish a ruling, or close a finding.

## Record identity and bytes

A replay batch belongs to one evaluation catalog. Within it, a review item
is the preregistered pair `(finding_id, candidate_sha)`. The catalog must
namespace finding IDs across projects before import. Proposal, host
validation and human ruling IDs belong to separate namespaces. The same
kind and ID with the same canonical bytes is a no-op. The same kind and ID
with different bytes rejects the entire import, with no partial result. The
returned records are sorted by kind and ID; input order cannot affect a
successful import.
Envelope version 1 has exactly these keys:

| Key | Value |
| --- | --- |
| `schema` | Integer 1, not a boolean. |
| `kind` | `proposal`, `host_validation` or `human_ruling`. |
| `id` | The PR1 identifier syntax. |
| `item` | Exactly `finding_id` (PR1 identifier) and `candidate_sha` (PR1 revision, including labeled synthetic revisions). |
| `body` | Object with the kind-specific fields below. |

The canonical byte rule is Python's `json.dumps(value, ensure_ascii=True,
sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")`.
Sort object keys by Unicode code point; preserve array order. There is no
trailing newline or Unicode normalization. Strings must be Unicode scalar
sequences (no lone surrogates). Permit null, booleans and integers in
±(2^53−1); reject floats, including integral-valued floats, and duplicate
keys on decode. Inputs are UTF-8 JSON using the existing per-file and batch
byte limits, with a maximum nesting depth of 32 (envelope at depth 1; keys and values
of members sit one level below their containing object). The 1 MiB
per-record and 32 MiB per-batch limits apply separately to both the UTF-8
input and the canonical output; the output budget counts distinct records,
while repeated input still consumes input budget. This ensures any
successful batch can be re-imported from its canonical records unchanged.
SHA-256 hashes the entire canonical envelope; the digest is not a field in
that envelope. This is not the encoding for PR1 artifacts: their digest
continues to hash the original, unmodified bytes.

## Payloads and meaning

These are the top-level body keys. Mechanical import does not authenticate
a record or validate the meaning or validity of its field values. Consumers
must establish those before using the record. Null IDs on mandatory links
are not permitted when making a ruling eligible.

| Kind | Body keys and meaning |
| --- | --- |
| `proposal` | `claim`, `constraint_ref` (ID and version), `suggested_status`, `evidence_refs` (ordered artifact identity, purpose and selector), `supersedes_proposal_id` (null or ID; never supersedes a ruling). |
| `host_validation` | `proposal_id`, `proposal_digest`, `contract_version`, `result` (passed/rejected), `reason` (null or proposal-level reason), `reference_checks` (one ordered result per proposal reference, with resolved identity, exact artifact digest, actual revision, role and selector, or a refusal). |
| `human_ruling` | `proposal_id`, `proposal_digest`, `validation_id`, `validation_digest`, `adopted_ref_indexes` (zero-based positions in successful validation results), `reviewer_id`, `conclusion`, `reason`, `remaining_gaps`, `supersedes_ruling_ids`. |

The host checks existence, role, revision and scope against verified
artifact bytes, not a display path or a digest supplied by a proposal. An
honest reference to a whole session can pass even if the session says
nothing about the target call. A reference to a nonexistent call must fail.
Historical context may coexist with current evidence, but only the latter
can support current behavior. A closure ruling must actually adopt at
least one validated current reference. A human decides whether the adopted
evidence answers the claim; a zero exit code alone does not.

## Replay and projection requirements

- Keep processing separate from meaning. Missing validation,
  `host_rejected`, `pending_human` and item-level `conflict` are open.
  `insufficient_evidence` is an explicit human conclusion, not a default.
- The caller supplies the frozen, verified active evidence snapshot.
  A ruling is eligible only if its proposal and validation IDs **and**
  digests match, its validation passed, and all required references still
  resolve to the same content, revision, role and scope. Changing an
  unrelated artifact does not invalidate a ruling. Never use an old
  artifact simply because its path or revision label still matches.
- Check supersession references on the complete batch. Missing target,
  cross-item link, self-link or cycle is an item graph error, not a closure.
  Only direct edges between eligible rulings remove a head. An ineligible
  intermediate ruling does not carry a successor's edge back to an earlier
  eligible ruling. A unique eligible head is effective; multiple heads
  conflict. An explicit correction naming all eligible heads resolves the
  conflict. Arrival order, timestamps and repeated imports cannot decide it.
- An effective `verified_fixed` or `no_longer_applicable` closes only in
  the read-only projection. `still_present` and `insufficient_evidence`
  remain open. For no longer applicable, preserve the old premise, its
  constraint version and why it legitimately ceased to apply.
- Count coverage over preregistered items, closure proposals by unique
  proposal ID, and final closures by unique effective ruling per item.
  Independent verification binds to that ruling's ID **and** digest.
  Unreviewed closures are not correct by default, and error rate with no
  reviewed closures is unavailable.

## Acceptance inputs for the next slice

### Ruling selection, not evidence adequacy

The nine rows in `revision/decisions.json` test evidence identity and which
ruling takes effect. The supplied conclusions are hand-authored inputs to
that test, not judgments independently verified by the fixture. A and B
are pre-labeled `verified_fixed` using X and Y; B names A. C is pre-labeled
`still_present` using Z and names B. D also uses Z and names A and C. In
particular, X/Y show positive **totals** and do not exercise the offending
`[-1]` input shown in Z. They cannot establish a fix for that input.

A replacement selects different observation bytes under the same artifact
ID, path, revision and role. The fresh-validation case retains the old
ruling and historical validation, but introduces a matching validation for
the replacement bytes without a new ruling. The order control repeats the
batch in reverse with a duplicate. The table supplies expected outcomes;
it is not an output of a projector.

### Evidence adequacy, not ruling selection

`revision/evidence-adequacy.json` is a separate set of synthetic, saved
observations with hand-authored human judgments. The claim is deliberately
narrow: the single-negative input `[-1]` must return a non-negative total.
The passing and failing controls use the same check, input and predicate
on separate labeled candidates. The passing target supports the narrow
claim; the same check failing supports `still_present`. A legitimate
passing observation for a positive input alone is admissible to the host
but leaves the human conclusion `insufficient_evidence`. Do not infer
that the passing control proves more general behavior, or use these
labels to test supersession.

The C4 tests pin the saved transcripts as candidate excerpts, build real
C3 host records, and replay the nine prewritten expectations. The separate
adequacy controls replay human inputs; no test program is executed and
no human adequacy label is inferred. C5–C7 add summary and independent
scoring. Live adapters, model calls, candidate execution, receipts and
merge gates are out of scope.

## C3 host entry point and selectors

`revision_validation.evidence_snapshot(review, finding_id, saved_observations)`
takes an independently validated PR1 review and a preregistered finding ID.
PR1 source evidence supplies its own role, revision, and verified bytes.
The optional saved observations are pairs of caller-assigned artifact IDs
and `VerifiedArtifact`s from the pinned loader under
`saved_observations/<case>/`. Their closed schema is
`schema, case, input_sha256, candidate, session, call_ids`. The host checks
case, admitted candidate, and exact review-input digest against the review
and requires unique observed call IDs. It does not treat a completed PR1
review run as evidence of an individual call. Only current-candidate
observations are admitted here; historical source is allowed as background.

`validate_proposal(raw, snapshot, validation_id=...)` accepts an offline
proposal record and emits a canonical host-validation record. References
are closed objects with `artifact_id, purpose, selector`. Purpose is
`current_basis` or `historical_context`. Supported selectors:

| Selector | Exact fields | Check |
| --- | --- | --- |
| Source excerpt | `kind: source_bytes, start, end` | Nonempty half-open range, measured in original bytes from zero. |
| Session | `kind: session, session_id` | Session ID is present in the admitted observation; no inference about calls or assertions. |
| Call | `kind: call, session_id, call_id` | Session and call IDs actually present; no inference about the outcome. |

C3 admits at most 1,024 references per proposal, including repeats. Since
each host binding grows the corresponding reference, an admitted proposal
must leave space for a round-trippable host record. Above the limit, C3
returns `too-many-evidence-refs: proposal.evidence_refs` before resolving
references; it does not truncate them or emit a partial host record.

Each check preserves proposal order and returns either a resolved binding
(ID, original digest, actual revision, role and selector) or a refusal.
Malformed input or item mismatch rejects before emitting a host record;
a syntactically valid but unresolved or wrong-role reference yields a
rejected host record. The proposal's suggested conclusion must be one of
the four semantic outcomes, but the validator does not endorse it.
Closure proposals must have at least one passing current reference.
A valid whole session can therefore pass the host for a closure proposal
without establishing that the proposed closure is correct. The proposer
cannot assign its own revision or override the evidence catalog.
Supersession, active ruling selection, and independent evidence-adequacy
judgments remain separate work.


## C4 effective ruling

`revision_adjudication.decide_revision(snapshot, raw_records)` imports a
complete offline batch under C2's limits and projects one preregistered
item. It recomputes C3 results using the **caller-selected active
snapshot**, not a catalog supplied by a proposal or ruling. To be active,
a saved validation must match the full recomputed body (including JSON
types and every ordered reference), proposal ID/digest, and the item. A
human ruling must also match the exact active validation ID/digest and
adopt only indexes of valid references; a closure must adopt current
evidence. An identical proposal with newly validated bytes still needs a
new ruling.

Proposal and human-ruling supersession are separate graphs. Declarations
are checked against their own kind in the complete batch for missing
targets, self-links, cross-item links and cycles. Such graph errors keep
the item open and identify the declaring record kind. An invalid proposal
link type is also a graph error. A valid proposal link records lineage;
it does not revoke or replace an existing human ruling. Only a direct edge from an eligible
ruling to another eligible ruling can remove a head. More than one head
keeps the item open as a conflict; a correction can name all heads.
Without an effective ruling, a unanimous proposal processing state is
reported as-is. Differing proposal states are reported as `pending` with
each state exposed; no proposals is `no_proposal`. None of these states
implies a human `insufficient_evidence` conclusion.

The result retains the exact effective ruling (including its digest)
for later independent scoring. It does not write to the repository,
resolve comments, or judge whether a human's rationale is sufficient.
