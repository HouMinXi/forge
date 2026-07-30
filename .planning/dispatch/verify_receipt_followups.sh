#!/usr/bin/env bash
# EXIT verifier for dispatch_receipt_followups_20260728.txt
#
# FROZEN 2026-07-28, before the delivery exists. Do not edit it to accommodate
# what arrives -- that is the failure mode this file exists to prevent. If a
# check here is wrong, say so in writing and record why, then change it.
#
# Usage: bash verify_receipt_followups.sh <path-to-mimo-worktree>
# Exit 0 only if every check passes.
#
# Sections A-F mirror the five order items plus hygiene. Section G is HELD OUT:
# those checks are deliberately absent from the work order, so a delivery
# written to satisfy the order rather than to be correct will fail them.
#
# Known-answer validated 2026-07-28 against .worktrees/excerpt-coverage, a tree
# where NONE of the five items was done: 14 pass, 6 fail, exit 1. The 6 were
# exactly A, B(x2), C, D, E -- the undone items -- while every G regression
# guard passed. A verifier never shown to fail is not evidence.
#
# That run also found two bugs in THIS file, both now fixed: a bare `grep -r`
# matched a stale .pyc and would have failed a correct deletion, and
# `ruff check src/ tests/` was a permanently-red gate (the tree carries 112
# pre-existing ruff errors) that would have failed any delivery for someone
# else's debt. Step 0's rule is zero NEW findings, not zero findings.

set -uo pipefail
W="${1:?usage: $0 <worktree>}"
cd "$W" || exit 2

PASS=0; FAIL=0
ok()    { printf '  PASS  %s\n' "$1"; PASS=$((PASS+1)); }
bad()   { printf '  FAIL  %s\n' "$1"; FAIL=$((FAIL+1)); }
head_() { printf '\n== %s ==\n' "$1"; }
# Run a python probe; its exit code decides, its stdout is shown either way.
probe() {
  local name="$1"; shift
  local out rc
  out=$(python3 - 2>&1); rc=$?
  [ -n "$out" ] && printf '%s\n' "$out" | sed 's/^/        /'
  [ $rc -eq 0 ] && ok "$name" || bad "$name"
}

BASE=$(git merge-base HEAD main 2>/dev/null) || BASE=""
[ -n "$BASE" ] || { echo "cannot find merge-base with main"; exit 2; }
echo "worktree: $W"
echo "base:     $BASE   head: $(git rev-parse --short HEAD)"

head_ "A. item 2 -- write_attestation deleted"
# --include=*.py, not a bare -r: a stale __pycache__/*.pyc keeps matching a
# symbol that was correctly deleted from source, which would fail a good
# delivery for a reason it cannot fix.
if grep -rn --include='*.py' "write_attestation" src/ tests/ >/dev/null 2>&1; then
  bad "write_attestation still referenced"
  grep -rn --include='*.py' "write_attestation" src/ tests/ | sed 's/^/        /'
else
  ok "no references remain"
fi

head_ "B. item 1 -- cross_repo reuses the guarded loader"
# B1 static: the wiring. Without it, B2 would only prove verify.py still works,
# which it did before this order.
if grep -n "json.loads" src/code_forge/cross_repo.py 2>/dev/null \
     | grep -q "receipt\|r\.read_text"; then
  bad "cross_repo.py still json.loads a receipt directly"
  grep -n "json.loads" src/code_forge/cross_repo.py | sed 's/^/        /'
else
  ok "no direct receipt json.loads in cross_repo.py"
fi
grep -q "_load_receipts" src/code_forge/cross_repo.py 2>/dev/null \
  && ok "cross_repo.py routes through _load_receipts" \
  || bad "cross_repo.py does not call _load_receipts -- pin not followed"
# B2 behavioural: the seam, against the real corruption shape on disk.
probe "corrupt receipt raises CorruptedReceiptError naming the file" <<'PY'
import sys, tempfile, pathlib
sys.path.insert(0, "src")
from code_forge.verify import _load_receipts
from code_forge.errors import CorruptedReceiptError
d = pathlib.Path(tempfile.mkdtemp())
(d / "receipt-c1p1.json").write_text('{\n "cycle": 1,\n "note": "a\nb"\n}')
try:
    _load_receipts(d)
