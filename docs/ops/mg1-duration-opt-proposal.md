# MG-1 duration optimization — ops note

**Status:** actionable ops proposal (landed from CoS-authorized draft).  
**Audience:** agents / humans running Forge #332 MG-1 (or similar) paired M/T full suites.  
**Non-goals:** do not weaken product semantics, classifier contracts, Entry include/exclude rules, or the T−M=0 READY gate under the guise of speed.

Source draft (box evidence, pre-land):  
`332-spec-r31/phase4-evidence/mg1/MG1-DURATION-OPT-PROPOSAL.md`  
sha256 `285622e16d00d055a76e3ee8c5bc250325a3c7e9a526dc93efb1fa1a078ab4d8`.

## Goals

1. Cut end-to-end MG-1 **wall** time.
2. Reduce flake from inode/ENOSPC and shared-tmpdir races.
3. Keep mutation loops cheap after allowlist / golden-alignment commits.

Measured suite timings below are labeled **「未经独立复现」** (Implementer-recorded; not independently reproduced).

## Checklist before a full suite

1. Record a unique `run-id` and set:
   - `TMPDIR=/workspace/332-p4-mg1-scratch/<run-id>`
   - pytest `--basetemp=$TMPDIR/bt`
   - `PYTHONDONTWRITEBYTECODE=1` and a shared `PYTHONPYCACHEPREFIX` outside the run-id tree
   - `SIGHUP=SIG_DFL` in the runner process; avoid mid-run scratch wipes
   - pytest `-p no:cacheprovider`
2. Preflight: `df -i` on the scratch filesystem. If free inodes are below the run script’s documented floor, **fail fast** (do not start the suite).
3. Prune only **completed** scratch trees older than the active run ids recorded in control files (e.g. `t-active-tmpdir.txt` / `m-done.txt`). Never delete the active run-id tree.
4. Pin the exact git commit under test; write `suite-{M,T}/full-summary.txt` with `commit=`, `authoritative=`, and counts before any INDEX rewrite.

## Proposals

### 1. pytest-xdist boundaries

- Prefer **process-level** isolation (`--dist=loadscope` or `loadfile`) over finest-grained balancing when tests share cwd/state fixtures.
- Cap workers by available CPUs **and** a measured inode/tmp budget (start at about `n/2` when basetemp trees dominate).
- Keep Phase 4 IC-T14/T15 and any RCA focus set **outside** the long xdist full suite while iterating wiring — serial or low-N focused runs first.

### 2. Fixed basetemp / anti-cleanup

- Always pass `--basetemp=$TMPDIR/bt` with a **unique** `TMPDIR` under `/workspace/332-p4-mg1-scratch/<run-id>/`.
- Do **not** wipe shared scratch mid-run; remove a run-id tree only after that run’s summary and junit are copied into `suite-{M,T}/`.
- Disable aggressive pytest temp-cleanup plugins if present; rely on post-run `rm -rf` of the finished run-id root only.

### 3. Inode pre-clean

- Before each full suite: `df -i` + count of scratch trees; prune completed `rca-*` / `suite-*-tmp-*` older than active run ids.
- Fail fast below the inode floor rather than dying mid-suite with ENOSPC.

### 4. Mutation / retest gate

After allowlist or golden-alignment commits, **do not** default to another multi-hour full M+T.

Default gate:

1. Prior T−M delta nodeid set (if any).
2. `tests/test_phase4_ic_t14_routes.py`
3. `tests/test_phase4_ic_t15_lifecycle.py`
4. Any newly touched non-allowlist tests CoS authorized.

Full suite only when CoS requests reconfirm for INDEX `READY`, or when the delta set is empty but a cross-cutting emitter changed.

### 5. Parallelism with evidence writes

- Write `failed-*.txt` / `EVIDENCE-INDEX` only from **frozen** junit/nodeid artifacts, never from a still-running suite’s partial log.
- Keep RCA traceback logs under the evidence tree with the run-id `TMPDIR` noted in the log header.

## Reference timings（「未经独立复现」）

Recorded during #332 Phase 4 MG-1 (box; not an independent repro):

| Suite | Commit pin | passed | failed | errors | wall_sec (approx) |
| --- | --- | ---: | ---: | ---: | ---: |
| M | `fe4c107…` | 27755 | 208 | 0 | ~2637 |
| T (prior) | `f0951adb…` | 27752 | 241 | 0 | ~17944 |
| T (golden-align) | `664f59c…` | 27808 | 209 | 0 | ~34815 |

Focused single-case probes (e.g. killpg) completed in ~8–9s when not under full-suite load — treat long single-case walls inside a full suite as load/timing risk, not as a green light to weaken T−M.

## Non-goals (hard)

- No product semantic weaken (including stripping additive `report_classification`).
- No T−M gate weaken (READY still requires authoritative full-suite T−M=0 unless CoS + user waiver says otherwise).
- No classifier / Entry include-exclude contract changes for “speed.”
- This file is ops documentation only — landing it does **not** authorize merge/enable/Exit of Phase 4 wiring, INDEX→READY, or tip re-runs.
