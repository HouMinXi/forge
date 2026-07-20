# Forge MCP on Windows (win-gpu) -- Deployment Test Plan

Date: 2026-07-11. Target: win-gpu (Windows 11, RTX 3080 20GB).
Forge version under test: 2.7.0 @ main f53bf84.
Author basis: full-source platform sweep + code-review-graph build
(277 files, 5099 nodes, languages python+bash). Everything below
marked PREDICTED is static analysis -- the point of this plan is
to replace predictions with real output.

## Gray areas found (test these, do not assume)

G1 LOCK PROBE KILLS ON WINDOWS (HIGH, concurrency blocker).
    lock.py:164 uses os.kill(pid, 0) as a liveness check. On
    Windows, any sig outside CTRL_C_EVENT/CTRL_BREAK_EVENT is
    delivered via TerminateProcess -- so the "probe" TERMINATES a
    live lock holder, then reports Busy. Predicted consequence:
    two concurrent forge runs on one workspace = second run kills
    the first. Until fixed upstream, RUN ONE REVIEW AT A TIME.
    T5a below confirms the behavior safely.

G2 ENCODING-LESS TEXT IO IN GRAPH TRIAGE (MEDIUM).
    graph_triage.py:186/190 write/read a diff via text mode
    without encoding=. Python 3.12-3.14 on Windows defaults to
    the ANSI code page (cp1252): any non-Latin-1 byte in a diff
    raises UnicodeEncodeError. Path only runs when the `sem`
    binary exists; absent sem = graceful skip. T5c probes it.
    Workaround if hit: set PYTHONUTF8=1 machine-wide.

G3 start_new_session ON WINDOWS (MEDIUM, cli-outlet only).
    llm_invoke.py:708 passes start_new_session=True (POSIX
    setsid). On Windows this parameter is POSIX-only; predicted
    ValueError crash instead of a clean config error. The cli
    outlet is already N/A on Windows (aicc is bash) -- the test
    checks the FAILURE MODE is readable, not that it works. T5d.

Non-issues verified in source: no fcntl/pty/termios/pwd/fork
imports; subprocess is list-args only (never shell=True); JSON
state files are ASCII-safe (ensure_ascii); packaged .sh files are
agent-facing skill docs, never executed by forge; chmod/0o755
calls are no-ops on Windows; lock acquire uses O_CREAT|O_EXCL
(portable).

## Prerequisites on win-gpu

P1 Python >= 3.12 on PATH (pyproject requires-python). Record
   `python --version`.
P2 Clone forge and install:
       git clone <forge repo> C:\src\forge
       cd C:\src\forge
       pip install -e .[mcp]
   pip generates code-forge.exe and code-forge-mcp.exe in the
   Scripts dir; confirm both with `where code-forge-mcp`.
P3 API key WITHOUT pass(1): set a user-level env var, never write
   the key into any config file:
       setx DEEPSEEK_API_KEY "<key>"
   (new terminal after setx). CN backends ride the box's existing
   Clash proxy; deepseek-direct needs no proxy from the US.
P4 A throwaway git repo as the review target, e.g.
   C:\src\hello-test with one Python file committed.
P5 Optional tools for the tool audit: `pip install ruff`.

## Tests

Record REAL terminal output for every step; a narrated "worked"
does not count. Any FAIL -> capture full traceback.

T1 Entry points.
   code-forge --version           -> expect 2.7.0
   code-forge-mcp responds to a raw initialize frame:
       echo {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}} | code-forge-mcp
   -> expect a serverInfo JSON reply on stdout (then Ctrl+C).
   PASS = both commands produce expected output.

