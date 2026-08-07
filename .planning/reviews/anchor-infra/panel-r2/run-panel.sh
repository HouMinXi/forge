#!/bin/bash
# Review the infra-anchor fix in .worktrees/anchor-infra: write_receipts now
# leaves INFRA findings out of the anchor list, so a failed backend call no
# longer makes a run permanently unattestable.
#
# PYTHONPATH IS pinned to the worktree under review, unlike the mutation-gate
# panel. The change is on receipt/verify's own path, so the engine writing
# this review's receipts is the code being reviewed. That is the same
# circularity the fix-receipt-ts panel had to handle (memory
# feedback_gate_yaml_split_brain.md sections 4-5), and pinning is the way it
# was handled: the fixed writer protects this run's own receipts, which is
# precisely what the fix claims to do. Running stock forge here would mean a
# backend hiccup mid-review poisons the very receipts meant to attest the
# fix for that poisoning.
#
# Backend is deepseek rather than the Flash default that died on the last
# panel, or the Pro route that 502'd under 35K-token concurrent load. This
# diff is ~1590 tokens, so neither failure mode's precondition holds; the
# round cap is 12 rather than 20 because deepseek is documented to oscillate
# past round 3 and a higher cap only buys more oscillation.
#
# Everything before the review call is provenance: what engine, what diff,
# what backend produced the verdict, so it can be judged later without
# trusting this script's narration.

set -uo pipefail

TARGET=/home/houminxi/code/forge/.worktrees/anchor-infra
OUT=/home/houminxi/code/forge/.planning/reviews/anchor-infra/panel-r2

cd "$TARGET" || exit 1
mkdir -p "$OUT"

{
  echo "=== engine provenance (PINNED to the worktree under review) ==="
  PYTHONPATH="$TARGET/src" python3 -c "
import inspect, code_forge, code_forge.receipt as r
print('code_forge loaded from:', code_forge.__file__)
src = inspect.getsource(r.write_receipts)
print('engine carries the anchor filter:', 'if f.source != \"INFRA\"' in src)
"
  echo "engine receipt.py md5: $(md5sum "$TARGET/src/code_forge/receipt.py" | cut -d' ' -f1)"

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

  echo
  echo "=== backend liveness ==="
  # deepseek is a direct api backend, not an OmniRoute route: it reads
  # DEEPSEEK_API_KEY and talks to api.deepseek.com. Probing the OmniRoute
  # models endpoint here would report the model absent and prove nothing.
  if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "FATAL: DEEPSEEK_API_KEY unset -- a missing backend can degrade to a"
    echo "silent fallback and produce a meaningless PASS. Refusing to run."
    exit 2
  fi
  curl -s -o /tmp/panel_models_ai.json -w "models endpoint http=%{http_code}\n" \
    --max-time 15 -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
    https://api.deepseek.com/v1/models
  python3 -c "
import json
try:
    d = json.load(open('/tmp/panel_models_ai.json'))
except Exception as e:
    print('models endpoint unreadable:', e); raise SystemExit(0)
ids = [m.get('id') for m in d.get('data', [])]
print('models offered:', ids)
print('target model deepseek-v4-flash present:', 'deepseek-v4-flash' in ids)
"
} 2>&1 | tee "$OUT/provenance.txt"

echo
echo "=== review starts $(date -Is) ==="

# --mode local: this shell is not a TTY, so the default would be ci, which is
# single-round by design and can never accumulate three consecutive clean
# rounds. --outlet subprocess: the inline outlet returns PASS without running
# any pass at all.
PYTHONPATH="$TARGET/src" code-forge review \
  --mode local \
  --baseline HEAD \
  --head INDEX \
  --outlet subprocess \
  --backend deepseek \
  --falsification-engine real \
  --max-total-rounds 12 \
  2>&1 | tee "$OUT/review-stdout.txt"

RC=${PIPESTATUS[0]}
echo "=== review ended $(date -Is) rc=$RC ==="

for f in state.json advisory-findings.json mutation-result.json; do
  [ -f ".code-forge/$f" ] && cp ".code-forge/$f" "$OUT/$f"
done
cp -r .code-forge/receipts "$OUT/" 2>/dev/null
git diff --cached > "$OUT/reviewed-diff.patch"

echo "rc=$RC" > "$OUT/exit-code.txt"
echo "artifacts in $OUT:"
ls -la "$OUT"
exit "$RC"
