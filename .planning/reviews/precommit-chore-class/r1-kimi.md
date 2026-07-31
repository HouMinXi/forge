[aicc] session: pc48-r1-kimi
⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY or another auth source is set and takes precedence over your claude.ai login · Unset it to load your organization's connectors
Review findings for r1.diff:

**MINOR** `docs/setup-vscode.md:+24-26` — The phrase "versus the baseline" is orphaned after the inserted declared-class paragraph; it previously completed the R1 test-gate sentence at line 8 and now reads as a stray fragment.

**MINOR** `tests/test_hook_carveout.py:+164-402` — No test verifies that presubmit linters still run for declared-class commits: `_install_hook` calls `generate_hook_content(..., presubmit_entries=None)`, so the entire `TestDeclaredClassCarveout` class executes with an empty presubmit block. A regression that skipped presubmit for declared commits would pass.

**NIT** `tests/test_hook_carveout.py:+260-283` — Test (m) exercises the staged-diff non-ASCII gate for declared commits but does not exercise the AI-vocabulary gate in the same d12 block, despite the brief listing both as preserved.

**NIT** `tests/test_hook_carveout.py:+164-402` — Only `chore` and `config` are exercised in execution tests; `docs` and `wip` values and the chain-skip path are untested (the case arm is identical, so the risk is low).

Verification run: `85 passed` in `tests/test_hook_carveout.py` + `tests/test_install_hooks.py`; generated hook passes `sh -n` and `shellcheck -s sh`; non-ASCII scan clean.

MAJOR:0 MINOR:2 NIT:2

[aicc] session saved: pc48-r1-kimi
[aicc] to resume:  aicc kimi --cont pc48-r1-kimi "continue"
kimi rc=0
