# L0 advisory path doubling, 2026-08-16

Spotted during the silent-exit smoke run (stub engine, fix worktree):
advisory output showed

  [pre-existing-l0] /home/houminxi/code/forge/.worktrees/silent-exit-fix/home/houminxi/code/forge/.worktrees/silent-exit-fix/tests/test_cli_banner.py:1-1

i.e. the worktree cwd was prefixed onto an already-absolute path in the
L0 pre-existing-findings advisory (os error 2 follows from the bogus
path). Pre-existing, out of scope for fix/silent-exit-visible; filed
for a follow-up. Likely site: the L0 detect path join where a resolved
absolute source path is re-joined with cwd. Repro: run any review with
an untracked file present (untracked files enter the review's source
set, which is how the smoke's test file got analyzed).
