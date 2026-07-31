#!/usr/bin/env bash
# EXPERIMENT 2 -- can a CENTRAL ledger work, and what does it cost?
#
# Exp1 proved a row dies with its worktree. This asks the follow-on
# questions before any code is written:
#   Q1 does forge honour an env var / config for the ledger path today?
#   Q2 does repo_root actually distinguish sources in one shared file?
#   Q3 does a central file survive a worktree removal?
#   Q4 is concurrent append from two repos safe (14 projects share it)?
#
# Scratch dirs only. Touches no real project and no real ledger.
set -u
S=/tmp/ledger_exp2
rm -rf "$S" 2>/dev/null
mkdir -p "$S" && cd "$S" || exit 1

say() { echo; echo "### $*"; }
mkrepo() {
  git init -q "$1" && cd "$1" || return 1
  git config user.email houminxi@gmail.com
  git config user.name "Minxi Hou"
  printf 'x = 1\n' > a.py
  git add a.py && git commit -qm base
  cd "$S" || return 1
}

say "Q1: is there ANY existing path override? (env var or config)"
# If forge already supports this, the whole change is configuration.
grep -rnE 'FORGE_LEDGER|LEDGER_PATH|ledger_path|ledger_dir' \
  /home/houminxi/code/forge/src/code_forge/ 2>/dev/null \
  | grep -v '\.pyc' | sed 's/^/  /'
echo "  (only _ledger_path definition/uses above = no override exists)"

say "Q2 + Q3 + Q4 SETUP: two repos, one with a worktree"
mkrepo repo-a >/dev/null 2>&1
mkrepo repo-b >/dev/null 2>&1
cd "$S/repo-a" && git worktree add -q ../wt-a -b feat 2>&1 | tail -1
cd "$S" || exit 1
echo "  repo-a, repo-b, and wt-a (a worktree of repo-a) created"

say "Q2: write from three different places, see what repo_root records"
for loc in repo-a repo-b wt-a; do
  out=$(cd "$S/$loc" && code-forge ledger mark "esc-from-$loc" ESCAPED --new 2>&1)
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  !!! write from $loc FAILED (exit $rc): $out"
    echo "  !!! everything below measures nothing. Stopping."
    exit 2
  fi
  echo "  wrote from $loc -> $(cd "$S/$loc" && pwd)"
done

echo
echo "  where the rows actually landed:"
find "$S" -name ledger.jsonl | while read -r f; do
  echo "    $f  ($(wc -l < "$f") row/s)"
done

echo
echo "  repo_root recorded in each row:"
find "$S" -name ledger.jsonl -exec cat {} \; \
  | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    print('    %-22s repo_root=%s' % (d['fingerprint'], d['repo_root']))
"

say "Q3: simulate a CENTRAL store -- one file, all three sources"
CENTRAL="$S/central-ledger.jsonl"
find "$S" -name ledger.jsonl -exec cat {} \; > "$CENTRAL"
echo "  merged into $CENTRAL: $(wc -l < "$CENTRAL") rows"
echo "  distinct repo_root values:"
python3 -c "
import json
seen = {}
for line in open('$CENTRAL'):
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    seen.setdefault(d['repo_root'], 0)
    seen[d['repo_root']] += 1
for k, v in sorted(seen.items()):
    print('    %d row/s  %s' % (v, k))
"

say "Q3b: now remove the worktree -- does the CENTRAL copy survive?"
cd "$S/repo-a" && git worktree remove --force ../wt-a 2>&1 | tail -1
echo "  per-repo file for wt-a: $([ -f "$S/wt-a/.code-forge/ledger.jsonl" ] && echo PRESENT || echo GONE)"
echo "  central file:           $([ -f "$CENTRAL" ] && echo "PRESENT ($(wc -l < "$CENTRAL") rows)" || echo GONE)"
echo "  wt-a's row in central:  $(grep -c 'esc-from-wt-a' "$CENTRAL") occurrence/s"

say "Q4: is append_row atomic enough for concurrent writers?"
# 14 projects could write at once. Look at HOW it writes before trusting it.
sed -n '62,78p' /home/houminxi/code/forge/src/code_forge/ledger.py | sed 's/^/  /'

echo
echo "=== EXPERIMENT 2 COMPLETE ==="
