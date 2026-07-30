#!/usr/bin/env bash
# Mechanical exit verifier for the receipt-followups R3 cleanup.
# Frozen 2026-07-29, before the cleanup exists.
#
# Two defects from the R2 verifier are fixed here, both of the same shape --
# a grep matching a token that correlates with the answer instead of
# measuring the answer:
#
#   * the R2 "pre-existing" check greps a word that legitimately appears
#     elsewhere in the report. Dropped; there is nothing left to check.
#   * the R2 order-ID check only read commit messages, so seven IDs in code
#     went through unseen. Section F now reads the code.
#
# Item A's behaviour and the regression guards live in the held-out
# adversary, which is deliberately not in this directory.
#
# Usage: verify_receipt_followups_r3.sh <worktree>
set -u

WT="${1:?usage: $0 <worktree>}"
BASE=891772a
PREV_TIP=ef3d961
cd "$WT" || exit 2

PASS=0; FAIL=0
ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
head_(){ echo; echo "== $1 =="; }

echo "worktree: $WT"
echo "base:     $BASE   prev tip: $PREV_TIP   head: $(git rev-parse --short HEAD)"

# ----------------------------------------------------------------------
head_ "E. the inert semgrep commit is gone"
d=$(git diff main..HEAD -- tests/test_taint_rule.py)
[ -z "$d" ] && ok "test_taint_rule.py matches main" \
             || { bad "test_taint_rule.py still differs from main"
                  echo "$d" | head -8 | sed 's/^/        /'; }

# A revert leaves both commits in history; a rebase-drop leaves neither.
# Either is acceptable, so check the tree, not the log -- but a *third*
# outcome, a replacement fix, is not, and shows up as a live reference.
git grep -qn 'GAI_OVERRIDE' -- . \
  && bad "GAI_OVERRIDE still referenced somewhere" \
  || ok "no GAI_OVERRIDE reference in the tree"

# ----------------------------------------------------------------------
head_ "F. work-order coordinates removed from code"
# No trailing \b: the class names read TestItem1CrossRepoGuard, so a word
# boundary after the digit never matches and three of the seven leaks go
# unseen. Verified on this tree -- with \b the pattern finds 4, without it 7,
# and 0 once the renames are applied.
hits=$(git grep -nE 'Item ?[0-9A-D]' -- src tests || true)
if [ -z "$hits" ]; then
  ok "no Item-N coordinate in src/ or tests/"
else
  bad "$(printf '%s\n' "$hits" | wc -l) coordinate(s) remain in code"
  printf '%s\n' "$hits" | head -8 | sed 's/^/        /'
fi

# The three renamed classes must still be collected under SOME name. A
# rename that breaks pytest's pattern drops them silently and the suite
# stays green with fewer tests.
collected=$(timeout 300 python3 -m pytest --collect-only -q 2>/dev/null \
            | grep -oE '^[0-9]+ tests collected' | grep -oE '^[0-9]+')
echo "        collected: ${collected:-unknown} (was 3011 at $PREV_TIP)"
[ "${collected:-0}" -eq 3011 ] && ok "collected count unchanged" \
                               || bad "collected count moved to ${collected:-unknown}"

# ----------------------------------------------------------------------
head_ "G. the report matches the delivery"
R=.planning/phases/dispatch-receipt-followups/report.md
if [ -f "$R" ]; then
  # FLIP DISCLOSED (S1): this check read `grep -qi semgrep` across the whole
  # report and produced a FAIL against the 017eb39 delivery. That FAIL was
  # wrong and the executor said so with a correct argument. The order asked
  # for the SECTION describing the reverted work to go; the word survives
  # legitimately in item E, which has to explain why the revert happened.
  # Now asserts on the heading, which the author controls deliberately.
  # Frozen result of the broken version is kept at
  # .planning/dispatch/results/r3_verify_delivery_017eb39.txt -- 13 pass,
  # 1 fail. Under this version the same tree gives 14 pass, 0 fail.
  grep -qiE '^## +Semgrep fix' "$R" \
    && bad "report still carries the standalone semgrep section" \
    || ok "standalone semgrep section removed"
  # Which PM gate was run, named by path. A results table without this is
  # the R2 failure repeating.
  grep -qE '\.planning/dispatch/|forge-pm-exit' "$R" \
    && ok "report names the PM artifacts it ran" \
    || bad "report does not name which PM gate it ran, by path"
  grep -qE '[0-9]+ pass,? *[0-9]+ fail' "$R" \
    && ok "report carries gate pass/fail numbers" \
    || bad "report has no gate numbers"
else
  bad "report.md missing at $R"
fi

# ----------------------------------------------------------------------
head_ "H. hygiene"
n=$(git log --oneline "$BASE"..HEAD | wc -l)
signed=$(git log --format='%B' "$BASE"..HEAD \
         | grep -c '^Signed-off-by: Minxi Hou <houminxi@gmail.com>$')
[ "$signed" -eq "$n" ] && ok "all $n commits signed off" \
                       || bad "only $signed/$n commits signed off"

authors=$(git log --format='%an <%ae>' "$BASE"..HEAD | sort -u)
[ "$authors" = "Minxi Hou <houminxi@gmail.com>" ] \
  && ok "author correct on all commits" \
  || { bad "unexpected author(s)"; printf '        %s\n' "$authors"; }

git log --format='%B' "$BASE"..HEAD \
  | grep -qiE 'P[0-3]\b|blocker|review cycle|Changes:|Added:|Fixed:|Item ?[0-9A-D]' \
  && bad "banned vocabulary or an order ID in a commit message" \
  || ok "commit messages clean"

git diff "$BASE"..HEAD --diff-filter=AM -U0 | grep '^+' \
  | grep -qP '[^\x00-\x7F]' \
  && bad "non-ASCII in the diff" \
  || ok "non-ASCII gate clean"

changed=$(git diff "$BASE"..HEAD --name-only --diff-filter=AM \
          | grep '\.py$' | grep -v '^\.planning/' || true)
if [ -n "$changed" ]; then
  ruff check $changed >/tmp/r3_ruff.txt 2>&1 \
    && ok "ruff clean on changed files" \
    || { bad "ruff findings on changed files"; sed -n '1,8p' /tmp/r3_ruff.txt; }
  fails=0
  for f in $changed; do
    python3 -m py_compile "$f" 2>/dev/null || fails=$((fails+1))
  done
  [ "$fails" -eq 0 ] && ok "py_compile clean on changed files" \
                     || bad "$fails file(s) failed py_compile"
else
  bad "no python files changed"
fi

# ----------------------------------------------------------------------
head_ "I. suite"
out=$(timeout 900 python3 -m pytest -q 2>&1 | tail -3)
printf '        %s\n' "$out"
got=$(echo "$out" | grep -oE '^[0-9]+ passed' | grep -oE '[0-9]+')
if [ -n "${got:-}" ]; then
  # 3002 was measured at ef3d961. The revert removes no tests, so the count
  # should hold. The semgrep test is network-flaky in BOTH directions (it
  # failed and passed on one unchanged tree within ten minutes), so a single
  # failure there is reported, not treated as a regression.
  [ "$got" -ge 3002 ] && ok "suite green, $got passed (baseline 3002)" \
                      || bad "passed count dropped to $got (baseline 3002)"
else
  bad "suite not green -- check whether the failure is the flaky semgrep test"
fi

echo
echo "== TOTAL: $PASS pass, $FAIL fail =="
[ "$FAIL" -eq 0 ] || exit 1
