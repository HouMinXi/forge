# Phase 21: Legacy + Intent - Context

**Gathered:** 2026-06-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 21 delivers REVIEW-LEGACY-01 + REVIEW-INTENT-01: when the static review
finds an issue in an unchanged line of a file the diff touches, forge surfaces it
as an ADVISORY "pre-existing / inherited" finding instead of silently dropping it.
Each finding is annotated with git-blame attribution (author, commit SHA, commit
message first line) and an intent label (workaround/SATD vs unintended bug) derived
from commit message text and in-code SATD markers.

Both axes are strictly ADVISORY -- they never block a cycle, never gate a commit,
never auto-suppress. Labels annotate, nothing more.

In scope: LegacyRunner advisory axis; git blame subprocess helper in git.py; SATD
detection from source lines; AdvisoryFinding emission with attribution + intent label.

Out of scope: blocking behavior of any kind; transitive dependency scanning
(callers/callees -- Phase 22 REVIEW-SYSTEM-01); GitHub PR description fetching
(network dependency); graph-triage ranking.

</domain>

<decisions>
## Implementation Decisions

### D-01: "touches" scope -- narrow
Only files returned by `extract_changed_lines(diff_text)` (files with actual added
or modified lines in the diff). "depends on" in the requirement does NOT extend to
transitive imports or callees -- that scope belongs to Phase 22 REVIEW-SYSTEM-01.
Zero additional git operations to determine scope.

### D-02: Legacy finding source -- l0_runner constructor injection + manual line-intersection

**Implementation architecture (Plan 21-02 supersedes the filter_delta call):**
LegacyRunner accepts `l0_runner` as a constructor parameter (default None). In `run()`,
if `_l0_runner` is None, it lazily imports `_default_l0_runner` from machine.py (avoids
circular import). Tests pass a fake callable; `cli.py` wires `LegacyRunner()` with no arg
so production uses the real runner. `machine.py` injects `registry` and `source_files`
via the `hasattr` guard in `_run_advisory_axes()`.

**Splitting delta vs pre-existing -- MANUAL LINE-INTERSECTION LOOP (not filter_delta()):**
`filter_delta()` is type-incompatible: it expects `Finding.line`/`.end_line` but
`StateFinding` uses `line_range:list[int]`. The PLAN implements the equivalent algorithm
manually using `sf.line_range[0]/[-1]` access. Pre-existing = l0_findings where
`line_range` does NOT intersect any changed line AND the file IS in the diff-touched file
set (D-01 scope). This IS the "R1 baseline primitive" conceptually -- implemented via the
manual loop, NOT a call to `filter_delta()`.

~~SUPERSEDED: "applies filter_delta to split (delta_findings, all_findings)..." and~~
~~"filter_delta already implements this split" -- replaced by Plan 21-02 manual loop.~~

Rationale for NOT reading state.json CONFIRMED findings: at the instant advisory axes
run on a PASS verdict, `_fixpoint_reached()` (machine.py:795-811) requires zero
unfixed CONFIRMED and zero UNCERTAIN. Pre-existing L0 findings on unchanged lines
get fixed by autofix (FIXED) or promoted to UNCERTAIN then HOLD. On genuine PASS,
CONFIRMED is empty; reading state.json CONFIRMED yields []. This approach follows
the TaintRunner model: advisory axes run their own tooling independently.