except CorruptedReceiptError as e:
    if "receipt-c1p1.json" in str(e):
        sys.exit(0)
    print("error does not name the file: %s" % e); sys.exit(1)
except Exception as e:
    print("wrong exception %s: %s" % (type(e).__name__, e)); sys.exit(1)
print("corrupt receipt loaded without error"); sys.exit(1)
PY

head_ "C. item 3 -- inverted range rejected"
probe "start_line > end_line is rejected at load" <<'PY'
import sys
sys.path.insert(0, "src")
from code_forge.verify import _validate_receipt_schema
r = {"cycle":1,"pass":1,"skill":"x","diff_sha256":"s","timestamp":"t",
     "findings_count":0,"findings":[],"anchors":[],
     "code_excerpts":[{"file":"f.py","start_line":9,"end_line":3,"content":"a"}],
     "covered_line_ranges":[]}
try:
    _validate_receipt_schema(r, "inv.json")
except Exception:
    sys.exit(0)
print("inverted range 9..3 was ACCEPTED"); sys.exit(1)
PY
# C2 is the trap that already bit this codebase once: a schema derived from the
# writer rejected 11 of 14 real receipts. One real rejection fails this.
probe "no real receipt on disk is rejected by the new schema" <<'PY'
import sys, json, pathlib
sys.path.insert(0, "src")
from code_forge.verify import _validate_receipt_schema
acc = rej = unp = 0; bad = []
for p in pathlib.Path.home().joinpath("code").rglob("receipt-c*p*.json"):
    try:
        d = json.loads(p.read_text())
    except Exception:
        unp += 1; continue
    if not isinstance(d, dict):
        unp += 1; continue
    try:
        _validate_receipt_schema(d, p.name); acc += 1
    except Exception as e:
        rej += 1; bad.append("%s: %s" % (p, e))
print("accepted=%d rejected=%d unparseable=%d" % (acc, rej, unp))
if rej:
    for b in bad[:10]: print(b)
    sys.exit(1)
sys.exit(0)
PY

head_ "D. item 4 -- _covered tolerates both shapes"
probe "_covered handles dict, string, mixed and empty" <<'PY'
import sys
sys.path.insert(0, "src")
from code_forge.verify import _covered
fails = []
for shape, val in (("dict", [{"file":"f.py","start":1,"end":3}]),
                   ("string", ["f.py:1-3"]),
                   ("mixed", [{"file":"f.py","start":1,"end":2}, "g.py:5-6"]),
                   ("empty", [])):
    try:
        n = len(_covered({"covered_line_ranges": val}))
        print("%-7s -> %d lines" % (shape, n))
    except Exception as e:
        fails.append("%s raised %s: %s" % (shape, type(e).__name__, e))
for f in fails: print(f)
sys.exit(1 if fails else 0)
PY

head_ "E. item 5 -- hook message points at the data"
grep -q "receipt verification failed" src/code_forge/install_hooks.py 2>/dev/null \
  && bad "message unchanged (still 'receipt verification failed')" \
  || ok "message changed"

head_ "F. hygiene"
n=$(git log --oneline "$BASE"..HEAD | wc -l)
echo "        $n commits since base (order asked for one per item)"
authors=$(git log --format='%an <%ae>' "$BASE"..HEAD | sort -u)
[ "$authors" = "Minxi Hou <houminxi@gmail.com>" ] \
  && ok "author correct on all commits" \
  || { bad "unexpected author(s)"; printf '        %s\n' "$authors"; }
git log --format='%B' "$BASE"..HEAD \
  | grep -qiE 'P[0-3]\b|blocker|review cycle|Changes:|Added:|Fixed:' \
  && bad "banned vocabulary in a commit message" \
  || ok "no banned vocabulary in commit messages"
