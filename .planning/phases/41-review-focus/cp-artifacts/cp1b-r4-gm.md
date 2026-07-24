# CP1b round-4 -- gemini (gm), HUMAN-RELAYED

PROVENANCE: human-relayed via gemini web UI (no live repo access). aicc gm
auto-dispatch was tried this round and produced only a 195-byte agentic
preamble ("I will run a script to verify...") -- unusable, because OmniRoute
gemini goes agentic and the print-mode capture never completes. So gm was
relayed manually with the no-repo prompt variant (cp1b-r4-prompt-manual.md).
The text below is gm's relayed output as pasted by the user.

SUMMARY: B=0 H=1 M=1 L=1

## Finding 1: HIGH -- REPLAN(a) tmpfile name captured after write (leak on write failure)

Plan location: Task 3b-3 REPLAN(a) dual-tmpfile pseudocode.
gm: NamedTemporaryFile(delete=False) creates the file on disk at call time, but
the pseudocode assigns `contract_tmp = tmp.name` only AFTER `.write()/.close()`.
If write/close raises (DiskFull, UnicodeEncodeError, KeyboardInterrupt),
contract_tmp is still None, the outer `except BaseException: _unlink(contract_tmp)`
unlinks nothing, and the already-created file leaks. Same for focus_tmp.
Required fix: capture `contract_tmp = tmp.name` (and focus_tmp = ftmp.name)
immediately after NamedTemporaryFile(...), before write/close.
gm status marker: [INFERRED] (unverified -- depends on tempfile stdlib semantics).

PM disposition: CONFIRMED, == kimi M3 (independent convergence). Verified vs real
code: contract path assigns contract_tmp at mcp_server.py:671 AFTER write (:669)
/ close (:670). The union-fix edit-7 pseudocode copied that ordering. Real defect.

## Finding 2: MEDIUM -- 3b-1 wiring table cites stale :765

Plan location: Task 3b-1 wiring table vs RECONCILE section.
gm: the table lists build_sampling_l1_provider call site as mcp_server.py:765,
but RECONCILE states that post-2edb9d4 the call moved to :853/:857 and
_dispatch_sampling to :800. Plan-internal contradiction; an implementer keying
off the table lands wrong.
Required fix: update the table row to :853-857 (or symbol-anchor it).
gm status marker: [KNOWN] (confirmed from plan-internal contradiction).

PM disposition: CONFIRMED, == kimi L1 (convergence; gm rates Medium, kimi Low).
Verified vs real code: build_sampling_l1_provider call at mcp_server.py:853.

## Finding 3: LOW -- 3a-1 "8192 bytes" vs len() character semantics

Plan location: Task 3a-1 size guard vs Task 3c-2 / _merge_contract_spec.
gm: 3a-1 says warn when the merged focus "exceeds 8192 bytes", but the mirror
_merge_contract_spec (cli.py:1861) uses `len(merged) > 4096` (character count,
not bytes). Mixing "bytes" with character-length can diverge on non-ASCII input.
Required fix: unify to characters (`len(merged) > 8192`) matching
_merge_contract_spec, or state `len(merged.encode('utf-8')) > 8192` explicitly.
gm status marker: [INFERRED] (unverified -- depends on codebase encoding habit).

PM disposition: CONFIRMED as a real wording imprecision (NEW -- not raised by
ds/kimi/lc). The mirror uses len() char count; "bytes" in 3a-1 is ambiguous.
Fix: reword 3a-1 to characters to match the mirror. Low.

## Note on gm's coverage profile (no-repo reviewer)

gm caught exactly the findings reachable by plan-internal reasoning (M3 from
pseudocode, L1 from RECONCILE-vs-table contradiction, the unit nit from wording)
and did NOT surface the repo-dependent ones (kimi M1 inline-test coverage, M2
evict knob, M4 trust-assert breakage), which need test-file / source inspection
it did not have. Consistent with the no-repo relay; its [INFERRED]/unverified
markers were honest about that boundary.
