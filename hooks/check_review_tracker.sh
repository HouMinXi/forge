#!/bin/bash
# Review tracker hook (B2+C1)
#
# PostToolUse on Bash  -> detect real qodo execution, parse findings, update state
# PreToolUse  on Edit/Write -> count modifications, enforce 3-round budget
#
# State machine:
#   - qodo_runs: total qodo executions detected (target: 6 per full review)
#   - last_qodo_has_findings: whether the most recent qodo run found issues
#   - mod_since_last_qodo: edits since last qodo run
#   - rounds_with_findings: rounds where qodo found issues after fixes
#   - review_passed: true when last qodo had no P0/P1/P2 findings
#
# Hard stop: after 3 rounds with findings -> block edits, require human intervention
#
# Exit 0 = allow, Exit 2 = block

export CLAUDE_SESSION_PID=${CLAUDE_SESSION_PID:-$PPID}

INPUT=$(cat)
PYFILE=$(mktemp /tmp/hook_py.XXXXXX) || exit 0
trap 'rm -f "$PYFILE"' EXIT
cat >"$PYFILE" <<'PYEOF'
import sys, json, os, re, fcntl, tempfile

_state_dir = os.path.join(os.path.expanduser('~'), '.local', 'state', 'claude')
os.makedirs(_state_dir, mode=0o700, exist_ok=True)
try:
    os.chmod(_state_dir, 0o700)
except OSError:
    pass

_pid = re.sub(r'[^a-zA-Z0-9_-]', '', os.environ.get('CLAUDE_SESSION_PID', 'default'))[:64]
STATE = os.path.join(_state_dir, f"review_tracker_{_pid}.json")

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool = data.get('tool_name', '')
tinput = data.get('tool_input', {})
tresult = data.get('tool_result', '')

# Normalize tool_result to string
if isinstance(tresult, dict):
    tresult = json.dumps(tresult, ensure_ascii=False)
elif isinstance(tresult, list):
    tresult = '\n'.join(str(x) for x in tresult)
elif not isinstance(tresult, str):
    tresult = str(tresult) if tresult else ''


def _load_state():
    try:
        with open(STATE, 'r') as f:
            return json.load(f)
    except Exception:
        return {
            'qodo_runs': 0,
            'last_qodo_has_findings': False,
            'mod_since_last_qodo': 0,
            'rounds_with_findings': 0,
            'review_passed': False,
            'hard_stopped': False,
        }


