# Qwen3.8 forge-review eval -- handoff to trinity-router session

From: forge PM session. Date: 2026-08-16.
Work order: /tmp/draft_forge_qwen38_eval_order_20260816.txt (Kimi PM, frozen
E4 gates live in that order -- the freezing evidence is the order itself).

## What forge delivered: the question bank (E1) -- UPDATED, ready

Path: /home/houminxi/code/forge/.planning/eval-bank/v1/
- manifest.yaml -- 11 entries, each with repo, commit, diff_state: pre-fix,
  pre_fix_source (exact origin of the diff), answer file path, axis.
- diffs/<name>.diff -- 11 PRE-FIX diffs. Critical: every diff is the
  pre-fix state the reviewers actually reviewed (post-fix diffs would make
  the answer keys un-hittable -- the first extraction had this bug and was
  rebuilt). Sources include git fsck unreachable commits, a git stash
  snapshot, and panel-reviewed diff patches.
- answers/<name>.json -- 35 findings total, each with file, line_range,
  description, source record. Line ranges were remapped to the pre-fix
  state and script-verified to intersect the diff's ADDED lines.
- 4 candidate entries were dropped (no recoverable pre-fix state / zero
  confirmed findings in the record) -- never reverse-patched.

Spot verification done by forge PM on hang-idle-timeout (answer ranges
match the diff's added lines byte-for-byte).

## E2: model configurations (from the work order, parameters pinned)

- Qwen3.8: thinking off (reasoning_effort low), ctx 65536 q8,
  max_tokens 16384. Service: gpu-win 192.168.100.11:8082, nssm
  llama-server-qwen38, manual start.
- Bonsai: production defaults. Service: same host :8081.
- Both ends get the EXACT same forge review prompt and flow. Forge side
  must not change default config/routes -- use override params or temp
  config (a temp backend block pointing at the llama-server OpenAI
  endpoint with a dummy api key env).
- Trinary data (5 questions, Qwen3.8 81.8% vs Bonsai 63.6%) is adopted
  directly -- do NOT re-run it.

## E3: per-question per-model row

| 题目 | 模型 | 命中 confirmed 数 | 误报数 | 输出可解析 | 耗时 |

Parseability judge: forge's own validate_reviewer_json.

## E4: frozen decision gates (do NOT adjust after seeing results)

- Hit rate primary: Qwen3.8 >= Bonsai + 10 percentage points -> quality win
- Parseability: either end < 90% -> that end is unusable regardless of hit rate
- False-positive rate: > 20% -> unusable
- All three pass before a recommendation to the main session is allowed

## E5: tally design (deliver a concrete plan, not a slogan)

If recommending adoption: a fixed tally file path (you define it), the
append mechanism (per real review using the local backend: hits/FPs
appended), and the ~30-question re-review trigger.

## Hard constraints (from the work order)

- gpu-win single-GPU serial: bonsai.ps1 stop before Qwen3.8 runs;
  bonsai.ps1 start after; verify RUNNING via ssh directly.
- gpu-win powered off -> report "waiting for power-on"; NEVER WOL
  (the WOL chain belongs to ashare).
- All raw outputs land on disk; the report cites their paths.

## Deliverable back to the fleet

/tmp/draft_forge_qwen38_eval_<date>.txt: E3 table + per-gate E4
verdicts + E5 plan + adopt/no-adopt recommendation.
Memory: forge project memory (per the work order's acceptance section).
