# CP1b round-3 -- gemini (gm), human-relayed

PROVENANCE: gm is HUMAN-RELAYED (the user hand-forwards the payload to gemini
and pastes the result back). gm's verbatim round-3 text arrived in the session
chat, not via aicc, so -- unlike ds/kimi/mm/lc which have raw aicc stdout in
cp1b-r3-{model}.md -- there is no captured stdout file for gm. This file is a
PM reconstruction of gm's two relayed findings, NOT gm's verbatim prose. Each
finding was independently re-verified by the PM against real source before
being accepted (see verification notes). The verbatim paste, if needed, is in
the session transcript for 2026-07-23.

SUMMARY (as relayed): B=0 H=0 M=1 L=1. H1 core fix confirmed correct.

## [M] REPLAN(a): second tmpfile created outside the cleanup try leaks the first

Location: plan REPLAN (a) inside `_dispatch_cli`; real code mcp_server.py:664-700.

Finding (relayed): `_dispatch_cli` creates `contract_tmp` BEFORE the dispatch
`try` block. The plan's focus addition adds a second pre-try tmpfile creation
(`focus_tmp`). If creating/writing `focus_tmp` raises (OSError), the
already-created `contract_tmp` leaks -- the `except BaseException:
_unlink(contract_tmp)` guards only the dispatch call, which is never reached.

PM verification: CONFIRMED against real source. mcp_server.py:664-672 creates
`contract_tmp` before `try:` at :674; the except at :678-680 only wraps
`_run_cli_budgeted`. A focus-tmpfile creation failure would indeed leak
contract_tmp. This is a gap the focus addition INTRODUCES, not a pre-existing
bug (today there is only one pre-try tmpfile).

Disposition: FIXED. REPLAN(a) rewritten -- both tmpfiles None-initialized, both
creations wrapped in one try/except that unlinks whichever exists on failure
(`_unlink` is already None-safe, proven by the existing contract=None path).

## [L] is_trusted_focus pseudocode omits the store load

Location: plan 3a-2 `is_trusted_focus` pseudocode.

Finding (relayed): the pseudocode references `store.get(...)` but never loads
`store`, unlike the real `is_trusted` which loads it at function top.

PM verification: CONFIRMED. Real `is_trusted` (trust.py:131) and `record_trust`
(trust.py:167) both call `_load_trust_store()`. The plan pseudocode used `store`
undefined.

Disposition: FIXED. Added `store = _load_trust_store()` after the empty-focus
short-circuit (placement preserves the "short-circuit before store read"
migration property the surrounding plan comment requires).

## Note on gm round-2 (context)

gm's round-2 (human-relayed) returned 0/0/0/0 and retracted its own round-1 H1
with a backwards code claim ("_load_gate_backends returns (cfgs, gd)" -- the
real code returns ([], {}) when untrusted, cli.py:160). That false-green is why
the PM ground-truth layer never accepts an external "0/0/0/0" as a CP1b exit.
gm's round-3 above did NOT repeat the retraction; it engaged the H1 fix as
applied and found two real gaps, both now fixed.
