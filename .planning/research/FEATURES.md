# Feature Landscape: Forge v2.4 "Honest Green"

**Domain:** Fix validation, trust-boundary review, verdict calibration, legacy surfacing, graph triage, false-green evaluation
**Researched:** 2026-06-09
**Overall confidence:** MEDIUM (industry patterns well-documented; forge integration points verified from source)

---

## Axis 1: Fix-Validation (STING / Revert-Test-RED)

The pattern: after a fix, REVERT it and confirm the test suite turns RED. Green-after-revert means the fix is untested or the test is overfit.

| Aspect | Industry State | Forge Relevance |
|--------|---------------|-----------------|
| **Mutation testing** (Stryker, mutmut, PITest) | Mature. Mutates code, checks if tests catch it. Forge R2 already does diff-scoped mutation. | R2 mutates CODE; fix-validation reverts the FIX. Different axis. |
| **RegMiner** (ISSTA 2022) | Mines regressions as 4-tuples `(test, rfc, ric, wc)`. Validates: test passes fix-commit, fails inducing-commit, passes working-commit. 100% precision, 56% recall. | The 3-state validation IS the STING pattern. Forge can do this for single-commit fixes. |
| **Testora** (ICSE 2026) | Generates tests, classifies behavioral diffs as intended/unintended via PR intent. 55% precision, 67% recall. $0.003/PR. | Complementary overfit guard. Too noisy (55% precision) for a gate; useful as advisory. |
| **Overfit guard** | No named tool. Concept: after autofix, verify fix is not tautological. | Forge autofix loop (machine.py:677) has PARSE_FAIL. Overfit guard adds: revert fix, re-run test, confirm failure. |

### Classification

| Feature | Category | Complexity | Depends On |
|---------|----------|------------|------------|
| Revert-test-RED for committed fixes | **Differentiator** | Med | git revert, gate_check test runner |
| Overfit guard for autofix | **Differentiator** | Med | autofix.py, gate_check |
| Testora-style intent classification | **Defer** (55% precision too noisy) | High | LLM invoke, PR metadata |

---

## Axis 2: Trust-Boundary Taint (Config-to-Sink)

Trace data from config files / external inputs to dangerous operations. Score each input by provenance.

| Aspect | Industry State | Forge Relevance |
|--------|---------------|-----------------|
| **Semgrep taint mode** | `mode: taint` with sources/sinks/sanitizers. Cross-file via `--pro`. | Semgrep is an L0 tool. Forge's role: ensure taint-sensitive files are COVERED (coverage.py) and flag taint findings at higher severity. |
| **CodeQL taint tracking** | 491 security queries, 166 CWEs (v2.25). Inter-procedural, cross-file. | Another L0 tool. CodeQL SARIF feeds forge's pipeline directly. |
| **direnv trust model** | `.envrc` hash-whitelisted; any change needs re-approval. | Analogous to forge's gate.yaml trust: who controls the config that controls execution? |
| **Danger score** | No standard tool. Concept: provenance-based score (HTTP input=10, env var=7, constant=0). | L1 prompt change: "for each external input, state provenance and worst-case attacker value." |

### Classification

