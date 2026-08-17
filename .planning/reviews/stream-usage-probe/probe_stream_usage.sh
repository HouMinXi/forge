#!/usr/bin/env bash
# Does an OpenAI-compatible SSE stream carry usage without stream_options?
#
# Three arms against the same backend and prompt:
#   A  stream:true,  no stream_options      <- what forge sends today
#   B  stream:true,  include_usage:true     <- what fix/stream-usage adds
#   C  stream:false                         <- control, usage must be present
#
# Prints, per arm, whether any SSE chunk (or the JSON body for C) carried a
# usage object with nonzero counts. Reports what it saw; draws no conclusion.
set -uo pipefail

URL="https://192.168.100.10:20128/v1/chat/completions"
MODEL="${1:-oc-ds-flash-free}"
PROMPT='Reply with exactly: ok'

if [ -z "${OMNIROUTE_API_KEY:-}" ]; then
  echo "OMNIROUTE_API_KEY not set" >&2
  exit 1
fi

probe() {
  local arm="$1" body="$2" out
  out=$(curl -sk -m 120 "$URL" \
    -H "Authorization: Bearer ${OMNIROUTE_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$body" 2>&1)

  # Pull every usage object the response carried, streamed or not.
  local usage
  usage=$(printf '%s' "$out" \
    | sed -n 's/^data: //p' \
    | grep -v '^\[DONE\]$' \
    | python3 -c '
import sys, json
seen = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except Exception:
        continue
    u = d.get("usage")
    if u:
        seen.append(u)
print(json.dumps(seen[-1]) if seen else "")
' 2>/dev/null)

  # Arm C is a plain JSON body, not SSE.
  if [ -z "$usage" ]; then
    usage=$(printf '%s' "$out" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
u = d.get("usage")
print(json.dumps(u) if u else "")
' 2>/dev/null)
  fi

  local chunks
  chunks=$(printf '%s' "$out" | grep -c '^data: ' || true)

  if [ -n "$usage" ]; then
    printf '  %-46s SSE-chunks=%-4s usage=%s\n' "$arm" "$chunks" "$usage"
  else
    printf '  %-46s SSE-chunks=%-4s usage=ABSENT\n' "$arm" "$chunks"
    printf '     first 160 chars: %s\n' "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-160)"
  fi
}

echo "backend model: $MODEL"
probe "A  stream:true, no stream_options" \
  "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"$PROMPT\"}],\"stream\":true,\"max_tokens\":16}"
probe "B  stream:true, include_usage:true" \
  "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"$PROMPT\"}],\"stream\":true,\"stream_options\":{\"include_usage\":true},\"max_tokens\":16}"
probe "C  stream:false (control)" \
  "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"$PROMPT\"}],\"stream\":false,\"max_tokens\":16}"
