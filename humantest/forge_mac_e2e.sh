#!/bin/bash
# forge macOS customer E2E test
# ==============================
# Full-flow validation on a customer Mac, no scp and no API keys needed:
#   clone -> install -> unit tests -> CLI smoke -> mock-file code diagnosis
#
# Usage (customer runs exactly one command):
#   bash forge_mac_e2e.sh
#
# Optional overrides:
#   FORGE_REPO_URL=https://github.com/HouMinXi/forge.git
#   FORGE_BRANCH=main            (set to the branch under test)
#
# Prerequisites on the Mac:
#   - git
#   - Python 3.12+ (brew install python@3.13 if missing)
#   - network access to github.com and pypi.org
#
# Everything runs inside a fresh temp directory. Nothing outside it is
# touched. When done, send back the single report file whose path is
# printed at the end.
#
# Compatible with the stock macOS /bin/bash 3.2.

set -u

REPO_URL="${FORGE_REPO_URL:-https://github.com/HouMinXi/forge.git}"
BRANCH="${FORGE_BRANCH:-main}"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/forge-e2e.XXXXXX")"
REPORT="$WORKDIR/forge_mac_e2e_report.txt"
SRC="$WORKDIR/forge"
DEMO="$WORKDIR/mock-demo"
VENV="$WORKDIR/venv"
MOCK_PIDS=""

# Mirror everything to the report file.
exec > >(tee "$REPORT") 2>&1

# shellcheck disable=SC2329  # invoked indirectly via trap
cleanup() {
    for pid in $MOCK_PIDS; do
        kill "$pid" >/dev/null 2>&1
    done
}
trap cleanup EXIT

# --- step bookkeeping (bash 3.2: plain indexed arrays) ---------------
STEP_NAMES=()
STEP_RESULTS=()
FAILED=0

record() {  # record <PASS|FAIL|INFO> <name>
    STEP_RESULTS+=("$1")
    STEP_NAMES+=("$2")
    if [ "$1" = "FAIL" ]; then
        FAILED=1
    fi
    echo ""
    echo "==> [$1] $2"
    echo ""
}

banner() {
    echo ""
    echo "======================================================="
    echo "  $1"
    echo "======================================================="
}

# ---------------------------------------------------------------------
banner "S0: environment report"
uname -a
if command -v sw_vers >/dev/null 2>&1; then sw_vers; fi
echo "locale: ${LANG:-unset} / ${LC_ALL:-unset}"
git --version 2>&1 || true
echo "workdir: $WORKDIR"
record INFO "S0 environment report"

# ---------------------------------------------------------------------
banner "S1: prerequisites (git + Python >= 3.12)"
PYBIN=""
for cand in python3.14 python3.13 python3.12 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
            >/dev/null 2>&1; then
            PYBIN="$cand"
            break
        fi
    fi
done
if [ -z "$PYBIN" ] || ! command -v git >/dev/null 2>&1; then
    echo "MISSING PREREQUISITE:"
    echo "  git found:        $(command -v git || echo NO)"
    echo "  python >= 3.12:   ${PYBIN:-NOT FOUND (try: brew install python@3.13)}"
    record FAIL "S1 prerequisites"
    echo "Cannot continue. Report file: $REPORT"
    exit 1
fi
echo "using python: $PYBIN ($("$PYBIN" --version 2>&1))"
record PASS "S1 prerequisites"

# ---------------------------------------------------------------------
banner "S2: git clone $REPO_URL ($BRANCH)"
if git clone --branch "$BRANCH" "$REPO_URL" "$SRC"; then
    (cd "$SRC" && git log --oneline -1)
    record PASS "S2 clone"
else
    record FAIL "S2 clone"
    echo "Report file: $REPORT"
    exit 1
fi

# ---------------------------------------------------------------------
banner "S3: virtualenv + install (forge + test dependencies)"
# pytest-asyncio: the mcp/lock/sampling tests are async (@pytest.mark.asyncio).
# code-review-graph: pulls tree-sitter-language-pack; without it the
#   cross-repo-impact and dead-code tests fail instead of skipping.
if "$PYBIN" -m venv "$VENV" \
    && "$VENV/bin/python" -m pip install --quiet --upgrade pip \
    && (cd "$SRC" && "$VENV/bin/python" -m pip install --quiet -e ".[mcp,vertex]") \
    && "$VENV/bin/python" -m pip install --quiet \
        pytest pytest-asyncio jsonschema ruff code-review-graph; then
    "$VENV/bin/code-forge" --version
    record PASS "S3 install"
