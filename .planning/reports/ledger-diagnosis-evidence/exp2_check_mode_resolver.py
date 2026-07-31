#!/usr/bin/env python3
"""EXPERIMENT 2: calls the REAL code_forge.mode_resolver.resolve_mode
function (not a rewrite) to measure what mode a non-interactive
invocation (stdout not a TTY -- the case for any subprocess spawned by
an agent, MCP server, or CI system) resolves to, with no --mode flag
and no FORGE_MODE env var set.
"""
from code_forge.mode_resolver import resolve_mode

env_clean = {}  # no FORGE_MODE, no CI vars -- simulates a bare invocation

print("resolve_mode(cli_arg=None, env={}, stdout_isatty=False) ->",
      resolve_mode(None, env_clean, False))
print("resolve_mode(cli_arg=None, env={}, stdout_isatty=True)  ->",
      resolve_mode(None, env_clean, True))
print("resolve_mode(cli_arg='local', env={}, stdout_isatty=False) ->",
      resolve_mode("local", env_clean, False))
print("resolve_mode(cli_arg=None, env={'FORGE_MODE':'local'}, stdout_isatty=False) ->",
      resolve_mode(None, {"FORGE_MODE": "local"}, False))
