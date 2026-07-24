# Phase 41 (review-focus) -- CP1b external review: PM adjudication

Status: OPEN -- round-3 union fix APPLIED, awaiting round-4. Round-2 was a gm
false-green (its own round-1 [H1] retracted with a backwards code claim);
round-3 (5-model) confirmed the H1 fix core CORRECT and surfaced 11
completeness findings; ALL 11 are now fixed in 41-PLAN.md and PM-verified
(non-ASCII 5, fences balanced, shared helper syntax + 4 edge cases executed).
Next: round-4 to the kimi/gemini/deepseek panel carrying round-3 disposition.
As of 2026-07-23.

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
  - STATUS: FIX APPLIED by PM this session (3a-3, 41-PLAN.md:301-355):
    standalone `_load_gate_yaml_raw` (no trust gating), review_focus read
    from it and gated ONLY on is_trusted_focus; _load_gate_backends left
    untouched; all 3 focus paths unified on the raw loader; + an H1
    bug-inject guard and an acceptance criterion ("untrusted BACKENDS with a
    still-trusted review_focus STILL inject focus"). The fix is a NEW claim
    -- round-3 must review it.

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

## Round-3 (5-model panel, 2026-07-23) -- reviewing the H1 fix

Dispatched the reconciled plan (cp1b-r3-payload.md) to ds/kimi/mm/lc via aicc
+ gm via manual relay. H1 CORE fix confirmed CORRECT by ALL FIVE independently
(each traced the 6 verification points against real code). No core-logic
defect. 11 distinct NEW findings, all completeness/robustness, all PM-verified
against code. Raw responses: cp1b-r3-{ds,mm,kimi,gm,lc}.md.

| #  | src     | sev | gap | fix |
|----|---------|-----|-----|-----|
| 1  | ds      | M | _merge_focus_spec missing \n\n separator (3a-1) | mirror _merge_contract_spec:62 |
| 2  | ds      | L | (e) tests skip _evict_stale/snapshot leak paths | add 2 tests + per-consumer inject notes |
| 3  | kimi/mm | M | line 772 stale ("_load_gate_backends {}->no focus") | rewrite to is_trusted_focus-gated scenario |
| 4  | kimi    | M | cli.py:2182 "never re-read gate.yaml raw" invariant conflicts w/ H1 | revise comment to carve out focus exception |
| 5  | kimi    | M | bug-inject edits non-dangerous field -> is_trusted stays True -> HOLLOW | edit a DANGEROUS_FIELDS field; assert backends dropped first |
| 6  | kimi    | M | extract+gate duplicated CLI vs sampling; no shared helper (GR4) | new cli._load_trusted_yaml_focus(); both call it |
| 7  | kimi    | L | branch: whitespace-str warns "not a string"; falsy non-str silently dropped | restructure isinstance/strip branches |
| 8  | kimi    | L | 3a-4 attributes validation to argparse (--contract has no FileType) | guards live in _load_focus_file (CliError) |
| 9  | gm      | M | REPLAN(a) 2nd tmpfile created outside try -> leaks 1st on OSError | init both None; creates inside try/except-unlink-both |
| 10 | gm      | L | is_trusted_focus pseudocode missing store=_load_trust_store() | add the load line |
| 11 | lc      | L | Task 1 factories.py:576 "unreachable" stale + dangling "Task 3g" | rewrite caveat (reachable post-2edb9d4); delete Task 3g |

Keystone: kimi #6 (shared helper `cli._load_trusted_yaml_focus`) collapses #6 +
the sampling instantiation (mm-L) + #7 (branch logic fixed once) + part of #4.

## Round-3 union fix -- APPLIED 2026-07-23 (PM, in 41-PLAN.md)

All 11 landed as plan-document edits; each verified against real source before
writing (line numbers re-derived, not transcribed from the relay):
- #6+#7+#4+mm-L -> keystone: new `_load_trusted_yaml_focus(gate_yaml_path,
  warn_fn)` helper in 3a-3 (raw load + trust-gate + fixed branch); CLI `_run`
  and `_dispatch_sampling` both call it; 3b(d) now shows the explicit sampling
  call block (workspace/staged in scope, computes gate_yaml_path, mirrors the
  :847 warn lambda); invariant comment cli.py:2182-2184 carve-out instructed.
- #1 -> 3a-1 `_merge_focus_spec` gets the `(merged + "\n\n" if merged else "")`
  separator, mirroring `_merge_contract_spec` (cli.py:1889, verified).
- #5 -> 3a-3 bug-inject now edits a DANGEROUS_FIELDS field (base_url) so
  is_trusted actually flips; asserts "Untrusted repo backends ignored" first.
- #9 -> REPLAN(a): both tmpfiles None-init, created inside one try/except that
  unlinks both (contract_tmp is created pre-try at mcp_server.py:664-672, verified).
- #2 -> REPLAN(e): added _evict_stale (TTL) + snapshot_tempfile_paths leak tests.
- #3 -> 3c-2 stale row rewritten to the is_trusted_focus-gated scenario, kept
  distinct from the H1 acceptance row (no shared fixture).
- #8 -> 3a-4 attribution corrected: guards live in `_load_focus_file` (--contract
  has no argparse FileType, cli.py:351, verified).
- #10 -> is_trusted_focus pseudocode gets `store = _load_trust_store()` (real
  loader name, trust.py:131, verified) after the short-circuit.
- #11 -> Task 1 caveat rewritten: factories.py:576 reachable post-2edb9d4;
  dangling "Task 3g" deleted.

Ground-truth proof beyond static: the shared helper pseudocode was extracted,
py_compile'd, and RUN on 4 edge cases -- whitespace-str silent, empty-list
warns "not a string", trusted-str returned, untrusted-str warns "not trusted"
-- all PASS. This is the #7 branch fix proven by execution, not narration.

## Convergence status -- round-3 union APPLIED, round-4 pending

Round-2 H1 false-green -> H1 fix -> round-3 (5-model) confirmed H1 core CORRECT,
found 11 completeness gaps -> ALL 11 now fixed and PM-verified. Because this is
a NEW batch of confirmed findings, convergence stays RESET. To close CP1b:
1. [DONE] Apply the union fix (11 findings, organized around the shared helper).
2. Round-4: re-dispatch carrying this round-3 disposition -- panel now
   kimi/gemini/deepseek (minimax dropped 2026-07-23, no quota; gm manual).
3. Any new finding -> fix -> next round, per the non-convergence protocol.
Then: user final human review before /gsd:execute-phase (forge CP1b exit).

Every external verdict, incl. any "0/0/0/0", is a CLAIM verified against code
before it counts -- round-2 H1 (gm self-certified a false green) is the proof.

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
