# MCP vs CLI prompt-caching experiment, 2026-08-20

Backend pinned: mimo-pro (mimo-v2.5-pro, anthropic format,
api.xiaomimimo.com). Same diff both runs: HEAD~1..HEAD on forge main
(4087b05e, 2 files: cli.py + test_post_image_window.py), --committed,
--allow-main.

## CLI path (code-forge review --committed --backend mimo-pro --allow-main)

- Wrapper wall clock: 1146.8s (includes shell startup/date overhead)
- Forge-internal cost line: "85426 tokens (50922 in + 34504 out), 3
  passes, 336.7s (wall: 920.7s)"
- Per-pass tokens (round 0): qodo 16974 in / 8605 out; adversarial
  16973 in / 9061 out; expert 16975 in / 16838 out
- Verdict: FAIL findings=4 confirmed=1 uncertain=2 dismissed=1
- Full log: cli_run1_mimo_full.log

## MCP path (forge_review tool, backend=mimo-pro, committed=true, allow_main=true)

- job_id b5a5a7ec-d40a-4d8f-9165-c6cfa9c93d22
- Hit the MCP job's own 900s cap: verdict=TIMEOUT, exit_code=130,
  duration_s=900.10 (never reached a verdict on its own)
- Per-pass tokens (round 1, note: NOT round 0): expert 15 in / 13929
  out; qodo 14 in / 15148 out; adversarial 13 in / 32259 out
- Raw result: mcp_run1_TIMEOUT_result.json

## Headline finding (falsifies the charter's stated hypothesis)

The charter assumed the MCP path is slow because it re-sends the SAME
size prompt every round without cache_control, paying full prefill
each time. That is not what happened here.

The MCP-path input token counts (13-15) are not "the same prompt,
uncached" -- they are two to three orders of magnitude SMALLER than
the CLI-path input token counts (16973-16975) on the identical diff.
Whatever prompt mimo-pro received via the MCP-dispatched subprocess
carried almost no diff/context content at all, while output tokens
were 1.6-3.7x LARGER than the CLI path's per-pass output (32259 vs
9061 max). That pattern (near-empty input, oversized output, no
convergence inside 900s) reads like a near-blank prompt causing the
model to ramble without real content to review, not like a caching
gap.

Also note: MCP round was labeled "round 1" and explicitly logged
"ignoring prior state.json in CI mode (STATE-09)" -- a prior
state.json existed (written by the CLI run moments earlier on the
same unchanged diff) and CI mode says it starts fresh, yet the round
counter still opened at 1 instead of 0. Whether that round-numbering
delta and the near-empty prompt are the same root cause or two
separate issues is NOT YET DETERMINED -- next step is to trace what
_run_cli_budgeted actually put in the subprocess's stdin/argv vs what
the interactive CLI invocation built, and to inspect
.code-forge/state.json content/mtime around both runs.

This directly falsifies hypothesis (a) as stated in the charter
("MCP path constructs prompts differently... defeating prefix
caching") in its literal form -- it's not merely defeating a cache
prefix, the prompt content itself looks broken/truncated on the MCP
side. Hypothesis (b) (different backend/config) is NOT what happened
either -- both runs hit mimo-pro per the log's own model/backend
field. This is a THIRD possibility the charter did not enumerate:
prompt construction produces near-empty input specifically on the
MCP->subprocess dispatch path, independent of caching or backend
routing.
