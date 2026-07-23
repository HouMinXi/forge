# Phase 41 (review-focus) -- CP1b external review: PM adjudication

Status: OPEN -- CP1b REFUTED at round-2. gm (human-relayed) round-2 returned
0/0/0/0 ("ready for direct implementation"), but PM ground truth found that
verdict rests on a false-green: gm's OWN round-1 [H1] (_load_gate_backends
wipes review_focus when backends are untrusted) is a REAL, code-confirmed,
UNFIXED finding. CP1b needs another round after the H1 fix. As of 2026-07-23.

## Panel and channels (IMPORTANT: gm is human-relayed)

- deepseek (ds), kimi, lc -- aicc auto-dispatch; responses saved as
  cp1b-r1-{model}.md in this directory.
- gemini (gm) -- HUMAN-RELAYED by the user (manual forward to gemini and
  back), NOT aicc-automated. The tiny /tmp/p41-r1-gm-*.md stubs (74-352
  bytes) are FAILED aicc auto-dispatch attempts (agy/gm auth-VPN quirk),
  NOT gm's review. gm's real input/output flows through the user's hand
  forward, so gm's round-2 response arrives when the user forwards it --
  its absence from /tmp is expected, not a gap.

Diverse-angle panel (perspective-diverse by design):
ds = general severity; gm = runtime crash; kimi = cross-boundary data
integrity (focus dropped / double-merged / trust-gate lost);
lc = plan-vs-code accuracy.

## Round-1 findings (8) -- ALL FIXED in 41-PLAN.md

(The round-2 gm dispatch header says "6 issues"; 8 are actually listed --
a mimo count slip, immaterial to the fixes.)

| ID | Sev | Finding | Fix | PM independent cross-check |
|----|-----|---------|-----|----------------------------|
| B1 | Blocker | Double-merge risk in MCP focus plumbing | raw passthrough: _dispatch_cli / _dispatch_sampling receive RAW focus, merge exactly once | = PM finding C4; verified correct vs cli.py:2044/2068 |
| H1 | High | Superseded 3b-3/3b-4/3b-5 boundary unclear | "SUPERSEDED -- do NOT implement" banner added before old sections | seen in plan diff (Task 3b) |
| H2 | High | Acceptance D5.7 contradiction | acceptance bullet updated to merged state (2edb9d4) | seen in plan diff (Acceptance) |
| H3 | High | staged guard missing for yaml_focus (sampling) | `if not staged:` guard, mirrors contracts_yaml gating | = PM finding C3; verified vs mcp_server.py:839 (+ D2 comment) |
| M1 | Med | record_trust dict replace "loses future keys" | merge-first `{**store.get(key,{}), ...}` | = PM finding C2; PM CORRECTED the rationale -- contracts_hash lives under a different store key (trust.py:302), so gate entry has nothing to clobber; merge-first kept as defensive-only |
| M2 | Med | hash_focus_text Optional[dict] vs dict | signature -> dict | = PM finding C6; matches is_trusted signature (trust.py:125) |
| M3 | Med | trust store key format | `str(path.resolve())` to match is_trusted | = PM finding C1; verified vs trust.py:132 -- a REAL lookup-miss bug the baseline had |
| M4 | Med | blame date synthetic test strategy | GIT_AUTHOR_DATE / GIT_COMMITTER_DATE env-var test | seen in plan diff (Task 4) |

Cross-confirmation strength: the PM independently verified C1/C3/C4/C6 as
correct and flagged C2's rationale as imprecise BEFORE reading any CP1b
file. The panel's M1 carried the SAME imprecise "loses future keys"
rationale the PM had already corrected -- two independent paths reaching
the same answer. This is the strongest evidence class (independent
convergence, not shared context).

## Round-2 items (raised by ds/kimi, for gm to confirm/refute) -- dispositioned

- H-claim "gate-check doesn't go through _dispatch_cli, so parity is wrong"
  -> DISPROVED. forge_gate_check calls _dispatch_cli at mcp_server.py:1078
  (confirmed via code-review-graph 3-caller set: forge_review :1025,
  forge_gate_check :1078, _dispatch_sampling fallback :917). Parity holds.
  Does not reset the cycle (disproved by ground truth).
- M1-wording "raw-in/merge-inside (Phase 41) vs merged-down (cross-repo)"
  -> already documented intentional at 41-PLAN.md:403-404 ("deliberately
  does NOT mirror the contract mechanism ... threads the merged focus_spec
  directly to build_l1_provider"). Non-issue.
- L1 "superseded list missing old 3b-5's tmpfile-content==RAW assertion"
  -> ADDRESSED. replan (e) carries the tmpfile CONTENT / RAW-not-merged
  assertion at 41-PLAN.md:520-522.

## gm (human-relayed) round-1 + round-2 -- and [H1] CONFIRMED OPEN

gm's two reviews arrived via the user's manual relay on 2026-07-23.

gm round-1 (on p41-plan-for-agy.md): B=1 H=1 M=2 L=2. Its B1 (outlet-C
double-merge) = B1 above; its M1 (sampling-fallback raw focus) = the L1
tmpfile-content item; its L1/L2 independently CONFIRM the PM's C1 (resolve
key) and C3 (staged guard). A valuable round -- and it raised a NEW finding
the ds/kimi/lc panel missed:

