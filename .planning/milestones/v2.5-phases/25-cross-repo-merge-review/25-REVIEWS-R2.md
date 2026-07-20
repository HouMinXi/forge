---
phase: 25
round: 2
reviewers: [deepseek, kn-kimi, minimax, gemini-agy]
reviewed_at: 2026-06-17T14:00:00Z
mimo: skipped (402 insufficient balance)
---

# Cross-AI Plan Review — Phase 25 Round 2

4 reviewers: DeepSeek V4 Pro (aicc ds), Kimi native (aicc kn), MiniMax M3 (aicc mm), Gemini (aicc gm).

---

## DeepSeek V4 Pro Review

Overall: MEDIUM. Round 1 fixes absorbed. Two HIGH execution blockers remain.

### HIGH: primary_ref is "git:HEAD" not "baseline..head"

Plan 04 Task 2 passes `primary_ref=baseline_repr` to run_cross_repo(). But
`baseline_repr = serialize_baseline_spec(baseline_spec)` (cli.py:1336) returns
"git:HEAD"/"git:main"/etc -- NOT "main..feature". get_sibling_diff() requires ".."
in the ref and will raise ValueError on every invocation.
Fix: Either construct "baseline..head" from raw refs, or pass pre-computed
`primary_diff: str` (resolved.git_diff is already available at dispatch point).

### HIGH: gate_yaml_dir=primary_path inconsistency in Plan 03 Step 2

Plan 03 calls `validate_siblings(siblings, gate_yaml_dir=primary_path, ...)` but
Plan 01 uses `gate_yaml_dir=Path(config_path).parent` (which is primary_path/.code-forge).
A path that passes validation may resolve differently at runtime. Spec-vs-implementation
mismatch.
Fix: Either consistently use primary_path/.code-forge, or document that Plan 03 uses
primary_path for pragmatic path resolution.

### MEDIUM: D-17 monkeypatch approach for sibling FAIL test underspecified

Plan 05 test_run_cross_repo_primary_determines_verdict proposes monkeypatching
StateMachine.run() for one sibling, but this patches the class globally, affecting
all threads. Option: test D-17 by verifying advisory WARNING string in output_fn
captures, without controlling sibling verdict.

### MEDIUM: ResolvedReview construction incomplete

Plan 03 interface lists git_diff, source_files, baseline_spec_repr but misses
mode_hint="git" and baseline_content=None (baseline.py:59-62 has 4 fields).
Executor will have to reverse-engineer from baseline.py.

### LOW: diff stats parser edge cases (cosmetic stats only)

---

## Kimi Native Review

Overall: HIGH risk. 4 execution blockers.

### HIGH: StubAutoFixer import path wrong

Plan 03 says "from .autofixer import StubAutoFixer". Ground truth: class is in
src/code_forge/autofix.py (NOT autofixer.py). Wave 2 fails at import.
Fix: "from .autofix import StubAutoFixer"

### HIGH: StateMachine does not accept output_fn parameter

Plans describe passing _capture_output(label) as output_fn= to StateMachine.
Actual StateMachine.__init__ has no output_fn parameter. Findings go through
receipts/state, advisory status goes to sys.stderr. Plan 06's format_cross_repo_output
has no source of captured lines.
Fix: After thread.join(), read per-cwd receipts/state.json and format grouped by label.

### HIGH: Symlink guard rejects documented sibling paths

Plan 01 resolves repo: relative to gate_yaml_dir (.code-forge/) and passes
gate_yaml_dir as cwd to _symlink_guard_passes(). For repo: ../forge-plugin and
gate.yaml at /project/.code-forge/gate.yaml: resolved=/forge-plugin;
cwd.parent=/project; /forge-plugin NOT under /project -> rejected.
The documented CONTEXT.md example fails validation.
Fix: Resolve relative to gate_yaml_dir.parent (project root) and/or pass
gate_yaml_dir.parent as cwd.

### HIGH: load_gate_config requires test: section, breaks backend-only gate.yaml

Plan 04 calls load_gate_config(gate_yaml_path) from review CLI path.
load_gate_config raises ValueError("gate.yaml must have a test section") if test:
key absent. Review CLI currently uses yaml.safe_load; many forge repos configure
only backends in gate.yaml.
Fix: In Plan 04 dispatch, parse gate.yaml with yaml.safe_load and call
validate_siblings() directly, without requiring test: section.

### MEDIUM: Receipt collection underspecified

write_receipts() is called inside StateMachine.run(); orchestrator never sees
return value. Plan 03 Step 8 says "rename write_receipts() output files" without
explaining how to discover them.
Fix: State that orchestrator globs per_cwd/.code-forge/receipts/receipt-cNpM.json
after sm.run() returns.

### MEDIUM: CLI return value: .value vs verdict_to_exit

Interface block shows return run_cross_repo(...).value; Task 2 says use verdict_to_exit().
Verdict is str Enum; .value = "PASS"/"FAIL" not int exit code.
Fix: Always use verdict_to_exit(run_cross_repo(...)).

### MEDIUM: D-19 receipt naming may have no automated coverage (xfail escape)

### LOW: D-09 timing divergence from locked wording; test_thread_isolation oversells

---

## MiniMax M3 Review

