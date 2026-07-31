# Evidence 05 -- worktree lifecycle destroys a ledger row (H2)

## What this tests

H2's proposed mechanism directly: `.code-forge/` is gitignored/untracked,
and this project (per the user's global CLAUDE.md, "Always use git
worktrees: NEVER edit files in a main worktree") mandates doing real
work inside linked worktrees that get deleted after merge (per forge's
own CLAUDE.md, "Branch Hygiene: Feature and fix branches MUST be
deleted immediately after merge"). If a ledger row is ever written
while cwd is a worktree, does it survive worktree removal? This is a
general git property, demonstrated here on a disposable scratch repo
(not the forge repo itself, to avoid any risk to the real repository) --
the mechanism is identical because forge's own `.code-forge/` is
gitignored in exactly the same way (see evidence-01, item 3).

Scratch repo: `/tmp/.../scratchpad/ledger-diag/exp4/main_repo/`.

## Step 1 -- write a ledger row in the "main" tree, confirm it is gitignored

```
$ mkdir -p .code-forge
$ echo '{"fingerprint":"demo","terminal_state":"FIXED"}' > .code-forge/ledger.jsonl
$ git check-ignore -v .code-forge/ledger.jsonl
.gitignore:1:.code-forge/	.code-forge/ledger.jsonl
```

## Step 2 -- create a linked worktree (the project-mandated workflow)

```
$ git branch work
$ git worktree add ../wt work
Preparing worktree (checking out 'work')
HEAD is now at ecf4527 init scratch repo ...

$ ls -la ../wt/.code-forge
ls: cannot access '../wt/.code-forge': No such file or directory
```
Confirmed: a fresh linked worktree does NOT inherit the main tree's
untracked `.code-forge/` directory -- including whatever ledger.jsonl
already existed there. Any worktree that wants to run forge review
needs its own freshly created `.code-forge/` (exactly what
`smoke_lock_busy.sh` does by hand: `mkdir -p "$WS/.code-forge"` +
hand-written gate.yaml).

## Step 3 -- write a row inside the worktree, then remove the worktree

```
$ mkdir -p ../wt/.code-forge
$ echo '{"fingerprint":"written-inside-worktree","terminal_state":"FIXED"}' \
    > ../wt/.code-forge/ledger.jsonl
$ cat ../wt/.code-forge/ledger.jsonl
{"fingerprint":"written-inside-worktree","terminal_state":"FIXED"}

$ git worktree remove ../wt --force
$ ls ../wt
ls: cannot access '../wt': No such file or directory
```
Confirmed: the row written inside the worktree's `.code-forge/` is
permanently destroyed the moment the worktree is removed -- there is no
git mechanism that would have preserved it (it was never tracked).

## Step 4 -- the main tree's own ledger is unaffected either way

```
$ cat .code-forge/ledger.jsonl
{"fingerprint":"demo","terminal_state":"FIXED"}
```
The main tree's row survives (worktree lifecycle only affects the
worktree's own copy) -- but this only matters if a row ever reaches the
main tree's `.code-forge/` in the first place, which requires a human
to run forge interactively (LOCAL mode, evidence-03) directly in the
main tree rather than in a mandated worktree.

## Reading

H2's mechanism is real and reproducible with plain git, no forge code
involved. It is independent of H1 but compounds it: even on the rare
run that does reach LOCAL mode (the only mode that writes rows at all,
per evidence-02), if that run happens inside a worktree -- the
project's mandated way of doing any editing work -- the row does not
survive to be found later in the main tree. A large population of
already-existing `.code-forge/` directories scattered across many
worktrees on this machine, none containing a ledger.jsonl (evidence-01,
item 3), is consistent with this mechanism having applied repeatedly in
real usage, though it cannot be distinguished from "no LOCAL-mode run
ever happened in those worktrees at all" without a scan this diagnosis
did not have time to run project-by-project (see report, section 5).