[H1] _load_gate_backends wipes review_focus when backends are untrusted.
  - CONFIRMED REAL by PM ground truth. _load_gate_backends returns
    ([], {}) on untrusted backends (cli.py:160; docstring: "empty dict if
    ... untrusted"). Plan 3a-3 (41-PLAN.md:302-308) extracts review_focus
    from exactly that dict -- `raw = gate_data.get("review_focus", "")` --
    and passes the same {} into is_trusted_focus. So with untrusted
    backends a legitimately-trusted review_focus is SILENTLY dropped and the
    focus-specific warning never fires: focus is coupled to backend trust,
    defeating the independent focus-trust design (D5.6). All paths hit it
    (3a-3 says the sampling path also calls _load_gate_backends for focus,
    41-PLAN.md:316-319).
  - STATUS: OPEN. mimo's diff has no _load_gate_backends / 3a-3 change --
    H1 was never fixed.

gm round-2 (on p41-r2-gm-combined.md): SUMMARY B=0 H=0 M=0 L=0, "ready for
direct implementation." REFUTED. gm marked its own H1 "fully resolved" on
the claim that "_load_gate_backends returns (cfgs, gd)" -- the exact
opposite of cli.py:160 (returns ([], {}) when untrusted). A single-model
cross-round false-green: round-1 finds a real bug, round-2 (same model, no
new evidence) retracts it -- the adversarial-qe retraction-skepticism red
flag, gemini variant of the deepseek rounds-3+ oscillation. gm's 0/0/0/0 is
not trustworthy on H1. (This is why the PM ground-truth layer exists;
external "0/0/0/0" is a claim, never an exit by itself.)

gm round-1 [M2] (hash_focus_text Optional[dict] + strip whitespace): the
None concern is made MOOT by the H1 fix below (focus_gd from a raw load is
always a dict), so `dict` (C6) stays correct; the whitespace nit is LOW.

### Recommended H1 fix (3a-3)
Read review_focus INDEPENDENTLY of backend trust. Add
`_load_gate_yaml_raw(path) -> dict` (parsed dict, or {} on absent/invalid,
NO trust gating); extract review_focus from it; gate ONLY on
is_trusted_focus(gate_yaml_path, focus_gd). Do NOT change
_load_gate_backends -- 24+ callers depend on its trust-gated {}-on-untrust
contract (semantic > defensive). The sampling path already does an
independent read, so this unifies all paths on _load_gate_yaml_raw, and it
also resolves M2's None concern.

## PM plan edits this session (post-mimo, applied by the PM)

- C7: reverted Task 1 step-2 from mimo's "skip comments/docstrings"
  narrowing back to "replace ALL". The narrowing contradicted the unchanged
  verify / done / acceptance ("zero Contract Reference left"); all 24 real
  occurrences are assertions / docstrings / assert-messages that must track
  the rename -- none are historical notes to keep.
- C2: kept merge-first, corrected the rationale (grounded to trust.py:302).
- typo: removed the stray backtick before "mirror" (41-PLAN.md:503).
- Snapshot: planning-local @ b4f5316 after the edits;
  integrity disk=642 == snapshot=642.

## Convergence status -- NOT 0/0/0/0 (H1 open)

gm round-2 arrived (0/0/0/0) and was REFUTED on H1 (above). To close CP1b:
1. Fix H1 in 3a-3 (recommended fix above) -- a NEW confirmed finding, so it
   RESETS convergence; the corrected plan must go through another round.
2. Re-dispatch round-3 carrying full prior disposition (R1 fixes verified,
   H-claim disproved, M1-wording/L1 addressed, H1 fix new) -- gm via manual
   relay, ds/kimi/lc via aicc. ds/kimi/lc round-2 responses were never
   captured (r2-plan.md/r2-prompt.txt at 08:21 suggest one was prepared;
   fold them into round-3 if they surface).
3. Any new finding -> fix -> next round, per the non-convergence protocol.
Then: user final human review before /gsd:execute-phase (forge CP1b exit).

Every external verdict, incl. any "0/0/0/0", is a CLAIM verified against code
before it counts -- H1 is the proof of why (gm self-certified a false green).

## /tmp inventory map (campaign 2026-07-23)

Migrated into this directory (per forge CLAUDE.md S2 artifact-persistence):
- p41-cp1b-prompt-{ds,gm,kimi,lc}.md -> cp1b-r1-prompt-{model}.md (angles)
- p41-r1-ds.md            -> cp1b-r1-ds.md      (round-1 response)
- p41-r1-kimi-v3.md       -> cp1b-r1-kimi.md    (round-1 response; v3 = retry)
- p41-r1-lc.md            -> cp1b-r1-lc.md      (round-1 response)
- p41-r2-gm-combined.md   -> cp1b-r2-gm-dispatch.md (round-2 gm dispatch +
  the round-1 disposition scorecard; the package the user hand-forwards)

Left in /tmp (noise / superseded, deliberately not migrated):
- p41-r1-gm-*.md (74-352 bytes) -- failed aicc auto-attempts to reach gm;
  gm is human-relayed, so these are not gm's review.
- p41-cp1b-combined-{model}.md (~44KB) -- round-1 dispatch packages that
  embed the whole plan; the plan already lives in 41-PLAN.md, not duplicated.
- p41-* dated 2026-07-20 / 07-21 -- the earlier pre-reconcile review
  campaign (r1..r6), superseded by this CP1b run.
