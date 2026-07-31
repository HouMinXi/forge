# Evidence 03 -- resolve_mode's TTY default, called directly (real function, not a rewrite)

## What this tests

H1: what mode does a bare invocation (no --mode, no FORGE_MODE) resolve
to when stdout is not a TTY -- the case for every subprocess spawned by
an agent, script, or the MCP server's CLI-fallback dispatch.

Script: `exp2_check_mode_resolver.py` (this directory). Imports the
real, unmodified `code_forge.mode_resolver.resolve_mode` -- the exact
function shipped at `src/code_forge/mode_resolver.py:17-50`.

## Command and real output

```
$ python3 exp2_check_mode_resolver.py
resolve_mode(cli_arg=None, env={}, stdout_isatty=False) -> Mode.CI
resolve_mode(cli_arg=None, env={}, stdout_isatty=True)  -> Mode.LOCAL
resolve_mode(cli_arg='local', env={}, stdout_isatty=False) -> Mode.LOCAL
resolve_mode(cli_arg=None, env={'FORGE_MODE':'local'}, stdout_isatty=False) -> Mode.LOCAL
```

Second check -- confirm a real subprocess whose stdout is piped (the
normal case for any programmatic caller) genuinely reports non-TTY:
```
$ python3 -c "import sys; print('isatty when piped through tee:', sys.stdout.isatty())" | tee -a transcript.txt
isatty when piped through tee: False
```

## Reading

The bare/default case (`cli_arg=None`, no FORGE_MODE, non-TTY stdout)
resolves to `Mode.CI`. Any explicit override (`--mode local` or
`FORGE_MODE=local`) does produce `Mode.LOCAL`, so the resolver is not
broken -- it is doing exactly what its own docstring says. The
significance is entirely about which value real invocations supply: the
project's actual invocation paths (see evidence-04 and evidence-06)
supply neither override, so they fall through to the TTY check, and a
subprocess's stdout is essentially never a TTY.