def _save_state(st):
    dir_name = os.path.dirname(STATE) or '/tmp'
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(st, f)
        os.replace(tmp, STATE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _is_real_qodo(cmd, output):
    """Check if this Bash command was a real qodo review execution."""
    # Must have qodo in command (not just grep/help/config)
    if not re.search(r'\bqodo\b', cmd):
        return False
    # Exclude non-review commands
    if re.search(r'qodo\s+(?:--help|--version|config|login|logout)', cmd):
        return False
    if re.search(r'(?:grep|which|type|command\s+-v|rpm|pip|dnf|yum)\s.*qodo', cmd):
        return False
    # Check for qodo initialization markers
    init_markers = [
        r'Initializing\s+Qodo',
        r'Session\s+ID',
        r'MCP\s+Server',
        r'qodo.*agent',
        r'Qodo\s+Agent',
    ]
    has_init = any(re.search(m, output, re.IGNORECASE) for m in init_markers)
    # Check for review content markers (Chinese review output)
    review_markers = [
        r'(?:审查|检查|分析|总结|结论|评审)',
        r'(?:文件|代码|脚本|函数)',
        r'(?:P[012]|必修|高风险|正确|安全|通过)',
    ]
    has_review = sum(1 for m in review_markers if re.search(m, output)) >= 2
    # Short output without markers is noise (e.g. error messages)
    if len(output) < 500 and not has_init and not has_review:
        return False
    return has_init or has_review


def _has_findings(output):
    """Parse qodo output to determine if it contains P0/P1/P2 findings.

    Returns True if findings detected, False if clean, True if uncertain (conservative).
    """
    # Strong finding signals - if any match, definitely has findings
    finding_signals = [
        # Severity labels
        r'\bP0\b',
        r'\bP1\b',
        r'\bP2\b',
        # Chinese
        r'必修',
        r'高风险',
        r'需要修复',
        r'建议修复',
        r'应该修改',
        r'必须修改',
        r'关键修复',
        r'严重问题',
        r'逻辑错误',
        r'安全问题',
        r'命令注入',
        r'注入.*(?:风险|漏洞)',
        r'(?:风险|漏洞).*注入',
        r'崩溃',
        r'死锁',
        r'(?:必须|需要|应该).*(?:修复|修改|处理)',
        # English
        r'REQUEST_CHANGES',
        r'\bmust\s+fix\b',
        r'\bshould\s+fix\b',
        r'\bcritical\b.*\b(?:issue|bug|error|fix)',
        r'\bhigh\s+risk\b',
        r'\bsecurity\s+(?:issue|vulnerability|risk)',
    ]
    if any(re.search(s, output, re.IGNORECASE) for s in finding_signals):
        return True

    # Clean signals - if any match AND no finding signals, clean
    clean_signals = [
        # Chinese
        r'未发现.*(?:新|明显|额外).*(?:问题|bug|错误)',
        r'没有.*(?:新|剩余|额外).*(?:问题|需要)',
        r'(?:实现|逻辑|代码).*(?:正确|安全)',
        r'均已.*(?:确认|通过|修复)',
        r'符合预期',
        r'符合.*要求',
        r'行为一致',
        r'覆盖完整',
        r'全部通过',
        r'(?:没有|未).*(?:引入|发现).*(?:新|回归)',
        # English
        r'APPROVE',
        r'(?:no|zero)\s+(?:new\s+)?(?:issues?|bugs?|errors?|findings?)\s+found',
        r'(?:all|every)\s+(?:fixes?|checks?)\s+(?:confirmed|passed|correct)',
        r'(?:code|logic|implementation)\s+(?:is\s+)?(?:correct|safe|sound)',
        r'no\s+(?:actionable\s+)?findings',
        r'review\s+(?:passed|approved|clean)',
    ]
    if any(re.search(s, output, re.IGNORECASE) for s in clean_signals):
        return False

    # Ambiguous weak signals - check ratio
    weak_finding = [r'风险', r'错误', r'失败', r'不一致', r'异常', r'问题']
    weak_clean = [r'正确', r'安全', r'通过', r'\bcorrect\b', r'\bsafe\b', r'\bpassed\b']
    f_count = sum(1 for s in weak_finding if re.search(s, output, re.IGNORECASE))
    c_count = sum(1 for s in weak_clean if re.search(s, output, re.IGNORECASE))
    if f_count > 0 and c_count == 0:
        return True
    if c_count > 0 and f_count == 0:
        return False

    # Conservative: unknown = has findings
    return True


# ── PostToolUse on Bash: detect qodo execution ──────────────────────────
if tool == 'Bash':
    cmd = tinput.get('command', '')
    if not _is_real_qodo(cmd, tresult):
        sys.exit(0)

    lock_path = STATE + '.lock'
    with open(lock_path, 'w') as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        st = _load_state()
        st['qodo_runs'] += 1
        had_mods = st['mod_since_last_qodo'] > 0
        st['mod_since_last_qodo'] = 0
        findings = _has_findings(tresult)
        st['last_qodo_has_findings'] = findings
        if findings:
            # Only count as a new failed round if edits happened since last qodo
            if had_mods or st['qodo_runs'] == 1:
                st['rounds_with_findings'] += 1
            st['review_passed'] = False
        else:
            st['review_passed'] = True
        _save_state(st)
        # Capture values for reporting outside the lock
        run_num = st['qodo_runs']
        rounds_num = st['rounds_with_findings']

    if findings:
        print(
            f"REVIEW TRACKER: qodo run #{run_num} detected findings. "
            f"Rounds with findings: {rounds_num}/3.",
            file=sys.stderr
        )
    else:
        print(
            f"REVIEW TRACKER: qodo run #{run_num} passed clean. "
            f"Review status: PASSED.",
            file=sys.stderr
        )
    sys.exit(0)

# ── PreToolUse on Edit/Write: count modifications, enforce budget ────────
if tool in ('Edit', 'Write'):
    fp = tinput.get('file_path', '')
    if not fp:
        sys.exit(0)
    fp = os.path.realpath(fp)

    # Exempt infrastructure files (prefix-based to avoid substring false matches)
    home = os.path.expanduser('~')
    exempt_prefixes = [
        os.path.join(home, '.claude') + os.sep,
        '/tmp/',
    ]
    exempt_segments = {'.git', '__pycache__'}
    if any(fp.startswith(p) for p in exempt_prefixes):
        sys.exit(0)
    fp_parts = set(fp.split(os.sep))
    if fp_parts & exempt_segments:
        sys.exit(0)

    # Write to a brand-new file is not a modification round
    if tool == 'Write' and not os.path.exists(fp):
        sys.exit(0)

    lock_path = STATE + '.lock'
    with open(lock_path, 'w') as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        st = _load_state()

        # Hard stop check
        if st.get('hard_stopped', False):
            print(
                "HARD STOP: Review tracker has been stopped after 3 failed rounds.\n"
                "Human intervention required. The modification direction may be wrong.\n"
                "To reset: rm '" + STATE + "'",
                file=sys.stderr
            )
            sys.exit(2)

        # Increment modification counter
        st['mod_since_last_qodo'] += 1

        # Check if 3 rounds with findings have been exhausted
        if st['rounds_with_findings'] >= 3:
            st['hard_stopped'] = True
            _save_state(st)
            print(
                "HARD STOP: 3 rounds of review have found issues each time.\n"
                "The modification direction may be wrong. Human intervention required.\n"
                "To reset: rm '" + STATE + "'",
                file=sys.stderr
            )
            sys.exit(2)

        _save_state(st)

    sys.exit(0)

sys.exit(0)
PYEOF
printf '%s' "$INPUT" | python3 "$PYFILE"
