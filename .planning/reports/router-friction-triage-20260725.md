# Router Friction RCA -- verified triage + schedule

Source: /tmp/forge-router-friction-rca.md (sub-session, 2026-07-24, OmniRoute/gemini CP3 attempt).
Verified by: forge PM, 2026-07-25, against main @ 74adbf2. Every file:line was
re-read; verdicts are PM-independent ground truth, NOT the report's self-assessment.

One line: of 5 reported "problems," 1 is a real blocker (F1), 1 a real footgun
(F3), 1 doc-only (F2), 1 partially already-shipped (F4), 1 already fully shipped
and DISPROVED (F5). Net actionable code: F1 + F3 + an F4 live-probe. Net docs:
F2 + F5.

## Verdicts (evidence-backed)

| #  | Report claim | Verdict | Evidence (main @ 74adbf2) |
|----|--------------|---------|---------------------------|
| F1 | SSE body parsed as JSON, blocks SSE-forcing routers | CONFIRMED -- real, reproduced blocker | llm_invoke.py:1014 gates on backend.stream; :1023 else-branch calls _parse_response_body -> json.loads (:974). The error raised at :977 is the exact string the RCA quotes. stream defaults False (backend.py:104). |
| F2 | base_url double /v1 -> HTTP 400 | CONFIRMED -- doc gap, not a bug | llm_invoke.py:990 `url = base_url + "/chat/completions"`, no /v1 normalization. Correct-as-designed; only under-documented in gate.schema.json. |
| F3 | trust is CWD-scoped, no --path | CONFIRMED -- real footgun | trust_parser exposes only --status/--revoke (cli.py:649-657); _run_trust uses Path.cwd() (:1528) -> gate_yaml_path = cwd/.code-forge/gate.yaml (:1112), no $HOME / not-a-project guard. Phase 37 $HOME-walkup-defuse (ADR-0009) does NOT cover this path. |
| F4 | no backend connectivity test; only full review verifies | PARTIALLY DISPROVED | `code-forge doctor` already probes every backend: doctor.py:118 _check_backends -> :151 probe_backend. BUT probe_backend for type=api is env-key-presence only, no network (backend.py:558-562) -- it does NOT exercise the SSE/parse path and would NOT catch F1/F2. Residual = a LIVE probe. Report's "~100 LOC new command" and "only way is a full review" are both wrong. |
| F5 | no user-level backend inheritance (~30 LOC) | DISPROVED -- already ships | _merge_user_into runs on the line after every _load_gate_backends on the CLI review path: cli.py:2302-2303 (review), :1045-1046 (eval), :3320-3321 (resolve-outlet). Shipped in Phase 37.1 ("F5 backend passthrough", 965c247). Author read _load_gate_backends in isolation and did not trace one line further. |

## DO NOT rebuild (this section exists so the next session does not waste the work)

- F5: user-level backend inheritance EXISTS (cli.py._merge_user_into +
  user_config.load_user_backends/merge_backends). The author's real problem was
  DISCOVERABILITY -- they never created ~/.config/code-forge/config.yaml. Correct
  disposition: a one-line doc pointer, surfaced in `doctor`/`setup-mcp`. NOT ~30
  LOC of merge logic.
- F4: do NOT write a from-scratch `backend test` command. `doctor` already owns
  backend probing. The only gap is that _probe_api is offline; the fix is a LIVE
  mode on the existing probe, which is ALSO the acceptance test for F1.

## Corrected priority (replaces the RCA's table)

| Pri | Item | Real scope | Value | Notes |
|-----|------|-----------|-------|-------|
| 1 | F1 SSE auto-detect | ~10-15 LOC in the SHARED parse path + tests | Unblocks all SSE-forcing OpenAI/Anthropic/Vertex routers; unblocks the gemini CP3 backend the fleet wants | MUST land in _parse_response_body (or a shared read wrapper), NOT in _invoke_openai alone. There are 3 non-streaming parse sites -- llm_invoke.py:1023 (openai), :1121 (anthropic), :1295 (vertex). A one-site fix leaves 2 siblings broken. |
| 2 | F4 live backend probe | extend probe_backend/_probe_api with an opt-in live mode (`doctor --live` or `backend test`) | 10x faster debug loop; doubles as the F1 acceptance test | Reuses existing doctor plumbing. Sequence WITH or AFTER F1 so the probe validates the SSE fix. |
| 3 | F3 trust path confirmation | ~10 LOC: print resolved gate.yaml path before confirm + warn if cwd is not a git repo | Prevents wrong-dir trust | Follow ADR-0009's existing $HOME policy; do not invent a new one. |
| 4 | F2 base_url doc | ~5 lines in gate.schema.json base_url description | Prevents /v1 confusion | Doc-only. |
| 5 | F5 discoverability doc | 1-2 lines: document existing user-config inheritance + surface in doctor | Prevents per-project reconfig confusion | Doc-only; feature already ships. |

## Proposed grouping + placement (RECOMMENDATION -- needs user OK)

Genre: an evidence-driven consumer-pain batch, same shape as the shipped
"surflare consumer-pain fixes," "Usability on-ramp batch," and "Windows MCP
support wave 1" -- unplanned, evidence-gated, grouped.

Theme fit: v2.8 is literally "Onboarding + Throughput." Router onboarding
friction fits v2.8's theme, but v2.8 is nearly closed (only Phase 42 left).

Recommendation:
- EXPEDITE F1 as a small standalone fix. It has a live consumer need right now:
  the fleet's CP1b/CP3 panel wants the OmniRoute gemini backend, and F1 blocks it
  entirely. Do not park a current blocker behind Phase 42.
- BATCH F3 + F4-live-probe + F2-doc + F5-doc as a "Router onboarding friction"
  wave, scheduled as a v2.8 onboarding entry after Phase 42.

Phase numbering: DO NOT assign 48/49 (earmarked SCOUT/COMPILATION in v2.9) or 50
(agent driver). Use 54+ if a number is wanted. Recommend recording as a named
batch first (like prior consumer-pain waves), number it at plan time.

## Open decision for the user
1. F1 placement: expedite now (recommended) vs queue after Phase 42.
2. Whether the remaining four ship as one batch or F2/F5 fold into the next doc pass.