Note on "R1 baseline primitive" (ROADMAP SC#3, REQUIREMENTS REVIEW-LEGACY-01): the
phrase means the diff changed-line delta (extract_changed_lines), NOT the R1
test-failure baseline. "NEW vs baseline delta" = findings on changed lines vs findings
on unchanged lines. Implemented via the manual line-intersection loop in `run()` --
`filter_delta()` is NOT called (type incompatible).

### D-03: Intent classification signal -- commit message + SATD (fully local)
- `git blame --porcelain` per file + `git log --format="%an <%ae>%n%H%n%s"` per SHA
- In-code SATD: scan +-3 lines around the finding for TODO/FIXME/HACK/WORKAROUND/XXX
- Classification: `"intended"` if commit message contains workaround/hack/temp/
  fixme/known-issue signals OR surrounding lines have SATD marker; else `"unintended"`
- ~55% precision (REQUIREMENTS.md cites Testora) -- label is informational only
- No network calls, no GitHub API

### D-04: Attribution format
`AdvisoryFinding.attribution` = `"git-blame: {author_name} {sha[:8]} {commit_subject}"`

Example: `"git-blame: Alice abc1234f fix: handle null case in parser"`
(author is name-only per porcelain `raw_line[7:]` stripping "author " prefix; email is on the
separate "author-mail" line which is ignored; see 21-02 interfaces section for the correction note)

### D-05: Advisory axis structure -- follow TaintRunner/RuntimeRunner model
- `LegacyRunner(is_advisory=True)` in `src/code_forge/legacy.py`
- `source_files` attribute declared on runner; injected by machine.py before run()
- `infra_errors: list[str]` cleared at start of each run()
- Added to `advisory_runners` at `cli.py:1514` (inside `_run_hold_loop`, def at 1480)
- D-04 never-silent: if L0 tools fail or source_files empty -> emit SKIPPED finding

### D-06: git blame helper -- add to git.py
Add `git_blame(file_path, repo_root) -> dict[int, dict]` to `src/code_forge/git.py`
(currently owns git diff subprocess calls; git_blame widens this charter).
Returns `{line_number: {"author": str, "sha": str, "subject": str}}`.

### D-07: Intent label embedded in description
Format: `"[pre-existing] {original_description} [intent: intended/unintended]"`
No new fields on AdvisoryFinding -- attribution carries blame, description carries label.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Core infrastructure to reuse
- `src/code_forge/delta.py` -- `filter_delta`: (delta_findings, all_findings);
  the set difference is pre-existing. Docstring notes "preserved for the reporter
  to show N pre-existing violation(s) in unchanged code."
- `src/code_forge/diff.py` -- `extract_changed_lines(diff_text)`: returns
  `{file: set[int]}` of added/modified lines. Inverse = unchanged lines.
- `src/code_forge/reporter.py` -- Already shows pre-existing count on PASS output
  (lines 95-110). Phase 21 expands each to a full attributed AdvisoryFinding.
- `src/code_forge/git.py` -- Currently scoped to git DIFF subprocess calls
  (module docstring line 5). Phase 21 adds `git_blame`, widening the charter to
  "git subprocess calls (diff, blame)"; update the docstring when git_blame lands.
  NOTE: git log calls remain in machine.py (_run_fixval) and are NOT extracted in
  Phase 21 -- do NOT include "log" in the updated docstring.
  Do NOT call git directly from legacy.py -- keep git.py as the single git-subprocess owner.

### Advisory axis structural models
- `src/code_forge/taint.py` -- TaintRunner: source_files injection, infra_errors,
  _findings_to_advisories converter. Direct structural model.
- `src/code_forge/runtime.py` -- RuntimeRunner: _build_skipped_finding never-silent
  pattern; infra_errors.clear() at start of run(). Follow both patterns.
- `src/code_forge/advisory.py` -- AdvisoryFinding + AxisRunner Protocol. Constraint:
  "Do not widen this signature." (run receives only diff_text + repo_root)

### Machine integration
- `src/code_forge/machine.py` lines 969-994 -- `_run_advisory_axes()`: source_files
  injection lines 978-983; advisory run ordering after convergence at line 180.
- `src/code_forge/cli.py` lines ~1491-1495 -- Where to add LegacyRunner to
  advisory_runners alongside TaintRunner and RuntimeRunner.

### Requirements (authoritative)
- `.planning/REQUIREMENTS.md` section REVIEW-LEGACY-01 -- "Grandfather-but-surface";
  3 success criteria: advisory+blame, never suppressed, reuses R1 baseline.
- `.planning/REQUIREMENTS.md` section REVIEW-INTENT-01 -- "commit/PR text as oracle";
  ~55% precision; label-only, never suppresses or blocks.
- `.planning/ROADMAP.md` Phase 21 section -- 5 success criteria, depends on Phase 20.

### State source (D-02)
- `.code-forge/state.json` at runtime -- NOTE: at PASS, CONFIRMED findings are empty
  (convergence constraint). D-02 does NOT use state.json; it re-runs L0 tools
  directly. This entry is kept for the planner to understand why state.json was
  considered and rejected as the source.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `extract_changed_lines(diff_text)`: changed-line map; inverse = unchanged lines.
- `filter_delta` logic: pre-existing = all_findings NOT in delta_findings.
- `_build_skipped_finding` pattern from runtime.py: copy for never-silent fallback.
- `AdvisoryFinding.attribution` field exists -- no dataclass changes needed.

### Established Patterns
- `source_files` injection: machine.py sets on any runner with the attribute.
  LegacyRunner declares `self.source_files: Optional[list[Path]] = None`.
- `infra_errors: list[str]`: clear at start of run(), machine.py collects after.
- git.py currently owns git diff subprocess calls; git_blame/git_log widen its
  charter -- add them there, update the module docstring when they land.
- Atomic write (mkstemp + replace): advisory-findings.json already handled by
  machine.py; no new files to write in Phase 21.

### Integration Points
- LegacyRunner added to advisory_runners at cli.py:1514 (inside _run_hold_loop).
- machine.py:_display_advisories() handles generic display; no changes needed.
- advisory-findings.json serialization in _serialize_advisories() flows automatically.

### git blame parsing notes
- `git blame --porcelain <file>`: each block starts with SHA (40 chars), followed
  by "author", "author-mail", "summary" lines. Parse once per file, cache by SHA.
- `git log --format="%an <%ae>%n%s" <sha> -1`: author+email on line 1, subject on 2.
- SATD keywords: TODO, FIXME, HACK, WORKAROUND, XXX, KLUDGE (case-insensitive).
- Intent commit signals (-> "intended"): workaround, hack, temp, fixme, known-issue,
  legacy, grandfather, suppress, intentional (case-insensitive substring).

</code_context>

<specifics>
## Specific Ideas

- reporter.py pre-existing count (L0 tools) and Phase 21 advisory findings (L1 LLM)
  are complementary and can coexist without conflict.
- git blame porcelain: cache SHA -> {author, subject} to avoid N git-log calls.

</specifics>

<deferred>
## Deferred Ideas

- Transitive dependency scanning (callers/callees) -> Phase 22 REVIEW-SYSTEM-01
- GitHub PR description as intent signal (requires network + auth)
- Per-author legacy debt dashboard
- Mechanical surface catalog for legacy patterns

</deferred>

---

*Phase: 21-legacy-intent*
*Context gathered: 2026-06-13*
