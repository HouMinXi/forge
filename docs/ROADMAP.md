# Roadmap

[中文版](ROADMAP.zh-CN.md)

What has shipped, what is being measured now, and what comes next. Dates are
the day a milestone closed on `main`. Each line names something you can find
in the tree or in `git log`; nothing here is a plan dressed up as a feature.

## Shipped milestones

| Version | Theme | Closed | What it added |
|---|---|---|---|
| v2.0 | Foundation | 2026-05-20 | The review loop as a state machine: five dispositions (CONFIRMED / UNCERTAIN / DISMISSED / FIXED / PENDING), LOCAL / CI / HOLD modes, a consecutive-clean counter, ten exit codes, and a file lock per workspace. |
| v2.1 | Dynamic gate | 2026-05-27 | A real `pre-commit` hook that runs the test suite and blocks on new failures (`gate-check`, `install-hooks`), diff-scoped mutation testing after static review, a cross-component coverage heuristic, per-pass receipts and `code-forge verify`. |
| v2.2 | Trusted review execution | 2026-06-04 | Pluggable review backends with `FORGE_BACKEND` resolution, outlet selection (subprocess / inline / subagent) that fails closed when the backend is unreachable, Python toolchain auto-detection for the lint layer. |
| v2.3 | Backend wiring | 2026-06-09 | Anthropic, OpenAI-compatible and Vertex API backends wired end to end and dogfooded; reviewer independence so the calling model never grades its own diff; relief mechanisms for stuck reviews. |
| v2.4 | Honest green | 2026-06-15 | Trust gate: a repo-supplied `gate.yaml` cannot point the API key at a foreign endpoint without an explicit `code-forge trust`. Taint rule for config-to-shell flows, fix validation, verdict honesty, code-graph triage, daemon state. |
| v2.5 | Releasable and cross-repo | 2026-06-26 | Self-documenting `gate.yaml` with a JSON schema, joint review of a change spanning two repos, consecutive-timeout circuit breaker (exit 6), truncation false-green guard, reviewer canaries, dead-code false-positive filter. |
| v2.6 | Adoption | 2026-06-29 | Retry and error-code handling for five Chinese API providers, a per-change intent contract (`--contract FILE`), and `code-forge-mcp`, a stdio MCP server for IDE clients. |
| v2.7 | Provider capability | 2026-07-01 | Provider-aware parameter passthrough and SSE streaming, MCP sampling so the client's own model subscription can serve as the review backend, a 55-finding usability sweep. |
| v2.8 | Onboarding and throughput | 2026-08-16 | User-level config at `~/.config/code-forge/config.yaml`, `setup-mcp`, the three review passes run in parallel, honest partial results with `passes=N/M`, a `--focus` parameter, Go / C / C++ / Java / JS / TS lint support, a `doctor` audit of which tools actually ran. |
| v2.9 | Environment grounding | 2026-08-30 | Every finding carries an epistemic basis. Environment manifest tiers (declared / observed / absent). The reviewed diff is executed before the verdict in a disposable directory with the lockfile verified by sha256. Large diffs are split along def-use lines instead of silently truncating; `doctor --live` probes real backends. |

The commit ranges and per-phase evidence behind each row are in the milestone
archive kept with the project's planning files; the rows above are the user
visible summary.

## Current: v3.0, measured review quality

Opened 2026-08-30. Until this milestone, the claim that three convergent
review cycles beat a single pass had never been measured on this tool's own
output. The milestone builds the instrument and runs it.

| Step | Status | Where to look |
|---|---|---|
| Metric aggregation | Merged 2026-08-30 | Precision, recall, F1 and signal-to-noise as `EvalSummary` properties in `src/code_forge/eval/scorer.py`; `None` on a zero denominator instead of a fake 0.0; best-of-N replaced with mean and standard error; ratios gated behind 30 entries in the table. |
| Evaluation corpus | Merged 2026-08-31 | 150 entries from SWE-bench Verified: 75 defect diffs (the fix reversed) and 75 clean controls (the fix itself), 11 repositories, at most 8 per repo, regenerated from a pinned seed by `python3 -m code_forge.eval.build_corpus`. Clean controls can assert that no finding belongs to them. |
| Depth sweep | Ledgers complete 2026-09-04 | Same 150 entries at 1, 2 and 3 required clean rounds. Results in [EVALUATION.md](EVALUATION.md). |
| Falsification ablation | Both arms complete 2026-09-05 | Same corpus with the falsification gate on and off at a three-round cap. Results in [EVALUATION.md](EVALUATION.md). |
| Resumable, concurrent eval runner | Merged 2026-09-04 | `code-forge eval --jobs N --resume-log FILE`: a bounded process pool, an append-only JSONL ledger written as each entry lands, arm settings passed to each process by value and checked before any review is spent. `scripts/analyse_arms.py` turns ledgers into the reported numbers and prints the line numbers behind each one. |
| Provider switch | Merged 2026-09-03 | `scripts/forge-provider.py`: one command to point a machine at a review backend across every `gate.yaml` on the host, keeping trust records in step. |

Also carried in this milestone, not started: containerised execution for the
execution-before-verdict step. It is waiting on a demonstrated need; the
native subprocess path has covered every reviewed case so far.

## Next: v3.1, decomposed review

Phase 59, in progress. The depth sweep showed that more rounds raise recall
but leave precision where it was, so this milestone works on the two levers
that outside evidence says act on precision: how the falsification judgment is
posed, and how much source-attributed fact reaches the reviewer. See
[REFERENCES.md](REFERENCES.md) for the papers behind that framing.

Two lines, each landing in small independent merges:

Judgment line.

- A1, merged 2026-09-05 (`ee45427`, `bf3cc09`): a malformed falsifier reply
  (not a dict, no verdict, a verdict outside the enum, or FIXED, which the
  falsifier may never return) is now a `FalsifyProtocolError` recorded as an
  infrastructure failure, not silently read as "unsure". Convergence behaviour
  is unchanged; only the attribution is.
- A2, planned: a hand-adjudicated calibration set of findings drawn from the
  Phase 58 ledgers, to measure how often the falsifier itself is right.
- A3 / A4, conditional on A2: if calibration accuracy is low, split the single
  ten-step falsification prompt into separate yes/no questions.

Evidence line.

- B1, merged 2026-09-05 (`59dcd03`, `4104e37`): `src/code_forge/context_sources.py`,
  a contract for external fact providers feeding the reviewer prompt. Every
  fact carries its source; a provider indexed at a different commit than the
  one under review is refused unless the operator opts in; a provider that
  raises is recorded as an error instead of becoming an empty table; and a
  broken `gate.yaml` is an error, not an empty config.
- B2, in progress: wire context sources into the prompt builder without
  changing a single byte of the prompt for configurations that do not use
  them, so the shared prefix stays cacheable.
- B3 / B4, planned: one evaluation arm on the same corpus with a context
  source enabled, and a report.

A null result on either line ships as one.

## Not on the roadmap

Things people ask for that this tool does not do and has no plan to do soon:
feedback learning from dismissed findings, technical-debt scoring,
performance regression benchmarking, and multi-repo dependency analysis
beyond the two-repo joint review that already exists. The README's "Honest
limitations" section is the authoritative list.
