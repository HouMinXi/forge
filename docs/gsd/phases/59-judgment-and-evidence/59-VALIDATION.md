---
phase: 59
slug: judgment-and-evidence
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-10-04
---

> Published planning snapshot, 2026-10-18. Source statements below retain their
> original dates and acceptance limits; historical "current" claims are not current-main
> verification. Private identity labels and unavailable evidence are explained in the
> [cloud start index](../../README.md). Publication grants no execution admission.

# Phase 59 Validation Strategy

This is a prospective validation contract derived from 59-RESEARCH.md and the existing acceptance recovery. It records no new implementation, review, isolation qualification or phase acceptance pass. The planner must replace the provisional requirement rows with actual plan/task IDs and waves, preserving every required control.

## Test Infrastructure

| Property | Value |
| --- | --- |
| Framework | Existing pytest; research observed 9.1.0 on PRIVATE_HOST_A |
| Configuration | pyproject.toml and existing repository test guards |
| Existing quick anchor | `PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/test_llm_invoke.py -k 'complete_invalid_json_gets_one_correction or persistent_bad_json_exhausts_attempts_with_kind or stop_unterminated_string_continues_instead_of_failing'` |
| Full suite | `PYTHONPATH=src /usr/bin/python3 -m pytest` |
| Runtime | Not measured by this planning step; quick subsets target <=30 seconds and worker integration has explicit bounded timeouts |

Execute from the admitted isolated implementation checkout with its bound source and graph. Record actual passed/failed/skipped counts and exit status; exit 0 alone does not prove tests ran. Serialize shared Git mutations against the repository-state guard. Preserve raw failed runs; a rerun never overwrites earlier evidence.

## Sampling Rate

- Before implementation, freeze independent expected outputs and exact-site defect cuts.
- After each meaningful implementation task, run its focused automated verification; include defect injection, restored green control and executed changed-statement coverage where logic changes.
- After each wave, run the combined affected suites and applicable real-path controls.
- Before delivery, complete three independent clean whole-diff reviews and parent smoke, then one signed commit/MR. New code changes reset clean review credit.
- Before phase verification, run the full suite and all admitted integration controls. Existing baseline failures stay visible and prevent an unqualified all-green or phase-complete claim.
- Every task must have automated verification or an explicit dependency that creates it. No three consecutive tasks may lack executable feedback.

## Provisional Verification Map

All proposed test files below are missing Wave0work unless marked existing or a preserved delivered slice. For the amended03candidate, Task03.1 explicitly creates tests/test_invocation_audit.py and freezes golden outputs; Task03.2 implements all sections1-6 behavior (including identity/summary/nullable accounting) and executes its finite invariant controls; Task03.1 retains pointed behavioral RED. Other provisional rows remain unqualified. The typed planner owns exact task IDs, waves, threat references and final commands.

| Requirement | Required observation | Automated verification target | State |
| --- | --- | --- | --- |
| P59-CLOSE | Recover or explicitly reconstruct historical source/config/label binding, retain A4-0b 12/20 conflict and A4-0c 13/20 below original18/20; B3 canary/cap/context and B4 matched IDs/raw references | Existing acceptance-recovery scripts plus a reviewed closeout validator | Acceptance remains open |
| INV-01 | Frozen base/post/ranges, deleted/untracked/UTF8 cases; race/symlink/escape/stale refusal and no outside canary | `tests/test_investigation_snapshot.py` | Missing |
| INV-02 | Exact invocation line and qualified relationship; missing/stale/incomplete graph differs from empty | `tests/test_investigation_calls.py` | Missing |
| INV-03 | Actually enabled fixed replay through public entry; useful known-answer contrast; env/fd/file/network/child/exec denial; timeout/output/memory/cancel and observed descendant cleanup | `tests/test_investigation_recipe.py` unit and bounded integration subsets | Missing; host qualification open |
| INV-04 | Real owned HTTP fixture exercises complete tool calls, correlation, final validation, ordinary/grouped paths; prose/partial/unsupported denied | `tests/test_investigation_protocol.py`, `tests/test_investigation_public_path.py` | Missing |
| INV-05 | Atomic N/N+1 contenders; one run budget across passes/groups/retries/tools; deadlines, bytes, worker count and cleanup cannot renew silently | `tests/test_investigation_budget.py` | Missing |
| INV-06 | Exact final rejected raw plus every failed/recovered attempt, trusted identities, terminal siblings, bounded artifacts and truthful sink failure | `tests/test_raw_l1_rejections.py`, `tests/test_invocation_audit.py`, existing `tests/test_machine_receipt_gate.py` | Final raw273 test delivered; complete audit missing, created by03.1; receipt anchor exists |
| INV-07 | Distinct causes and owned sends; unknown versus known0; cumulative2/5/9/9 yields9; cached/reasoning subsets and concurrent wall time | `tests/test_invocation_audit.py`, existing `tests/test_llm_invoke_usage_contract.py` | Audit missing, created by03.1, implemented/qualified by03.2; usage anchor exists |
| INV-08 | Source/tool/provider content cannot expand authority; denied/incomplete/persistence/cleanup failures cannot produce accepted clean evidence | `tests/test_investigation_public_path.py`, existing `tests/test_machine_receipt_gate.py` | Public test missing; receipt anchor exists |

