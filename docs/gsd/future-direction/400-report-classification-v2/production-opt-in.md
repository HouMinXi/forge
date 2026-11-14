# #400: opt-in report v2 production delivery

## Decision and compatibility boundary

The default remains v1. `code-forge review --report-version v2 --mode ci`
enables a single selected v2 classification on ordinary, single-repository
subprocess review. Explicit baseline/head ranges are ordinary review. LOCAL,
canary, whole-file, cross-repository, kernel-context, gate-check, historical
readers and other command surfaces are deferred; unsupported v2 requests are
rejected rather than silently claiming v2 delivery. No State migration,
provider execution policy, exit code, credit or receipt change is introduced.

MCP `forge_review(report_version="v2")` requests the same CLI surface and
explicit CI mode. This is a tool input, not MCP protocol negotiation. Version
is fixed when a background job is created, not selected by a later poll.
`forge_gate_check` accepts only `report_version="v1"` and rejects v2 before execution. The opt-in structured result adds
`report_reader` with `requested_version`, `selected_version` (nullable),
`envelope_condition`, `selection_reason` and `block`. That block is
exactly the one selected view, or null if unavailable. The existing
`report_classification` retains its v1 parser semantics even on opt-in calls;
a v2 schema is therefore unsupported to that legacy field. There is no union of
v1 quarantine and v2 execution. Default v1 results omit `report_reader`,
including nested job results.

## Acquisition, publication and failure

The production emitter reloads canonical State once, captures tool versions
and advisories once, and prepares its real complete legacy output before
optional processing. Its captured locator is rechecked using the existing
identity policy, not advertised as an atomic filesystem snapshot. The optional
owned snapshot must reproduce that exact baseline before v2 is accepted.
Snapshot admission refusal, typed optional computation failures, validation or
budget refusal restore the original baseline, not a newly synthesized empty
report. Original baseline errors and unexpected/fatal/cancellation failures
propagate. Preparing streams together is not atomic publication to two OS
streams; write failures retain normal behavior.

Legacy fit remains **1,048,576 Python characters**, including stdout's final
newline. New reader/candidate UTF-8 input is limited to **8 MiB**, nesting to
**64**, and record occurrences to **50,000**. Owned admission uses at most
**1,000,000 source nodes**, **8 Mi characters** and **50,000 finding
occurrences**. Candidate stderr is limited to **1 MiB UTF-8**. These are
presentation limits, not review execution budgets. Oversize baseline output
is preserved; preservation does not claim it is readable. Limits cannot
justify removing products or truncating JSON.

MCP strict selection consumes actual subprocess stdout bytes before any
replacement decoding used for display. Jobs retain the selection computed
from those bytes. Timeout without stdout reports unavailable; no reconstructed
UTF-8 encoding of lossy display text may be used as evidence. Existing display,
findings counts, exit verdicts and legacy consumers remain separate fields.

Raw SARIF retains its extensions. Normalized v1 export is not a lossless v2
transport. No dual-version sibling is added. Selection of v1 on an opt-in
request is explicitly downgraded; a reader of the final SARIF alone cannot
distinguish an old writer from optional writer fallback. The opt-in CLI appends
a fixed writer downgrade notice after the unchanged baseline stderr only when
it fits the stderr budget; otherwise the baseline streams remain untouched.
Writer failure reasons and reader `v2-absent` are deliberately distinct. Persisted mutation
observations remain `inherited-predicate` / `persisted-record-unbound`, not
current invocation status or origin authentication. #68 and #33/#493 remain
independent.

## Sources and validation boundary

This continues the accepted pure projection, baseline, preparation and reader
layers in #400; see the issue's source links for SARIF 2.1.0 extension semantics,
RFC 8259 UTF-8/limits and compatibility rationale. The actual repository
`mcp>=1.27,<2` dependency is exercised, not an invented negotiation API.
Focused tests qualify the wired CLI emitter, supported route guards and MCP
inline/job byte selection. They do not qualify all deferred consumers or a
particular third-party SARIF viewer. Rollback is omission of the opt-in input.

## Candidate verification (2026-11-14)

Base: `45e48cc723142449a301451e4c9d3783b03fc0ce`. Independent review covered
correctness, trust, compatibility, resources/failures and scope. Review found
and corrected three integration issues before publication: preserving the old
MCP field, making CLI downgrade visible within budget, and preparing the
notice before writing either stream.

The 120 new controls cover wrapper admission/fallback and exception identity,
CLI default/opt-in routing and exact legacy preservation, actual emitter write
failure through ordinary-audit cleanup (including double failure), actual MCP
SDK tools/list and tools/call, and four real emitter subprocess byte handoffs
(mutation/mixed × inline/job). The subprocess handoffs use synthetic saved
State and forbid network; they are not full native review acceptance. Tests
use Python 3.13.12, MCP 1.30.0 and Pydantic 2.14.3; this is not a test of every
version allowed by the dependency range.

The local interpreter lacks `os.pidfd_open` and `signal.pidfd_send_signal`.
The unchanged `test_report_projection_compat::ci_pass_stub` therefore fails
mutation ownership admission on both this candidate and a clean full clone of
the exact base (base module: 9 passed, 1 failed). The resulting State infra
errors were compared and match. Twenty-four existing real Linux ownership
controls skip under their existing guard. Neither test assertions nor guards
were relaxed. Hosted exact-source CI is needed for those controls; local
focused green results do not mean an all-green suite.
