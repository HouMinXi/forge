# Offline review follow-up (schema 1)

This is the saved-output baseline protocol for issue #574. It does not launch
Forge review, a provider, or a candidate program. The checked-in inputs,
observations and human labels are synthetic controls, not historical model
outputs or evidence of improved review quality.

## Replay

From a checkout, on a filesystem supporting hard links:

```sh
fixture=tests/fixtures/review_followup/synthetic
out=$(mktemp -d)
PYTHONPATH=src python -m code_forge.eval.review_followup validate "$fixture"
PYTHONPATH=src python -m code_forge.eval.review_followup replay "$fixture" --output "$out/report.json"
PYTHONPATH=src python -m pytest -q docs/eval/review-followup
```

The offline suite is outside default CI collection. Run it explicitly; a green
repository-wide workflow is not evidence that this suite ran.

Exit 0 means the requested operation completed, not that a reviewer found
nothing. Exit 2 means input was rejected; a report can include a valid
`provider_failed` or `invalid_response` observation without returning 2.
Publication failure returns 1. Use a new output filename for each invocation;
a pre-existing result is deliberately not overwritten. Report publication
links a fully written temporary file and does not claim crash durability.
Once the link succeeds, inability to remove the scratch name does not turn
the published report into a failed run; a scratch file may remain.

## Frozen input and scoring boundary

All JSON schemas are closed; examples in the fixture tree are the v1 field
reference. Integer schema value 1 is required. Case IDs are unique in the
manifest, and a lineage cannot straddle calibration and holdout. Base and
candidate are either `git:<40 lowercase hex>` or labeled synthetic revisions.
Case-local artifact IDs identify base or candidate bytes in ordered groups.
A first-review input has no prior findings; revision follow-up can see old
identities but never rulings. Review inputs may open only their own
`inputs/<case>/` subtree. Saved observations and scoring artifacts each have
separate manifests and case-owned subtrees.

Only a trusted, static fixture tree supplied by the operator is supported.
Manifest and artifact paths must stay under that root and named symlink
components are rejected. This is an offline loader boundary, not a sandbox or
protection against concurrent replacement. Per-file size limit: 1 MiB;
cumulative read limit: 32 MiB per batch. JSON rejects invalid UTF-8,
duplicate keys and nonfinite numbers. Input-envelope references are resolved
before the artifact phase. For each batch, schema and references are checked
before files, including checking every path's syntax before any file lookup.
All files are read before any digest mismatch is reported;
manifest order decides ties. SHA-256 hashes the exact read bytes, including
whitespace and line endings. Consumers receive those verified bytes.

`validate` needs neither saved observations nor answers. Replay checks and
seals saved observations before opening scoring data. Each observation
references case, candidate revision and the digest recomputed from the raw
review input. The human adjudication additionally binds case, candidate,
run ID and the digest recomputed from the raw observation. Updating the
ordinary artifact manifest and retaining a run ID cannot make an old ruling
apply to new observation bytes. Usage is a nonnegative total token count or
`null`; synthetic values are controls, not measured provider usage.

First-review scoring counts each confirmed known root once per case, even if
multiple reports match it. False blockers and pending judgments are counted
per finding, separately from root hits. Unsupported/unknown observations
remain visible. An explicitly empty answer key can be scored; an unlabelled
one cannot. Null usage is not zero. Follow-up uses saved human conclusions,
not automated closure; missing human labels and insufficient evidence remain
open. Each report case includes the relative paths and SHA-256 of the exact
answer and adjudication bytes consumed by scoring.

## Read-only sample

| Slice | Hand-checked expectation |
| --- | --- |
| First review | 4 attempted, 2 completed, 2 failed; 1 root hit, 1 duplicate, 1 false blocker and 1 pending finding. |
| Revision follow-up | One verified fixed, one no longer applicable, one insufficient evidence and one without a ruling. Both latter findings stay open. |
| Cost | First-review controls include explicit zero and unknown values; a fully unknown subtotal is `null`. |

This does not qualify production behavior, real A/B, live GitHub/mail adapters,
runtime supersession, receipts, or merge gates.

## Targeted context preview (PR2)

```sh
fixture=tests/fixtures/review_followup/context
out=$(mktemp -d)
PYTHONPATH=src python -m code_forge.eval.review_followup preview-context "$fixture" \
  --case example --output "$out/context.json"
```