## Wave 0 Requirements

- `[ ]` Bind current base, isolated worktree, private source graph and immutable plan inputs before product changes.
- `[ ]` Create only the fixtures required by the admitted slice: scripted local HTTP transport, frozen canary tree, tiny schema13 graph, concurrent budget clock, writer faults and owned worker lifecycle fixtures.
- `[ ]` Map every proposed verification command to a real file or a prior explicit fixture-creation task; do not add empty passing stubs.
- `[ ]` Freeze exact source-site omissions for dispatch, capture, identity, reservation, publication and cleanup guards separately.
- `[ ]` Capture preservation guards for config/index/refs/foreign changes and avoid shared Git mutations during pytest.
- `[ ]` Retain the independent distinction between early final-raw delivery and complete attempt journal/accounting.

## Operational Qualification

Endpoint/tool conformance and the selected Linux isolation policy remain unmeasured assumptions. Local deterministic HTTP and worker tests must precede any bounded live conformance check. Availability of a binary or library does not qualify a policy. No manual approval ceremony substitutes for tests; the user's implementation and reviewed delivery authorization already applies.

Historical acceptance reconstruction requires source-bound evidence review. A different acceptance threshold must be explicit and independently reviewed; do not silently replace18/20 or count artifact recovery as acceptance success. Phase60 owns later corpus/comparative evaluation.

## Validation Sign-Off

- `[ ]` Final plan/task IDs and waves replace provisional rows.
- `[ ]` All tasks have automated checks or prior fixture dependencies.
- `[ ]` Sampling continuity and bounded subprocess supervision are checked.
- `[ ]` All missing test references are covered by explicit work.
- `[ ]` Typed plan checker, independent perspective review and parent admission pass.
- `[ ]` `nyquist_compliant` is set only after the complete plan mapping is checked.

Approval: pending engineering review. Implementation outcomes remain pending independently of plan approval.


## Exact amended03prospective validation map

The entire03test producer remains missing until admitted Task03.1 creates it. Task03.1 and03.2 each have the explicit focused pytest command in the plan; there is no sampling gap between them. Schema/locking/platform gates are defined in59-03-CONTRACT.md sections1-7 and source-grounded by59-03-SOURCE-ADDENDUM.md. No runtime outcomes or completed phase-wide Nyquist mapping are claimed.

| Task / requirement | Frozen assertion boundary | Proposed real check | Current status |
| --- | --- | --- | --- |
|03.1 INV06/07/08 | typed denied start0dispatches, immutable IDs, event replay, known0/null, cumulative9/incremental9, subsets, failed-known plus unknown and work6/wall4 | create tests/test_invocation_audit.py with independent goldens then its named pytest selector | Unimplemented, meaningful RED must be retained |
|03.2 INV06/08 | raw file-sync/rename/directory-sync/acquired-event commit; original failure+sink and success+sink; torn/orphan replay | actual owned tmp files and exact individual commit cuts/restored GREEN | Unimplemented |
|03.2 INV06/07/08 | full16MiB envelopes include encoded metadata/marker partition/temp/orphans; root512MiB last slot admits1of2; tombstone+32envelopes denied; terminal-freeze delta admission before writes at post-delta N-1/N/N+1, quota-denied prior snapshot/reopen/charge unchanged, competing freezes/new-run slot and admitted-midfreeze full charge; lock timeout/fork description/freeze/prune | existing bounded owned-process controller and actual byte boundaries on qualified Linux root | Unqualified; no generic controller or PID-only reclaim |
|03.2 INV06/08 | structural auth/env/endpoint exclusion, exact sensitive raw, default-off preview and explicit unsupported refusal and automatic first-run readiness | metadata/raw canary and automatic first-run/mount/profile/fault controls plus engineering profile qualification | Unimplemented; later05legacy_compatibility_gate still required |

Later04owner and05physical send tests must cut each actual consuming open/start/acquired/final path and prove no-dispatch/error folding;03helper tests cannot supply their wiring credit. Every implementation site change requires pointed RED/restored GREEN, executed changed-logic coverage, independent reviews and parent smoke. Retain old raw273 failures and current graph missing-edge limitations; fresh file hashes alone do not establish exhaustive relationships for later59-10. nyquist_compliant/wave_0_complete remainfalse until full-phase checks are actually completed.

Durable attribution controls bind every admitted start's complete immutable context and disk-reopened attempt_contexts across distinct rounds/passes/groups/purposes, same-logical retries and causal/logical parent links; context omission and cross-group retry rebinding each require pointed RED/restored GREEN. These are prospective Task03.1/03.2 controls, not executed product evidence.
