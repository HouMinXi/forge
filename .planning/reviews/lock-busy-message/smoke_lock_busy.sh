#!/usr/bin/env bash
# Real-path smoke for the busy-lock message: hold the lock with a live
# process, run the real CLI, capture what a user actually sees.
#
# Not a unit test -- it exercises the whole CLI down to the lock, which
# is the layer the unit tests mock out.
set -u
WS=/tmp/lockdemo
rm -rf "$WS" 2>/dev/null
mkdir -p "$WS/.code-forge"
cd "$WS" || exit 1

git init -q .
git config user.email houminxi@gmail.com
git config user.name "Minxi Hou"
printf 'def f():\n    return 1\n' > a.py
git add a.py
git commit -qm "base"
printf 'def f():\n    return 2\n' > a.py

cat > .code-forge/gate.yaml <<'YAML'
outlet: subprocess
backends:
  mock-local:
    type: api
    format: openai
    base_url: "http://127.0.0.1:1"
    api_key_env: FORGE_MOCK_KEY
    model: mock-1
    default: true
YAML

# A live holder: a sleeper whose PID goes into the lock file.
# FORGE_ALLOW_MAIN is needed because forge refuses to review a main tree
# and this scratch repo has no worktree.
sleep 300 &
HOLDER=$!
printf '%d\n' "$HOLDER" > .code-forge/code-forge.lock
echo "holder pid: $HOLDER (alive: $(kill -0 "$HOLDER" 2>/dev/null && echo yes || echo no))"

echo "----------------- what the user sees -----------------"
FORGE_ALLOW_MAIN=1 FORGE_MOCK_KEY=x timeout 120 code-forge --mode ci a.py 2>/tmp/lockdemo.err >/dev/null
rc=$?
tail -20 /tmp/lockdemo.err
echo "------------------------------------------------------"
echo "code-forge exit code: $rc (3 = EXIT_BUSY)"

kill "$HOLDER" 2>/dev/null
wait "$HOLDER" 2>/dev/null
# The sleeper is killed on purpose; its 143 must not become this
# script's exit status.
[ "$rc" -eq 3 ] && exit 0 || exit 1