else
    record FAIL "S3 install"
    echo "Report file: $REPORT"
    exit 1
fi

# ---------------------------------------------------------------------
banner "S4: unit test suite (takes ~5-10 minutes)"
# 21 tests in these two classes invoke the external 'claude' CLI binary.
# On machines without it they fail for environmental (not code) reasons,
# so they are deselected when the binary is absent.
#   with claude on PATH:    expect 2734 passed, 8 skipped (main branch)
#   without claude on PATH: expect 2713 passed, 8 skipped
PYTEST_ARGS=""
if command -v claude >/dev/null 2>&1; then
    echo "claude CLI found on PATH -- running the FULL suite"
else
    echo "claude CLI not found -- deselecting the 21 claude-CLI-dependent tests"
    PYTEST_ARGS="--deselect tests/test_llm_invoke.py::TestLLMInvoke --deselect tests/test_llm_invoke.py::TestSubprocessCleanup"
fi
# shellcheck disable=SC2086  # PYTEST_ARGS is intentionally word-split
if (cd "$SRC" && "$VENV/bin/python" -B -m pytest tests/ -q $PYTEST_ARGS); then
    record PASS "S4 unit tests"
else
    record FAIL "S4 unit tests"
fi

# ---------------------------------------------------------------------
banner "S5: CLI smoke in a fresh demo repo (init / doctor / detect)"
mkdir -p "$DEMO"
cd "$DEMO" || { echo "FATAL: cannot enter $DEMO"; exit 1; }
git init -q .
# Docstrings matter: forge's L0 layer runs any system linters found by
# 'detect' (pylint flags missing docstrings as confirmed findings).
printf '%s\n' \
    '"""Mock target for forge e2e diagnosis."""' \
    '' \
    '' \
    'def greet(name):' \
    '    """Return a greeting for name."""' \
    '    return "hello " + name' \
    > mock_target.py
# Ignore forge's own artifacts: the coverage axis enumerates untracked
# files via 'git ls-files --others --exclude-standard', so without this
# every .code-forge/ file (gate.yaml, state.json, receipts) is flagged
# "no review layer examined this file" and CI fails on coverage gaps.
printf '.code-forge/\n' > .gitignore
git add mock_target.py .gitignore
git -c user.name="forge-e2e" -c user.email="forge-e2e@example.com" \
    commit -q -m "seed mock target"

if "$VENV/bin/code-forge" init; then
    record PASS "S5a code-forge init"
else
    record FAIL "S5a code-forge init"
fi

# doctor exits non-zero whenever anything is FAIL or SKIP; with no real
# backend configured that is expected, so this step is informational.
"$VENV/bin/code-forge" doctor || true
record INFO "S5b code-forge doctor (informational; non-zero expected without a real backend)"

"$VENV/bin/code-forge" detect || true
record INFO "S5c code-forge detect (informational)"

# ---------------------------------------------------------------------
banner "S6: mock file -> code diagnosis (mock LLM backend, CLEAN scenario)"
# Append a new function to the committed file: this working-tree change
# is the diff that forge reviews.
printf '%s\n' \
    '' \
    '' \
    'def divide(a, b):' \
    '    """Return a divided by b."""' \
    '    return a / b' \
    >> mock_target.py

