# Immediate cross-repository query summary

After the primary cross-repository runner resolves named changed symbols, its
existing output callback reports the scope of actual direct-caller analysis.
CLI attributes this advisory line to the primary repository; immediate and
background MCP retain the CLI child's stdout. No new output channel is added.
Without an output callback the runner remains silent.

For a nonempty enumerated scope, the summary reports completed queries out of
registered sibling entries, with attempted and unavailable counts. An entry is
one occurrence after primary-root exclusion, not a distinct repository: repeated
entries count separately. A query is attempted only when the existing sibling
query helper is called, and completed only when that helper successfully returns
all changed-symbol groups, including a successful zero-caller result. Missing
graphs, graph-discovery failures and handled query failures count as unavailable.
At ordinary completion, completed plus unavailable equals the entry count.
All sibling queries still precede the unchanged global top-10 finding cap.
For example, `completed for 2 of 3 registered sibling entries (2 attempted;
1 unavailable)` means two query calls returned and one entry was unavailable
before a query could be attempted. It is not a finding count.

An empty sibling scope instead reports that direct-caller impact was not
evaluated. An unavailable registry or root enumeration reports that the registered
query scope was not established, with no partial counts. Existing infrastructure
diagnostics remain unchanged. Blank diffs, primary prerequisite failures and the
existing no-named-target warning retain their previous behavior and do not gain
this summary. A missing registry file retains the existing discovery convention
of an empty registry; it does not prove that no repositories exist. Cache reuse
emits no new summary or fresh-query credit; genuinely
retried noncached early exits can emit their message again.

New summary text contains only fixed explanations and counts, not paths, aliases,
symbols or error details. This does not redact existing findings, host labels or
infrastructure diagnostics. Ordinary output failures are best effort and do not
change findings, cache eligibility, persistence delivery status, State, receipts,
credit or verdicts. Process-control exceptions still propagate; an unexpected
failure before completion does not emit a completion summary.

These counts are **not a durable receipt**, graph snapshot, freshness proof,
full-impact guarantee, or evidence of no impact. Repositories outside the selected
registered scope were not searched for direct callers. Static graph liveness
filtering and one-hop limitations remain unchanged. The separate
[persistent v1 contract](cross-repo-diagnostics-v1.md), schema and analysis states
are unchanged; these summary lines are not stored in v1 diagnostics. The parent
[roadmap](https://github.com/HouMinXi/forge/issues/33) and
[diagnostic issue](https://github.com/HouMinXi/forge/issues/441) remain open;
their result-outlet and acceptance decisions are unchanged.

This is an immediate visibility increment, not the roadmap’s host/input-bound
registered-query receipt or later retrieval. It does not complete the first parent
capability or grant a pending design or acceptance decision.

## Qualification boundaries

`tests/test_cross_repo_query_summary.py` exercises real registry files and SQLite,
including duplicate entries, zero callers, missing/corrupt graphs and multiple
changed symbols. Completion counts whole sibling helper returns: if the second
symbol query is interrupted after the first returns a caller, that occurrence
is unavailable, not completed, and no partial findings escape. Independent
query traces distinguish returned helper results from skipped SQL.

Four `test_actual_cli_mcp_child_transports_query_summary` cases run the real CI,
L1, falsification, receipt and advisory paths through a CLI child and public
immediate/background MCP handlers. A deterministic local backend supplies review
inputs; it does not supply Verdict. These docs-only inputs deliberately place a
named node in the fixture graph to reach the named-target branch. They qualify
summary transport and gate isolation, not code-change impact quality or graph
freshness. The primary computes PASS or FAIL; the sibling retains its existing
coverage-gap FAIL and advisory warning. The cases do not claim every repository
passes, SDK-stdio transport, mutation workloads, HOLD/recovery or later retrieval.

These controls require exactly one attributed summary in structured output and
visible MCP text, an actual zero-caller sibling query, unchanged gate-relevant
state and receipt bytes, and a strictly read empty targets-resolved v1 observation. Separate
native gate cases retain normal/broken-output sink controls. Test-owned cleanup
bounds and observes the CLI, its tasks and known backend identities; it does not
expand the production lifecycle guarantee.
