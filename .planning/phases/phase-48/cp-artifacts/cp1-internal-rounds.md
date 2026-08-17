# CP1 internal review records (phase 48), 2026-08-16

## gsd-plan-checker round 1 (pre-CP1b)
VERIFICATION PASSED after the planner revision loop (blocker T4-Test-1
arithmetic + W-1..W-7). Prompt/response transcripts in the session.

## CP1b R1 external panel (kimi + mimo)
12 findings, all accepted -> CP1B-R1 AMENDMENTS A-1..A-10.
Raw outputs: /tmp/cp1b_r1_kimi.txt, /tmp/cp1b_r1_mimo.txt (copies below).

## Internal round 1 (checker + PBR 8-pass) on the amended plan
checker: 0B/1H/1M/3L; PBR: 0B/1H/3M/3L. Consensus defects:
H: A-3 threshold=0 vacuous probe. M: amendment-vs-body divergences
(A-1 fold wiring, A-9 numbering, A-10 inline guard). L: rationale
mis-cite, line-ref drift, A-4 ordering unstated.
Resolved by AMENDMENTS ROUND 2 (A-11..A-16).

## Internal round 2 (checker delta re-verify)
0B/0H/1M/0L: A-16's I9 unprovable as specified (except-clause swap
inert). Resolved by AMENDMENTS ROUND 3 (A-17).

## PBR 8-pass raw record (agent output)
F-6.1 HIGH Incorrect Fact: A-3 threshold=0 trips pre-dispatch, probe
makes 0 HTTP calls, vacuously confirms A1; on-disk a1_probe.py predates
A-3; exit-11 unachievable.
F-6.2 MEDIUM: A-1 fold wiring contradicts T4 GREEN "nothing else
changes"; test table + acceptance counts stale.
F-6.3 MEDIUM: A-6/A-9 vertex test absent from behavior numbering,
table, and "All 7 new tests".
F-2.1 MEDIUM: A-10 isinstance guard not propagated into T3 GREEN (b);
truthy non-str partial crashes; untested.
F-2.2 LOW: A-2 normalizes None only; non-str continuation TypeErrors.
F-5.1 LOW: A-4 except ordering implied but never stated.
F-1.1 LOW: line refs off by one (cli 3004->3005, cross_repo 302->305).

## Internal rounds 3-5 (2026-08-16)
Round 3 (A-17/A-18 delta): 0B/0H/0M/1L (A-17 rationale mechanics
claim; resolved by A-19). Round 4 (A-19 delta): 0B/0H/0M/0L PASS.
Round 5 (A-20..A-23 delta, closing mimo-R2 #1-#5): 0B/0H/0M/0L PASS.

## CP1b R3 (final external round) -- 0/0 exit
mimo: NO FINDINGS. kimi leg: substituted with deepseek-v4-flash
(user decision; kimi k2.7 hard-capped at 262144 and the local proxy
assigned a 256K-tier key; KIMI_MODEL=kimi-k3 did not change the
proxy's key selection). dsflash: NO FINDINGS (finish: stop).
