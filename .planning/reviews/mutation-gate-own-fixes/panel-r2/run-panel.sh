#!/bin/bash
# Review the mutation-gate branch's own 3 staged fixes (daemon=False,
# unusable-config infra_error, l2_runner wiring) in .worktrees/mutation-gate.
#
# Deliberately NO PYTHONPATH override: the engine is the main tree's stock
# forge. The circularity that forced the override for fix-receipt-ts is
# specific to receipt/verify timestamp code reviewing itself (memory
# feedback_gate_yaml_split_brain.md sections 4-5). None of the three fixes
# under review here are on that path, so stock forge reviews them cleanly.
#
# Note what this costs: stock forge does NOT carry the l2_runner wiring, so
# this review's own mutation gate is the silent no-op the fix removes. That
# is a known, accepted limitation of reviewing the fix with the unfixed
# tool -- the fix's evidence is bug-injection, not this review.
#
# Everything before the review call is provenance: what engine, what diff,
# what backend produced the verdict, so the result can be judged later
# without trusting this script's own narration.

set -uo pipefail

TARGET=/home/houminxi/code/forge/.worktrees/mutation-gate
OUT=/home/houminxi/code/forge/.planning/reviews/mutation-gate-own-fixes/panel-r2

cd "$TARGET" || exit 1

{
  echo "=== engine provenance (stock main tree, no PYTHONPATH) ==="
  env -u PYTHONPATH python3 -c "
import inspect, code_forge, code_forge.machine as m
print('code_forge loaded from:', code_forge.__file__)
src = inspect.getsource(m.StateMachine._run_ci)
print('engine carries daemon=False        :', 'daemon=False' in src)
print('engine carries unusable-config log :', 'test.command not configured' in src)
import code_forge.cli as c
print('engine carries l2_runner wiring    :', 'l2_runner' in inspect.getsource(c._run_hold_loop))
"
  echo "engine machine.py md5: $(md5sum /home/houminxi/code/forge/src/code_forge/machine.py | cut -d' ' -f1)"
  echo "engine cli.py md5:     $(md5sum /home/houminxi/code/forge/src/code_forge/cli.py | cut -d' ' -f1)"

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
  env -u PYTHONPATH python3 -c "
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
  curl -sk -o /tmp/panel_models_mg.json -w "models endpoint http=%{http_code}\n" \
    --max-time 10 -H "Authorization: Bearer $OMNIROUTE_API_KEY" \
    https://192.168.100.10:20128/v1/models
  python3 -c "
import json
d = json.load(open('/tmp/panel_models_mg.json'))
ids = [m['id'] for m in d.get('data', [])]
print('target model onmi-gemini3.6 present:', 'onmi-gemini3.6' in ids)
"
} 2>&1 | tee "$OUT/provenance.txt"

echo
echo "=== review starts $(date -Is) ==="

# --mode local: this shell is not a TTY, so the default would be ci, which is
# single-round by design and can never accumulate three consecutive clean
# rounds. --outlet subprocess: the inline outlet returns PASS without running
# any pass at all.
env -u PYTHONPATH code-forge review \
  --mode local \
  --baseline HEAD \
  --head INDEX \
  --outlet subprocess \
  --backend gemini-pro \
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
