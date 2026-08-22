# Phase 44: EVAL-ON-DUTY - Research

**Researched:** 2026-08-22
**Mode:** Focused implementation-pattern research (CONTEXT already locks 22
decisions D-01..D-22 with file:line anchors; this fills the HOW, not the WHAT)
**Base:** main @ 8bd01bc

Two plans per D-18: **44-01 write path** (CI-write, H2 persistence,
adjudicate, schema honesty, kill-switch) and **44-02 export-eval extractor**.

---

## R-1. `ledger adjudicate` subcommand — wiring pattern (44-01)

**Dispatch:** `_run_ledger` (cli.py:1458) routes on `args.ledger_command ==
"mark" / "list"`. Add an `"adjudicate"` branch. Registration:
`ledger_subs.add_parser("adjudicate", ...)` at cli.py:789 (mark) / list parser
follows. Non-review subcommand convention: return EXIT_PASS/EXIT_CLI_ERROR, no
CliError.

**Signature (per D-10/D-20):**
```
code-forge ledger adjudicate <fingerprint> <terminal_state>
    [--evidence "..."] [--base-sha S --head-sha S]
```
- terminal_state limited to FIXED/DISPROVED/DUPLICATE/ESCAPED (the four
  terminal states; adjudicating INTO UNADJUDICATED is meaningless).
- Looks up the source UNADJUDICATED row by fingerprint via `iter_rows(cwd)`.
  Errors (EXIT_CLI_ERROR) when: fingerprint absent; no UNADJUDICATED row for
  it (already adjudicated — latest row wins); terminal_state invalid.
- **Metadata inheritance (D-10/B-2 fix):** the appended terminal row copies
  file/line/axis_claim/base_sha/head_sha/repo_root from the source
  UNADJUDICATED row. `--base-sha/--head-sha` optional overrides (still
  pair-or-none, 40-hex per c5d420d). `--evidence` is the adjudication
  rationale -> evidence_class, capped per D-07/D-21.
- **Append-only (D-10):** writes a NEW row with terminal state; the source
  UNADJUDICATED row is never modified.
- **Echo (D-20a):** after writing, prints the inherited metadata
  (file:line, axis_claim, base/head) so the operator sees what was carried.
- pass_provenance for adjudicated rows: `"adjudicated"` (distinguishes from
  machine-written `"..."` sources and manual `"manual"`).

## R-2. `ledger list --unadjudicated` filter (44-01, D-20a)

The list branch (cli.py:1661-1690) filters by `--fingerprint` only. Add
`--unadjudicated` flag: rows whose fingerprint's LATEST row is UNADJUDICATED.
Implementation: group iter_rows by fingerprint, take max-ts row per
fingerprint, keep those with terminal_state == UNADJUDICATED. Works for both
TSV and --json output paths.

## R-3. CI write path — insertion point + failure isolation (44-01)

**Insertion:** `_run_ci` terminal at machine.py:~540 — after
`self._state.verdict = verdict; self._state.converged = ...`, after
`_persist_state()`, BEFORE `return verdict`. In-memory `self._state.findings`
carry fingerprints already (D-12, reviewer_json.py:153-181 — NO recompute).

**Row granularity (D-07):** one UNADJUDICATED row per CONFIRMED finding
(file/line/axis_claim per-finding). PASS runs (zero CONFIRMED) emit a single
run-level row with axis_claim="clean", file="", line=0 — the
false-positive-rate negative sample. FAIL runs: one row per CONFIRMED
finding.

**SHAs (D-13 verified):** base/head from `self.resolved_review` (snapshotted
at run start, machine.py:201,218) = pre-fix SHAs = correct polarity. Both
None -> skip writing (matches existing `_write_ledger_rows` guard at
machine.py:1318).

**Failure isolation (D-19):** wrap the write in try/except OSError; on
failure append to `self._state.infra_errors` and stderr-warn, NEVER change
the verdict.

**Kill-switch (D-19):** checked at the top of the CI-write helper:
`os.environ.get("CODE_FORGE_DISABLE_LEDGER")` truthy -> skip; else gate.yaml
`ledger.enabled == false` -> skip (load via existing gate config, tolerate
absent key = enabled default).

**Dedup (D-08):** UNADJUDICATED dedup key = (fingerprint, base_sha,
head_sha); re-running the same diff does not re-append. Read-side dedup in
the extractor (D-15) is the backstop for the TOCTOU window.

## R-4. H2 main-repo persistence (44-01, D-05/D-11/D-20b)

