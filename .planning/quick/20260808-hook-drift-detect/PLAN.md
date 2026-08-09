---
task: hook-drift-detect
date: 2026-08-08
status: planned
---

# Quick task: report a stale installed git hook

## Problem (essence)

Nothing tells the user their installed `.git/hooks/pre-commit` predates the
generator that produced it. Measured 2026-07-30: a blocked commit printed the
generic "receipt verification failed" line because the installed hook was
older than e605b26, which had already changed the generator to capture and
replay verify's real output (`corrupt receipt: receipt-c2p1.json: Invalid
control character ...`). The fix existed in `src/` and helped nobody.

Cost of doing nothing: a hook installed before a security-relevant generator
change keeps enforcing last month's gate while the repo looks gated.

## Ground truth measured before planning

Run against the real repo (read-only):

    resolve_forge_path()  -> /home/houminxi/.local/bin/code-forge gate-check
    gate.yaml non_ascii   -> ai-smell     presubmit -> None
    is_forge_repo         -> True
    generate_hook_content(...) == .git/hooks/pre-commit   -> True
    generate_commit_msg_hook_content(...) == commit-msg   -> True

The generator is deterministic for a given (forge path, gate.yaml, chain,
repo-kind) and reproduces the installed file byte for byte. That single fact
decides the option below.

Installed hook mtime 2026-07-31 12:26 -- install-hooks was re-run after the
incident, so this repo's hook is currently NOT stale. The detector must stay
silent here and fire only on an injected stale hook.

## Options

1. **Stamp a generator hash into the hook; doctor compares stamp.**
   Rejected. Needs a version constant somebody must remember to bump, and it
   can only say "different", never what differs. Regeneration already
   reproduces the file byte for byte, so the stamp buys nothing.

2. **install-hooks reports drift when it overwrites something different.**
   Rejected. It speaks only at the moment the user has already fixed the
   problem by running install-hooks. In the measured incident nobody ran it,
   so this reports to an empty room.

3. **Do nothing; document that install-hooks must be re-run after upgrade.**
   Rejected, but close. Docs were available on 2026-07-30 and did not help,
   and a doc cannot fire on the security-drift case. The chosen option costs
   ~40 lines reusing an existing generator, so "do nothing" does not win on
   cost.

**Chosen: option 1 without the stamp** -- `doctor` regenerates what
`install-hooks` would write right now and compares it to the installed file.

## Tasks

1. `src/code_forge/install_hooks.py`: extract the generator-input assembly
   (forge path, gate.yaml non_ascii + presubmit, forge-repo detection) that
   `run_install_hooks` inlines today into one reusable function, called by
   both. Done-condition: existing install-hooks tests pass unchanged.

2. `src/code_forge/doctor.py`: per hook file, regenerate expected content and
   compare. forge-generated and identical -> PASS; forge-generated and
   different -> FAIL naming re-install; missing or foreign -> SKIP (forge is
   usable MCP-only, absence is not failure). A hook chaining a
   `*.code-forge-backup` is compared against a chained regeneration.
   Done-condition: new rows in real `code-forge doctor` output, both PASS.

3. `tests/test_doctor.py`: current / stale / missing / foreign / chained.
   Done-condition: each proven by bug-injection at the comparison itself.

4. Real-path bug-injection: real `code-forge install-hooks` into a throwaway
   git repo, detector silent; rewrite the verify block back to the
   pre-e605b26 form, detector FAILs; restore, silent again.

## Inversion (how this fails)

A detector that always fires proves nothing. Guarded by requiring the silent
side on the real repo and on a freshly installed hook, both measured. Second
failure mode: it goes stale itself when a future generator input is added to
`run_install_hooks` only -- task 1 removes that path.

## Out of scope

- `src/code_forge/backend.py`, `src/code_forge/llm_invoke.py` (in flight)
- Re-install dropping an existing chain (pre-existing generator behavior)
