#!/bin/bash
# Review the staged receipt-timestamp delivery in .worktrees/fix-receipt-ts
# using the FIXED forge code from .worktrees/mutation-gate.
#
# The editable install pins code_forge to the main tree, which carries
# neither the mutation daemon fix nor the unusable-gate-config fix. Without
# the PYTHONPATH pin below, this review would run the very code whose
# defects made earlier verdicts meaningless.
#
# Everything before the review call is provenance: it records what engine,
# what diff, and what backend produced the verdict, so the result can be
# judged later without trusting this script's own narration.

set -uo pipefail

ENGINE=/home/houminxi/code/forge/.worktrees/mutation-gate/src
TARGET=/home/houminxi/code/forge/.worktrees/fix-receipt-ts
OUT=/home/houminxi/code/forge/.planning/reviews/fix-receipt-ts/panel-local-r2

export PYTHONPATH="$ENGINE"
cd "$TARGET" || exit 1

{
  echo "=== engine provenance ==="
  python3 -c "
import inspect, code_forge, code_forge.machine as m
print('code_forge loaded from:', code_forge.__file__)
src = inspect.getsource(m.StateMachine._run_ci)
print('carries daemon=False        :', 'daemon=False' in src)
print('carries unusable-config log :', 'test.command not configured' in src)
"
  ENGINE_MD5=$(md5sum "$ENGINE/code_forge/machine.py" | cut -d' ' -f1)
  echo "engine machine.py md5: $ENGINE_MD5"

  echo
  echo "=== target under review ==="
  echo "worktree: $TARGET"
  echo "branch:   $(git rev-parse --abbrev-ref HEAD)"
  echo "HEAD:     $(git rev-parse HEAD)"
  echo "staged diffstat:"
  git diff --cached --stat
  echo "staged diff sha256: $(git diff --cached | sha256sum | cut -d' ' -f1)"

  echo
  echo "=== gate config ==="
  echo "gate.yaml md5: $(md5sum .code-forge/gate.yaml | cut -d' ' -f1)"
  python3 -c "
from code_forge.gate_check import load_gate_config
c = load_gate_config('.code-forge/gate.yaml')
print('test.command:', c['test']['command'])
"

  echo
  echo "=== backend liveness ==="
  if [ -z "${OMNIROUTE_API_KEY:-}" ]; then
    echo "FATAL: OMNIROUTE_API_KEY unset -- a missing backend can degrade to a"
    echo "silent fallback and produce a meaningless PASS. Refusing to run."
    exit 2
  fi
  curl -sk -o /tmp/panel_models.json -w "models endpoint http=%{http_code}\n" \
    --max-time 10 -H "Authorization: Bearer $OMNIROUTE_API_KEY" \
    https://192.168.100.10:20128/v1/models
  python3 -c "
import json
d = json.load(open('/tmp/panel_models.json'))
ids = [m['id'] for m in d.get('data', [])]
print('target model onmi-gemini3.6 present:', 'onmi-gemini3.6' in ids)
"
} 2>&1 | tee "$OUT/provenance.txt"

echo
echo "=== review starts $(date -Is) ==="

# --mode local is explicit: this shell is not a TTY, so the default would be
# ci, which is single-round by design and can never accumulate the three
# consecutive clean rounds the delivery gate asks for.
# --outlet subprocess is explicit: the inline outlet returns PASS without
# running any pass at all.
code-forge review \
  --mode local \
  --baseline HEAD \
  --head INDEX \
  --outlet subprocess \
  --backend gemini-omniroute \
  --falsification-engine real \
  --max-total-rounds 20 \
  2>&1 | tee "$OUT/review-stdout.txt"

RC=${PIPESTATUS[0]}
echo "=== review ended $(date -Is) rc=$RC ==="

# Collect the state the verdict was written from. These are what get read
# back to decide whether a gate actually ran, rather than the stdout summary.
for f in state.json advisory-findings.json mutation-result.json; do
  [ -f ".code-forge/$f" ] && cp ".code-forge/$f" "$OUT/$f"
done
cp -r .code-forge/receipts "$OUT/" 2>/dev/null
git diff --cached > "$OUT/reviewed-diff.patch"

echo "rc=$RC" > "$OUT/exit-code.txt"
echo "artifacts in $OUT:"
ls -la "$OUT"
exit "$RC"
