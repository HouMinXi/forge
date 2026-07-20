---
phase: 01a-trust-instrumentation
plan: 04
type: execute
wave: 3
depends_on:
  - 01a-01
  - 01a-02
  - 01a-03
files_modified:
  - cli/forge_cli.py
autonomous: true
requirements:
  - CLI-01
  - TRUST-05

must_haves:
  truths:
    - "forge <diff-spec> invokes Claude Code with SKILL.md as system prompt and produces review output"
    - "forge --dry-run <diff-spec> runs Step 0 checks directly in Python with zero LLM cost"
    - "forge --stats displays per-dimension FP rate table with split: tool-error (cat 1-4) vs user-preference (cat 5-6)"
    - "forge --stats --json outputs machine-readable dashboard"
    - "forge --bootstrap <file> loads historical FP data into findings.json"
    - "Each pipeline run logs token count and estimated cost in USD"
    - "Token/cost tracking uses portable schema (input_tokens, output_tokens, cost_usd in findings.json)"
    - "CLI wrapper writes run-level metadata to .forge/runs/<uuid>.json sidecar, NOT to findings.json directly"
    - "cost_usd uses 'is not None' check, not truthiness"
    - "total_passes is counted from findings.json after run, not hardcoded to 9"
    - "--append-system-prompt-file has a timeout fallback to --system-prompt inline"
  artifacts:
    - path: "cli/forge_cli.py"
      provides: "Standalone CLI wrapper for forge invocation outside Claude Code"
      contains: "def run_forge"
      exports: ["run_forge", "run_dry_run", "show_stats", "bootstrap_historical", "main"]
  key_links:
    - from: "cli/forge_cli.py"
      to: "skills/forge/SKILL.md"
      via: "subprocess.run(['claude', '-p', ..., '--append-system-prompt-file', skill_path]) with fallback"
      pattern: "append-system-prompt-file"
    - from: "cli/forge_cli.py"
      to: ".forge/findings.json"
      via: "json.load for dashboard (read-only during run)"
      pattern: "findings\\.json"
    - from: "cli/forge_cli.py"
      to: ".forge/runs/"
      via: "atomic_write for run metadata sidecar"
      pattern: "runs/"
    - from: "cli/forge_cli.py"
      to: "cli/config.json"
      via: "json.load for model pricing"
      pattern: "config\\.json"
---

<objective>
Create the forge CLI wrapper (cli/forge_cli.py) that enables standalone forge invocation outside Claude Code, with FP rate dashboard and cost metering.

Purpose: CLI-01 makes forge portable -- it can be invoked from any terminal against any git diff. TRUST-05 provides visibility into per-dimension FP rates with split metrics (tool-error vs user-preference). Cost metering (D8) ensures the 9-pass pipeline economics are known. The CLI wrapper is the primary user-facing entry point for non-interactive forge usage.

Output: cli/forge_cli.py with full CLI interface (review, --dry-run, --stats, --bootstrap, --classify).

Review fixes addressed:
- Issue #1 (HIGH): --dry-run runs Step 0 checks directly in Python, zero LLM cost
- Issue #2 (HIGH): CLI wrapper writes run metadata to .forge/runs/<uuid>.json sidecar, not findings.json
- Issue #3 (HIGH): --append-system-prompt-file fallback to --system-prompt inline on timeout
- Issue #8 (MEDIUM): Dashboard splits FP rate: tool-error (cat 1-4) vs user-preference (cat 5-6)
- Issue #13 (MEDIUM): cost_usd uses 'is not None' check, not truthiness
- Issue #14 (MEDIUM): total_passes counted from findings.json after run, not hardcoded
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01a-trust-instrumentation/01a-CONTEXT.md
@.planning/phases/01a-trust-instrumentation/01a-RESEARCH.md
@.planning/phases/01a-trust-instrumentation/01a-PATTERNS.md
@.planning/phases/01a-trust-instrumentation/01a-01-SUMMARY.md
@.planning/phases/01a-trust-instrumentation/01a-02-SUMMARY.md
@.planning/phases/01a-trust-instrumentation/01a-03-SUMMARY.md