This compares an explicitly ordered baseline with a deterministic targeted
selection over an explicitly supplied catalog. The example is synthetic.
There is no live scanning, model call, or scoring; the preview can run with
only the review manifest and input tree. Its input visibility is the PR1
trusted static fixture boundary, not a sandbox or a way to detect a person
embedding answers in otherwise authorized review inputs.

The case-local `inputs/<case>/context.json` binds the exact PR1 review input
SHA-256. It accepts first-review cases only. Group IDs must exactly match the
frozen grouping. For each group, the request gives the same nonempty diff,
contract and three lens texts to both arms, a shared byte limit, and an
ordered baseline list of candidate IDs. These texts are supplied by the
fixture author; the planner does not reconstruct or attest production
prompts. The preview records the same group and member order in both arms.

Candidate rows declare a group, revision and relationship, and either a
candidate artifact ID with a zero-based half-open **byte** span, or three
nulls (explicitly missing, never empty successful evidence). Every present
span is checked against the already verified bytes of an artifact owned by
that group and candidate revision, even if it will not fit. A missing file
or invalid reference is a rejection, not a declared missing candidate.
A span must decode as UTF-8, without newline normalization. Limits are 32
groups and 256 candidate rows per case, and up to 1 MiB measured per group.

Targeted ordering is failure exit, reader, writer, schema, generator, handler,
test setup, skip, cleanup, then background. Within one relationship the tie
order is artifact ID, span start, span end, then candidate ID. The baseline
keeps the caller's list order. In each arm, take the next whole span if it
fits, continue after skips, and keep a selected exact artifact/span at most
once per group. Overlapping but unequal spans are separate. The preview
distinguishes missing, duplicate, over-budget and not-in-baseline; a baseline
span is displaced only if its exact artifact/span is absent in the targeted
payload. No partial spans are emitted, so `truncated` is always empty.

The measurement is the length of compact sorted-key UTF-8 JSON for
`{"required": <group's required object>, "optional": <arm's optional array>}`,
without trailing newline, using Python JSON with `ensure_ascii=False` and
separators `(",", ":")`. This includes escaping, separators, provenance and
all supplied required text. If required context alone exceeds the limit, fail
without publishing. The preview records each arm's byte length and SHA-256
over exactly this payload, and a normalized context-request SHA-256
(candidate rows in targeted order, baseline lists unchanged). The preview
file's own pretty-printed JSON is not what the budget measures. Neither byte
counts nor this envelope are provider tokens, billable usage, or proof that
a production prompt fits. Verify actual prompt equivalence before any A/B.

The synthetic storage group uses 469/469 bytes in baseline and 456/469 in
targeted: the caller's handling of a failed write is outside the required
diff and replaces the overview. The preview also records a duplicate, a
missing writer and an over-budget reader. The tests group fits all three
whole spans at exactly 712 bytes. Run the same offline pytest command above
to verify both replay and context preview. No quality gain is claimed.

## Paired factory prompt dry run (PR3, rendering slice)

```sh
fixture=tests/fixtures/review_followup/context
out=$(mktemp -d)
PYTHONPATH=src python -m code_forge.eval.review_followup dry-run-context "$fixture" \
  --case example --max-prompt-bytes 16000 --output "$out/dry-run.json"
```

The serial offline command captures at the entry to `llm_invoke`, using the
existing ordinary and grouped L1 factories. The ordinary path runs each
frozen fixture group separately. The grouped path runs the actual fan-out
over those same ordered groups. Since the grouped factory only forwards
**one shared** `context_sources_text`, the offline adapter supplies each
group's context and contract at child construction. It does not add a
production group-local argument or run CLI topology selection.

A third, no-optional-context render pins the insertion point immediately
before Design Intent. Each arm must equal those exact bytes plus only its
group-local Context Sources block; missing, duplicated, moved or different
material is not silently removed. Pass order and names, role suffixes,
production L1 lens selection, reviewer JSON repair keys, model placeholder,
one configured attempt per
slot, zero correction slots and output cap are fixed across the arms. The
preview's illustrative lens labels are not the real factory's lens text.
The selected spans are serialized as a compact UTF-8 JSON array; when empty,
the Context Sources block is absent.

The report records the original preview budget and selected/displaced IDs
separately from the measured complete-prompt byte limit and per-slot
digests. Each path/arm render also has an 8 MiB total capture limit.
Byte-identical arms stay in the report. For the two-group fixture,
there are six logical slots per arm per path. **Zero requests are sent**,
and provider usage is unknown, not zero tokens. The offline invoker always
returns a known synthetic refusal after capture; no verdict, finding or
receipt is inferred.

