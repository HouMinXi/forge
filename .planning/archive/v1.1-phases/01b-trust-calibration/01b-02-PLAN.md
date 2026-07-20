---
phase: 01b-trust-calibration
plan: 02
type: execute
wave: 2
depends_on: [01b-01]
files_modified:
  - cli/forge_cli.py
autonomous: true
requirements:
  - TRUST-03
must_haves:
  truths:
    - "Every forge invocation classifies the change into full/light/step0 tier before LLM invocation"
    - "Classification is deterministic Python -- LLM never sees tier options"
    - "Critical files always route to full review regardless of override"
    - "AI-generated code routes to minimum light tier"
    - "Comment-only and whitespace-only changes route to step0-only"
    - "10% of light-classified runs are silently upgraded to full for audit"
    - "Run sidecar records tier and was_audited metadata"
    - "backfill_confidence runs at end of every forge invocation, persisting computed scores"
  artifacts:
    - path: "cli/forge_cli.py"
      provides: "classify_change(), _get_changed_files(), _count_diff_lines(), _detect_change_type(), _has_critical_files(), _detect_ai_generated()"
      contains: "def classify_change"
    - path: "cli/forge_cli.py"
      provides: "Modified run_forge() with tier routing and backfill_confidence call"
      contains: "'tier':"
  key_links:
    - from: "cli/forge_cli.py:classify_change"
      to: "cli/forge_cli.py:run_forge"
      via: "classify_change called at top of run_forge before prompt construction"
      pattern: "tier = classify_change"
    - from: "cli/forge_cli.py:classify_change"
      to: "cli/config.json"
      via: "reads critical_patterns and ai_markers from config"
      pattern: "tier_classification"
    - from: "cli/forge_cli.py:run_forge"
      to: "cli/forge_cli.py:backfill_confidence"
      via: "backfill_confidence called at end of run_forge after run sidecar written"
      pattern: "backfill_confidence"
---

<objective>
Add deterministic tier classification to forge CLI (D2/TRUST-03) and wire backfill_confidence into run_forge (H1).

Purpose: Auto-classify changes into full/light/step0-only review depth using composite scoring. Classification runs in deterministic Python before LLM invocation -- the LLM never knows other tiers exist. This reduces cost on trivial changes (comment-only, whitespace) while maintaining full security review on critical files. 10% audit sampling validates that lighter tiers are not missing important findings. Also wires backfill_confidence() (from Plan 01) into run_forge() so confidence scores are computed and persisted after every review run.

Output: Extended forge_cli.py with classify_change() and 5 helper functions, modified run_forge() with tier-aware prompt routing, audit sampling, backfill_confidence call, modified run_dry_run() path for step0 tier, extended run sidecar schema with tier/was_audited fields.
</objective>

<execution_context>
@/home/houminxi/.claude/get-shit-done/workflows/execute-plan.md
@/home/houminxi/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01b-trust-calibration/01b-CONTEXT.md
@.planning/phases/01b-trust-calibration/01b-RESEARCH.md
@.planning/phases/01b-trust-calibration/01b-PATTERNS.md

<interfaces>
<!-- Key types and contracts the executor needs. Extracted from codebase. -->

From cli/forge_cli.py (run_forge, lines 441-569):
```python
def run_forge(diff_spec):
    """Invoke claude -p with forge SKILL.md as system prompt."""
    skill_path = os.path.realpath(FORGE_SKILL)
    # ... validation ...
    prompt = (
        f"Run the full forge review pipeline on the git diff: {diff_spec}. "
        "Follow the complete 5-step pipeline in your system prompt."
    )
    cmd = [
        'claude', '-p', prompt,
        '--append-system-prompt-file', skill_path,
        '--output-format', 'json',
        '--allowedTools', 'Bash,Read,Edit,Write,Grep,Glob',
    ]
    # ... invoke, parse result ...
    run_record = {
        'id': run_id,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'commit_sha': commit_sha,
        'diff_spec': diff_spec,
        'dry_run': False,
        'total_passes': actual_passes,
        'total_cost_usd': final_cost,
        'total_tokens': {'input': input_tokens, 'output': output_tokens},
        'outcome': 'completed',
    }
```

From cli/forge_cli.py (run_dry_run, lines 202-348):
```python
def run_dry_run(diff_spec):
    """Run Step 0 checks directly in Python (bash -n, shellcheck, pylint, etc.)."""
```

