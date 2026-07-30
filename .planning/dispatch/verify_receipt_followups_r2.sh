#!/usr/bin/env bash
# Mechanical exit verifier for the receipt-followups R2 rework.
# Frozen 2026-07-29, before the delivery exists. Not mentioned by the order.
#
# Scope note, deliberately loud: item A is a BEHAVIOURAL contract and this
# file does not attempt to prove it. Grepping the generated hook for a
# substring is exactly how the R1 verifier's section E produced a false FAIL
# on a correct change. Item A's proof lives in the held-out adversary, which
# executes the generated hook. What is checked here is everything mechanical
# the order asks for, plus the doc/code consistency a behavioural test cannot
# see.
#
# Usage: verify_receipt_followups_r2.sh <worktree>
set -u

WT="${1:?usage: $0 <worktree>}"
BASE=891772a
OLD_TIP=b6df31a
cd "$WT" || exit 2

PASS=0; FAIL=0
ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
head_(){ echo; echo "== $1 =="; }

echo "worktree: $WT"
echo "base:     $BASE   old tip: $OLD_TIP   head: $(git rev-parse --short HEAD)"

# ----------------------------------------------------------------------
head_ "A. item A -- the docstring no longer contradicts the code"
# A consistency check, not a substring check: whatever the generated hook
# actually invokes, the hook-order docstring must describe the same thing.
code_quiet=0
grep -q 'code-forge verify --quiet' src/code_forge/install_hooks.py \
  && code_quiet=1
doc_quiet=0
sed -n '/Hook execution order:/,/^$/p' src/code_forge/install_hooks.py \
  | grep -q -- '--quiet' && doc_quiet=1
if [ "$code_quiet" = "$doc_quiet" ]; then
  ok "docstring matches the invocation (both quiet=$code_quiet)"
else
  bad "docstring and code disagree (code quiet=$code_quiet doc quiet=$doc_quiet)"
fi
echo "        item A behaviour is NOT checked here -- see the held-out adversary"

# ----------------------------------------------------------------------
head_ "B. item B -- Signed-off-by on every commit"
n=$(git log --oneline "$BASE"..HEAD | wc -l)
signed=$(git log --format='%B' "$BASE"..HEAD \
         | grep -c '^Signed-off-by: Minxi Hou <houminxi@gmail.com>$')
[ "$signed" -eq "$n" ] && ok "all $n commits signed off" \
                       || bad "only $signed/$n commits signed off"

# ----------------------------------------------------------------------
head_ "C. item C -- 6851dd5 split, content preserved"
echo "        $n commits since base (was 4)"
[ "$n" -ge 6 ] && ok "commit count grew, bundle was split" \
               || bad "still $n commits -- 6851dd5 looks unsplit"

# The rewrite may only ADD item A's files. Anything else moved content.
stray=$(git diff "$OLD_TIP"..HEAD --name-only \
        | grep -vE '(install_hooks\.py|tests/test_install_hooks\.py|\.planning/)' \
        || true)
if [ -z "$stray" ]; then
  ok "no content moved during the rewrite"
else
  bad "files changed outside item A's allowance:"
  printf '        %s\n' $stray
fi

# The three items that shared 6851dd5 all live in verify.py, so a real
# split shows up as three separate commits touching that file. Checking
# instead that the words appear "somewhere" in the log is useless: the test
# commit's own subject already names inverted ranges and _covered.
vp=$(git log --format='%H' "$BASE"..HEAD -- src/code_forge/verify.py | wc -l)
[ "$vp" -ge 3 ] && ok "verify.py is touched by $vp commits (items 2, 3, 4 separated)" \
                || bad "only $vp commit(s) touch verify.py -- the bundle is still bundled"

# ----------------------------------------------------------------------
head_ "D. item D -- the report describes what shipped"
R=.planning/phases/dispatch-receipt-followups/report.md
if [ -f "$R" ]; then
  grep -qiE 'BLOCKED on commit|not committed \(hook blocks\)' "$R" \
    && bad "report still claims the work is uncommitted" \
    || ok "report no longer claims BLOCKED"
  # A labelled line, not a keyword. The current report already contains the
  # word "bypass" in a sentence ASKING the PM to perform one -- grepping the
  # bare word passes on a report that discloses nothing.
  grep -qE '^Hook bypass: .+' "$R" \
    && ok "report records the bypass method on a labelled line" \
    || bad "report has no 'Hook bypass:' line"
  grep -qi 'pre-existing' "$R" \
    && bad "report still calls the extra skip pre-existing" \
    || ok "the extra skip is described correctly"
else
  bad "report.md missing at $R"
fi

# ----------------------------------------------------------------------
head_ "E. hygiene"
authors=$(git log --format='%an <%ae>' "$BASE"..HEAD | sort -u)
[ "$authors" = "Minxi Hou <houminxi@gmail.com>" ] \
  && ok "author correct on all commits" \
  || { bad "unexpected author(s)"; printf '        %s\n' "$authors"; }

git log --format='%B' "$BASE"..HEAD \
  | grep -qiE 'P[0-3]\b|blocker|review cycle|Changes:|Added:|Fixed:' \
  && bad "banned vocabulary in a commit message" \
  || ok "no banned vocabulary in commit messages"

git log --format='%B' "$BASE"..HEAD \
  | grep -qE '\b[Ii]tem [A-D]\b|\bF[0-9]+:|\b[Dd]-[0-9]' \
  && bad "order/finding IDs leaked into a commit message" \
  || ok "no order or finding IDs in commit messages"

git diff "$BASE"..HEAD --diff-filter=AM -U0 | grep '^+' \
  | grep -qP '[^\x00-\x7F]' \
  && bad "non-ASCII in the diff" \
  || ok "non-ASCII gate clean"

changed=$(git diff "$BASE"..HEAD --name-only --diff-filter=AM \
          | grep '\.py$' | grep -v '^\.planning/' || true)
if [ -n "$changed" ]; then
  # Changed files only: the tree carries 112 pre-existing ruff errors and
  # Step 0's rule is zero NEW findings, not zero findings.
  if ruff check $changed >/tmp/r2_ruff.txt 2>&1; then
    ok "ruff clean on changed files"
  else
    bad "ruff findings on changed files"; sed -n '1,8p' /tmp/r2_ruff.txt
  fi
  for f in $changed; do
    python3 -m py_compile "$f" 2>/dev/null || bad "py_compile failed: $f"
  done
  ok "py_compile clean on changed files"
else
  bad "no python files changed -- item A cannot have landed"
fi

# ----------------------------------------------------------------------
head_ "F. suite"
out=$(timeout 900 python3 -m pytest -q 2>&1 | tail -3)
printf '        %s\n' "$out"
echo "$out" | grep -qE '^[0-9]+ passed' && {
  got=$(echo "$out" | grep -oE '^[0-9]+ passed' | grep -oE '[0-9]+')
  [ "$got" -ge 2999 ] && ok "suite green, $got passed (baseline 2999)" \
                      || bad "passed count dropped to $got (baseline 2999)"
} || bad "suite not green"

echo
echo "== TOTAL: $PASS pass, $FAIL fail =="
[ "$FAIL" -eq 0 ] || exit 1
