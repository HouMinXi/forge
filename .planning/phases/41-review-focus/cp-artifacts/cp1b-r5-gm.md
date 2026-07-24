# CP1b round-5 -- gemini (gm), HUMAN-RELAYED

PROVENANCE: human-relayed by the user. UNLIKE round-4, gm ran agentically WITH
live repo access this round (its transcript shows Read trust.py, grep on
mcp_jobs.py / mcp_server.py, Read mcp_server.py) -- so this verdict is
repo-grounded, not no-repo inference. The text below is gm's relayed output.

SUMMARY: B=0 H=0 M=0 L=0  (plus one NOTE, below)

## NOTE (gm rated below-Low; PM + lc escalated to a real finding)

gm: In Task 3a-1 the plan describes cli.py:1861 as a character-count comparison,
but the real source cli.py:1861 is `len(effective_content.encode("utf-8")) > 4096`
-- a UTF-8 BYTE count. gm judged it non-blocking because the plan's own
`_merge_focus_spec` rule (`len(merged) > 8192`) is internally self-consistent and
introduces no leak/crash, so gm passed the plan 0/0/0/0 for execution.

gm's verified-clean areas (repo-grounded):
- A. REPLAN(a) dual-tmpfile: `.name` captured after NamedTemporaryFile before
  write, unified try/except BaseException, all exit paths unlink correctly, no
  leak / no double-unlink.
- B. REPLAN(e): `_evict_stale` gated only on `_JOB_TTL_SECONDS` + terminal
  status; injecting a stale `status="failed"` entry bypasses `_wait_for_job`'s
  finally -- falsifiable. Inline-path unlink assertion present.
- C. 3b(d): `raw_focus = focus_spec` saved before merge; workspace/staged in
  scope; gate_yaml_path built, warn_fn consistent with _merge_contract_spec.
- D. Cross-refs: header-rename targets, git_blame date parse, test_legacy date
  assert, test_trust_empty_backends assert update -- all correct.

## PM disposition

CONFIRMED as a real finding -- CONVERGED with lc Medium (Finding 1), and with the
PM's own ground-truth read of cli.py:1861. Three independent confirmations:
gm (repo-grounded NOTE), lc (repo-grounded MEDIUM), PM (direct Read of :1861).

The defect is the plan author's round-4 edit A (fix #6): it recast the original
"8192 bytes" guard to "8192 CHARACTERS ... matching cli.py:1861's char-count
guard ... NOT bytes" on gm's round-4 claim -- which the PM marked CONFIRMED in
round-4 WITHOUT reading :1861 (a Golden-Rule-1 grounding lapse). cli.py:1861 is
`len(...encode("utf-8")) > 4096` (byte count, threshold 4096), so edit A was
wrong on both the unit (byte, not char) and created an internal contradiction
with 41-PLAN.md:936 ("contract body <=4096 bytes"). gm rated it a NOTE; lc rated
it Medium; the PM adopts Medium (a false cross-reference parity claim misleads
implementers, per lc's rationale).

FIX APPLIED (round-5 union, 41-PLAN.md 3a-1): reverted to byte-count at the
ORIGINAL 8192 threshold -- `len(merged.encode("utf-8")) > 8192`, mirroring
`_merge_contract_spec`'s `len(...encode("utf-8")) > 4096` byte guard; the
8192-vs-4096 gap kept as intentional (focus warns-only, no summarization). The
false "char-count / NOT bytes / gm r4" rationale deleted. Not lc option (a)
(drop to 4096) -- that would be an unrequested behavioral change; the bug was the
unit label only, so the threshold stays. Contradiction with :936 resolved.

## Coverage note

gm ran with repo access this round (no-repo relay was NOT needed). Its
repo-grounded 0/0/0/0-plus-NOTE independently landed the same units defect lc
found -- the union's single finding. Everything else gm and lc checked is clean.
