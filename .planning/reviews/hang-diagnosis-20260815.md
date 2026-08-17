# Review hang diagnosis + fix, 2026-08-15

The check6 R1 review (deepseek-direct) hung for 24+ minutes after its
three passes completed. Root cause chain, fully evidenced:

## What was observed

- Three pass log lines printed (`[deepseek-direct:qodo|expert|adversarial]`),
  then nothing for 10+ minutes (log mtime frozen).
- Process sleeping: main thread futex_do_wait, second thread
  poll_schedule_timeout, 0% CPU.
- One ESTAB socket to 43.242.198.77:443 (api.deepseek.com EdgeOne node,
  Nanjing), zero data flowing. TCP alive, application layer dead.
- Killed with SIGABRT after 24 min (no faulthandler registered).

## Root cause chain

1. The three passes complete; L1 then runs `falsifier.falsify()` per
   finding, which issues another LLM call on the same backend.
2. DeepSeek accepted the connection and never sent bytes (server-side
   hang on a streaming/non-streaming response).
3. `_read_with_deadline` (llm_invoke.py) reads the body in a daemon
   thread and joins with `timeout=remaining`. `remaining` comes from
   the backend's `timeout_s` -- 1200s in the user config, 2400s in the
   repo gate.yaml. A hung connection therefore parks the round for
   20-40 minutes before the deadline fires.
4. The socket timeout urlopen() installed is the same 1200-2400s: it is
   sized for a legitimate slow pass (411.7s measured, a non-streaming
   backend generates the whole answer before the first body byte), so
   a hung connection and a slow pass look identical until it is far too
   late.

## Why other dialogs are fast

hermes/opencode/claude-code dialogs calling /code-forge without an
explicit --backend ride the `deepseek` backend (default: true): the
OmniRoute gateway on the LAN (192.168.100.10:20128) + sn-deepseek-flash.
The direct route was chosen here deliberately to escape the gateway
semantic cache, which replays same-prompt rounds as byte-identical
responses (A4 experiment). That bought correctness at the price of a
993s/cycle baseline and today's hang.

## Fixes applied

1. User config (~/.config/code-forge/config.yaml) gains
   `deepseek-nocache`: same LAN gateway and free model as `deepseek`,
   plus `X-OmniRoute-No-Cache: true` (the documented OmniRoute bypass)
   and `reasoning_effort: low` (the truncation guard the user-config
   `deepseek` block was missing -- see note below). Measured on check6:
   3 passes in 53.3s, 182.8s wall for the whole run, versus the direct
   route's 993s/cycle baseline.
2. Branch fix/hang-idle-timeout (d24a4a5): `_read_with_deadline`
   tightens the raw socket to a 900s idle bound before the read starts.
   A silent connection now raises socket.timeout, converted to the
   existing timeout error, in 15 minutes instead of stalling the full
   20-40 minute budget. 900 sits above the 411.7s measured slow pass.
   Bug-injection verified both halves (settimeout install and the
   TimeoutError conversion); tests/test_llm_invoke.py 256 pass.

## Note: untrusted repo backends in worktrees

Running review from a worktree prints "Untrusted repo backends
ignored": the worktree's .code-forge/gate.yaml backends are NOT used;
the user config supplies them. The user-config `deepseek` block lacks
the `reasoning_effort: low` the repo gate.yaml carries, so a review run
from an untrusted worktree on that backend risks the ~16384-output
truncation the repo config guards against. deepseek-nocache carries the
guard itself, so it is safe from worktrees. If the repo backends ever
get trusted for a worktree (code-forge trust), re-check this split.

## Open

- The hang happened inside the falsify stage; forge has no per-call
  progress line there, so a silent hang is indistinguishable from a
  slow falsify until the idle bound fires. A per-finding falsify log
  line would close that observability gap (backlog candidate).
- `_read_sse` (streaming path) has the same silent-hang shape and no
  idle bound; all current backends run stream: false, so it is not
  urgent, but a streaming backend would inherit the defect.

## MCP end-to-end verification, 2026-08-16 (appendix)

Full protocol chain proven against a real MCP server instance:

- initialize + tools/list: OK, forge_review and forge_job_status
  present, zero server errors (stdio transport is newline-delimited
  JSON, not Content-Length framed).
- forge_review (backend=deepseek-nocache, uncommitted diff present):
  returned {"job_id": "26a72c7d-...", "status": "running",
  "poll_after_seconds": 10}.
- forge_job_status polling: 6 consecutive polls all surfaced
  "[forge]" progress events from the job's stderr tempfile.
- Realpath layer: the stderr NamedTemporaryFile the MCP server
  creates carries the full event chain; _read_stderr_tail (the exact
  function job_status uses) reads it (RESULT: True, multiple polls).

Divergence found and filed (todos/pending/
mcp-cli-gate-lookup-divergence-20260816.md): CLI walks up from cwd
for gate.yaml, MCP with FORGE_PROJECT_DIR checks the exact root only,
so a fresh worktree review works via CLI and fails via MCP.

Note: e2e4's review run was a LOCAL-mode job on the worktree; the
branch was subsequently rebased by the user onto the new main, so
verdict numbers in the job logs refer to the pre-rebase head.

## Wrapper re-verification, 2026-08-16 (after MCP reconnect)

The full chain re-run from the main session after the MCP reconnect,
this time through the real launch wrapper `code-forge-mcp-pass` (the
exact command the MCP config registers), main merged at f43323a:

- First attempt from the main tree failed with "code-forge review must
  run inside a linked git worktree" -- the cli.py main-tree guard, so
  the guard is proven live on the MCP path too.
- Re-run from a fresh linked worktree (smoke/mcp-e2e at f43323a,
  uncommitted marker diff, gate.yaml copied per the divergence note
  above): forge_review returned job_id 48c97628 running; forge_job_status
  surfaced "[forge]" events on all 4 polls, verdict arrived by poll 3
  (~120s wall, deepseek-nocache backend). WRAPPER E2E RESULT: True.
- The stale `code-forge-mcp` process (pid 3480989) seen during the run
  belongs to a different claude session's MCP connection, not this one.