The command itself does not exercise provider serialization, credentials, pricing,
cache, real attempts or CLI ordinary/grouped selection. In particular, a
live CLI A/B must not let optional context reselect topology, and equal byte
ceilings do not imply equal input length. Live attempt capture, a preregistered A/B protocol, and separate
authorization for model use remain necessary. No production review or candidate execution is enabled here.

The manual suite passes all captures through the production OpenAI-compatible
JSON serializer with an in-memory HTTP stand-in. It checks the exact body
and ties the submitted message back to the recorded prompt digest. A
threshold control shows that varying only optional context could change the
kernel-enabled CLI's topology choice; the offline adapter keeps its topology
fixed. This does not run the production CLI selector, network, or a model.

## Revision follow-up records (PR4)

The [offline record boundary](revision-contract.md) pins the C1–C2 encoding
and import rules. Its hand-authored decision table gives the later host
validator and ruling projector expected outcomes, not run results.

## Next gate: one-pair live smoke (not yet authorized or wired)

The first real run is a wiring and cost-accounting smoke, not a quality
experiment. Select one benign synthetic or appropriately licensed fixture, one or two total
frozen groups, one factory topology, one concrete model, and three existing
passes per group. Pin both arms' order before looking at outputs. Use the
same budget, model parameters and required evidence; only optional context
can differ. Record the Forge commit, fixture and plan digests, exact
serialized request-body digests (no headers or credentials), and selected
and displaced context before authorizing the run.

- **No more than 6 sends for one group or 12 for two.** Each logical slot
  permits one physical attempt. The offline gate reserves a slot before
  dispatch and retains failure, unknown outcome and not-started separately.
  It refuses a late retry, unexpected slot or changed request body. It has no
  transport and does not itself prevent an uninstrumented send. In
  particular, Forge's configured outer attempts do not count continuation
  requests. The future runner must disable retries, continuation, headroom,
  repairs, redirects and fallback or refuse them before any send. Do not
  resume an uncertain attempt under a newly initialized gate.
- **No implicit pool routing.** Establish the exact concrete model and
  upstream connection, no fallback or redirect, eligible payload policy,
  output cap, and billing account before use. If depending on promotional
  credits, verify that the selected upstream bills the credited Personal
  organization and check its balance separately. An HTTP success or a
  response model string alone does not prove routing or billing identity.
- **Keep the whole ledger.** Preserve actual sent bytes privately, response
  identity, failures and pre-filter/final results; record reported input,
  cached and output usage and cost without filling missing values with zero.
  If provider attribution is unresolved, a slot drifts, or a cap cannot be
  enforced, stop and retain remaining slots as not-started. Keep both arms
  and their failures in the record. Do not repeat failed slots.
- **No effect claim.** This pair only establishes that requests were sent as
  frozen and the attempt/usage records can be reconciled. Preregister a
  separate blinded, lineage-paired comparison before drawing conclusions
  about findings or false blockers. All live requests still require approval.

The gate is tested both with no transport and against bodies captured via the
existing in-memory OpenAI-compatible serialization test. It is not a release,
receipt or production review gate.


### One-shot runner boundary (fake transport only)

`run_smoke_once` preflights every frozen body before handing anything to a
sender. Against a trusted, pre-existing local POSIX journal directory, it
exclusively creates a journal keyed by the preregistered run ID and syncs
the file and directory. It syncs each reservation **before** invoking the
sender. Only an exact Boolean can become a terminal outcome, and the
runner syncs that outcome before updating its in-memory state. A returned
failure consumes the slot; a lost reply, interrupted process, or result-sync
failure leaves an unknown reservation and does not advance to later slots.
A terminal line visible after sync fails is not evidence of durability.
An existing journal (even empty or partial) refuses a fresh invocation.
Keep the same journal directory/run ID and retain its marker; this design
does not support resumption, repointing the directory, or reset.

The journal contains candidate request digests, slot identifiers, and
attempt outcomes, not prompt bodies, credentials, or provider responses.
Every sender invocation must itself be single-attempt; the orchestrator
cannot observe retries hidden inside its caller's transport. The repo
does not provide a live transport here. The live enablement, pinned route,
usage attribution and spending decision remain separate.

### Single-request HTTPS transport: offline qualification only

