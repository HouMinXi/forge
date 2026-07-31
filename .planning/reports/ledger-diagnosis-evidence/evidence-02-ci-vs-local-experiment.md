# Evidence 02 -- CI vs LOCAL, identical finding, real StateMachine

## What this tests

H1 (mode) and H4 (disposition) directly. Pattern copied from forge's own
`tests/test_realpath_ledger.py` (read only, not modified): a real git
repo, a real diff, real SHAs from `git rev-parse`, the real
`code_forge.machine.StateMachine`, the real `code_forge.ledger` module.
The ONLY variable changed between the two runs is `mode`. No source or
test file under the forge repo was touched; the repo built for this
experiment lives entirely under
`/tmp/claude-1000/.../scratchpad/ledger-diag/exp1/scratch/` (outside
the forge repo).

Script: `exp1_run_ci_vs_local.py` (this directory).

## Sanity check run first: does forge's OWN real-path test currently pass?

```
$ cd /home/houminxi/code/forge && python3 -m pytest tests/test_realpath_ledger.py -v
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.0, pluggy-1.6.0
collected 1 item

tests/test_realpath_ledger.py::test_real_review_run_writes_real_sha_ledger_row PASSED [100%]

============================== 1 passed in 0.11s ===============================
```
Confirms: on HEAD 695f739, the writer mechanism itself (LOCAL mode, as
that test constructs it) works correctly. This rules out "the writer is
just broken" as an explanation.

## Command

```
cd /tmp/.../scratchpad/ledger-diag/exp1 && \
python3 run_ci_vs_local.py /tmp/.../scratchpad/ledger-diag/exp1/scratch
```

## Real output (full transcript in exp1_transcript_raw.txt)

```
======================================================================
RUN A: mode=Mode.LOCAL
======================================================================
  mode: 'LOCAL'
  verdict: 'Verdict.PASS'
  ledger_file_exists: True
  ledger_row_count: 2
  ledger_rows: ['fp-modecompare:FIXED', 'fixval-skipped:DISPROVED']
  finding_disposition_after_run: 'Disposition.FIXED'

======================================================================
RUN B: mode=Mode.CI (identical finding, identical autofixer/falsifier)
======================================================================
  mode: 'CI'
  verdict: 'Verdict.FAIL'
  ledger_file_exists: False
  ledger_row_count: 0
  ledger_rows: []
  finding_disposition_after_run: 'Disposition.CONFIRMED'

======================================================================
COMPARISON
======================================================================
  LOCAL ledger_file_exists=True rows=2
  CI    ledger_file_exists=False rows=0
  CONCLUSION: identical finding/disposition writes a ledger row under LOCAL and writes ZERO rows under CI.
```

## Reading

1. Same synthetic CONFIRMED finding, same autofixer, same falsifier.
   Under LOCAL it becomes FIXED and a ledger row is written (plus a
   second row from the FIXVAL-skip path, itself DISPROVED -- an
   unrelated but real second row). Under CI, the exact same finding
   stays CONFIRMED -- it is never even offered to the autofixer, because
   `_apply_autofix_loop_to` (the only code path that sets
   `Disposition.FIXED`) is gated `if self.mode == Mode.LOCAL` at
   `machine.py:832`. This is a second, independent structural fact:
   under CI, FIXED is unreachable regardless of the ledger question.
2. No `.code-forge/ledger.jsonl` file was created at all in the CI run
   -- not an empty file, no file.
3. Verdict differs (PASS vs FAIL) because CI's verdict rule
   (`machine.py:519-525`) counts any remaining CONFIRMED finding as
   FAIL, and this finding was never autofixed away in CI.

## Leak check (scope fence compliance)

```
$ ls /home/houminxi/code/forge/.code-forge/ | grep -i ledger
(no output -- confirmed clean)
$ git status --short --branch   (in /home/houminxi/code/forge)
## main
M  src/code_forge/lock.py
M  tests/test_lock.py
?? .coverage
?? .mcp.json
$ git rev-parse HEAD
695f739daf2209295682b487764b451bbf3511b8
```
No new files, no HEAD movement, no ledger.jsonl inside the forge repo.
The `.coverage` / `.mcp.json` untracked files and the staged
lock.py/test_lock.py diff pre-existed this session and were not created
or touched by this experiment.