From cli/forge_cli.py (main, lines 867-922):
```python
def main():
    parser = argparse.ArgumentParser(...)
    parser.add_argument('diff_spec', nargs='?', default=None, ...)
    parser.add_argument('--dry-run', action='store_true', ...)
    # ... other args ...
    if args.diff_spec:
        if args.dry_run:
            run_dry_run(args.diff_spec)
        else:
            run_forge(args.diff_spec)
```

From cli/forge_cli.py (subprocess pattern, run_dry_run):
```python
result = subprocess.run(
    ['git', 'diff', '--name-only', diff_spec],
    capture_output=True, text=True, timeout=10, check=False,
)
```

From cli/forge_cli.py (_get_commit_sha, line 182):
```python
def _get_commit_sha():
    """Get short SHA of HEAD commit."""
```

From cli/forge_cli.py (Plan 01 additions):
```python
FINDINGS_FILE = ...  # path to .forge/findings.json

def backfill_confidence(findings_data):
    """Compute confidence scores for all findings based on dimension FP rates."""

def load_findings():
    """Load .forge/findings.json."""

def atomic_write(filepath, data):
    """Atomically write JSON data to filepath."""
```

From cli/config.json (tier_classification section -- added by Plan 01 Task 2):
```json
"tier_classification": {
    "critical_patterns": [
        "(?:auth|security|crypto|secret|token|password|credential)",
        "(?:hooks/check_)",
        "(?:SKILL\\.md)"
    ],
    "ai_markers": ["Generated by", "Co-Authored-By"],
    "audit_rate": 0.10,
    "small_diff_threshold": 10
}
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add tier classification functions to forge_cli.py and wire backfill_confidence into run_forge</name>
  <files>cli/forge_cli.py</files>
  <read_first>
    - cli/forge_cli.py (full file -- 922 lines: run_forge lines 441-569, run_dry_run lines 202-348, main lines 867-922, constants lines 43-65, subprocess pattern)
    - .planning/phases/01b-trust-calibration/01b-CONTEXT.md (D2: classification signals, tier definitions, anti-gaming design, override rules)
    - .planning/phases/01b-trust-calibration/01b-RESEARCH.md (Pattern 1: classify_change, change type detection, anti-patterns section on LLM gaming)
    - .planning/phases/01b-trust-calibration/01b-PATTERNS.md (subprocess+git pattern, pure function pattern, error output pattern, section header pattern)
  </read_first>
  <action>
Add a new section AFTER the "Confidence Scoring" section (which Plan 01 adds) and BEFORE the "Core: run_dry_run" section. If Plan 01 has not been executed yet, add it AFTER the `calculate_cost` function (around line 179) and BEFORE `run_dry_run` (line 202).

Section header:
```python
# ---------------------------------------------------------------------------
# Tier Classification (D2 -- deterministic, before LLM invocation)
# ---------------------------------------------------------------------------
```

Add these 6 functions:

**Function 1 -- `_get_changed_files(diff_spec)`:**
- Runs `git diff --name-only <diff_spec>` via subprocess.run with capture_output=True, text=True, timeout=10, check=False
- Returns list of file paths (stripped, non-empty)
- On error, prints to stderr and returns empty list (conservative -- will cause full tier)
- Handles both branch-vs-branch (`main..feature`) and commit range (`HEAD~1`) diff specs

**Function 2 -- `_count_diff_lines(diff_spec)`:**
- **(Addresses review M7)** Runs `git diff --numstat <diff_spec>` via subprocess.run (NOT `--stat` -- `--numstat` produces fixed-format, locale-independent output)
- Parses each line: `<added>\t<deleted>\t<filename>`. Sum all added + deleted values.
- Skip lines where added or deleted is `-` (binary files).
- Returns int count of total changed lines.
- On error, returns 999 (conservative -- biases toward full tier)

**Function 3 -- `_detect_change_type(diff_spec, files)`:**
- Returns one of: 'whitespace_only', 'comment_only', 'code'
- Step 1: Run `git diff -w --stat <diff_spec>` -- if output is empty (returncode 0, no stdout), all changes are whitespace-only, return 'whitespace_only'
- Step 2: Run `git diff -U0 <diff_spec>` to get hunks. For each added/removed line (starts with + or -), skip +++ and --- headers, skip blank content lines. Check remaining content against language-aware comment patterns:
  - .py/.sh/.bash: `^\s*#`
  - .js/.ts/.go: `^\s*//`
  - .c/.h: `^\s*(?://|\*)`
  - .md: **(Addresses review H2)** `r'^\s*(?:<!--|-->|$)'` (valid Python regex with non-capturing group, NOT the invalid `'^\s*<!--' or ...` pseudo-syntax)