`DirectSmokeTransport` is an explicit library transport for the smoke runner,
not a CLI flag, production reviewer, or live-run approval. It posts the
already frozen body bytes to one named HTTPS host on port 443 at
`/v1/chat/completions`, using certificate and hostname verification. Each slot
has a fresh HTTP connection and one POST; there is no SDK, application proxy,
redirect handling, retry, context repair, continuation, or fallback. All
bodies must pass preflight before reservation: one pinned model, plain text
messages, no streaming or tools, and one explicit bounded output-token limit.
The socket timeout is an idle/operation limit. Use a separate
`max_elapsed_s` (direct transport only, in seconds, greater than zero and
at most 3,600) for a live run's elapsed budget.

The durable journal binds the connection policy to the slot digests and
saves a per-attempt client correlation ID before the POST. It records the
HTTP status, available response and server request-ID digests, matching
reported model, and provider-reported input, cached input and output token
counts. Sending a client ID does not prove a provider forwarded or recorded it.
Absent or invalid counts stay unknown; a real zero remains zero. The journal
does not contain keys, headers, prompts, reply text, or price estimates.
A reply is accepted only if its sole choice explicitly reports
`finish_reason: stop`, contains non-blank assistant text, has no non-null
refusal field, and passes the usage checks. A blank or refused reply is a
received failure: its bounded receipt is retained, but the pair stops. A truncated,
filtered, tool-use, or missing/unrecognized finish is a received failure, not
an unknown transport outcome. Its digest and available usage are retained.
The journal stores only recognized finish reasons, never arbitrary text.
A non-accepted result stops the run with later slots untouched. If the reply
cannot be obtained in full because of a timeout or connection failure, the
reserved attempt stays unknown and the run cannot be resumed.

Private body retention is off by default. For a separately authorized smoke,
pass an existing owner-only (`0700`) POSIX `artifact_dir` to `run_smoke_once`
with the direct transport. Choose a local directory with trusted ancestors
outside checkouts, CI uploads, or synced folders. The final root component
must not be a symlink. A new per-run namespace holds owner-only files; no
existing run or payload is overwritten. Files are plaintext: restrict access
and set a deletion window before use. Keep the separate one-use journal marker
after deleting payloads.

The runner retains only exact frozen request bodies for reserved sends and
complete accepted response entities (after HTTP framing, without headers).
It rejects a known credential in request bodies before any send or in an
otherwise accepted reply before writing that reply, including JSON-escaped
echoes. This is not a general-purpose secret scrubber. Non-accepted responses
retain only their sanitized receipt, not their body. Existing per-body limits
and the fixed attempt ceiling bound retained payload bytes (at most 96 MiB
with the maximum 12 slots and 4 MiB per direction).

Journal references contain the run namespace, per-attempt filename, byte
length and SHA-256, not the private root path or raw content. The request
must be durable before the reservation and POST; a retained reply must be
durable before its terminal journal event. An I/O interruption can leave
unreferenced or incomplete files; only journal-referenced bytes matching
their digests are evidence. If reply storage fails but the journal is still
writable, the run records a failed attempt (`private_reply_unstored`) with
the sanitized wire receipt, available usage, and no response artifact
reference. It stops without sending the next slot. If the terminal journal
event cannot be made durable, no terminal state is exposed. Neither failure
allows resume or reuse.

The optional elapsed budget starts before preflight, uses a monotonic clock,
and is pinned in the start journal event. It checks time again after durable
reservation and before HTTP sends, and shrinks the timeout on each raw
socket read, so progress just inside an idle timeout cannot prolong a run
indefinitely. Expiry stops admission and cannot authorize a resend. If a
reserved exchange expires before a full reply, its durable reservation
remains unknown; a reply already obtained may still finish its local
receipt after the budget, but no subsequent request may start. This is not
a hard process return deadline: blocking DNS, TLS/connect setup, filesystem
flushes or scheduler delays can return late. Late connect cannot start a
POST. Use a separately qualified outer supervisor if a hard return bound
is required.

The manual offline suite drives the real HTTP serializer/parser over a
socketpair and does not open a network connection or perform TLS. This
qualifies the client boundary only. The model echo is not provider or billing
attribution. Before an approved live smoke, separately pin and verify the
upstream and billing account, cap enforcement, actual elapsed-time budget,
and a private destination and deletion window for the retained bodies. No real model was called by these tests.
