# PBR findings: new documentation correction

Status: PROPOSED CORRECTION; COMPLETE RECHECK PENDING; IMPLEMENTATION ADMISSION HOLD. Documentation only. No product source, tests, runtime or external state changed.

## Exact prior input and review boundary

- Prior published proposal: [PR261 head a62cdfa30432361ff3603abd74c7d5506b4447d4](https://github.com/HouMinXi/forge/tree/a62cdfa30432361ff3603abd74c7d5506b4447d4/docs/gsd/proposals/2026-10-19-offline-analyser).
- Prior [MANIFEST.json](https://github.com/HouMinXi/forge/blob/a62cdfa30432361ff3603abd74c7d5506b4447d4/docs/gsd/proposals/2026-10-19-offline-analyser/MANIFEST.json): SHA-256: 245540e29c19371920114718e798bfc2237e3ce5c7082b11a3444d9966ac00da, 18 files, 216042 bytes. It remains unchanged at that immutable commit and in the retained review input.
- Completed review report SHA-256: 6922d8115cd28a49aa624e580933be2cf9341fe7baf938567fccb4f77930c6c0. Baseline unique findings: B0/H0/M1/L2. This identifies the retained complete report; it is not a claim that the privately retained report is downloadable from this bundle.
- User-supplied methodology identity: 581 LF lines, 98475 bytes, SHA-256: 9c9fa4ed73f259696603ea48f7d41a2adbea6bd65b908a679b29cbaf1e07e793. The reviewer found it self-contained and completed the whole applicable procedure. No private methodology text is included here.
- Review-time main observation: 0ec35ee3c1c1fd8a24f460672ad19233a5265e4f. The analyser, existing test module and read-only ledger/corpus contracts matched their prior exact hashes. This did not renew ownership, runtime/import/source-graph evidence or the earlier PR-overlap observation.

The earlier GSD documentary and packaging reports are preserved as historical results on their exact inputs. Their PBR-missing statements described that earlier state. This additive disposition supplies current status and takes precedence for review status in this new revision; it does not rewrite any prior report into a pass.

## PBR-M01: equivalent zero-F1 deletion — MEDIUM

The metric contract is unchanged: when both base metrics are defined and zero, F1=0; if either base metric is undefined, F1 is undefined. A branch-free exact count formula is allowed whenever both base metrics are defined:

`Fraction(2 * TP, 2 * TP + FP + FN)`

For defined P=R=0, TP=0, FP>0 and FN>0. Deleting an explicit redundant zero-return branch can therefore leave the same zero result through the count formula. C16 cannot honestly distinguish that equivalent deletion.

**Explicit narrow override:** where the immutable frozen controls, N07 design or task text demands a separate zero-branch omission RED, require a separate **non-equivalent zero-result fault** instead. Bind the exact candidate hash, source site, test node and expected assertion before the cut. A suitable mutation restores the legacy zero-to-undefined decision at that actual F1 computation/decision site. C16 must then catch expected zero becoming undefined (including the exact CLI value where relevant). The implementation need not add a redundant branch merely to delete it.

Record an equivalent deletion with the exact source/cut identity, algebraic and source rationale, and independent disposition. Mark it equivalent, not RED, not a successful negative-control witness. Then retain the separate non-equivalent invariant witness. No fabricated failure or weakened expected value is allowed. Every changed statement still needs actual execution coverage/assertions. Independent precision/recall numerator-denominator and undefined-F1 witnesses, exact restored bytes/restored GREEN, actual counts/raw streams and independent review remain mandatory.

The frozen 59-16-FROZEN-CONTROLS.md stays byte-identical at SHA-256: c34edb7ed52546099383cd126a947adac7c5aaf3909a55c7527fef422834a617. Its C16 value oracle and all other controls remain unchanged. Only its N07 mutation-mechanism wording is superseded by this explicit disposition. PATCH-DESIGN, the typed task and validation map now point to this rule consistently.

## PBR-L01: raw/rendered LF ambiguity — LOW

PATCH-DESIGN now uses an inline-code expression with one raw backslash:

`enumerate(text.split("\n"), start=1)`

Enumeration still precedes blank-record filtering. Raw Markdown transcription and rendered code now agree on one LF delimiter. The former ordinary-prose doubled backslash could render correctly under CommonMark; the preliminary HIGH classification was withdrawn. This is a documentation ambiguity correction, not a reproduced product defect. C12/C13 and escaped-newline C24 remain prospective controls, not executed results.

## PBR-L02: public-helper export inventory — LOW

The analyser artifact's exports metadata now lists load_entry_rows and entry_level_summary. Their signatures and the existing interfaces block remain unchanged. Existing load, _mean_se, _fmt, summarise, _lines, print_arm and positional main behavior remain preserved. No __all__, new module, runtime behavior or downstream interface change is introduced.

## PBR-M02: verified candidate backup before mutation — MEDIUM

The first complete rerun examined the unchanged 19-file successor manifest 5a60cd280a6828d504a0b589dc09c7a5b400e8dc42141e138aeba715ae939095 (227215 bytes). Its complete report is bound by SHA-256 961ff2c181bbc6f4879a75c8cae6f928d368addb5f52830668f6200f1fad3b4c. All core passes and applicable additional checks completed with B0/H0/M1/L0: M01/L01/L02 were verified closed, but it found a further pre-existing omission: exact restoration alone does not preserve the still-uncommitted candidate if a cut is interrupted or restored from HEAD. Hash detection cannot reconstruct discarded bytes. This is a planning omission, not a regression or observed data loss.

Before every cut, copy every affected candidate file to named owned temporary backup storage under the admitted evidence prefix, outside the mutation targets. Record each original/backup path and exact candidate hash, then read back and verify the backup. A failed backup/verification stops the cut before any mutation.

Run the bounded witness in a try/finally restoration transaction. Once the owned witness is no longer running, restore only the owned target files from the verified candidate backups on normal completion, assertion failure, exception or timeout. Never use git restore or any HEAD/index-based cleanup to restore a mutation. Rehash the restored files before the positive witness, next cut, review or publication. Keep the backups and raw witness/restoration evidence; do not touch foreign files.

A process interruption can prevent finally from running. If that happens, or restoration/process state is uncertain, stop the sequence and recover from the retained verified backups before resuming. No RED/GREEN or recovery success is invented. This clarifies the existing exact-restoration obligation and preserves the one final product commit; no temporary source commit is required or authorized.

## Current gate status

All four issue dispositions are incorporated into new exact documentation bytes. The first complete rerun verified the initial three corrections but found M02; this successor now requires another complete rerun. The finding-bearing baseline is not a clean round. Re-run the complete Stage 1 review on this new revision, including applicable empirical checks and source currency, then satisfy the required independent convergence. No previous same-family opinion or GSD documentary check is relabeled that convergence.

Operative admission, exact ownership ACK, owned runtime/interpreter/import/rules/graph/budget, all actual tests/RED/GREEN/mutations/coverage, native Forge review, CI, signed delivery, adoption/hygiene and historical qualification remain unestablished. 59-16 stays synthetic wave 1; 59-17 still depends on 02+15+16. Original 18/20, retained 12/20 and 13/20 failures, source/config/labels, frozen IDs, actual three-canary 20-or-150 selection, B3/B4 and P59-CLOSE are unchanged.