<interfaces>
<!-- Key types and contracts the executor needs. -->

From cli/config.json (created in Plan 02):
```json
{
  "pricing": {
    "claude-opus-4-6": {
      "input_per_mtok": 15.00,
      "output_per_mtok": 75.00,
      "cache_read_per_mtok": 1.50,
      "cache_creation_per_mtok": 18.75
    }
  },
  "default_model": "claude-sonnet-4-6"
}
```

From .forge/findings.json (schema defined in Plan 01, bootstrapped in Plan 02):
```json
{
  "version": 1,
  "findings": [
    {
      "id": "uuid",
      "timestamp": "ISO-8601",
      "file": "path",
      "line": 42,
      "dimension": "security",
      "pass": 2,
      "cycle": 1,
      "severity": "P2",
      "description": "finding text",
      "outcome": "accepted|rejected|pending",
      "reject_reason": "HALLUCINATION|CONTEXT_MISSING|INTENTIONAL|NOT_APPLICABLE|STYLE_PREFERENCE|ACCEPTABLE_RISK|null",
      "commit_sha": "abc123",
      "cost_tokens": {"input": 1200, "output": 450}
    }
  ],
  "runs": []
}
```

From .forge/runs/<uuid>.json (NEW -- sidecar for run metadata):
```json
{
  "id": "run-uuid",
  "timestamp": "ISO-8601",
  "commit_sha": "abc123",
  "diff_spec": "HEAD~1",
  "dry_run": false,
  "total_passes": 9,
  "total_cost_usd": 0.35,
  "total_tokens": {"input": 45000, "output": 2400},
  "outcome": "completed"
}
```

From claude -p --output-format json (verified in RESEARCH.md):
```json
[
  {"type": "assistant", "content": "..."},
  {
    "type": "result",
    "total_cost_usd": 0.35,
    "usage": {
      "input_tokens": 45000,
      "output_tokens": 2400,
      "cache_read_input_tokens": 10000,
      "cache_creation_input_tokens": 5000
    }
  }
]
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create forge CLI wrapper with dry-run as direct Python, sidecar writes, split dashboard, and fallback</name>
  <files>cli/forge_cli.py</files>
  <read_first>
    - cli/config.json (model pricing config created in Plan 02)
    - .planning/phases/01a-trust-instrumentation/01a-CONTEXT.md (D7: CLI design, D8: cost metering)
    - .planning/phases/01a-trust-instrumentation/01a-RESEARCH.md (Pattern 5: CLI Wrapper Architecture, Pitfall 3: CLI Wrapper Timeout)
    - .planning/phases/01a-trust-instrumentation/01a-PATTERNS.md (CLI wrapper pattern, argparse pattern)
    - skills/forge/SKILL.md (the file that will be used as --append-system-prompt-file)
  </read_first>
  <action>
Create `cli/forge_cli.py` with the following complete implementation.

**File header:**
```python
#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Forge CLI wrapper -- standalone code review outside Claude Code.

Invokes Claude Code in headless mode (-p) with forge SKILL.md as system prompt.
Supports full review, dry-run (Step 0 only, zero LLM cost), FP dashboard, and
data bootstrap.

Usage:
    forge <diff-spec>               # Full review
    forge --dry-run <diff-spec>     # Step 0 only, zero LLM cost (direct Python)
    forge --stats                   # FP rate dashboard
    forge --stats --json            # Machine-readable dashboard
    forge --bootstrap <file>        # Load historical FP data
    forge --classify                # Classify pending findings

