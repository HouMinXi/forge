[aicc] session: pc48-r1-kimi
⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY or another auth source is set and takes precedence over your claude.ai login · Unset it to load your organization's connectors
R3 delta confirmed against the staged worktree.

Wording fixes verified:
- `src/code_forge/install_hooks.py:439-440`: docstring order list now reads `FORGE_COMMIT_CLASS=docs|config|chore|wip marks _FORGE_DECLARED`; the "same vocabulary" phrase is gone.
- `docs/setup-vscode.md:227-232`: export warning now says the printed declared-class line cannot distinguish intent from residue; the "nothing warns" contradiction is resolved.

Fresh round-1-style pass on gate semantics, fail-closed behavior, POSIX shell correctness, and test adequacy found no new issues. Tests pass (96), generated hook passes `sh -n` and `shellcheck`.

0/0/0

[aicc] session saved: pc48-r1-kimi
[aicc] to resume:  aicc kimi --cont pc48-r1-kimi "continue"
kimi rc=0