signed=$(git log --format='%B' "$BASE"..HEAD | grep -c "Signed-off-by: Minxi Hou")
[ "$signed" -eq "$n" ] && ok "all $n commits signed off" \
                       || bad "only $signed/$n commits signed off"
git diff "$BASE"..HEAD --diff-filter=AM -U0 | grep '^+' | grep -qP '[^\x00-\x7F]' \
  && bad "non-ASCII in added lines" || ok "non-ASCII gate clean"
git log --format='%B' "$BASE"..HEAD | grep -qE '\b(F[0-9]+|D-[0-9]+)\b' \
  && bad "finding ID leaked into a commit message" \
  || ok "no finding IDs in commit messages"
# Scoped to the files this delivery touched. The tree carries 112 pre-existing
# ruff errors (measured 2026-07-28); `ruff check src/ tests/` is therefore a
# permanently-red gate that would fail any delivery for someone else's debt.
# Step 0's rule is zero NEW findings, not zero findings.
changed=$(git diff --name-only "$BASE"..HEAD -- '*.py')
if [ -z "$changed" ]; then
  bad "no .py files changed since base -- nothing was delivered"
else
  # shellcheck disable=SC2086
  if python3 -m ruff check $changed >/dev/null 2>&1; then
    ok "ruff clean on changed files"
  else
    bad "ruff findings on changed files"
    # shellcheck disable=SC2086
    python3 -m ruff check $changed 2>&1 | tail -15 | sed 's/^/        /'
  fi
fi

head_ "G. HELD OUT -- not mentioned in the work order"
# G1. The covered_line_ranges exclusion is load-bearing and item 3 edits the
# same function. A delivery that "tidies" while in there breaks every receipt.
probe "covered_line_ranges still excluded from the schema" <<'PY'
import sys, pathlib
src = pathlib.Path("src/code_forge/verify.py").read_text()
if "_LIST_OF_DICT_FIELDS" not in src:
    print("_LIST_OF_DICT_FIELDS is gone"); sys.exit(1)
tail = src.split("_LIST_OF_DICT_FIELDS", 1)[1][:400]
sys.exit(1 if "covered_line_ranges" in tail else 0)
PY
# G2. Nobody moved a threshold to make something pass.
for pat in "0\.6" "0\.8"; do
  git diff "$BASE"..HEAD -- src/code_forge/verify.py \
    | grep -E "^-.*$pat" | grep -q . \
    && bad "threshold $pat was modified in verify.py" \
    || ok "threshold $pat untouched"
done
# G3. No existing test deleted or weakened to go green.
del=$(git diff "$BASE"..HEAD -- tests/ | grep -cE '^-[[:space:]]*def test_')
[ "$del" -eq 0 ] && ok "no test function deleted" || bad "$del test functions removed"
wk=$(git diff "$BASE"..HEAD -- tests/ | grep -cE '^-[[:space:]]*assert ')
[ "$wk" -le 2 ] && ok "assertions removed: $wk (<=2 tolerated for rewrites)" \
                || bad "$wk assertions removed -- inspect for weakening"
# G4. The Phase 42 excerpt-coverage fix must still be in force.
probe "wide range with thin content still credits only shown lines" <<'PY'
import sys
sys.path.insert(0, "src")
from code_forge.verify import _excerpt_covered
n = len(_excerpt_covered({"code_excerpts": [
    {"file":"f.py","start_line":1,"end_line":1000,"content":"a\nb\nc"}]}))
if n == 3: sys.exit(0)
print("credited %d lines, expected 3" % n); sys.exit(1)
PY

head_ "H. suite"
suite=$(python3 -m pytest -q 2>&1 | tail -3)
printf '%s\n' "$suite" | sed 's/^/        /'
printf '%s' "$suite" | grep -qE '[0-9]+ passed' \
  && ! printf '%s' "$suite" | grep -qE 'failed|error' \
  && ok "suite green" || bad "suite not green"

printf '\n== TOTAL: %d pass, %d fail ==\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
