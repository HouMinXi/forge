# Exact 59-16 prospective validation overlay

Status: PROPOSED FOR REVIEW, NOT RUN. This is an additive slice map, not a replacement for the full unchanged phase validation strategy. Phase nyquist_compliant=false and wave_0_complete=false stay unchanged. No runtime estimate, collection count, pass count or tool availability is asserted.

## Typed task and automated-feedback mapping

| Task | Plan wave | Task prerequisite | Existing test anchor / automated command | Required outcome |
| --- | --- | --- | --- | --- |
| 59-16.1 | 1 | Exact plan/source/runtime admission, no historical dependency | PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/test_analyse_arms.py | Real collected baseline RED attributable to missing strict behavior; original legacy observations retained |
| 59-16.2 | 1 | 16.1 frozen independent controls | Same focused command | Actual frozen controls and unchanged legacy tests pass on exact source; all failures retained |
| 59-16.3 | 1 | 16.2 exact candidate | Same focused command plus separately bound focused witnesses | Each non-equivalent fault produces pointed RED, exact restoration GREEN; equivalent N07 deletions are explicitly dispositioned; changed statements execute; CLI composition and all independent review/delivery gates separately reported |

All three tasks contain Files, Read First, Behavior, Action, Automated Verify, Acceptance Criteria and Done in the candidate. The test file already exists; Task16.1 appends genuine tests rather than creating an empty stub. There is no missing new-file Wave0 dependency. Task order is sequential inside the proposed wave1 root; every three-task window has3/3 explicit automated anchors. This structural mapping is not a whole-phase Nyquist PASS.

Focused runs target prompt feedback with test-owned bounded subprocess timeouts. Actual timing and exact test node IDs are measured/bound later, not invented in this plan. The same task-level anchor does not excuse unrun variants or cuts; retain actual collected/pass/fail/skip counts and raw streams.

## Complete frozen-control to task/must-have/cut mapping

Every row is specified and frozen by16.1, implemented or respected by16.2, and qualified by16.3. The immutable frozen table defines ALL named variants, not one representative per group. T1 exact identities/exclusions; T2 citations/uncertainty; T3 honest exits; T4 legacy isolation; T5 no historical credit correspond to the candidate's five must-have truths in order.

| Control | Exact covered family | Truths | Independent cut families |
| --- | --- | --- | --- |
| C01 | Eight-entry oracle; counts/fractions/delta signs/units/full snapshot | T1,T2,T3 | N07,N09,N10 |
| C02 | SKIPPED and ESCALATED incomplete outcomes | T1,T3,T5 | N06,N09 |
| C03 | Missing/null/FUTURE_STATUS excluded outcomes | T1,T3 | N06,N09 |
| C04 | Non-string/non-null selected verdict schema | T1,T3 | N09 and each changed schema guard |
| C05 | Left/both missing IDs; empty/missing ledger | T1,T3 | N01,N09 |
| C06 | Identical/conflicting/outside-set duplicate IDs | T1,T3 | N02 |
| C07 | All malformed ID-file variants; LF/no-LF/CRLF valid variants | T1,T3 | Each changed ID guard plus N09 |
| C08 | Separate depth type/value, engine and backend variants | T1,T3 | N03 per distinct guard |
| C09 | Within/cross-arm backend mismatch | T1,T3,T5 | N03 per equality guard |
| C10 | All strict UTF-8/JSON/nonobject/duplicate-key/nonfinite/torn-tail variants and placements | T1,T3 | Each changed raw-input guard plus N09 |
| C11 | Frozen20 from150; reorder/order/substitution/missing | T1,T2,T3,T5 | N01,N05,N07,N08 |
| C12 | Exact physical LF1/4 small snapshot | T2 | N04,N05 |
| C13 | CRLF/no-terminal-LF/blank-tail/bare-CR | T2,T3 | N04,N05,N09 |
| C14 | Uncapped metrics, outside and excluded reference sets; complete AND incomplete variants | T2,T3 | N05,N08 |
| C15 | All three undefined-denominator cases | T2,T3 | N07 per undefined branch |
| C16 | Defined zero precision/recall gives new-mode F1=0 | T2,T4 | N07 exact-site non-equivalent zero-to-undefined fault; equivalent redundant-branch deletions are explicitly dispositioned, not claimed RED |
| C17 | Single-entry and multi-entry SE=n/a | T2 | Each changed metric-SE rendering site |
| C18 | Optional/misleading metadata has no scoring/provenance authority | T1,T5 | N10 plus changed ignored-field boundary |
| C19 | Synthetic sibling refs survive explicitly incomplete parent | T3,T5 | N06,N09; external fixture/index integrity check only |
| C20 | Synthetic actual-consumed versus canonical-only unavailable graph evidence | T5 | N10; external evidence disposition only |
| C21 | Full original tests/default CLI and all invalid strict flag combinations | T3,T4 | N11,N09 |
| C22 | Full frozen150 and one-missing variant | T1,T2,T3,T5 | N01,N05,N07,N08 |
| C23 | Alternative valid20 may complete arithmetic; frozen external digest refuses history authority | T5 | N10; external digest assertion only |
| C24 | Outcome/path/ID/backend/OS-error/CLI-error control-text escaping | T2,T3,T5 | N12 separately per stdout/stderr/citation/error seam |

N01-N12 remain families, not a claim that twelve omissions qualify every changed line. Each exact source statement/hunk and witness must be bound to reviewed candidate bytes before running. Additional changed guards appearing above require the same exact-site attribution even if the original family label is broad. For defined-zero F1, a branch-free exact count formula is allowed. An equivalent deletion of a redundant zero branch is recorded with algebraic/source rationale and independent disposition; it earns no RED credit. Bind a separate exact-site non-equivalent zero-to-undefined fault for C16. Every changed statement still needs execution coverage and assertion, and exact restoration/other independent witnesses remain required. PBR-FINDING-DISPOSITIONS.md explicitly overrides only the frozen N07 mutation mechanism. Each cut additionally requires a named, read-back/hash-verified backup of every affected uncommitted candidate file before mutation, a try/finally restoration from those backups after the owned witness stops, and restored-hash verification before the positive witness or any further action. Git restore and HEAD/index-based cleanup are forbidden for mutation recovery. Interrupted or uncertain restoration stops the sequence until recovery from retained backups is verified. No source mutation or cut is run in this documentation step.

C19, C20 and C23 fixtures are created inside tests/test_analyse_arms.py in test-owned temporary paths. Their evidence-index/disposition logic remains in the qualification/test layer. No runtime product evidence-consumer, historical-file import or third product file is permitted. They prove the boundary, not actual producer durability/consumption/history.

## Unchanged downstream acceptance mapping

- 59-02: real recovered-or-unknown source/config/label binding; original gate/failure and bounded recovery facts.
- 59-17.1: original18/20, actual three-canary cap and exact ID/config/label freeze; source-bound validator and any explicit pre-result reconstruction revision.
- 59-17.2: real retained original or authorized reconstructed calibration/canary/B3/d1 results and all measured fields, failures and raw references.
- 59-17.3: real59-16 report plus independent binding/acceptance adjudication; exact original/reconstructed/reviewed status. B4/P59-CLOSE remain unestablished without this evidence.

This overlay makes no new task changes to02/17. Their current commands, admission, budgets and all evidence requirements remain unchanged.