- If ALL non-blank changed content matches a comment pattern for the relevant file type, return 'comment_only'
- Conservative: if ANY changed line does not match a comment pattern, return 'code'
- IMPORTANT: Python docstrings (triple-quoted strings) are NOT comments -- classify as 'code' (Pitfall 5 from RESEARCH.md)

**Function 4 -- `_has_critical_files(files, config)`:**
- Takes list of file paths and config dict
- Reads `config.get('tier_classification', {}).get('critical_patterns', [])` with hardcoded defaults:
  ```python
  default_patterns = [
      r'(?:auth|security|crypto|secret|token|password|credential)',
      r'(?:hooks/check_)',
      r'(?:SKILL\.md)',
  ]
  ```
- Uses `re.search(pattern, filepath, re.IGNORECASE)` for each pattern against each file
- Returns True if ANY file matches ANY pattern

**Function 5 -- `_detect_ai_generated(diff_spec, config)`:**
- Runs `git diff -U0 <diff_spec>` to get diff content
- **(Addresses review M3)** Also runs `git log -1 --format=%B` to get the commit message. Searches BOTH diff content AND commit message for AI markers.
- Reads `config.get('tier_classification', {}).get('ai_markers', [])` with hardcoded defaults: `['Generated by', 'Co-Authored-By']`
- Searches diff content for any marker in added lines (lines starting with +), case-insensitive
- Searches commit message for any marker, case-insensitive
- Returns True if any marker found in either source
- Per RESEARCH.md Open Question 1: this is a heuristic; users can also use --full for AI code

**Function 6 -- `classify_change(diff_spec, override=None, config=None)`:**
- Pure function (reads git state, but no writes)
- Docstring: "Classify a change into full/light/step0 tier. Deterministic Python -- LLM never sees tier options. Override only escalates (per D2)."
- If config is None, call load_config()
- If override == 'full', return 'full' immediately
- Call _get_changed_files, _count_diff_lines, _detect_change_type, _has_critical_files, _detect_ai_generated
- Logic (in order of priority):
  1. Critical files: return 'full' (cannot downgrade)
  2. AI-generated: if override == 'step0', return 'light' (reject downgrade, enforce minimum); if diff_lines > 50 return 'full'; else return 'light'
  3. comment_only or whitespace_only: return 'step0'
  4. Small non-critical changes (diff_lines < small_diff_threshold from config, default 10): return 'light'
  5. Default: return 'full' (conservative until audit data validates)
- Returns one of: 'full', 'light', 'step0'

Now modify `run_forge(diff_spec)` to accept tier routing:

1. Change signature to `run_forge(diff_spec, override_tier=None)`
2. After `skill_path` validation (line 454), insert tier classification:
```python
    config = load_config()
    tier = classify_change(diff_spec, override=override_tier, config=config)

    # Audit sampling: 10% chance of upgrading light -> full (D2)
    was_audited = False
    audit_rate = config.get('tier_classification', {}).get('audit_rate', 0.10)
    if tier == 'light' and random.random() < audit_rate:
        was_audited = True
        tier = 'full'

    # step0-only: delegate to run_dry_run and return
    if tier == 'step0':
        print(f"forge: tier classification: step0-only")
        run_dry_run(diff_spec)
        run_id = str(uuid.uuid4())
        run_record = {
            'id': run_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'commit_sha': _get_commit_sha(),
            'diff_spec': diff_spec,
            'dry_run': True,
            'tier': 'step0',
            'was_audited': False,
            'total_passes': 0,
            'total_cost_usd': 0.0,
            'total_tokens': {'input': 0, 'output': 0},
            'outcome': 'completed',
        }
        os.makedirs(RUNS_DIR, exist_ok=True)
        run_file = os.path.join(RUNS_DIR, f'{run_id}.json')
        atomic_write(run_file, run_record)
        return

    print(f"forge: tier classification: {tier}"
          + (" (audit)" if was_audited else ""))
```
3. Modify prompt construction based on tier. **(Addresses review M2: light prompt says what to do, never mentions what is being skipped):**
```python
    if tier == 'light':
        prompt = (
            f"Run a focused forge review on the git diff: {diff_spec}. "
            "Run Step 0 checks, then run one cycle of passes 1-3 "
            "(qodo-review, code-review-expert, adversarial-qe)."
        )
    else:
        prompt = (
            f"Run the full forge review pipeline on the git diff: "
            f"{diff_spec}. Follow the complete 5-step pipeline "
            f"in your system prompt."
        )
```
4. Add `'tier': tier` and `'was_audited': was_audited` to run_record dict (after 'dry_run': False)