# Local mock LLM server speaking the Anthropic messages format.
cat > "$WORKDIR/mock_server.py" <<'MOCKPY'
"""Minimal mock of an Anthropic-format LLM endpoint for forge e2e."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

resp_file, port = sys.argv[1], int(sys.argv[2])
with open(resp_file, encoding="utf-8") as f:
    reviewer_json = f.read()
body = json.dumps({
    "content": [{"type": "text", "text": reviewer_json}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 10},
}).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _reply
    do_GET = _reply

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
MOCKPY

free_port() {
    "$VENV/bin/python" -c \
        'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

write_gate() {  # write_gate <port>
    mkdir -p .code-forge
    cat > .code-forge/gate.yaml <<GATEEOF
outlet: subprocess
backends:
  mock-local:
    type: api
    format: anthropic
    base_url: http://127.0.0.1:$1
    api_key_env: FORGE_MOCK_KEY
    model: mock-1
    max_tokens: 16384
    default: true
GATEEOF
    # Repo-supplied backends are untrusted until 'code-forge trust' records
    # the gate.yaml hash; re-trust after every gate.yaml rewrite.
    "$VENV/bin/code-forge" trust
}

export FORGE_MOCK_KEY="mock-key-for-e2e"

# Scenario 1: reviewer returns zero findings -> verdict PASS (exit 0).
cat > "$WORKDIR/reviewer_clean.json" <<'RJSON'
{"findings": [], "code_excerpts": [{"file": "mock_target.py", "start_line": 9, "end_line": 11, "content": "def divide(a, b):\n    \"\"\"Return a divided by b.\"\"\"\n    return a / b"}]}
RJSON

PORT1="$(free_port)"
"$VENV/bin/python" "$WORKDIR/mock_server.py" "$WORKDIR/reviewer_clean.json" "$PORT1" &
MOCK_PIDS="$MOCK_PIDS $!"
sleep 1
write_gate "$PORT1"

# --allow-main: forge refuses main-tree reviews by default (worktree
# discipline); the demo repo is a throwaway main tree, so opt out.
# NOTE: no --falsification-engine stub here. The stub engine disables the
# whole L1 semantic layer (l1_active = engine != "stub"), which would mean
# the mock backend is never consulted -- the exact path this step tests.
if "$VENV/bin/code-forge" review --mode ci \
        --allow-main --backend mock-local > "$WORKDIR/diag_clean.log" 2>&1; then
    echo "clean scenario: exit 0 (PASS verdict) as expected"
    tail -5 "$WORKDIR/diag_clean.log"
    record PASS "S6 mock diagnosis: clean change -> PASS verdict"
else
    RC=$?
    echo "clean scenario: unexpected exit $RC; last 40 lines:"
    tail -40 "$WORKDIR/diag_clean.log"
    record FAIL "S6 mock diagnosis: clean change -> PASS verdict"
fi

# ---------------------------------------------------------------------
banner "S7: mock file -> code diagnosis (L1 finding surfaced as UNCERTAIN)"
# Scenario 2: the mock reviewer reports one P2 finding. CI semantics:
# L1 (LLM) findings enter as UNCERTAIN and are left for human
# disposition -- the CI verdict fails only on CONFIRMED findings
# (deterministic L0 linters / infra errors) or coverage gaps
# (machine.py: verdict = FAIL if confirmed > 0 or coverage_gaps > 0).
# So the expectation here is exit 0 WITH the finding present in the
# report: source L1, disposition UNCERTAIN.
cat > "$WORKDIR/reviewer_finding.json" <<'RJSON'
{"findings": [{"file": "mock_target.py", "line": 11, "severity": "P2", "description": "divide() lacks a zero-divisor guard: b == 0 raises ZeroDivisionError to the caller"}], "code_excerpts": [{"file": "mock_target.py", "start_line": 9, "end_line": 11, "content": "def divide(a, b):\n    \"\"\"Return a divided by b.\"\"\"\n    return a / b"}]}
RJSON

PORT2="$(free_port)"
"$VENV/bin/python" "$WORKDIR/mock_server.py" "$WORKDIR/reviewer_finding.json" "$PORT2" &
MOCK_PIDS="$MOCK_PIDS $!"
sleep 1
write_gate "$PORT2"

"$VENV/bin/code-forge" review --mode ci \
    --allow-main --backend mock-local > "$WORKDIR/diag_finding.log" 2>&1
RC=$?
if [ "$RC" -eq 0 ] \
        && grep -q "ZeroDivisionError" "$WORKDIR/diag_finding.log" \
        && grep -q '"source": "L1"' "$WORKDIR/diag_finding.log" \
        && grep -q "uncertain=1" "$WORKDIR/diag_finding.log"; then
    echo "finding scenario: exit 0 and the L1 finding is in the report as UNCERTAIN:"
    grep -n "ZeroDivisionError" "$WORKDIR/diag_finding.log" | head -3
    record PASS "S7 mock diagnosis: L1 finding surfaced (UNCERTAIN, human disposition)"
else
    echo "finding scenario: exit $RC (expected 0 + L1 finding in report); last 40 lines:"
    tail -40 "$WORKDIR/diag_finding.log"
    record FAIL "S7 mock diagnosis: L1 finding surfaced (UNCERTAIN, human disposition)"
fi

# ---------------------------------------------------------------------
banner "S7b: deterministic L0 defect -> CONFIRMED -> FAIL verdict (exit 1)"
# The FAIL path is owned by the deterministic L0 layer: an undefined
# name is confirmed by every installed linter (pylint E0602, ruff and
# flake8 F821) with no LLM involved, so CI must exit 1.
printf '%s\n' \
    '' \
    '' \
    'def use_undefined():' \
    '    """Trigger a deterministic L0 linter finding."""' \
    '    return undefined_name' \
    >> mock_target.py

PORT3="$(free_port)"
"$VENV/bin/python" "$WORKDIR/mock_server.py" "$WORKDIR/reviewer_clean.json" "$PORT3" &
MOCK_PIDS="$MOCK_PIDS $!"
sleep 1
write_gate "$PORT3"

"$VENV/bin/code-forge" review --mode ci \
    --allow-main --backend mock-local > "$WORKDIR/diag_l0.log" 2>&1
RC=$?
if [ "$RC" -eq 1 ] \
        && grep -q "undefined" "$WORKDIR/diag_l0.log" \
        && grep -Eq "confirmed=[1-9]" "$WORKDIR/diag_l0.log"; then
    echo "L0 scenario: exit 1 (FAIL verdict) with confirmed linter finding(s):"
    grep -o 'FAIL findings=[0-9]* confirmed=[0-9]* uncertain=[0-9]*' \
        "$WORKDIR/diag_l0.log" | head -1
    record PASS "S7b L0 defect: undefined name -> CONFIRMED -> FAIL verdict"
else
    echo "L0 scenario: exit $RC (expected 1); last 40 lines:"
    tail -40 "$WORKDIR/diag_l0.log"
    record FAIL "S7b L0 defect: undefined name -> CONFIRMED -> FAIL verdict"
fi

# ---------------------------------------------------------------------
banner "S8: OPTIONAL real-backend diagnosis (runs only if a key is provided)"
# To enable, the tester exports a key (shared out-of-band, never stored
# in any file) before running:
#   export FORGE_REAL_API_KEY=sk-...
# Optional overrides (defaults target Xiaomi MiMo UltraSpeed):
#   export FORGE_REAL_BASE_URL=https://api.xiaomimimo.com/anthropic
#   export FORGE_REAL_MODEL=mimo-v2.5-pro-ultraspeed
#   export FORGE_REAL_FORMAT=anthropic
# The working tree still carries the S7b undefined-name defect, so the
# expected verdict is FAIL (exit 1) driven by the deterministic L0 layer;
# what the real model adds on top is not deterministic. The assertion is
# therefore mechanical: exit code must be a verdict (0 PASS / 1 FAIL),
# not an infra error (2+). Transient 429s are retried by forge itself.
if [ -n "${FORGE_REAL_API_KEY:-}" ]; then
    REAL_URL="${FORGE_REAL_BASE_URL:-https://api.xiaomimimo.com/anthropic}"
    REAL_MODEL="${FORGE_REAL_MODEL:-mimo-v2.5-pro-ultraspeed}"
    REAL_FORMAT="${FORGE_REAL_FORMAT:-anthropic}"
    echo "real backend: $REAL_URL ($REAL_MODEL, $REAL_FORMAT)"
    mkdir -p .code-forge
    cat > .code-forge/gate.yaml <<GATEEOF
outlet: subprocess
backends:
  real-remote:
    type: api
    format: $REAL_FORMAT
    base_url: $REAL_URL
    api_key_env: FORGE_REAL_API_KEY
    model: $REAL_MODEL
    max_tokens: 16384
    default: true
GATEEOF
    "$VENV/bin/code-forge" trust
    "$VENV/bin/code-forge" review --mode ci --allow-main \
        --backend real-remote > "$WORKDIR/diag_real.log" 2>&1
    RC=$?
    echo "real-backend review exit: $RC; last 10 lines:"
    tail -10 "$WORKDIR/diag_real.log"
    if [ "$RC" -le 1 ]; then
        record PASS "S8 real-backend diagnosis reached a verdict (exit $RC)"
    else
        record FAIL "S8 real-backend diagnosis infra error (exit $RC)"
    fi
else
    echo "FORGE_REAL_API_KEY not set -- skipping real-backend scenario"
    record INFO "S8 real-backend diagnosis skipped (no key provided)"
fi

# ---------------------------------------------------------------------
banner "SUMMARY"
i=0
while [ "$i" -lt "${#STEP_NAMES[@]}" ]; do
    printf '  %-4s %s\n' "${STEP_RESULTS[$i]}" "${STEP_NAMES[$i]}"
    i=$((i + 1))
done
echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "OVERALL: PASS"
else
    echo "OVERALL: FAIL (see steps above)"
fi
echo ""
echo "Please send back this single file:"
echo "  $REPORT"
echo "(supporting logs are in $WORKDIR)"
exit "$FAILED"