Design decisions:
- --dry-run runs Step 0 checks directly in Python (bash -n, shellcheck,
  pylint, non-ASCII grep). Does NOT invoke claude -p. Zero LLM cost.
  (Addresses review issue #1)
- Run metadata written to .forge/runs/<uuid>.json sidecar, NOT to
  findings.json directly. SKILL.md writes findings during review;
  CLI reads findings.json read-only after claude -p finishes.
  (Addresses review issue #2)
- --append-system-prompt-file has timeout fallback to --system-prompt
  inline if hanging detected. (Addresses review issue #3)

Per D7: This is a wrapper that invokes 'claude -p', NOT a standalone
reimplementation. The review value is in Claude's multi-pass convergence.
"""
```

**Imports (stdlib only):**
```python
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
```

**Constants:**
```python
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FORGE_SKILL = os.path.join(SCRIPT_DIR, '..', 'skills', 'forge', 'SKILL.md')
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config.json')
FINDINGS_FILE = '.forge/findings.json'
RUNS_DIR = '.forge/runs'

# FP category split (D2 key insight)
# Categories 1-4 = tool wrong (improve the tool)
TOOL_ERROR_REASONS = {'HALLUCINATION', 'CONTEXT_MISSING', 'INTENTIONAL', 'NOT_APPLICABLE'}
# Categories 5-6 = tool right, user won't act (don't count as tool FP)
USER_PREF_REASONS = {'STYLE_PREFERENCE', 'ACCEPTABLE_RISK'}
```

**Utility functions:**

1. `load_config()` -- Load cli/config.json, return dict. Exit with error if missing.

2. `load_findings()` -- Load .forge/findings.json, return dict. Return empty structure `{"version": 1, "findings": [], "runs": []}` if file missing.

3. `load_all_runs()` -- Load all .forge/runs/*.json sidecar files, return list of run dicts. Create RUNS_DIR if needed.

4. `atomic_write(filepath, data)` -- Atomic JSON write using tempfile.mkstemp + os.replace.

5. `calculate_cost(usage, config)` -- Calculate cost in USD from token usage and pricing config. Pure function, no platform dependency.

6. `_get_commit_sha()` -- Get current git SHA via subprocess.check_output (NOT shell substitution).

**Core function: run_dry_run() (addresses review issue #1 -- zero LLM cost)**

CRITICAL: --dry-run must NOT invoke claude -p. Implement Step 0 checks directly in Python:

```python
def run_dry_run(diff_spec):
    """Run Step 0 checks directly in Python. Zero LLM cost.

    Addresses review issue #1: --dry-run must not invoke claude -p.
    Runs: bash -n, shellcheck, pylint/ruff, non-ASCII grep.
    """
    print("forge: dry-run mode (Step 0 only, zero LLM cost)")
    print(f"forge: diff spec: {diff_spec}")

    # Get list of changed files from diff spec
    try:
        result = subprocess.run(
            ['git', 'diff', '--name-only', diff_spec],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            # Try as branch comparison
            result = subprocess.run(
                ['git', 'diff', '--name-only', diff_spec, '--'],
                capture_output=True, text=True, timeout=10
            )
        changed_files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
    except Exception as e:
        print(f"Error: failed to get diff files: {e}", file=sys.stderr)
        sys.exit(1)

    if not changed_files:
        print("forge: no changed files found")
        return

    print(f"forge: {len(changed_files)} files to check")
    findings = []
    total_issues = 0

    for filepath in changed_files:
        if not os.path.isfile(filepath):
            continue  # deleted file, skip

        # Step 0a: Syntax check
        if filepath.endswith('.sh') or filepath.endswith('.bash'):
            # bash -n
            r = subprocess.run(['bash', '-n', filepath],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                findings.append(('syntax', filepath, 'bash -n', r.stderr.strip()))
                total_issues += 1

            # shellcheck
            r = subprocess.run(['shellcheck', filepath],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                for line in r.stdout.strip().split('\n'):
                    if line.strip():
                        findings.append(('lint', filepath, 'shellcheck', line.strip()))
                        total_issues += 1

        elif filepath.endswith('.py'):
            # python3 -m py_compile
            r = subprocess.run([sys.executable, '-m', 'py_compile', filepath],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                findings.append(('syntax', filepath, 'py_compile', r.stderr.strip()))
                total_issues += 1

            # pylint or ruff
            for linter in ['ruff check', 'pylint --enable=W,C']:
                linter_parts = linter.split()
                try:
                    r = subprocess.run([linter_parts[0]] + linter_parts[1:] + [filepath],
                                       capture_output=True, text=True, timeout=60)
                    if r.returncode != 0 and r.stdout.strip():
                        for line in r.stdout.strip().split('\n')[:5]:  # cap per-file
                            findings.append(('lint', filepath, linter_parts[0], line.strip()))
                            total_issues += 1
                    break  # use first available linter
                except FileNotFoundError:
                    continue  # try next linter

        # Step 0c: Non-ASCII check (all files)
        try:
            r = subprocess.run(
                ['grep', '-Pn', '[^\\x00-\\x7F]', filepath],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0 and r.stdout.strip():
                for line in r.stdout.strip().split('\n')[:3]:  # cap
                    findings.append(('non-ascii', filepath, 'grep', line.strip()))
                    total_issues += 1
        except FileNotFoundError:
            pass  # grep not available

    # Report results
    if findings:
        print(f"\nforge: Step 0 found {total_issues} issue(s):\n")
        for category, fpath, tool, detail in findings:
            print(f"  [{category}] {fpath} ({tool}): {detail}")
        print(f"\nforge: FAIL -- fix {total_issues} issue(s) before review")
        sys.exit(1)
    else:
        print("\nforge: Step 0 PASS -- all checks clean")
```

**Core function: run_forge() (with fallback, sidecar writes, cost_usd fix)**

```python
def run_forge(diff_spec):
    """Invoke claude -p with forge SKILL.md as system prompt.

    Per D7: wrapper invokes 'claude -p', not standalone reimplementation.
    Writes run metadata to .forge/runs/<uuid>.json sidecar (review issue #2).
    Falls back to --system-prompt inline if --append-system-prompt-file
    hangs (review issue #3).
    """
    skill_path = os.path.realpath(FORGE_SKILL)
    if not os.path.isfile(skill_path):
        print(f"Error: SKILL.md not found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    prompt = (
        f"Run the full forge review pipeline on the git diff: {diff_spec}. "
        f"Follow the complete 5-step pipeline in your system prompt."
    )

    # Try --append-system-prompt-file first (review issue #3: with fallback)
    cmd = [
        'claude', '-p', prompt,
        '--append-system-prompt-file', skill_path,
        '--output-format', 'json',
        '--allowedTools', 'Bash,Read,Edit,Write,Grep,Glob',
    ]

    print(f"forge: invoking claude -p (full review)...")
    print(f"forge: diff spec: {diff_spec}")

    result_data = _invoke_claude(cmd, skill_path, prompt)
    if result_data is None:
        sys.exit(1)

    # Extract result item with cost data
    result_item = None
    for item in result_data:
        if isinstance(item, dict) and item.get('type') == 'result':
            result_item = item
            break

    if result_item is None:
        print("Warning: no result item found in claude output", file=sys.stderr)
        return

    # Extract token usage and cost
    usage = result_item.get('usage', {})
    cost_usd = result_item.get('total_cost_usd')
    input_tokens = usage.get('input_tokens', 0)
    output_tokens = usage.get('output_tokens', 0)

    # Calculate cost (addresses review issue #13: 'is not None', not truthiness)
    config = load_config()
    calculated_cost = calculate_cost(usage, config)
    final_cost = cost_usd if cost_usd is not None else calculated_cost

    # Count actual passes from findings.json (addresses review issue #14)
    findings_data = load_findings()
    findings = findings_data.get('findings', [])
    commit_sha = _get_commit_sha()
    # Count findings from this commit as proxy for passes completed
    this_run_findings = [f for f in findings if f.get('commit_sha') == commit_sha]
    # Count unique (cycle, pass) pairs as actual passes
    pass_set = set()
    for f in this_run_findings:
        pass_set.add((f.get('cycle', 0), f.get('pass', 0)))
    actual_passes = len(pass_set) if pass_set else 0

    # Write run metadata to sidecar file (addresses review issue #2)
    run_id = str(uuid.uuid4())
    run_record = {
        'id': run_id,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'commit_sha': commit_sha,
        'diff_spec': diff_spec,
        'dry_run': False,
        'total_passes': actual_passes,
        'total_cost_usd': final_cost,
        'total_tokens': {
            'input': input_tokens,
            'output': output_tokens,
        },
        'outcome': 'completed',
    }

    os.makedirs(RUNS_DIR, exist_ok=True)
    run_file = os.path.join(RUNS_DIR, f'{run_id}.json')
    atomic_write(run_file, run_record)

    # Print cost summary
    print(f"\nforge: run complete")
    print(f"forge: passes detected: {actual_passes}")
    print(f"forge: tokens -- input: {input_tokens:,}, output: {output_tokens:,}")
    reported = f"${cost_usd:.4f}" if cost_usd is not None else "N/A"
    print(f"forge: cost -- {reported} (reported) / ${calculated_cost:.4f} (calculated)")

    # Print assistant content
    for item in result_data:
        if isinstance(item, dict) and item.get('type') == 'assistant':
            content = item.get('content', '')
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'text':
                        print(block.get('text', ''))
            elif isinstance(content, str):
                print(content)
```

**Helper: _invoke_claude() with fallback (addresses review issue #3)**

```python
def _invoke_claude(cmd, skill_path, prompt):
    """Invoke claude -p with timeout and fallback.

    First tries --append-system-prompt-file. If it times out (hangs),
    falls back to --system-prompt with SKILL.md content inline.
    Addresses review issue #3.
    """
    # First attempt: --append-system-prompt-file
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
    except subprocess.TimeoutExpired:
        print("Warning: --append-system-prompt-file timed out after 600s. "
              "Falling back to --system-prompt inline.", file=sys.stderr)
        # Fallback: read SKILL.md and pass as --system-prompt
        try:
            with open(skill_path, 'r') as f:
                skill_content = f.read()
        except IOError as e:
            print(f"Error: cannot read SKILL.md for fallback: {e}", file=sys.stderr)
            return None

        fallback_cmd = [
            'claude', '-p', prompt,
            '--system-prompt', skill_content,
            '--output-format', 'json',
            '--allowedTools', 'Bash,Read,Edit,Write,Grep,Glob',
        ]
        try:
            result = subprocess.run(
                fallback_cmd, capture_output=True, text=True, timeout=600
            )
        except subprocess.TimeoutExpired:
            print("Error: fallback also timed out after 600s", file=sys.stderr)
            return None
    except FileNotFoundError:
        print("Error: 'claude' command not found. Install Claude Code CLI first.",
              file=sys.stderr)
        return None

    if result.returncode != 0:
        print(f"Error: claude exited with code {result.returncode}", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print("Error: failed to parse claude output as JSON", file=sys.stderr)
        print("Raw output (first 500 chars):", file=sys.stderr)
        print(result.stdout[:500], file=sys.stderr)
        return None
```

**Core function: show_stats() with split FP rates (addresses review issue #8)**

```python
def show_stats(json_format=False):
    """Display FP rate dashboard from findings.json (TRUST-05).

    Addresses review issue #8: split into tool-error FP (categories 1-4)
    and user-preference FP (categories 5-6).

    Tool-error FP rate = rejected with cat 1-4 / total decided
    User-preference rate = rejected with cat 5-6 / total decided
    """
    findings_data = load_findings()
    findings = findings_data.get('findings', [])
    runs = load_all_runs()

    if not findings and not runs:
        print("No findings data yet. Run forge first.")
        return

    # Aggregate findings by dimension
    dims = {}
    for f in findings:
        dim = f.get('dimension', 'unknown')
        if dim not in dims:
            dims[dim] = {
                'accepted': 0, 'rejected': 0, 'pending': 0,
                'tool_error': 0, 'user_pref': 0,
            }
        outcome = f.get('outcome', 'pending')
        dims[dim][outcome] = dims[dim].get(outcome, 0) + 1

        # Split rejections by category
        reason = f.get('reject_reason')
        if outcome == 'rejected' and reason:
            if reason in TOOL_ERROR_REASONS:
                dims[dim]['tool_error'] += 1
            elif reason in USER_PREF_REASONS:
                dims[dim]['user_pref'] += 1

    # Aggregate runs for cost summary
    total_cost = sum(r.get('total_cost_usd', 0) or 0 for r in runs)
    total_input = sum(r.get('total_tokens', {}).get('input', 0) for r in runs)
    total_output = sum(r.get('total_tokens', {}).get('output', 0) for r in runs)
    run_count = len(runs)

    if json_format:
        output = {
            'dimensions': dims,
            'runs': {
                'count': run_count,
                'total_cost_usd': total_cost,
                'total_tokens': {'input': total_input, 'output': total_output},
                'avg_cost_per_run': total_cost / run_count if run_count else 0,
            },
            'findings_total': len(findings),
        }
        print(json.dumps(output, indent=2))
        return

    # Terminal table with split FP rates
    print("=" * 82)
    print("Forge FP Rate Dashboard")
    print("=" * 82)
    print()
    header = (f"{'Dimension':<18} {'Accept':>6} {'Reject':>6} {'Pend':>5} "
              f"{'ToolFP':>7} {'UserFP':>7} {'FP%':>5}")
    print(header)
    print("-" * 82)

    t_accepted = t_rejected = t_pending = t_tool = t_user = 0

    for dim in sorted(dims.keys()):
        c = dims[dim]
        accepted = c.get('accepted', 0)
        rejected = c.get('rejected', 0)
        pending = c.get('pending', 0)
        tool_err = c.get('tool_error', 0)
        user_prf = c.get('user_pref', 0)

        t_accepted += accepted
        t_rejected += rejected
        t_pending += pending
        t_tool += tool_err
        t_user += user_prf

        decided = accepted + rejected
        # Tool-error FP rate (the actionable one for improving forge)
        fp_pct = f"{tool_err / decided * 100:.0f}%" if decided > 0 else "N/A"
        print(f"{dim:<18} {accepted:>6} {rejected:>6} {pending:>5} "
              f"{tool_err:>7} {user_prf:>7} {fp_pct:>5}")

    print("-" * 82)
    total_decided = t_accepted + t_rejected
    total_fp = f"{t_tool / total_decided * 100:.0f}%" if total_decided > 0 else "N/A"
    print(f"{'TOTAL':<18} {t_accepted:>6} {t_rejected:>6} {t_pending:>5} "
          f"{t_tool:>7} {t_user:>7} {total_fp:>5}")

    # Legend
    print()
    print("ToolFP = cat 1-4 (HALLUCINATION, CONTEXT_MISSING, INTENTIONAL, NOT_APPLICABLE)")
    print("UserFP = cat 5-6 (STYLE_PREFERENCE, ACCEPTABLE_RISK)")
    print("FP%    = ToolFP / (Accept + Reject) -- rate that measures tool quality")

    # Cost summary
    print()
    print(f"{'Cost Summary':<22}")
    print("-" * 42)
    print(f"{'Total runs:':<22} {run_count:>8}")
    print(f"{'Total cost:':<22} {'$' + f'{total_cost:.4f}':>8}")
    avg = f"${total_cost / run_count:.4f}" if run_count else "N/A"
    print(f"{'Avg cost/run:':<22} {avg:>8}")
    print(f"{'Total input tokens:':<22} {total_input:>8,}")
    print(f"{'Total output tokens:':<22} {total_output:>8,}")
    print("=" * 82)
```

**Remaining functions:**

7. `_get_commit_sha()` -- Get short git SHA via subprocess.check_output:
```python
def _get_commit_sha():
    """Get short git SHA of HEAD via subprocess."""
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return 'unknown'
```

8. `load_all_runs()` -- Load run metadata from sidecar files:
```python
def load_all_runs():
    """Load all run records from .forge/runs/*.json sidecar files."""
    os.makedirs(RUNS_DIR, exist_ok=True)
    runs = []
    for run_file in sorted(glob.glob(os.path.join(RUNS_DIR, '*.json'))):
        try:
            with open(run_file, 'r') as f:
                runs.append(json.load(f))
        except (json.JSONDecodeError, IOError):
            continue
    return runs
```

9. `bootstrap_historical(filepath)` -- Delegate to bootstrap script:
```python
def bootstrap_historical(filepath):
    """Load historical FP data from analysis file."""
    script = os.path.join(SCRIPT_DIR, '..', 'bootstrap', 'convert_historical.py')
    script = os.path.realpath(script)
    if not os.path.isfile(script):
        print(f"Error: bootstrap script not found at {script}", file=sys.stderr)
        sys.exit(1)
    result = subprocess.run(
        [sys.executable, script, filepath],
        capture_output=False, timeout=30
    )
    sys.exit(result.returncode)
```

10. `classify_findings()` -- Interactive classification of pending findings (same as before, with input() loop).

11. `main()` -- Argparse entry point:
```python
def main():
    parser = argparse.ArgumentParser(
        prog='forge',
        description='Forge code review CLI -- standalone wrapper for Claude Code',
    )
    parser.add_argument(
        'diff_spec', nargs='?', default=None,
        help='git diff spec to review (e.g., HEAD~1, branch..main)',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Run Step 0 only (syntax + lint + non-ASCII), zero LLM cost',
    )
    parser.add_argument(
        '--stats', action='store_true',
        help='Show FP rate dashboard from findings.json',
    )
    parser.add_argument(
        '--json', action='store_true',
        help='Output dashboard in JSON format (use with --stats)',
    )
    parser.add_argument(
        '--bootstrap', metavar='FILE',
        help='Load historical FP data from analysis file',
    )
    parser.add_argument(
        '--classify', action='store_true',
        help='Interactively classify pending findings (accept/reject)',
    )

    args = parser.parse_args()

    if args.stats:
        show_stats(json_format=args.json)
    elif args.classify:
        classify_findings()
    elif args.bootstrap:
        bootstrap_historical(args.bootstrap)
    elif args.diff_spec:
        if args.dry_run:
            run_dry_run(args.diff_spec)
        else:
            run_forge(args.diff_spec)
    elif args.dry_run:
        parser.error('--dry-run requires a diff_spec argument')
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
```

**Make executable:**
After creating the file, run: `chmod +x cli/forge_cli.py`
  </action>
  <verify>
    <automated>python3 -m py_compile cli/forge_cli.py && echo "syntax OK" && python3 -c "
import ast, sys
with open('cli/forge_cli.py') as f:
    tree = ast.parse(f.read())
funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
required = ['run_forge', 'run_dry_run', 'show_stats', 'bootstrap_historical',
            'classify_findings', 'calculate_cost', 'atomic_write',
            'load_findings', 'load_config', 'load_all_runs', 'main']
missing = [r for r in required if r not in funcs]
if missing:
    print(f'FAIL: missing functions: {missing}', file=sys.stderr)
    sys.exit(1)
print('functions OK')
" && python3 cli/forge_cli.py --help 2>&1 | head -5 && echo "help OK" && grep -q "TOOL_ERROR_REASONS" cli/forge_cli.py && echo "split FP OK" && grep -q "is not None" cli/forge_cli.py && echo "cost check OK" && grep -q "run_dry_run" cli/forge_cli.py && echo "dry-run func OK" && grep -q "runs/" cli/forge_cli.py && echo "sidecar OK" && grep -q "system-prompt" cli/forge_cli.py && echo "fallback OK"</automated>
  </verify>
  <acceptance_criteria>
    - cli/forge_cli.py exists and passes `python3 -m py_compile`
    - File starts with `#!/usr/bin/env python3` shebang
    - File contains SPDX license and copyright "Minxi Hou"
    - Contains `run_dry_run()` that runs bash -n, shellcheck, pylint, grep directly in Python WITHOUT invoking claude -p (addresses review issue #1)
    - `run_dry_run()` uses subprocess.run for each tool (bash, shellcheck, pylint, grep), NOT claude -p
    - Contains `run_forge()` that writes run metadata to .forge/runs/<uuid>.json sidecar, NOT to findings.json (addresses review issue #2)
    - Contains `_invoke_claude()` with --append-system-prompt-file first attempt and --system-prompt inline fallback on timeout (addresses review issue #3)
    - `show_stats()` displays BOTH tool-error FP rate (cat 1-4) and user-preference rate (cat 5-6) as separate columns (addresses review issue #8)
    - TOOL_ERROR_REASONS and USER_PREF_REASONS constants are defined
    - `run_forge()` uses `cost_usd if cost_usd is not None else calculated_cost`, NOT `cost_usd if cost_usd else ...` (addresses review issue #13)
    - `run_forge()` counts actual passes from findings.json by unique (cycle, pass) pairs, NOT hardcoded 9 (addresses review issue #14)
    - `load_all_runs()` reads from .forge/runs/*.json sidecar files
    - `_get_commit_sha()` uses subprocess.check_output, NOT shell substitution
    - Uses only stdlib imports
    - No non-ASCII characters: `grep -P '[^\x00-\x7F]' cli/forge_cli.py` returns nothing
    - File is executable: `test -x cli/forge_cli.py` exits 0
    - `python3 cli/forge_cli.py --help` prints usage without error
  </acceptance_criteria>
  <done>cli/forge_cli.py provides: (1) --dry-run as direct Python Step 0 with zero LLM cost, (2) run metadata in .forge/runs/ sidecar not findings.json, (3) --append-system-prompt-file fallback to --system-prompt inline, (4) split FP dashboard (tool-error vs user-preference), (5) cost_usd 'is not None' check, (6) actual pass count from findings.json. Review issues #1, #2, #3, #8, #13, #14 all addressed.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| User CLI input -> subprocess | diff_spec passed to claude -p as prompt argument |
| Claude output -> JSON parse | claude -p output parsed as JSON for token/cost extraction |
| User input -> classify | Interactive input for accept/reject classification |
| SKILL.md -> --system-prompt | SKILL.md content passed inline as fallback |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01a-10 | T (Tampering) | diff_spec injection | accept | diff_spec is passed as a prompt string to claude -p, not as a shell command |
| T-01a-11 | D (Denial of Service) | subprocess timeout | mitigate | timeout=600 on subprocess.run; TimeoutExpired caught with fallback |
| T-01a-12 | T (Tampering) | .forge/runs/*.json writes | mitigate | Atomic write via tempfile.mkstemp + os.replace |
| T-01a-13 | T (Tampering) | findings.json reads | accept | CLI reads findings.json read-only after claude -p; SKILL.md is the only writer during review |
</threat_model>

<verification>
1. `python3 -m py_compile cli/forge_cli.py` passes
2. `python3 cli/forge_cli.py --help` prints usage
3. `python3 cli/forge_cli.py --stats` works (shows empty state or bootstrapped data)
4. `python3 cli/forge_cli.py --stats --json 2>/dev/null | python3 -m json.tool` outputs valid JSON
5. `grep "run_dry_run\|subprocess.run.*bash\|subprocess.run.*shellcheck" cli/forge_cli.py` confirms dry-run runs tools directly
6. `grep "is not None" cli/forge_cli.py` confirms cost_usd check
7. `grep "runs/" cli/forge_cli.py` confirms sidecar write path
8. `grep "system-prompt" cli/forge_cli.py` confirms fallback mechanism
</verification>

<success_criteria>
The forge CLI wrapper is complete. --dry-run runs Step 0 directly in Python with zero LLM cost. Full review writes run metadata to sidecar files, not findings.json. --append-system-prompt-file falls back to --system-prompt inline on timeout. Dashboard shows split FP rates (tool-error vs user-preference). Cost handling uses 'is not None'. Pass count is dynamic. All 6 review issues assigned to this plan are resolved.
</success_criteria>

<output>
After completion, create `.planning/phases/01a-trust-instrumentation/01a-04-SUMMARY.md`
</output>