| Feature | Category | Complexity | Depends On |
|---------|----------|------------|------------|
| Ensure Semgrep taint rules cover security files | **Table stakes** (coverage.py already generic) | Low | coverage.py |
| Add "input provenance" to L1 reviewer prompt | **Differentiator** | Low | factories.py prompt |
| Danger-score metadata on findings | **Differentiator** | Med | state.py (additive field) |
| Custom Semgrep rules for gate.yaml | **Defer** (user-authored, not forge's job) | N/A | N/A |

---

## Axis 3: Runtime-Contract Review (Verdict Calibration)

Distinguish "PASSED" (verified clean) from "RAN but could not verify" (UNVERIFIED). Silent PASS on unreachable backend = false green.

| Aspect | Industry State | Forge Relevance |
|--------|---------------|-----------------|
| **OpenAI code verifier** | Calibration formula: `P(correct) * C_saved - C_verify - P(incorrect) * C_alarm`. | Forge's falsifier outputs CONFIRMED/DISMISSED/UNCERTAIN. Gap: unreachable falsifier returns UNCERTAIN silently. |
| **Cloudflare AI review** | Lifecycle phases: bootstrap (non-fatal), configure (fatal), postConfigure (async). | Forge has no lifecycle phases. Per-layer "ran"/"skipped" status would make coverage.py more precise. |
| **Forge coverage gap** (own code) | coverage.py:25: "git review whose backend is unreachable yields no L1 findings but is still treated as L1-covered." | **THE KNOWN GAP.** `l1_active` is diff-based, not reachability-based. Backend timeout looks identical to clean code. |

### Classification

| Feature | Category | Complexity | Depends On |
|---------|----------|------------|------------|
| UNVERIFIED verdict when L1 backend unreachable | **Table stakes** (forge's thesis demands it) | Med | l1_provider return type, machine.py |
| Per-layer "ran" vs "skipped" tracking | **Differentiator** | Med | state.py schema, machine.py |
| Lifecycle phase analysis | **Defer** (over-engineering) | High | N/A |

---

## Axis 4: Legacy Code Surfacing (Blame-Aware Review)

When a diff touches old code, surface age and ownership for calibrated review depth.

| Aspect | Industry State | Forge Relevance |
|--------|---------------|-----------------|
| **SonarQube Clean-as-You-Code** | NCD (New Code Definition): previous version, N days, reference branch. Only new-code issues gate. Blame-based assignment. | Forge's `--baseline HEAD` IS SonarQube's reference-branch NCD. Gap: no "this file last touched 3 years ago" signal. |
| **Testora intent classification** | Classifies behavioral diffs using PR title/description. 55% precision. | Advisory for old-code changes. Not a gate. |
| **git blame** | `git log --format='%ai' -1 -- <file>` gives last-modified date. | Straightforward. Question: what does forge DO with age? Higher scrutiny? Advisory annotation? |

### Classification

| Feature | Category | Complexity | Depends On |
|---------|----------|------------|------------|
| File-age annotation in findings | **Differentiator** | Low | git.py, state.py (additive) |
| Higher cycle threshold for old-code changes | **Differentiator** | Low | diff.py tier_threshold |
| Blame-based reviewer routing | **Defer** (no multi-user workflow) | N/A | N/A |

---

## Axis 5: Graph-Triage (Blast-Radius Ranking)

Use dependency graph to rank findings by how many callers/dependents are affected.

| Aspect | Industry State | Forge Relevance |
|--------|---------------|-----------------|
| **code-review-graph** (MCP) | Tree-sitter call graph. Perfect recall blast-radius. 2900-file re-index <2s. 6.8-49x token reduction. | External tool. Forge should CONSUME output via MCP, not rebuild. |
| **Endor Labs blast radius** | Reachability-based: unused-library CVE = blast radius 0. | Same principle: bug in 47-caller function > bug in leaf. |
| **Entity extraction** | Tree-sitter parses diff for function/class names -> graph query keys. | Forge diff.py has file+line. Need function-level extraction. |

### Classification

| Feature | Category | Complexity | Depends On |
|---------|----------|------------|------------|
| Entity extraction from diff hunks | **Table stakes** (needed for graph) | Med | tree-sitter or regex, diff.py |
| Finding prioritization by caller count | **Differentiator** | Med | External graph tool |
| Blast-radius annotation on findings | **Differentiator** | Low-Med | Entity extraction + graph |

---

## Axis 6: False-Green-Rate Evaluation

Measure how often the pipeline says PASS when bugs are present.

| Aspect | Industry State | Forge Relevance |
|--------|---------------|-----------------|
| **BugsInPy** | 493 real bugs, 17 Python projects. Bug + test + fix per entry. | Gold-standard corpus. Replay: checkout buggy, run forge, check overlap. |
| **CR-Bench** (2026) | 584 defects from SWE-Bench as PR review corpus. Usefulness + SNR metrics. | Most relevant benchmark. New; stability unproven. |
| **Forge's own false-green** | 639-mock + 9-pass false-green (CLAUDE.md). | Cheapest starting corpus: forge's own history. |

### Classification

| Feature | Category | Complexity | Depends On |
|---------|----------|------------|------------|
| Replay BugsInPy corpus | **Table stakes** for eval claims | Med | checkout script, CLI automation |
| False-green-rate metric (FN/(FN+TP)) | **Table stakes** | Low | Labeled corpus + output parser |
| Per-backend comparison | **Differentiator** (retires voting with data) | Med | Multiple backends, corpus replay |
| CR-Bench integration | **Defer** (too new) | High | N/A |

---

## Anti-Features

| Anti-Feature | Why Avoid | Instead |
|--------------|-----------|---------|
| Build own taint engine | Semgrep/CodeQL do this. Forge orchestrates. | Consume L0 tool output. |
| ML-based false-positive filter | Circular training data. Low volume. | Rule-based blast-radius ranking. |
| Automatic bug fix from eval | Conflates review with repair. | Report locations. Developer fixes. |
| Rebuild dependency graph | code-review-graph does this. | MCP integration. |

## Build Order

1. **Axis 3** -- foundational; fixes forge's own false-green gap
2. **Axis 6** -- measures everything; without metrics, improvements unverifiable
3. **Axis 1** -- builds on gate_check
4. **Axis 2** -- low-effort prompt change
5. **Axis 5** -- depends on external tooling
6. **Axis 4** -- lowest false-green impact

## Sources

- [RegMiner (ISSTA 2022)](https://dl.acm.org/doi/abs/10.1145/3540250.3558929)
- [Testora (ICSE 2026)](https://arxiv.org/abs/2503.18597)
- [Semgrep Taint Mode](https://semgrep.dev/docs/writing-rules/data-flow/taint-mode/overview)
- [CodeQL Taint Tracking](https://codeql.github.com/docs/codeql-language-guides/analyzing-data-flow-in-cpp/)
- [SonarQube Clean-as-You-Code](https://docs.sonarsource.com/sonarqube-server/10.6/user-guide/clean-as-you-code)
- [code-review-graph](https://github.com/tirth8205/code-review-graph)
- [BugsInPy](https://dl.acm.org/doi/abs/10.1145/3368089.3417943)
- [CR-Bench](https://arxiv.org/html/2603.11078)
- [OpenAI Code Verification](https://alignment.openai.com/scaling-code-verification/)
- [Endor Labs Blast Radius](https://www.endorlabs.com/learn/vulnerability-blast-radius-how-to-measure-and-reduce-impact)
