#!/bin/bash
# Review tracker hook (B2+C1)
#
# PostToolUse on Bash  -> detect real qodo execution, parse findings, update state
# PreToolUse  on Edit/Write -> count modifications, enforce 3-round budget
#
# State machine:
#   - qodo_runs: total qodo executions detected (target: 6 per full review)
#   - last_qodo_has_findings: whether the most recent qodo run found issues
#   - last_max_severity: highest severity from last review pass (P0/P1/P2/P3/none)
#   - last_review_pass: which pass was last detected (qodo-review/code-review-expert/adversarial-qe)
#   - mod_since_last_qodo: edits since last qodo run
#   - rounds_with_findings: rounds where review found P0/P1/P2 issues after fixes
#   - review_passed: true when last review had no P0/P1/P2 findings
#
# Hard stop: after 3 rounds with P0/P1/P2 findings -> block edits
#            (P3-only rounds do not count toward hard stop)
# Detection: all 3 passes (qodo-review, code-review-expert, adversarial-qe)
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
            'last_max_severity': 'none',
            'last_review_pass': '',
            'mod_since_last_qodo': 0,
            'rounds_with_findings': 0,
            'review_passed': False,
            'hard_stopped': False,
        }


def _save_state(st):
    dir_name = os.path.dirname(STATE) or '/tmp'
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False)
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


def _is_review_pass(cmd, output):
    """Check if this Bash command was any of the three review passes.

    Detects: qodo-review (Pass 1), code-review-expert (Pass 2),
    adversarial-qe (Pass 3). Returns the pass name or None.

    Addresses review issue #7: severity-gated state machine must
    apply to all three passes, not just qodo.
    """
    # Check for qodo (existing detection)
    if _is_real_qodo(cmd, output):
        return 'qodo-review'

    # Check for code-review-expert (Pass 2)
    # English markers
    code_review_markers = [
        r'code.review.expert',
        r'/code-review-expert',
        r'SOLID.*architecture',
        r'P[0-3]\s+(?:Critical|High|Medium|Low)',
    ]
    # Chinese markers -- literal characters matching _has_findings() style
    code_review_cn = [
        '架构',           # jia gou (architecture)
        '代码审查专家',  # dai ma shen cha zhuan jia (code review expert)
        'SOLID',
    ]
    has_code_review = (
        any(re.search(m, output, re.IGNORECASE) for m in code_review_markers)
        or any(marker in output for marker in code_review_cn)
    )
    if has_code_review and len(output) >= 500:
        return 'code-review-expert'

    # Check for adversarial-qe (Pass 3)
    adversarial_markers = [
        r'adversarial.qe',
        r'/adversarial-qe',
        r'red.team',
        r'12\s+(?:attack\s+)?dimensions?',
        r'(?:Critical|High|Medium|Low|Nit)\s+severity',
    ]
    # Chinese markers for adversarial-qe -- literal style
    adversarial_cn = [
        '对抗',           # dui kang (adversarial)
        '红队',           # hong dui (red team)
        '攻击维度',  # gong ji wei du (attack dimensions)
    ]
    has_adversarial = (
        any(re.search(m, output, re.IGNORECASE) for m in adversarial_markers)
        or any(marker in output for marker in adversarial_cn)
    )
    if has_adversarial and len(output) >= 500:
        return 'adversarial-qe'

    return None


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


def _max_severity(output):
    """Parse review output to determine highest severity level.

    Returns: 'P0', 'P1', 'P2', 'P3', or 'none' (no findings).
    Used for severity-gated cycle reset (TRUST-07).
    """
    # P0 signals
    p0_signals = [
        r'\bP0\b',
        r'\bcritical\b.*\b(?:security|crash|data.loss)',
    ]
    if any(re.search(s, output, re.IGNORECASE) for s in p0_signals):
        return 'P0'

    # P1 signals (English)
    p1_signals = [
        r'\bP1\b',
        r'\bmust\s+fix\b',
        r'\bhigh\s+risk\b',
    ]
    # P1 signals (Chinese) -- literal characters matching _has_findings() style
    p1_cn = [
        '必修',         # bi xiu (must fix)
        '高风险',     # gao feng xian (high risk)
        '严重问题',   # yan zhong wen ti (serious problem)
        '必须修改',   # bi xu xiu gai (must modify)
    ]
    if any(re.search(s, output, re.IGNORECASE) for s in p1_signals):
        return 'P1'
    if any(marker in output for marker in p1_cn):
        return 'P1'

    # P2 signals (English)
    p2_signals = [
        r'\bP2\b',
        r'\bshould\s+fix\b',
        r'REQUEST_CHANGES',
    ]
    # P2 signals (Chinese) -- literal characters matching _has_findings() style
    p2_cn = [
        '建议修复',   # jian yi xiu fu (suggest fix)
        '应该修改',   # ying gai xiu gai (should modify)
        '需要修复',   # xu yao xiu fu (need to fix)
    ]
    if any(re.search(s, output, re.IGNORECASE) for s in p2_signals):
        return 'P2'
    if any(marker in output for marker in p2_cn):
        return 'P2'

    # Check if there are any findings at all (using existing _has_findings logic)
    if _has_findings(output):
        return 'P3'  # has findings but none matched P0/P1/P2 = style nits

    return 'none'


# ── PostToolUse on Bash: detect qodo execution ──────────────────────────
if tool == 'Bash':
    cmd = tinput.get('command', '')
    pass_name = _is_review_pass(cmd, tresult)
    if pass_name is None:
        sys.exit(0)

    lock_path = STATE + '.lock'
    with open(lock_path, 'w') as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        st = _load_state()
        st['qodo_runs'] += 1
        had_mods = st['mod_since_last_qodo'] > 0
        st['mod_since_last_qodo'] = 0
        findings = _has_findings(tresult)
        severity = _max_severity(tresult)
        st['last_qodo_has_findings'] = findings
        st['last_max_severity'] = severity
        st['last_review_pass'] = pass_name
        if findings:
            if had_mods or st['qodo_runs'] == 1:
                # Only count rounds with P0/P1/P2 toward hard stop (TRUST-07)
                # P3-only rounds do not count
                if severity in ('P0', 'P1', 'P2'):
                    st['rounds_with_findings'] += 1
            st['review_passed'] = False
        else:
            st['review_passed'] = True
        _save_state(st)

        # Write severity data for SKILL.md state machine consumption
        session_file = os.path.join('.forge', 'current_session.json')
        os.makedirs('.forge', exist_ok=True)
        session_data = {
            'last_max_severity': severity,
            'last_review_pass': pass_name,
            'qodo_runs': st['qodo_runs'],
            'rounds_with_findings': st['rounds_with_findings'],
        }
        try:
            s_fd, s_tmp = tempfile.mkstemp(dir='.forge', suffix='.json')
            with os.fdopen(s_fd, 'w', encoding='utf-8') as sf:
                json.dump(session_data, sf, ensure_ascii=False)
            os.replace(s_tmp, session_file)
        except Exception as e:
            # Addresses review issue #15: log warning, don't silently pass
            print(
                f"REVIEW TRACKER WARNING: failed to write sidecar "
                f"{session_file}: {e}",
                file=sys.stderr
            )
        # Capture values for reporting outside the lock
        run_num = st['qodo_runs']
        rounds_num = st['rounds_with_findings']

    if findings:
        print(
            f"REVIEW TRACKER: {pass_name} run #{run_num} detected findings "
            f"(max severity: {severity}). Rounds with findings: {rounds_num}/3.",
            file=sys.stderr
        )
    else:
        print(
            f"REVIEW TRACKER: {pass_name} run #{run_num} passed clean. "
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
