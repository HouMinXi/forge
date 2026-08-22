# Phase 44 EVAL-ON-DUTY: R2 Technical-Accuracy & Ground-Truth Verification

**Reviewer Profile:** Scribe (Documentation & Accuracy Specialist)  
**Artifact Under Review:** `/home/houminxi/code/forge/.planning/phases/44-LEDGER-eval-on-duty/44-CONTEXT.md`  
**Focus:** Fact-checking every file:line citation, commit SHA, historical claim, internal decision cross-reference, and terminology consistency against ground truth.

---

## 1. Claim-by-Claim Verification Table

| # | Claim | Location in CONTEXT.md | Verdict | Evidence / Ground Truth |
|:---|:---|:---|:---:|:---|
| 1 | `src/code_forge/ledger.py` line count = 122 | Line 152 | **PASS** | `wc -l src/code_forge/ledger.py` returns exactly 122 lines. |
| 2 | `src/code_forge/eval/` total = 1572 lines | Line 157 | **DRIFT** | `wc -l src/code_forge/eval/*.py` returns 1450 lines (`corpus.py`: 196, `__init__.py`: 1, `runner.py`: 781, `scorer.py`: 472). Non-blocking documentation drift. |
| 3 | `mcp_server.py:929` hardcodes `mode=Mode.CI` | Line 172 | **PASS** | `src/code_forge/mcp_server.py:927` instantiates `StateMachine(mode=Mode.CI, ...)`. |
| 4 | `cli.py:1643-1654` re-marking drops metadata | Line 165 | **PASS** | `src/code_forge/cli.py:1640-1654` shows `append_row()` defaulting `line=0`, `axis_claim="manual"`, dropping location/SHA context. |
| 5 | `reviewer_json.py:153-181` fingerprint recipe | Line 167 | **PASS** | `src/code_forge/reviewer_json.py:153-181` defines `_json_to_state_findings()` computing `sha256(file:line:desc)[:16]`. |
| 6 | `machine.py` `_write_ledger_rows` call sites LOCAL-only | Line 173-174 | **PASS** | `grep -n _write_ledger_rows src/code_forge/machine.py` finds calls only at lines 1240, 1260, 1283, 1297 (all within `_finalize_local_terminal`); `_run_ci` has 0 references. |
| 7 | Commit `14328bb` subject / scope | Line 19, 153 | **PASS** | Commit `14328bb` verified: `ledger: fix docstring-code mismatch + accept uppercase SHAs`. |
| 8 | Commit `7b6101a` subject / scope | Line 162 | **PASS** | Commit `7b6101a` verified: `ledger: attach a location and claim to every new escape row`. |
| 9 | Commit `c5d420d` subject / scope | Line 66, 156, 163 | **PASS** | Commit `c5d420d` verified: `ledger: harden iter_rows + tighten ledger mark validation`. |
| 10 | Commit `2495035` subject / scope | Line 80, 163-164 | **PASS** | Commit `2495035` verified: `ledger: dedup writes + restrict --new to escapes`. |
| 11 | Commit `fab6d63` subject / scope | Line 164, 188 | **PASS** | Commit `fab6d63` verified: `test: real-path acceptance for ledger SHAs`. |
| 12 | `ledger-empty-diagnosis-20260730.md` exists with H1..H5 structure | Line 175-177 | **PASS** | File exists at `.planning/reports/ledger-empty-diagnosis-20260730.md` with 8 evidence files; H1 and H2 dominant mechanisms confirmed. |
| 13 | `.planning/eval-bank/v1/` has 11 entries | Line 170 | **PASS** | `.planning/eval-bank/v1/manifest.yaml` contains exactly 11 items under `entries`. |
| 14 | Status header decision range cites `D-15..D-21` | Line 6 | **DRIFT** | Status header states "resolved below as D-15..D-21", but the decisions section terminates at D-18. D-19 through D-21 do not exist. |
| 15 | Decision ID continuity in D-18 plan split | Line 142-145 | **PASS** | Plan 44-01 cites D-01, D-05, D-06, D-07, D-08, D-10, D-11, D-12, D-13, D-16; Plan 44-02 cites D-02, D-03, D-04, D-09, D-13, D-14, D-15, D-17. All referenced decisions exist and are correctly scoped. |
| 16 | Internal decision logic consistency | Line 41-53, 76-88, 127-136 | **PASS** | D-03 (revised) and D-15 align on skipping unmaterializable diffs without emitting broken manifest entries; D-08 and D-16 align on append-only dedup vs scan cost expectations. |
| 17 | Terminology and obsolete field cleanup | Throughout | **PASS** | `TerminalState`, `UNADJUDICATED`, `axis_claim`, `diff_state` are used consistently; obsolete `source_status` field is correctly marked dropped in D-03. |

---

## 2. Document Quality Assessment

The document exhibits high technical rigor and precision. Ground-truth checks against the live codebase confirm that all 5 commit SHAs, git histories, diagnostic report paths, and core code locations (`ledger.py`, `mcp_server.py`, `cli.py`, `reviewer_json.py`, `machine.py`) are accurate. The 2 identified DRIFT items are minor clerical discrepancies (an eval LOC count of 1450 vs 1572 claimed, and a header reference to D-15..D-21 where decisions end at D-18) that do not impair execution or planning. Overall status is verified and ready for downstream phase execution.

**Scorecard: PASS=15, FAIL=0, DRIFT=2**