5. **(Addresses review H1: backfill_confidence never called)** At the END of run_forge(), after the run sidecar file is written (after `atomic_write(run_file, run_record)`), add:
```python
    # Backfill confidence scores for all findings using updated FP data (H1)
    findings_data = load_findings()
    findings_data = backfill_confidence(findings_data)
    atomic_write(FINDINGS_FILE, findings_data)
```
This ensures confidence scores are recomputed after every review run, incorporating the latest FP data from newly recorded findings.

6. In `main()`, add two new arguments and update routing:
```python
    parser.add_argument(
        '--full', action='store_true',
        help='Force full review (override tier classification)',
    )
    parser.add_argument(
        '--step0', action='store_true',
        help='Force Step 0 only (rejected for critical files)',
    )
```
Update the diff_spec routing block:
```python
    elif args.diff_spec:
        if args.dry_run:
            run_dry_run(args.diff_spec)
        else:
            override = None
            if args.full:
                override = 'full'
            elif args.step0:
                override = 'step0'
            run_forge(args.diff_spec, override_tier=override)
```
  </action>
  <verify>
    <automated>cd /home/houminxi/code/forge && python3 -m py_compile cli/forge_cli.py && python3 -c "
import sys; sys.path.insert(0, 'cli')
from forge_cli import classify_change, _detect_change_type, _has_critical_files, _detect_ai_generated, _count_diff_lines
# Test critical file detection
assert _has_critical_files(['src/auth/login.py'], {}) == True, 'auth file not critical'
assert _has_critical_files(['README.md'], {}) == False, 'README should not be critical'
assert _has_critical_files(['skills/forge/SKILL.md'], {}) == True, 'SKILL.md not critical'
assert _has_critical_files(['hooks/check_review_tracker.sh'], {}) == True, 'hook not critical'
# Verify backfill_confidence is called in run_forge (H1)
import inspect
src = inspect.getsource(sys.modules['forge_cli'].run_forge)
assert 'backfill_confidence' in src, 'H1: backfill_confidence not called in run_forge'
assert 'atomic_write(FINDINGS_FILE' in src or 'atomic_write( FINDINGS_FILE' in src or 'FINDINGS_FILE' in src, 'H1: findings not persisted after backfill'
# Verify light prompt does not mention skipping (M2)
assert 'Skip' not in src.split('light')[1].split('full')[0] if 'light' in src else True, 'M2: light prompt mentions skipping'
# Verify _count_diff_lines uses --numstat (M7)
src_count = inspect.getsource(sys.modules['forge_cli']._count_diff_lines)
assert '--numstat' in src_count, 'M7: _count_diff_lines should use --numstat'
assert '--stat' not in src_count, 'M7: _count_diff_lines should NOT use --stat'
# Verify _detect_ai_generated checks commit message (M3)
src_ai = inspect.getsource(sys.modules['forge_cli']._detect_ai_generated)
assert 'git log' in src_ai or 'format=%B' in src_ai, 'M3: _detect_ai_generated should check commit message'
print('classify_change and helpers: IMPORT OK, basic checks PASSED')
"</automated>
  </verify>
  <acceptance_criteria>
    - cli/forge_cli.py contains `def classify_change(`
    - cli/forge_cli.py contains `def _get_changed_files(`
    - cli/forge_cli.py contains `def _count_diff_lines(`
    - cli/forge_cli.py contains `def _detect_change_type(`
    - cli/forge_cli.py contains `def _has_critical_files(`
    - cli/forge_cli.py contains `def _detect_ai_generated(`
    - cli/forge_cli.py contains `# Tier Classification (D2`
    - cli/forge_cli.py run_forge function contains `tier = classify_change(`
    - cli/forge_cli.py run_forge function contains `was_audited`
    - cli/forge_cli.py run_forge function contains `'tier': tier`
    - cli/forge_cli.py run_record dict contains `'was_audited': was_audited`
    - cli/forge_cli.py main() has `--full` argument
    - cli/forge_cli.py main() has `--step0` argument
    - cli/forge_cli.py main() passes override_tier to run_forge
    - run_forge prompt for 'light' tier says "Run Step 0 checks, then run one cycle of passes 1-3" (M2)
    - run_forge prompt for 'light' tier does NOT mention "Skip" or what is being omitted (M2)
    - run_forge prompt for 'full' tier mentions "complete 5-step pipeline"
    - LLM prompt never mentions tier options or tier names (anti-gaming D2)
    - run_forge calls backfill_confidence() after run sidecar is written (H1)
    - run_forge persists backfilled findings_data via atomic_write(FINDINGS_FILE, ...) (H1)
    - _detect_change_type uses `r'^\s*(?:<!--|-->|$)'` for .md files, not invalid pseudo-syntax (H2)
    - _detect_ai_generated searches both git diff output AND git log -1 --format=%B (M3)
    - _count_diff_lines uses `git diff --numstat`, not `git diff --stat` (M7)
    - python3 -m py_compile cli/forge_cli.py exits 0
  </acceptance_criteria>
  <done>Tier classification is deterministic Python in forge_cli.py. classify_change() runs before LLM invocation, returns full/light/step0. LLM receives tier-appropriate prompt text without knowing other tiers exist. 10% audit sampling silently upgrades light to full. Run sidecar records tier and was_audited. CLI accepts --full and --step0 override flags. backfill_confidence() is called at end of run_forge() and persists updated findings (H1). Markdown regex is valid Python (H2). Light prompt says what to do, not what's skipped (M2). AI detection checks commit messages too (M3). Diff line counting uses --numstat for locale independence (M7).</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| user override -> classify_change | User can request --full (always accepted) or --step0 (rejected for critical files) |