**Resolution order for the ledger path when writing from CI:**
1. `git rev-parse --path-format=absolute --git-common-dir` in cwd. On
   success: ledger lives at `<common-dir-parent>/.code-forge/ledger.jsonl`
   (the MAIN worktree root = common-dir's parent, NOT inside .git).
2. On any failure (not a git repo / rev-parse error): fall back to
   `cwd/.code-forge/ledger.jsonl` (D-11).

Rationale: in a linked worktree, `--git-common-dir` points at the main
repo's `.git`; its parent is the main worktree root where the durable
ledger belongs. Same path function is reused by adjudicate/list/export so
all subcommands read the SAME ledger the CI wrote.

**Note (devops DO-03):** O_APPEND single-write atomicity holds on POSIX
local filesystems (PIPE_BUF); on network/shared mounts it is best-effort.
Document as a known limit, not a gate.

## R-5. `ledger export-eval` extractor — materialization (44-02)

**CRITICAL reframe of D-03:** replay uses `git apply <file.diff>`
(runner.py:748) on a fresh temp repo — it does NOT need base/head to exist.
Only the EXTRACTOR needs them, to run `git diff base..head > file.diff`.
So dead-SHA handling lives entirely in the extractor:

- For each adjudicated terminal-state row (D-15: skip UNADJUDICATED with
  count+hint): resolve `git -C <repo_root> cat-file -e <base>` and `<head>`.
- Unresolvable -> D-03 skip path (stderr warning + stale count + listed in
  export summary report; NO manifest entry).
- Resolvable -> `git -C <repo_root> diff <base>..<head> >
  <out>/diffs/<name>.diff`.

**Expected-answers derivation (D-02/D-13):**
- FIXED / ESCAPED rows -> entry expects a CATCH at row.file/row.line with
  the row's claim (false-negative test for ESCAPED, hit test for FIXED).
- DISPROVED / DUPLICATE rows -> entry expects NO catch (false-positive
  test).
- diff_state: `pre-fix` for all ledger-derived entries (D-13 verified the
  SHAs are pre-fix).

**Manifest (D-04/D-09):** independent dir with manifest.yaml + diffs/ +
answers/. `source_status` field DROPPED (D-03 revision). Provenance records
repo BASENAME only (D-09 PII guard) — never the absolute repo_root.
`--repo-root` override remaps when the ledger was written elsewhere.

**Summary counters (D-15):** mutually exclusive by precedence —
unadjudicated > stale-sha > dedup-collapse. Report sums to total rows read.

**Output hygiene (D-22):** default `--out` = `.code-forge/eval-export`;
re-export cleans/overwrites only manifest-managed files, leaves foreign
files; non-empty pre-existing dir requires `--force`.

**Toolchain self-containment (D-17):** each entry emits a review-only
minimal gate config stub. The runner's `_create_gate_yaml` (runner.py:522)
ALREADY merges (reads existing, merges harness backend) — the stub follows
the same merge contract: no `test.command` (or a no-op), so replay never
invokes the foreign project's toolchain.

## R-6. Schema honesty (44-01, D-06)

TerminalState gains UNADJUDICATED. Choose ONE (planner decides):
- (a) rename enum to `LedgerState` + docstring rewrite (breaking, honest), or
- (b) keep name, rewrite docstring to "four terminal + one
  pending-adjudication" with the entry rule for each.
Either way: docstring MUST match code (the 14328bb class). Old readers
tolerate the new member silently (iter_rows skips unparseable rows, c5d420d)
— acceptable degradation, documented.

## R-7. Test strategy (both plans)

- **Real-path over mocks** (fab6d63 pattern): adjudicate tested against a
  real ledger.jsonl in a temp dir; export-eval tested against a real git
  repo with real SHAs.
- **44-02 consumes 44-01 output (D-18 coupling):** export-eval tests build
  their fixture ledger via the REAL CI-write path (a temp repo +
  StateMachine run), not hand-written JSONL — a 44-01 regression surfaces
  in 44-02's suite.
- **Row-size guard (D-07):** a test asserts every serialized row < 2048
  bytes (PIPE_BUF margin) including a maximally-truncated evidence field.
- **Bug-injection:** for the traversal/containment-style guards and the
  kill-switch, inject the defect (remove the guard) and confirm the test
  FAILs, then restore.

## R-8. LOC reality check

ROADMAP estimated 300-450; round-1 review confirmed ~750+. Split:
- 44-01 (write path + adjudicate + kill-switch + persistence): ~400-450
- 44-02 (export-eval extractor): ~300-350
The split keeps each plan inside a reviewable diff and isolates the
CI-write behavior change (44-01) from the read-only extractor (44-02).

---

## Open implementation choices deferred to planner

1. D-06 enum rename vs docstring-only (a vs b) — planner picks, document.
2. D-14 axis mapping fallback — skip-with-warning vs default axis.
3. Whether CI-write reuses `_write_ledger_rows` (extended) or a new
   `_write_ci_ledger_rows` helper — prefer extending the existing one to
   avoid two writers drifting (golden rule: no duplicate logic).
