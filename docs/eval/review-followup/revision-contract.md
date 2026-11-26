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

The checked-in `revision/decisions.json` is a hand-authored decision table,
not an output of the future projector. In the supersession family, all
rulings concern the same item: A is a verified fix using X, B is a verified
fix using Y and names A, C is still present using Z and names B, D is a
correction using Z that names A and C. Each has otherwise valid evidence,
validation and a human ruling. The changed-X cases keep the same artifact
ID, display path, revision and role while changing only the admitted
artifact digest. Revalidate without a new human ruling for the fresh
validation case. The historical validation stays in the batch but is not
an active matching validation for the current bytes. Replay the exact same batch in reverse order and with
duplicates in the order controls.

The table carries exact synthetic observation text (encoded as UTF-8),
artifact roles and paths, active mappings and the expected verdicts. A
replacement selects different bytes under the same artifact ID and path.
These are human-label simulations: the assertion text is saved input, not
a test this fixture runs. C3–C4 still need to materialize the verified
catalog, bind records and exercise these cases. Keep its expected outcomes
independent of the implementation. C5–C7 add summary and independent
scoring; live adapters, model calls, candidate execution, receipts and merge
gates are not in this work.