T2 Doctor + tool audit (validates the environment itself).
   In C:\src\hello-test create .code-forge\gate.yaml:
       backends:
         deepseek-direct:
           type: api
           model: deepseek-chat
           format: openai
           base_url: https://api.deepseek.com/v1
           api_key_env: DEEPSEEK_API_KEY
       outlet: subprocess
   and .code-forge\tools.yaml:
       tools:
         ruff:
           command: ruff check
           output_format: flake8
           file_patterns: ["*.py"]
         ghost:
           command: nonexistent-binary-xyz
           output_format: grep_line
           file_patterns: ["*.py"]
   Run: code-forge doctor
   PASS = tool-audit rows show ruff PASS with a version string,
   ghost FAIL not_installed, exit code 1 (echo %ERRORLEVEL%).
   Then delete the ghost entry, rerun: expect exit 0.

T3 MCP registration in Claude Code on Windows.
   In the test repo, .mcp.json:
       {"mcpServers": {"forge": {"type": "stdio",
         "command": "code-forge-mcp", "args": []}}}
   Key comes from the user-level env (P3), NOT from this file.
   Start Claude Code in the repo; /mcp -> forge connected.
   Call forge_resolve_outlet -> expect subprocess outlet with the
   deepseek-direct backend listed.

T4 Real review end-to-end (the actual smoke).
   Stage a small change in the test repo (add a function with an
   obvious flaw, e.g. bare except). Call forge_review via MCP.
   PASS = findings JSON returns, no traceback in MCP logs, and
   the run completes in sane time. This exercises resolver,
   registry, runner subprocesses, HTTP backend, and report IO on
   Windows in one pass.

T5 Gray-area probes.
   a. G1 lock kill (SAFE reproduction -- uses a sleeper, not real
      work). In the test repo:
        python -c "import time,os; print(os.getpid()); time.sleep(300)"
      Note the PID. Write that PID (as plain text) into the
      workspace lock file .code-forge\forge.lock. Then run
      code-forge review on the same workspace.
      RECORD: does the sleeper process die? (check its window /
      Task Manager). PREDICTED on Windows: sleeper is terminated
      AND forge reports lock busy. On Linux the sleeper survives.
      Either way capture output; this becomes the fix ticket's
      reproduction evidence.
   b. G2 non-ASCII review: add a file containing CJK + emoji in
      code comments, stage, forge_review again.
      PASS = review completes and the report renders the content
      (or escapes it) without UnicodeEncodeError.
   c. G2 graph triage: only if `sem` is installed on the box
      (it will not be, by default) -> confirm forge skips the
      triage path cleanly with sem absent (no crash).
   d. G3 cli outlet failure mode: temporarily set
      outlet: cli in gate.yaml with any cli backend stanza, run a
      review, RECORD the exact error text (predicted: ValueError
      about start_new_session, i.e. a crash rather than a clean
      "cli outlet unsupported on this platform" message). Restore
      outlet: subprocess afterwards.

T6 Relative-path tool on Windows (known limitation family).
   Add to tools.yaml a relative command using FORWARD slashes:
       relcheck:
         command: scripts/check.cmd
         output_format: grep_line
         file_patterns: ["*.py"]
   with scripts\check.cmd containing `@echo 1.0.0`.
   Run code-forge doctor. RECORD the tool-audit row. PREDICTED:
   not_installed -- _resolve_command detects relative paths via
   os.sep ("\" on Windows), so "scripts/check.cmd" is not
   recognized as a path. Retry with command: scripts\check.cmd
   and record whether it flips to PASS. Both outcomes are data
   for the portability ticket, not test failures.

## Acceptance summary

MUST PASS for "forge MCP usable on Windows": T1, T2, T3, T4, T5b.
DOCUMENT-ONLY (expected quirks, capture evidence): T5a, T5c, T5d,
T6.
HARD RULE until G1 is fixed: never run two forge processes
against the same workspace on Windows.

## Report back

One file per run: forge_win_test_<date>.txt containing python
version, pip freeze | findstr code-forge, and the raw output of
every T-step with its ERRORLEVEL. The G1/T5a evidence feeds the
lock fix; the T6 evidence feeds the resolver ticket. macOS needs
no separate plan: same steps minus T5a/T6 quirks (POSIX
semantics), plus `pass`-based key injection works there.
