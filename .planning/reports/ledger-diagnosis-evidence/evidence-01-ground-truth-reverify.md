# Evidence 01 -- ground truth re-verification (G1-G6)

All commands run against the real forge repo at HEAD 695f739 (read-only:
`find`, `grep`, `Read`). No files modified.

## G1 -- no ledger file anywhere under $HOME

Command:
```
find ~ -name "ledger*.jsonl" -not -path "*/node_modules/*"
```
Output: empty (confirmed, matches the dispatch order's claim).

## G2 -- ledger path is per-repo

`src/code_forge/ledger.py:58-59`:
```python
def _ledger_path(cwd: Path) -> Path:
    return cwd / ".code-forge" / "ledger.jsonl"
```
Confirmed by direct read of the file.

## G3 -- exactly one automatic writer, four call sites, all inside one function

Command:
```
grep -n "_finalize_local_terminal\|_finalize_ci\|class Mode\|Mode\.\|self\.mode\b" src/code_forge/machine.py
```
Relevant output:
```
614:                self._finalize_local_terminal()
1069:    def _finalize_local_terminal(self) -> None:
```
`_write_ledger_rows` call sites (grep for the literal string across the
whole 1531-line file): exactly four, at machine.py:1103, 1123, 1146, 1160
-- all inside `_finalize_local_terminal` (def at 1069). Confirmed by
direct read of machine.py:1069-1222 and by a second, independent
exhaustive grep pass. `_finalize_local_terminal` itself has exactly ONE
call site in the entire file: machine.py:614, inside `_run_local`.

Read in full: `_run_ci` (machine.py:289-535) never calls
`_finalize_local_terminal` or `_write_ledger_rows` under any branch --
verified by reading the complete function body, not just grepping (see
evidence-02 for the direct experimental confirmation of the consequence).

## G4 -- silent zero return on None SHAs

`src/code_forge/machine.py:1179-1182`:
```python
base = self.resolved_review.base_sha
head = self.resolved_review.head_sha
if base is None or head is None:
    return 0
```
Confirmed by direct read.

## G5 -- only FIXED/DISMISSED produce rows

`src/code_forge/machine.py:1191-1197`:
```python
for f in self._state.findings:
    if f.disposition == Disposition.FIXED:
        state = TerminalState.FIXED
    elif f.disposition == Disposition.DISMISSED:
        state = TerminalState.DISPROVED
    else:
        continue
```
Confirmed by direct read.

## G6 -- manual writer is separate

`src/code_forge/cli.py:1253-1255` (comment) and `:1314` (append_row call):
```python
# --new is reserved for DUPLICATE / ESCAPED (escapes from outside
# a run). FIXED / DISPROVED must originate from the state machine
# via _write_ledger_rows, not from a manual mark.
...
append_row(cwd, LedgerRow(...))
```
Confirmed by direct read of cli.py:1230-1330.

## Additional grounding beyond G1-G6 (not in the original order, found while re-verifying)

1. `src/code_forge/mcp_server.py:900-901` -- the in-process MCP sampling
   review path (how the `forge_review` MCP tool actually runs a review)
   constructs the state machine with a hardcoded, unconditional mode:
   ```python
   machine = StateMachine(
       mode=Mode.CI,
   ```
   There is no branch anywhere in `_run_review_via_sampling` (the
   function containing this construction) that ever passes
   `Mode.LOCAL`.

2. `src/code_forge/mode_resolver.py:45-50` -- the CLI's own mode
   resolver, for when sampling falls back to a CLI subprocess (or when
   a human/script runs `code-forge review` directly):
   ```python
   if cli_arg is not None:
       return _parse_mode_string(cli_arg, source="--mode")
   env_value = env.get("FORGE_MODE")
   if env_value is not None and env_value != "":
       return _parse_mode_string(env_value, source="FORGE_MODE env")
   return Mode.LOCAL if stdout_isatty else Mode.CI
   ```
   Default (no --mode, no FORGE_MODE) is `Mode.CI` whenever stdout is
   not a TTY -- true for every subprocess spawned by an agent, script,
   or MCP fallback dispatch.

3. `.code-forge/` is gitignored and untracked in the forge repo itself:
   ```
   $ git check-ignore -v .code-forge/gate.yaml
   .gitignore:51:.code-forge/	.code-forge/gate.yaml
   $ git ls-files | grep -c "^\.code-forge/"
   0
   ```
   A filesystem survey (`find ~ -maxdepth 6 -type d -name ".code-forge"`)
   found 40+ separate `.code-forge/` directories across many different
   projects and their linked worktrees on this machine (forge,
   plan-forge, ashare-lab, hermes-agent, OmniRoute, trinity-router,
   fleet-suite, harness, code-review-graph, etc.), none of which contain
   a ledger.jsonl -- consistent with G1 holding at machine scale, not
   just inside the forge repo.
