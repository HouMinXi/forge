#!/usr/bin/env bash
# EXPERIMENT 1 -- does a ledger row survive `git worktree remove`?
#
# This is the premise the whole centralisation argument rests on. It has
# been asserted three times today from reading .gitignore. Never measured.
# Measure it.
#
# Runs entirely in a scratch dir. Touches no real project.
set -u
S=/tmp/ledger_exp1
rm -rf "$S" 2>/dev/null
mkdir -p "$S" && cd "$S" || exit 1

say() { echo; echo "### $*"; }

say "SETUP: a scratch repo with a worktree"
git init -q main-repo
cd main-repo || exit 1
git config user.email houminxi@gmail.com
git config user.name "Minxi Hou"
printf 'def f():\n    return 1\n' > a.py
git add a.py && git commit -qm "base"
git worktree add -q ../wt -b feature 2>&1 | tail -2
echo "main repo : $(pwd)"
echo "worktree  : $S/wt"

say "STEP 1: write a ledger row FROM INSIDE the worktree"
cd "$S/wt" || exit 1
echo "cwd = $(pwd)"
# FLIP DISCLOSED (S1): the first version of this script invented the CLI
# flags (--fingerprint/--terminal-state/--file/--line/--axis-claim). Every
# one was rejected as an unrecognized argument, so NO row was ever written
# -- and the script then printed "the row DID NOT survive", which is the
# answer I expected. A false confirmation, not a measurement. The real
# signature is two positionals: `ledger mark <fingerprint> <state>`.
# Output is no longer piped, because the pipe was reporting tail's exit
# code (0) and swallowing the real failure.
out=$(code-forge ledger mark exp1-escaped-0001 ESCAPED --new --evidence manual 2>&1)
rc=$?
printf '%s\n' "$out" | tail -5
echo "exit: $rc"
if [ "$rc" -ne 0 ]; then
  echo "!!! the write FAILED -- everything below measures nothing. Stopping."
  exit 2
fi

say "STEP 2: where did it land?"
for p in "$S/wt/.code-forge/ledger.jsonl" "$S/main-repo/.code-forge/ledger.jsonl"; do
  if [ -f "$p" ]; then
    echo "  PRESENT  $p  ($(wc -l < "$p") row/s)"
  else
    echo "  ABSENT   $p"
  fi
done

say "STEP 3: is the row readable back through the CLI, from the worktree?"
code-forge ledger list 2>&1 | tail -5

say "STEP 4: remove the worktree (the routine post-merge action)"
cd "$S/main-repo" || exit 1
git worktree remove --force ../wt 2>&1 | tail -2
echo "worktree removed. remaining:"
git worktree list

say "STEP 5: THE VERDICT -- does the row still exist anywhere?"
found=0
for p in "$S/wt/.code-forge/ledger.jsonl" "$S/main-repo/.code-forge/ledger.jsonl"; do
  if [ -f "$p" ]; then echo "  SURVIVED  $p"; found=1; fi
done
# Look wider: anything anywhere under the scratch root.
hits=$(find "$S" -name "ledger.jsonl" 2>/dev/null)
if [ -n "$hits" ]; then
  echo "  files found under scratch root:"; printf '    %s\n' $hits
else
  echo "  NO ledger.jsonl anywhere under $S"
fi

say "STEP 6: could git have kept it? (objects are shared across worktrees)"
cd "$S/main-repo" || exit 1
echo "  branches: $(git branch --format='%(refname:short)' | tr '\n' ' ')"
echo "  is .code-forge tracked by git here? -> $(git ls-files .code-forge | wc -l) file(s)"

echo
if [ "$found" -eq 0 ]; then
  echo "=== RESULT: the row DID NOT survive. Centralisation is required. ==="
  exit 0
else
  echo "=== RESULT: the row SURVIVED. The premise was wrong -- re-examine. ==="
  exit 0
fi