| git diff output -> classification | Diff content parsed for change type detection |
| config.json -> critical_patterns | Regex patterns applied to file paths |
| git log output -> AI detection | Commit message searched for AI markers |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01b-04 | Elevation of Privilege | user --step0 override | mitigate | classify_change rejects --step0 for critical files; returns 'full' instead |
| T-01b-05 | Tampering | LLM tier gaming | mitigate | classification in Python before LLM invocation; LLM prompt says "execute X review" not "choose tier"; LLM has no tier awareness (D2 anti-gaming) |
| T-01b-06 | Tampering | config.json critical_patterns | mitigate | config changes go through forge review pipeline (dogfooding); hardcoded defaults as fallback |
| T-01b-07 | Denial of Service | git subprocess timeout | mitigate | all subprocess.run calls have timeout=10; failure returns conservative defaults (full tier) |
| T-01b-08 | Spoofing | AI-generated detection bypass | accept | heuristic detection acknowledged as imperfect (RESEARCH.md Open Question 1); users can always use --full for known AI code |
</threat_model>

<verification>
- python3 -m py_compile cli/forge_cli.py exits 0
- grep -q 'def classify_change' cli/forge_cli.py
- grep -q 'was_audited' cli/forge_cli.py
- grep -q "'tier':" cli/forge_cli.py
- grep -q '\-\-full' cli/forge_cli.py
- grep -q '\-\-step0' cli/forge_cli.py
- grep -q 'backfill_confidence' cli/forge_cli.py (appears in run_forge body)
- grep -q 'numstat' cli/forge_cli.py
- classify_change returns 'full' for auth files regardless of override
- classify_change returns 'step0' for comment-only changes
- classify_change returns 'full' as default for normal code changes
</verification>

<success_criteria>
1. Every forge invocation classifies changes before LLM sees them
2. Critical files (auth, security, SKILL.md, hooks) always get full review
3. Comment-only and whitespace-only changes route to step0-only
4. AI-generated code gets minimum light tier (cannot downgrade to step0)
5. 10% audit sampling silently upgrades light to full for validation
6. Run sidecar records tier classification and audit flag
7. LLM prompt text varies by tier but never reveals tier system exists
8. Light tier prompt says what to do, never mentions what is skipped (M2)
9. backfill_confidence() is called and findings persisted after every run_forge() (H1)
10. AI detection checks commit message in addition to diff content (M3)
11. Diff line counting uses git diff --numstat for locale independence (M7)
</success_criteria>

<output>
After completion, create `.planning/phases/01b-trust-calibration/01b-02-SUMMARY.md`
</output>
