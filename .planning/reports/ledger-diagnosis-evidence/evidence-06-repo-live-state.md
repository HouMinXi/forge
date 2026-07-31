# Evidence 06 -- forge's own real, un-manufactured operational state

## What this is

Not a scratch experiment: this is a read-only inspection of the actual
forge repo's own `.code-forge/state.json` and `.code-forge/receipts/`,
i.e. the residue of real review runs of forge reviewing itself. No file
was modified to produce this evidence.

## The repo's last real review run recorded mode=CI

```
$ cd /home/houminxi/code/forge
$ python3 -c "
import json
d = json.load(open('.code-forge/state.json'))
print('mode:', d.get('mode'))
print('verdict:', d.get('verdict'))
print('converged:', d.get('converged'))
print('round:', d.get('round'))
print('consecutive_clean_rounds:', d.get('consecutive_clean_rounds'))
"
mode: CI
verdict: PASS
converged: True
round: 0
consecutive_clean_rounds: 0

$ stat -c '%y %n' .code-forge/state.json
2026-07-27 22:24:23.026960554 -0400 .code-forge/state.json
```
`round: 0` and `consecutive_clean_rounds: 0` are consistent with
`_run_ci`'s structure (a single linear round, no fixpoint-loop concept
-- `consecutive_clean_rounds` is a LOCAL-only counter that `_run_ci`
never touches). This is the LAST real review of this repo before this
diagnosis began (2026-07-27, 22 days after Phase 43 shipped on
2026-07-04, and unchanged through today 2026-07-30 -- no later run has
touched this file).

## receipts/ directory: a longer history, same story

```
$ ls -la .code-forge/receipts/
receipt-c1p1.json.stale   2026-07-27 22:24
receipt-c1p2.json.stale   2026-07-27 22:24
receipt-c1p3.json.stale   2026-07-27 22:24
receipt-c2p1.json.stale   2026-06-01 09:52
receipt-c2p2.json.stale   2026-06-01 09:52
receipt-c2p3.json.stale   2026-06-01 09:52
receipt-c3p1.json.stale   2026-06-01 09:52
receipt-c3p2.json.stale   2026-06-01 09:52
receipt-c3p3.json.stale   2026-06-01 09:52
```
The c2/c3 (cycle 2, cycle 3) receipts from 2026-06-01 predate Phase 43
(shipped 2026-07-04) entirely -- the ledger feature did not exist yet,
so those runs cannot be a source of missing rows. They do show LOCAL
mode's multi-round convergence loop DID execute in this repo at some
point in the past (only LOCAL produces c2/c3; CI is always c1/round 0).
The most recent receipts (c1 only, 2026-07-27) are POST-Phase-43 and
match the CI-mode state.json finding exactly -- consistent, no
contradiction.

## gate.yaml -- this repo's own dogfooding config

```
$ cat .code-forge/gate.yaml
outlet: subprocess
backends:
  mock-local: ...
  gemini-omniroute:
    type: api
    format: openai
    base_url: "https://192.168.100.10:20128"
    ...
    default: true
```
`outlet: subprocess` confirms forge's own dogfooding goes through the
CLI-subprocess path (cli.py's `_run`), which resolves mode via
`resolve_mode()` (evidence-03) -- and the recorded result was CI.

## Reading

This is the single strongest piece of evidence in the whole diagnosis:
not a constructed scenario, but the actual forge project's own last
real self-review, which recorded `mode: CI` and left no ledger.jsonl
behind. It directly corroborates H1 using the exact object of the
investigation (this repo), not a stand-in.

## Limitation, stated plainly

`state.json` is overwritten wholesale on every run (loaded then
replaced -- see `machine.py:270-287` `_maybe_load_prior_state` and
`:251` `self._state.mode = self.mode`), so this file only ever shows
the LAST run's mode, never a full history of every mode used across all
past runs. The receipts directory extends the visible history somewhat
(by cycle/round) but does not itself record a `mode` field per receipt
either. This diagnosis cannot enumerate, from artifacts alone, every
mode every historical run of this repo ever used -- only the most
recent one, plus the coarse c1-vs-c2/c3 shape.
