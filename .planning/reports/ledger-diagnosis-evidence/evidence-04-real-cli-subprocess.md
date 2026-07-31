# Evidence 04 -- real `code-forge` CLI console script, real subprocess, no --mode flag

## What this tests

The most direct possible test of H1: run the actual installed
`code-forge` console script (not an in-process import, a real
subprocess via `timeout ... code-forge review ...`) in a scratch git
repo outside the forge project, with stdout redirected to a file
(guaranteed non-TTY, exactly like any agent/script/MCP-fallback
invocation), passing NO `--mode` flag and NO `FORGE_MODE` env var, and
inspect the resulting `.code-forge/state.json` for the resolved mode
and `.code-forge/` for a ledger file.

Scratch repo: `/tmp/.../scratchpad/ledger-diag/exp3b/` (outside the
forge repo; a fresh git init with one commit + one uncommitted change to
`a.py`, plus a hand-written `.code-forge/gate.yaml` using the
`smoke_lock_busy.sh` pattern from
`.planning/reviews/lock-busy-message/smoke_lock_busy.sh`).

## Attempt 1 -- blocked on an orthogonal trust gate (reported honestly, not glossed over)

```
$ FORGE_ALLOW_MAIN=1 FORGE_MOCK_KEY=x timeout 60 code-forge review \
    --no-color --backend mock-local a.py
exit code: 2
--- stderr ---
Untrusted repo backends ignored. Run 'code-forge trust' to enable.
code-forge: error: unknown backend 'mock-local' (configured: gemini-omniroute, deepseek)
```
This is a real, separate SEC-02 trust gate (repo-local gate.yaml
backends are ignored until trusted) -- not a fabricated result, and not
related to the mode question. Resolved by running the documented
command it names:
```
$ code-forge trust
Sensitive fields (review before trusting):
  mock-local.base_url = http://127.0.0.1:1
  mock-local.api_key_env = FORGE_MOCK_KEY
Trusted: /tmp/.../exp3b/.code-forge/gate.yaml
exit: 0
```
(Trust was revoked again after the experiment: `code-forge trust
--revoke` -- see tail of this file. The only side effect was one entry
in the user-level `~/.config/code-forge/trusted.json`, never anything
inside the forge repo.)

## Attempt 2 -- real run against the now-trusted backend

```
$ FORGE_ALLOW_MAIN=1 FORGE_MOCK_KEY=x timeout 60 code-forge review \
    --no-color --backend mock-local a.py > stdout.log 2> stderr.log
exit code: 124   (killed by the 60s `timeout` wrapper -- see below)
```

`stderr.log` tail (full file: `exp3_run2_stderr.log` in this directory):
```
code_forge.llm_invoke.LLMInvokeError: URLError from mock-local backend: [Errno 111] Connection refused
...
  File ".../machine.py", line 263, in run
    self._run_advisory_axes()
  File ".../machine.py", line 1299, in _run_advisory_axes
    findings = runner.run(diff_text, self.cwd)
  File ".../llm_invoke.py", line 969, in _invoke_api
    time.sleep(delay)
...
KeyboardInterrupt
```
Reading the traceback: the process reached `_run_advisory_axes()`,
which per `machine.py:248-268` runs strictly AFTER `self.run()`'s
dispatch to `_run_local`/`_run_ci` has already returned a verdict and
persisted state at least once. The `timeout` wrapper killed the process
later, during an unrelated advisory-axis retry/backoff loop against the
deliberately-unreachable mock backend (127.0.0.1:1, connection
refused) -- not during the primary review round. So the primary run/
persist cycle we care about had already completed and been written to
disk before the kill.

## The decisive artifact: state.json from this real run

`exp3_run2_state.json` in this directory (copied verbatim from the
scratch repo, un-edited):
```
$ python3 -c "
import json
d = json.load(open('.code-forge/state.json'))
print('mode:', d.get('mode'))
print('verdict:', d.get('verdict'))
print('converged:', d.get('converged'))
print('num findings:', len(d.get('findings', [])))
"
mode: CI
verdict: FAIL
converged: False
num findings: 3
  finding: l1-qodo-invoke-fail INFRA CONFIRMED
  finding: l1-expert-invoke-fail INFRA CONFIRMED
  finding: l1-adversarial-invoke-fail INFRA CONFIRMED
```
```
$ ls -la .code-forge/ | grep -i ledger
(no output)
$ ls -la .code-forge/
gate.yaml  receipts/  state.json  tools.yaml
```

## Reading

This is the real, installed `code-forge` console script -- not a
reimplementation, not an in-process shortcut -- run exactly the way an
agent or script would run it (no --mode flag, stdout redirected). It
resolved to `mode: CI` and produced zero ledger files, independently
corroborating evidence-02 and evidence-03 through the actual product
entry point. The FAIL verdict and INFRA findings are an artifact of the
mock backend being deliberately unreachable (127.0.0.1:1) -- they say
nothing about the ledger question themselves, but do not interfere with
it: `_write_ledger_rows` was still never reached, because CI mode never
calls it regardless of verdict (see evidence-01, G3).

## Cleanup

```
$ code-forge trust --revoke
Trust revoked for /tmp/.../exp3b/.code-forge/gate.yaml
Trusted: False
```