Overall: MEDIUM-LOW (improvement from MEDIUM-HIGH in Round 1). 1 HIGH, 2 MEDIUM, 3 LOW.

### HIGH: Plan 03/06 output-formatting coordination friction (double-emission risk)

Plan 03 Step 7 already inlines the grouped-output flush loop. Plan 06 Task 2
says to find and replace that loop. If executor misses this, output is double-emitted
(both inline loop and format_cross_repo_output run). If executor removes Plan 03's
loop but Plan 06 ships wrong, output is lost.
Fix: Plan 03 Step 7 should ONLY capture per-thread output to thread_output[label]
and add comment "format_cross_repo_output() in Plan 06 will emit these in order."
Do NOT ship any flush/header logic in Plan 03.

### MEDIUM: D-19 receipt rename logic underspecified for variable-output rounds

write_receipts() may return [] (clean round) or [r1, r2, r3] (multi-receipt).
Plan 03 Step 8 doesn't specify behavior for 0 receipts or multiple.

### MEDIUM: build_cross_repo_context diff-stats parser misses first diff line

`diff.count("\ndiff --git ")` undercounts by 1 (misses first file).
Fix: `sum(1 for line in diff.splitlines() if line.startswith("diff --git "))`

### LOW: test_thread_isolation name misleading; LOW: redundant validate_siblings call
in Plan 03 Step 2; LOW: format_cross_repo_output type hints inconsistent across plans.

---

## Gemini (agy) Review

Overall: HIGH. Two architectural concerns.

### HIGH: format_cross_repo_output() called in Plan 03 (Wave 2) but defined in Plan 06 (Wave 3)

Plan 03 Step 7 calls format_cross_repo_output() -- but this function isn't implemented
until Plan 06, Wave 3. Wave 2 will fail with NameError.
Fix: Either move Plan 06 into Wave 2, or have Plan 03 ship an inline stub that Plan 06
replaces.
(Note: this is the same coordination issue as MiniMax's HIGH -- root cause is that
Plan 03 currently contains BOTH the capture logic AND a reference to format_cross_repo_output
before it's defined.)

### HIGH: ForgeLock omission causes primary directory corruption risk

Skipping ForgeLock for cross-repo mode allows concurrent single-repo and cross-repo
runs to interleave, corrupting .forge/ directory and receipt files.
Fix: Acquire ForgeLock for the primary repository in run_cross_repo() or Plan 04 dispatch.

### MEDIUM: make_per_repo_cwd creates empty dir -- falsifier tests will fail

Falsifier executes test commands in cwd. Empty tmpdir means pytest commands will fail.
(Note: Per D-14 design, cwd is for mutation-result.json isolation only, not for
running tests. Falsifier uses primary repo path; per_cwd is only needed for .code-forge/
writes. This concern may be mitigated by the design but the plan should clarify.)

---

## Consensus Summary (4 reviewers)

### Agreed by 2+ reviewers

**[CRITICAL - 3 of 4] Plan 03 executor unknowns / output_fn design gap**
- kn: StateMachine has no output_fn -> D-12 output capture is architecturally broken
- gm: format_cross_repo_output called before it's defined (Wave 2 vs Wave 3)
- mm: Plan 03/06 coordination friction -> double-emission risk
Root cause: Plans assume StateMachine accepts output_fn; it does not.

**[CRITICAL - kn+gm] Symlink guard / path resolution bugs**
- kn: gate_yaml_dir as cwd to _symlink_guard_passes rejects ../forge-plugin (common case)
- ds: gate_yaml_dir=primary_path in Plan 03 Step 2 inconsistent with Plan 01

**[HIGH - kn only] StubAutoFixer import wrong** (.autofixer -> .autofix)

**[HIGH - ds only] primary_ref is baseline_repr not baseline..head**

**[HIGH - kn only] load_gate_config test: section required in review path**

**[MEDIUM - mm+kn] D-19 receipt rename underspecified**

**[MEDIUM - mm] diff stats parser undercounts files**

### Divergent Views

- ForgeLock: gm says HIGH, plan documents intentional bypass with rationale (kn accepts)
- make_per_repo_cwd: gm says needs repo code; design says tmpdir is for .code-forge/ isolation only

### Required Fixes Before Execution (priority)

1. Fix StubAutoFixer: `.autofixer` -> `.autofix` [CRITICAL, ground truth]
2. Fix output_fn design: StateMachine has no output_fn -- redesign D-12 output capture to read receipts/state.json after threads join [CRITICAL]
3. Fix symlink guard semantics: resolve sibling paths relative to gate_yaml_dir.parent; pass gate_yaml_dir.parent as cwd [CRITICAL]
4. Fix primary_ref: use raw git refs, not serialize_baseline_spec output [HIGH]
5. Fix load_gate_config in dispatch: use yaml.safe_load + direct validate_siblings(), not load_gate_config() [HIGH]
6. Fix Plan 03 Step 7: remove flush logic; only capture to thread_output; Plan 06 owns the flush [HIGH, coordination]
7. Specify receipt discovery: glob per_cwd/.code-forge/receipts/ after sm.run() [MEDIUM]
8. Fix diff stats: use splitlines() + startswith("diff --git ") [MEDIUM]
