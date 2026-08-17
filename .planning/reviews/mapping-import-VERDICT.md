# Mapping import -- verdict

Branch `fix/mapping-import`, commit `86da267`, 1 file +1/-1.
Worktree `.claude/worktrees/mapping-import`.

## What was wrong

`ruff check src/` reported one error, and had for as long as anyone had
been running it:

    F821 Undefined name `Mapping`
    src/code_forge/cli.py:2337  (line 2405 with the windowing change applied)

`_check_backend_credentials` annotates `env: Optional[Mapping[str, str]]`,
and cli.py never imported `Mapping`.

## What it does NOT break

Measured rather than assumed. `from __future__ import annotations` sits at
cli.py:9, so annotations are strings and this one is never resolved:

    fn.__annotations__  ->  {'env': 'Optional[Mapping[str, str]]', ...}   OK
    typing.get_type_hints(fn)  ->  NameError: name 'BackendConfig' is not defined

The resolution failure lands on `BackendConfig`, not on `Mapping`.
`BackendConfig` is imported under `if TYPE_CHECKING:` on purpose, so these
annotations were never resolvable and adding `Mapping` does not change
that. The latent-NameError story does not hold.

ruff flags `Mapping` but not `BackendConfig` because it understands the
TYPE_CHECKING block. Both names are equally absent at runtime; one is a
deliberate convention and one was an omission.

## Why fix it anyway

It is a permanent false positive on the lint gate. The pre-commit Step 0
requires zero NEW errors, so every run began by re-deciding whether this
error was one of yours. That decision cost is paid on every commit, and
the answer had been no since the line was written.

## The fix

`Mapping` added to the existing `from typing import ...` on cli.py:19,
next to `Optional`, which is also annotation-only. The TYPE_CHECKING block
was not used: this file reserves that for project-internal modules
(`.backend`, `.registry`), where it avoids a circular import, and typing
names live on line 19.

Scope checked first: `Mapping` appears exactly once in cli.py, and this was
the only F821 in the whole `src/` tree.

## Bug injection

| step | result |
|---|---|
| inject (remove `Mapping` from the typing import) | `ruff --select F821` FAILS, rc=1, names cli.py:2337 |
| revert from backup | md5 `360e4f75125d4d75d0fff973e2d92f8a`, matches pre-injection |
| after revert | `ruff --select F821` PASSES, rc=0 |

## Checks

- `typing.Mapping` still importable on this interpreter (Python 3.14.6),
  and raises no DeprecationWarning under `-W error::DeprecationWarning`.
  Worth checking: this commit adds a real runtime import to a module every
  code path loads, and `typing.Mapping` has been deprecated for years.
- `ruff check src/code_forge/cli.py`: clean. No F401 appeared -- ruff reads
  the string annotation and counts the name as used.
- non-ASCII gate on diff and commit message: no hits
- full suite: **3134 passed, 4 skipped, exit 0, 399.94s**

## Classification

Committed as `chore`. It changes no control flow and no runtime behaviour:
the only difference at import time is one more name bound in the module
namespace.
