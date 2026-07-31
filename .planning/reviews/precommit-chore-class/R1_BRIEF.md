# R1 review brief: FORGE_COMMIT_CLASS declared-class carve-out

## Ground truth (verify with these commands before judging)

Repo: /home/houminxi/code/forge/.worktrees/precommit-chore-class
Branch: fix/precommit-chore-class (staged, uncommitted)
Review target: r1.diff in this directory (git diff --cached, 301 lines)

Context: forge's pre-commit gate is GENERATED from
src/code_forge/install_hooks.py -> generate_hook_content(). The built
hook blocks any commit touching a code file unless the file set matches
an extension-based "non-code" carve-out (NON_CODE regex) OR the three
gate blocks (receipt attestation, LLM review, gate-check) pass. A
chore-class change inside a .py file could not be expressed: the lock
busy-message commit sat staged and uncommittable on main because
verify demanded 9 review receipts for a message-only change. The
session-side convention already includes a class marker
(`git commit -m "..." # docs|config|chore|wip`) that the session-side
hook parses.

## What the change does

1. New FORGE_COMMIT_CLASS=<docs|config|chore|wip> env variable. The
   generated hook matches it BEFORE attestation (piped into the same
   case arm set), and on match sets _FORGE_DECLARED=1.
2. Receipt attestation (code-forge verify) runs only when
   _FORGE_DECLARED is empty.
3. After the presubmit lint block, a declared commit exits 0. It
   therefore skips LLM review, the (optional) chained prior hook, and
   gate-check -- the same treatment a non-code commit gets today, plus
   the text gates below which non-code commits skip.
4. Preserved for declared commits (intentionally NOT skipped):
   planning-leak guard, extension carve-out order, non-ASCII staged
   diff gate, AI-vocab staged diff gate, presubmit linters.
5. An unrecognized class value falls through to the full gate unchanged.
6. Three sibling test assertions were sharpened from proxy vocabulary
   ("presubmit" not in content.lower()) to the precise emitted marker
   ("code-forge: presubmit" / the "exec ... gate-check" line), because
   the new comment/echo text legitimately contains those words.

## Verification already performed (do not re-run; judge adequacy)

- tests/test_hook_carveout.py: 14 passed (8 pre-existing + 6 new:
  declared 200-line class block placement; .py commit passes with
  FORGE_COMMIT_CLASS=chore despite stub-verify-that-always-fails;
  same commit is BLOCKED without the env var; declared class skips
  gate-check (verify-ok stub, gate-check-fails stub -> commit passes);
  declared commit with an em dash in the code file still fails the
  non-ASCII text gate; mistyped class (chore-todo) falls through to
  the full gate and is blocked by failing verify).
- tests/test_install_hooks.py + test_hook_failclosed.py: 77 passed
  including the three sharpened assertions.
- Bug-injection at two sites, executed by the implementer before this
  review: (1) neutralize `_FORGE_DECLARED=1` inside the case arm ->
  tests (j) and (l) went red -> restored byte-identical (md5 match);
  (2) neutralize the declared-exit condition -> test (l) went red ->
  restored byte-identical.
- Generated hook output passes `sh -n`; shellcheck clean; ruff clean;
  non-ASCII check on the diff clean. Full test suite running.

## What to review

- Gate semantics: is the set of gates skipped for a declared commit
  correct? Specifically: is skipping the chained pre-existing hook and
  gate-check acceptable, given that the extension carve-out already
  skips both of those today for non-code commits?
- Fail-closed: any way FORGE_COMMIT_CLASS can accidentally widen the
  skip (e.g. inherited from env on a logic-bearing commit)?
- Shell correctness: quoting, `set -u` interplay, POSIX sh compat
  (hooks run #!/bin/sh).
- Test adequacy: is there a case the 6 new tests miss that a shipped
  regression would show?
- Self-consistency between docstring order list and assembled blocks.

## Output format

Findings as MAJOR/MINOR/NIT with file:line into r1.diff; one-line
verdict per finding. If none: state 0/0/0/0 (MAJOR/MINOR/NIT/other).
Disprove-allowed: cite the exact hook text or a command's real output.
