# Phase 1: Layer 0 Baseline + Registry - Research

**Researched:** 2026-05-15
**Domain:** Deterministic static analysis tool pipeline with delta-only violation reporting
**Confidence:** HIGH

## Summary

Phase 1 implements the deterministic foundation of forge v2.0: run static analysis tools against diffs and report only NEW violations. The core challenge is threefold -- (1) parse output from heterogeneous tools into a common format, (2) compute the delta between base and changed code to filter pre-existing violations, and (3) make the tool set extensible via a declarative YAML registry.

The research confirms that all target tools either natively emit SARIF (ruff, semgrep) or have well-documented JSON output that can be normalized to a common internal format (shellcheck JSON, clippy JSON, checkpatch emacs-mode). The delta computation approach is straightforward: parse `git diff` to extract changed line ranges, then filter tool findings to only those touching changed lines. The `python-unidiff` library (0.7.5) handles diff parsing; SARIF normalization is best done with a thin custom layer rather than heavy third-party libraries, since we only need file/line/rule/message/level -- a subset of the full SARIF spec.

**Primary recommendation:** Build a lightweight internal finding format (file, line, endLine, ruleId, level, message, toolName), write per-tool parsers that normalize to it, and filter against `git diff` changed-line sets. YAML registry at `.forge/tools.yaml` maps language globs to tool commands + output format hints. No third-party SARIF library needed -- parse the JSON directly.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: REWRITE from scratch. v1 forge_cli.py (2861 lines) is reference only. v2.0 architecture (3-state gate, loop-until-fixpoint) is fundamentally different from v1 (3-cycle counter). Clean implementation aligned with v3 design doc.
- D-02: v1 --dry-run logic (bash -n, shellcheck, pylint, non-ASCII grep) informs Layer 0 tool list but does not constrain implementation.
- D-03: YAML config file at .forge/tools.yaml. Declarative, user-editable. Each entry: name, command, args, SARIF parser, language file pattern. Adding a tool = adding a YAML block.
- D-04: Default registry ships with entries for: shellcheck (shell), ruff (python), semgrep (all), clippy (rust), checkpatch.pl (kernel C). Users can add/override.
- D-05: Terminal output = plain text summary (human-readable, cargo-check style). Machine state = .forge/state.json (round-to-round tracking, inspectable).
- D-06: Tool output internally normalized via SARIF parsing before delta computation. SARIF is internal plumbing, not user-facing.
- D-07: KEEP: check_worktree.sh, check_non_ascii.sh. DROP: check_git_commit_review.sh, check_git_push_review.sh, check_review_tracker.sh.

### Claude's Discretion
- SARIF parser implementation strategy (per-tool vs generic)
- .forge/ directory structure for state files
- Internal module layout for v2.0 codebase

### Deferred Ideas (OUT OF SCOPE)
- None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| GATE-01 | Forge produces 3-state verdict (PASS/HOLD/FAIL) with ESCALATED sub-state (exit 2) | Phase 1 implements PASS/FAIL only (no HOLD -- that requires LLM in Phase 2). Exit codes: 0=PASS, 1=FAIL. ESCALATED deferred to Phase 2. |
| GATE-02 | FAIL verdict is deterministic given (code, tool versions, execution) | Delta computation + pinned tool versions guarantee determinism. Same diff + same tools = same FAIL. |
| GATE-04 | Layer 0 violations and confirmed findings gate-block; advisory/judgment findings never gate | Phase 1 only has Layer 0 -- all violations are gate-blocking. Advisory findings are Phase 2+. |
| LAYER0-01 | Per-language tool registry (shellcheck, ruff, semgrep, clippy, checkpatch) | YAML registry at .forge/tools.yaml with language glob patterns. Each tool entry is declarative. |
| LAYER0-02 | Baseline mode flags only NEW violations introduced by the diff | Delta computation: parse git diff for changed lines, filter tool output to changed-line intersection. |
| LAYER0-03 | SARIF parsing + line-number drift handling for delta computation | Per-tool output parsers normalize to internal finding format. Line drift handled by matching findings to diff hunk ranges rather than exact line numbers. |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Tool registry loading | CLI (Python) | -- | Config parsing is a CLI startup responsibility |
| Diff parsing | CLI (Python) | git (subprocess) | Git provides raw diff; Python parses hunk structure |
| Tool execution | CLI (Python) | External tools (subprocess) | Python orchestrates; tools are external processes |
| Output normalization | CLI (Python) | -- | Per-tool parsers are pure Python data transforms |
| Delta computation | CLI (Python) | -- | Set intersection of findings vs changed lines |
| Verdict determination | CLI (Python) | -- | Pure function: empty delta = PASS, non-empty = FAIL |
| State persistence | CLI (Python) | Filesystem (.forge/) | JSON files written atomically |
| Terminal output | CLI (Python) | -- | Plain text formatting, cargo-check style |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.14.4 | Runtime | Available on system [VERIFIED: `python3 --version`] |
| PyYAML | 6.0.2 | Parse .forge/tools.yaml registry | Standard Python YAML parser, already installed [VERIFIED: `python3 -c "import yaml"`] |
| argparse | stdlib | CLI argument parsing | Standard library, no dependency |
| subprocess | stdlib | Execute external tools | Standard library, no dependency |
| json | stdlib | Parse tool JSON output, write state files | Standard library, no dependency |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| unidiff | 0.7.5 | Parse git unified diff into structured hunks | Delta computation -- extract changed line ranges from diff [VERIFIED: PyPI version 0.7.5] |
| pathlib | stdlib | File path manipulation | Used throughout for cross-platform path handling |
| hashlib | stdlib | Tool version fingerprinting for baseline cache | Detect tool version changes between runs |
| dataclasses | stdlib | Internal finding/verdict data structures | Clean data modeling without boilerplate |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| unidiff | Manual diff parsing | unidiff handles edge cases (binary files, renames, mode changes) that manual parsing misses; 42KB pure Python, no C deps |
| unidiff | pygit2 | pygit2 requires libgit2 C library; heavier dependency for simple diff parsing |
| PyYAML | strictyaml | StrictYAML (1.7.3) adds schema validation but PyYAML is already installed and YAML schema validation can be done in application code |
| sarif-tools | Direct JSON parsing | sarif-tools (3.0.5) is a full framework; we only need 5 fields from SARIF -- direct JSON parsing is simpler and has zero dependencies |
| sarif-om | Direct JSON parsing | sarif-om provides typed classes but adds a dependency for minimal benefit -- our internal format is simpler than full SARIF |

**Installation:**
```bash
# unidiff is the only new dependency
uv pip install --system unidiff  # or: pip install unidiff
# PyYAML already installed (6.0.2)
# All other dependencies are stdlib
```

**Version verification:**
- Python 3.14.4 [VERIFIED: `python3 --version` on system]
- PyYAML 6.0.2 [VERIFIED: `python3 -c "import yaml; print(yaml.__version__)"` on system]
- unidiff 0.7.5 [VERIFIED: PyPI listing, 2024-01-12 release]
- shellcheck 0.11.0 [VERIFIED: `shellcheck --version` on system]
- ruff 0.15.13 [VERIFIED: `ruff --version` on system, installed via `uv tool`]
- semgrep 1.161.0 [VERIFIED: `semgrep --version` on system]
- cargo/clippy 1.95.0 [VERIFIED: `cargo --version` on system, clippy component installed]

## Architecture Patterns

### System Architecture Diagram

```
                     .forge/tools.yaml
                           |
                     [Registry Loader]
                           |
                     +-----v------+
                     | Tool Config |  (name, cmd, args, lang_glob, output_format)
                     +-----+------+
                           |
    git diff --name-only   |    git diff -U0
         |                 |         |
    [Changed Files]   [Tool Router]  [Diff Parser (unidiff)]
         |                 |         |
         |          +------v------+  |
         |          | Per-file    |  |
         |          | tool match  |  |
         |          | (lang_glob) |  |
         |          +------+------+  |
         |                 |         |
         v                 v         v
    +----------+    +-----------+  +---------------+
    | File     |    | Tool      |  | Changed Lines |
    | Filter   |    | Executor  |  | per file      |
    | (exists?)|    | (subproc) |  | {file: set()}  |
    +----+-----+    +-----+-----+  +-------+-------+
         |               |                |
         |        +------v------+         |
         |        | Output      |         |
         |        | Normalizer  |         |
         |        | (per-tool   |         |
         |        |  parser)    |         |
         |        +------+------+         |
         |               |                |
         |        +------v------+         |
         |        | Internal    |         |
         |        | Findings    |<--------+
         |        | [{file,line,|  (filter: finding.line
         |        |   rule,...}]|   in changed_lines[file])
         |        +------+------+
         |               |
         |        +------v------+
         |        | Delta       |
         |        | Filter      |
         |        +------+------+
         |               |
         |        +------v------+
         |        | Verdict     |----> exit 0 (PASS)
         |        | (empty=PASS |      exit 1 (FAIL)
         |        |  else=FAIL) |
         |        +------+------+
         |               |
         |        +------v------+
         +------->| Reporter    |----> Terminal (plain text)
                  | + State     |----> .forge/state.json
                  +-------------+
```

### Recommended Project Structure
```
forge/                       # project root
  .forge/
    tools.yaml               # tool registry (user-editable)
    state.json               # round-to-round state (machine-written)
  src/
    forge/
      __init__.py
      cli.py                 # argparse entry point
      registry.py            # YAML registry loader + validation
      diff.py                # git diff parsing, changed-line extraction
      runner.py              # tool execution (subprocess orchestration)
      parsers/
        __init__.py
        base.py              # abstract base + internal Finding dataclass
        shellcheck.py         # shellcheck JSON -> Finding
        ruff.py              # ruff SARIF -> Finding
        semgrep.py           # semgrep SARIF -> Finding
        clippy.py            # clippy JSON -> Finding
        checkpatch.py        # checkpatch emacs -> Finding
        non_ascii.py         # grep non-ASCII -> Finding
      delta.py               # delta computation (findings vs changed lines)
      verdict.py             # PASS/FAIL determination + exit codes
      reporter.py            # terminal output formatting
      state.py               # .forge/state.json read/write
  hooks/
    check_worktree.sh        # kept from v1
    check_non_ascii.sh       # kept from v1
  tests/
    test_registry.py
    test_diff.py
    test_parsers.py
    test_delta.py
    test_verdict.py
    test_integration.py
```

### Pattern 1: Internal Finding Format
**What:** A normalized data structure that all tool parsers emit, decoupling tool-specific output from delta computation.
**When to use:** Always -- every tool output flows through this.
**Example:**
```python
# Source: Design doc Part 2 + SARIF 2.1.0 spec structure
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Finding:
    """Normalized finding from any static analysis tool."""
    file: str           # relative path from repo root
    line: int           # 1-based start line
    end_line: int       # 1-based end line (same as line if single-line)
    column: int         # 1-based start column (0 if unknown)
    rule_id: str        # tool-specific rule identifier (e.g., SC2154, F401)
    level: str          # "error" | "warning" | "note"
    message: str        # human-readable description
    tool_name: str      # which tool produced this (e.g., "shellcheck")
    fix: Optional[str] = None  # suggested fix text, if available
```

### Pattern 2: Tool Registry Entry
**What:** YAML schema for declaring a tool in the registry.
**When to use:** Adding any new tool to forge.
**Example:**
```yaml
# Source: Design doc D-03 + D-04
# .forge/tools.yaml
tools:
  shellcheck:
    command: shellcheck
    args: ["-f", "json"]
    output_format: shellcheck_json   # parser to use
    file_patterns: ["*.sh", "*.bash"]
    required: false                  # missing tool = skip, not error
    timeout: 30                      # seconds

  ruff:
    command: ruff
    args: ["check", "--output-format", "sarif"]
    output_format: sarif
    file_patterns: ["*.py"]
    required: false
    timeout: 60

  semgrep:
    command: semgrep
    args: ["--config", "auto", "--sarif", "--quiet"]
    output_format: sarif
    file_patterns: ["*"]             # all languages
    required: false
    timeout: 120

  clippy:
    command: cargo
    args: ["clippy", "--message-format=json", "--quiet"]
    output_format: clippy_json
    file_patterns: ["*.rs"]
    required: false
    timeout: 120
    working_dir: cargo_root          # special: find Cargo.toml

  checkpatch:
    command: scripts/checkpatch.pl
    args: ["--emacs", "--show-types", "--color=never"]
    output_format: checkpatch_emacs
    file_patterns: ["*.c", "*.h"]
    required: false
    timeout: 60

  non_ascii:
    command: grep
    args: ["-Pn", "[^\\x00-\\x7F]"]
    output_format: grep_line
    file_patterns: ["*"]
    exclude_patterns: ["*.po", "*.pot", "*.png", "*.jpg", "*.gif",
                       "*.ico", "*.pdf", "*.mo"]
    required: true                   # always available (grep)
    timeout: 10
```

### Pattern 3: Delta Computation
**What:** Filter tool findings to only those on lines changed by the diff.
**When to use:** Always -- this is the core of baseline mode.
**Example:**
```python
# Source: Design doc Part 2 BASELINE MODE section
import unidiff

def extract_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Parse unified diff, return {file: set_of_changed_line_numbers}.

    Only added/modified lines are included (not deletions).
    Deleted files are excluded entirely.
    """
    changed = {}
    patch_set = unidiff.PatchSet(diff_text)
    for patched_file in patch_set:
        if patched_file.is_removed_file:
            continue  # deleted file: no violations possible
        filepath = patched_file.path
        lines = set()
        for hunk in patched_file:
            for line in hunk:
                if line.is_added:
                    lines.add(line.target_line_no)
        if lines:
            changed[filepath] = lines
    return changed

def filter_delta(findings: list[Finding],
                 changed_lines: dict[str, set[int]]) -> list[Finding]:
    """Keep only findings whose file+line intersects changed lines."""
    delta = []
    for f in findings:
        if f.file not in changed_lines:
            continue  # file not in diff
        file_lines = changed_lines[f.file]
        # Check if any line in the finding's range is in the changed set
        finding_range = range(f.line, f.end_line + 1)
        if any(l in file_lines for l in finding_range):
            delta.append(f)
    return delta
```

### Pattern 4: Tool Execution with Graceful Degradation
**What:** Run external tools via subprocess, handle missing tools gracefully.
**When to use:** Every tool invocation.
**Example:**
```python
# Source: v1 forge_cli.py _run_tool pattern + design doc "missing tool -> skip"
import subprocess
import shutil

def run_tool(tool_config: dict,
             files: list[str],
             timeout: int = 30) -> tuple[str, int] | None:
    """Run a tool, return (stdout, returncode) or None if unavailable.

    Missing tool with required=false -> None (skip with log).
    Missing tool with required=true -> raise RuntimeError.
    """
    cmd = tool_config['command']
    if not shutil.which(cmd):
        if tool_config.get('required', False):
            raise RuntimeError(f"Required tool not found: {cmd}")
        return None  # optional tool not installed

    full_cmd = [cmd] + tool_config.get('args', []) + files
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        return (result.stdout, result.returncode)
    except (OSError, FileNotFoundError,
            subprocess.TimeoutExpired) as exc:
        # Log but do not crash
        return None
```

### Anti-Patterns to Avoid
- **Running tools on all files, then filtering:** Run tools only on changed files to avoid unnecessary work and false positives from pre-existing code. Exception: semgrep with `--config auto` may need full context for some rules.
- **Relying on tool exit codes for finding count:** Some tools exit 0 even with findings (semgrep default), others exit 1 on any finding (shellcheck). Always parse output, never rely solely on exit codes.
- **Hardcoding tool paths:** Use `shutil.which()` to resolve commands. The registry should store command names, not absolute paths.
- **Parsing tool output with regex:** Tool JSON/SARIF output has well-defined structure. Parse as JSON, never regex. Regex breaks on edge cases (messages containing colons, multiline output).
- **Blocking on optional tools:** If clippy is not installed but no .rs files are in the diff, that is not an error. If clippy IS needed and missing, log a note and skip -- never hard-fail on optional tools.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Unified diff parsing | Custom line-by-line parser | `unidiff` library (0.7.5) | Handles binary files, renames, mode changes, encoding edge cases; 42KB pure Python [VERIFIED: PyPI] |
| YAML config loading | Custom YAML parser | `PyYAML` (6.0.2) | Standard, already installed, handles all YAML features needed [VERIFIED: system] |
| JSON output parsing | Regex-based extraction | `json.loads()` stdlib | SARIF and shellcheck JSON are well-formed; stdlib JSON is correct by construction |
| Tool binary lookup | PATH string splitting | `shutil.which()` stdlib | Cross-platform, handles PATH correctly, returns None for missing tools |
| Atomic file writes | Direct open/write | tempfile + os.replace | v1 `file_utils.py` already has `atomic_write()` -- reuse pattern for state.json writes |
| Git diff execution | Manual git subprocess | Thin wrapper function | v1 `_run_git()` pattern is correct -- capture_output, timeout, check=False |

**Key insight:** The tools do the hard work (analysis). Forge's job is plumbing: run tools, parse output, compute delta, render verdict. Every piece of this plumbing has a well-tested solution.

## Common Pitfalls

### Pitfall 1: Tool Output Format Varies by Version
**What goes wrong:** A tool update changes JSON field names or SARIF structure, breaking the parser silently (empty findings parsed as "clean").
**Why it happens:** Tools like ruff iterate rapidly; SARIF compliance varies.
**How to avoid:** Record tool version in state.json alongside findings. Parsers should validate expected fields exist and raise clear errors on missing fields. Test parsers against pinned tool output fixtures.
**Warning signs:** Forge reports PASS on code that clearly has violations.

### Pitfall 2: Line Number Mismatch Between Tool and Diff
**What goes wrong:** Tool reports violation on line 42 of the file, but the diff changed lines 40-45 of the TARGET file. If the tool ran on the full file (which may have different line numbering than the diff's hunk headers), the line numbers may not match.
**Why it happens:** Tools run on actual files, not diff patches. The diff shows target-side line numbers. These should match for added lines, but context-dependent tools may report on surrounding unchanged lines.
**How to avoid:** Run tools on the actual working tree files (post-patch), parse diff for target-side changed line numbers. Both reference the same file state. For findings that span a range (startLine to endLine), check if ANY line in the range intersects the changed set.
**Warning signs:** Known violations in changed code showing as PASS.

### Pitfall 3: Deleted Files Generate False Violations
**What goes wrong:** A tool is run against a file that was deleted in the diff, or findings from deleted files appear in the delta.
**Why it happens:** `git diff --name-only` lists deleted files. If forge runs tools on those files, they will not exist on disk and the tool will error.
**How to avoid:** Filter changed files to only those that exist on disk (`os.path.isfile()`). The delta computation should also exclude deleted files (their violations are removed, not added).
**Warning signs:** FileNotFoundError from tool subprocess, or spurious FAIL on cleanup commits.

### Pitfall 4: Semgrep `--config auto` Downloads Rules at Runtime
**What goes wrong:** First run downloads rule packs from semgrep.dev, adding 5-30 seconds. CI environments without internet access fail. Different runs may get different rule versions.
**Why it happens:** `--config auto` fetches the latest community ruleset.
**How to avoid:** Document this latency in the tool registry. For determinism, consider `--config p/default` or a pinned local rule pack. For offline environments, provide a `semgrep_rules_dir` config option.
**Warning signs:** First run much slower than subsequent runs; CI failures with network errors.

### Pitfall 5: cargo clippy Requires Project Context
**What goes wrong:** Running `cargo clippy` on individual .rs files fails because clippy needs Cargo.toml and the full crate structure.
**Why it happens:** Clippy is a compiler plugin, not a file-level linter. It needs the full compilation context.
**How to avoid:** The registry entry for clippy must specify `working_dir: cargo_root` to indicate forge should find the nearest Cargo.toml and run clippy from that directory, passing the full crate. Findings are then filtered to only the changed files.
**Warning signs:** clippy subprocess errors about missing crate root, or zero findings on Rust code.

### Pitfall 6: checkpatch.pl Path Resolution
**What goes wrong:** checkpatch.pl is not a system binary -- it lives in the kernel tree at `scripts/checkpatch.pl`. Running it outside a kernel tree fails.
**Why it happens:** checkpatch is a kernel development tool, not a general-purpose linter.
**How to avoid:** The registry should allow specifying a relative path from the project root (`scripts/checkpatch.pl`). The tool runner resolves this against the working directory. If the script is not found, skip gracefully (required: false).
**Warning signs:** "command not found" for checkpatch when running forge outside a kernel tree.

### Pitfall 7: Non-ASCII Check Excludes
**What goes wrong:** Binary files (images, PDFs) or localization files (.po) trigger non-ASCII violations.
**Why it happens:** grep -P '[^\x00-\x7F]' matches any non-ASCII byte.
**How to avoid:** The existing check_non_ascii.sh already excludes *.po, *.pot, *.mo, *.png, *.jpg, *.gif, *.ico, *.pdf, /tmp/*. The registry entry must replicate these excludes via `exclude_patterns`. [VERIFIED: hooks/check_non_ascii.sh source]
**Warning signs:** FAIL on commits that only touch image or translation files.

## Code Examples

### Example 1: Shellcheck JSON Parser
```python
# Source: shellcheck -f json output format
# [VERIFIED: `shellcheck -f json` produces this format, tested on system]
import json

def parse_shellcheck(output: str, tool_name: str = "shellcheck", exit_code: int = 0) -> list:
    """Parse shellcheck JSON output into Finding objects.

    NOTE: This example is illustrative. Plan 01-02 contract requires returning
    [ToolError(...)] on parse failure (NOT []), with exit_code propagated from the
    runner. Implementation MUST follow Plan 01-02 contract, not the simplified
    example below.
    """
    if not output.strip():
        return []
    try:
        raw = json.loads(output)
    except json.JSONDecodeError:
        return [ToolError(tool_name=tool_name, exit_code=exit_code, stderr="",
                          message=f"Failed to parse {tool_name} JSON output")]

    findings = []
    for item in raw:
        findings.append(Finding(
            file=item['file'],
            line=item['line'],
            end_line=item.get('endLine', item['line']),
            column=item.get('column', 0),
            rule_id=f"SC{item['code']}",
            level=item.get('level', 'warning'),
            message=item['message'],
            tool_name=tool_name,
            fix=None,
        ))
    return findings
```

### Example 2: SARIF Parser (ruff, semgrep)
```python
# Source: SARIF 2.1.0 spec + ruff SARIF output
# [VERIFIED: `ruff check --output-format sarif` produces this format]
import json

def parse_sarif(output: str, tool_name: str, exit_code: int = 0) -> list:
    """Parse SARIF 2.1.0 JSON output into Finding objects.

    NOTE: Same caveat as parse_shellcheck -- on parse failure, return
    [ToolError(...)] per Plan 01-02 contract, NOT [].
    """
    if not output.strip():
        return []
    try:
        sarif = json.loads(output)
    except json.JSONDecodeError:
        return [ToolError(tool_name=tool_name, exit_code=exit_code, stderr="",
                          message=f"Failed to parse {tool_name} SARIF output")]

    findings = []
    for run in sarif.get('runs', []):
        for result in run.get('results', []):
            for location in result.get('locations', []):
                phys = location.get('physicalLocation', {})
                artifact = phys.get('artifactLocation', {})
                region = phys.get('region', {})
                uri = artifact.get('uri', '')
                # Strip file:// prefix if present
                if uri.startswith('file:///'):
                    uri = uri[len('file:///'):]
                elif uri.startswith('file://'):
                    uri = uri[len('file://'):]
                findings.append(Finding(
                    file=uri,
                    line=region.get('startLine', 0),
                    end_line=region.get('endLine',
                                       region.get('startLine', 0)),
                    column=region.get('startColumn', 0),
                    rule_id=result.get('ruleId', 'unknown'),
                    level=result.get('level', 'warning'),
                    message=result.get('message', {}).get('text', ''),
                    tool_name=tool_name,
                ))
    return findings
```

### Example 3: Verdict Determination
```python
# Source: Design doc Part 1 + Part 8 exit conditions
import sys

def determine_verdict(delta_findings: list) -> int:
    """Return exit code: 0 = PASS, 1 = FAIL."""
    if not delta_findings:
        return 0  # PASS: no new violations
    return 1      # FAIL: new violations found

def format_verdict(delta_findings: list,
                   all_findings: list,
                   tool_versions: dict) -> str:
    """Format terminal output, cargo-check style."""
    lines = []
    if delta_findings:
        lines.append(
            f"forge: FAIL -- {len(delta_findings)} new violation(s)"
        )
        lines.append("")
        for f in delta_findings:
            lines.append(
                f"  {f.file}:{f.line}: [{f.tool_name}/{f.rule_id}] "
                f"{f.level}: {f.message}"
            )
        lines.append("")
        lines.append(
            f"forge: fix {len(delta_findings)} violation(s) "
            f"before commit"
        )
    else:
        skipped = len(all_findings) - len(delta_findings)
        lines.append("forge: PASS -- no new violations")
        if skipped:
            lines.append(
                f"  ({skipped} pre-existing violation(s) in "
                f"unchanged code, not blocking)"
            )
    return '\n'.join(lines)
```

## Tool Output Format Reference

This section documents the actual output format of each tool, verified on the development system.

### shellcheck
**Native formats:** tty (default), gcc, checkstyle, diff, json, json1, quiet [VERIFIED: man page]
**Recommended:** `-f json` -- structured, includes all fields needed
**JSON structure per finding:**
```json
{"file": "/path/to/script.sh", "line": 4, "endLine": 4,
 "column": 7, "endColumn": 9, "level": "warning",
 "code": 2154, "message": "y is referenced but not assigned.",
 "fix": null}
```
[VERIFIED: actual `shellcheck -f json` output on system]

### ruff
**Native SARIF:** `--output-format sarif` [VERIFIED: ruff docs]
**Other formats:** concise, full, json, json-lines, junit, grouped, github, gitlab, pylint, rdjson, azure, sarif
**SARIF compliance:** Standard 2.1.0 with `$schema`, includes `fixes` array
[VERIFIED: actual `ruff check --output-format sarif` output on system]

### semgrep
**Native SARIF:** `--sarif` flag [VERIFIED: semgrep docs]
**Other formats:** `--text`, `--json`, `--gitlab-sast`, `--gitlab-secrets`, `--junit-xml`, `--emacs`, `--vim`
**SARIF compliance:** Standard 2.1.0, includes rules in driver object
[VERIFIED: actual `semgrep --sarif` output on system]

### clippy
**No native SARIF.** Use `cargo clippy --message-format=json` to get Rust compiler JSON diagnostics, then normalize with custom parser. [CITED: github.com/psastras/sarif-rs]
**Alternative:** `clippy-sarif` crate (v0.8.0) converts clippy JSON to SARIF, but adds a Rust build dependency. Recommendation: parse clippy JSON directly.

### checkpatch.pl
**No native SARIF or JSON.** Use `--emacs --show-types --color=never` for machine-parseable output.
**Emacs format:** `file:line: TYPE: message`
Example: `drivers/net/foo.c:42: WARNING:LONG_LINE: line length 82 exceeds 80 columns`
[CITED: docs.kernel.org/dev-tools/checkpatch.html]

### Parser Strategy Decision (Claude's Discretion)

**Recommendation: Per-tool parsers, not a generic SARIF-only approach.**

Rationale:
1. Only 2 of 6 tools emit native SARIF (ruff, semgrep). The rest need custom parsers anyway.
2. A generic SARIF parser still needs per-tool quirk handling (URI schemes, rule ID formats).
3. Per-tool parsers are 15-30 lines each and trivially testable against fixtures.
4. The internal Finding dataclass IS the common format -- parsers are the adapters.

Structure: `parsers/base.py` defines Finding + abstract parse function. Each tool gets a module that implements the parse function. A dispatch dict maps `output_format` from the registry to the correct parser.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pylint for Python linting | ruff (Rust-based, 10-100x faster) | 2023-2024 | ruff replaces pylint/flake8/isort/pyupgrade in one tool; design doc D-02 specifies ruff over pylint |
| Per-tool text parsing | SARIF as interchange format | 2023+ (OASIS standard matured) | ruff and semgrep both emit SARIF natively; industry convergence |
| Full-codebase analysis | Diff-only / new-code analysis | SonarQube pioneered, now standard | SonarQube "Clean as You Code" philosophy matches forge baseline mode |
| shellcheck text output | shellcheck JSON output | shellcheck 0.7+ | Machine-parseable without regex |
| Manual non-ASCII checks | Hook-based enforcement | v1 forge | check_non_ascii.sh as Claude Code PreToolUse hook |

**Deprecated/outdated:**
- pylint as default Python linter: ruff is the current standard. v1 used pylint as fallback; v2.0 should use ruff as primary. [VERIFIED: design doc D-02]
- v1 forge_cli.py architecture: replaced entirely by v2.0 design. Do not reference v1 patterns for the pipeline structure.

## Design Doc Deferred Items for Phase 1

The design doc (Part 10 Q5) lists several items that must be addressed during Phase 1:

| Item | Description | Research Finding |
|------|-------------|------------------|
| S1 | Layer 0 baseline delta computation | Covered by this research: SARIF parsers, line drift, file deletion, tool version pinning, metric thresholds |
| D3 | Per-language tool registry spec | Covered: YAML schema in Pattern 2 above |
| D6 | Advisory/confirmed budget priority | Phase 1 only has Layer 0 (no advisory); document that confirmed are always processed first |
| D8 | D17 metric comparison definition | Defer to Phase 2 (state machine); Phase 1 has no multi-round comparison |
| D9 | Layer 0 per-finding fix attempt cap | Phase 1 does not have a fix loop; document MAX_LAYER0_FIX_ATTEMPTS=3 for Phase 2 |
| D10 | Sampling audit detail | Defer to Phase 5 (disposition governance) |

## Assumptions Log

> List all claims tagged [ASSUMED] in this research. The planner and discuss-phase use this
> section to identify decisions that need user confirmation before execution.

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | unidiff 0.7.5 is sufficient for all git diff edge cases (renames, binary markers, submodule changes) | Standard Stack | Low -- unidiff is mature and widely used; edge cases can be handled with fallback to raw parsing |
| A2 | checkpatch.pl emacs format is stable across kernel versions | Tool Output Format Reference | Low -- format has been stable for 10+ years; if it changes, parser is 15 lines to update |
| A3 | clippy JSON message format is the same as rustc diagnostic JSON | Tool Output Format Reference | Medium -- if format differs, parser needs adjustment; can be verified with a test fixture |
| A4 | `unidiff` can be installed in the project's Python environment | Standard Stack | Low -- pure Python, no C deps; worst case, can vendor the 42KB module |

**If this table is empty:** N/A -- 4 assumptions identified above.

## Open Questions (RESOLVED)

1. **Python packaging approach for v2.0**
   - What we know: v1 is loose scripts in `cli/`. v2.0 is a rewrite.
   - What is unclear: Should v2.0 use a proper `pyproject.toml` with a `forge` entry point, or remain as loose scripts?
   - Recommendation: Use `pyproject.toml` with `[project.scripts] forge = "forge.cli:main"` for clean installation. This is Claude's discretion per CONTEXT.md (internal module layout).

2. **Semgrep rule pinning for determinism**
   - What we know: `--config auto` fetches latest rules from semgrep.dev. This breaks determinism (GATE-02).
   - What is unclear: Whether to ship a pinned rule set, use `--config p/default`, or let users configure.
   - Recommendation: Default to `--config auto` for convenience but document in tools.yaml that users can override with a local config. Add a note in state.json recording which semgrep config was used.

3. **How to handle worsening-existing-violation (design doc S1)**
   - What we know: Design doc S1 asks whether a severity increase on an existing violation should gate-block.
   - What is unclear: The design doc defers this decision.
   - Recommendation: For Phase 1, treat it as NOT gate-blocking (consistent with "only NEW violations"). A finding on an unchanged line is pre-existing regardless of severity change. This can be revisited later.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | Runtime | Yes | 3.14.4 | -- |
| PyYAML | Registry loading | Yes | 6.0.2 | -- |
| git | Diff parsing | Yes | 2.53.0 | -- |
| shellcheck | Shell linting | Yes | 0.11.0 | Skip shell checks |
| ruff | Python linting | Yes | 0.15.13 | Skip Python checks |
| semgrep | SAST scanning | Yes | 1.161.0 | Skip SAST checks |
| cargo + clippy | Rust linting | Yes | 1.95.0 | Skip Rust checks |
| checkpatch.pl | Kernel C linting | Yes (at kernel tree) | -- | Skip kernel checks |
| jq | JSON processing (hooks) | Yes | 1.8.1 | -- |
| unidiff | Diff parsing library | No (needs install) | 0.7.5 (PyPI) | Manual diff parsing (complex) |
| grep -P | Non-ASCII detection | Yes | system | -- |

**Missing dependencies with no fallback:**
- None -- all critical tools are available.

**Missing dependencies with fallback:**
- `unidiff` needs installation (`pip install unidiff` or vendor the module). Fallback is manual diff parsing but unidiff is strongly recommended.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | N/A -- local CLI tool |
| V3 Session Management | No | N/A -- stateless per invocation |
| V4 Access Control | No | N/A -- runs as invoking user |
| V5 Input Validation | Yes | validate_diff_spec (flag injection prevention) + YAML safe_load |
| V6 Cryptography | No | N/A -- no crypto operations |

### Known Threat Patterns for CLI Tool Pipeline

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Command injection via diff_spec | Tampering | `validate_diff_spec()` from v1 file_utils.py -- rejects flags and shell metacharacters [VERIFIED: source code] |
| YAML deserialization attack | Tampering | `yaml.safe_load()` -- never `yaml.load()` with Loader=FullLoader/UnsafeLoader |
| Subprocess shell injection | Tampering | Never `shell=True` in subprocess calls; pass args as list [VERIFIED: v1 pattern] |
| Tool binary hijacking (PATH) | Spoofing | `shutil.which()` resolves from PATH; document that PATH must be trusted |
| Malicious SARIF/JSON from tool | Tampering | JSON parsed with `json.loads()` into dicts only; no eval/exec on tool output |

## Sources

### Primary (HIGH confidence)
- shellcheck 0.11.0 JSON output format -- verified via `shellcheck -f json` on system
- ruff 0.15.13 SARIF output format -- verified via `ruff check --output-format sarif` on system
- semgrep 1.161.0 SARIF output -- verified via `semgrep --sarif` on system
- SARIF 2.1.0 specification structure -- [OASIS SARIF v2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html)
- checkpatch.pl output format -- [Kernel docs](https://docs.kernel.org/dev-tools/checkpatch.html)
- v1 forge_cli.py _run_tool/_run_git patterns -- verified via source code read
- v1 file_utils.py atomic_write/validate_diff_spec -- verified via source code read
- hooks/check_non_ascii.sh exclude patterns -- verified via source code read

### Secondary (MEDIUM confidence)
- [unidiff PyPI](https://pypi.org/project/unidiff/) -- v0.7.5, pure Python unified diff parser
- [sarif-tools PyPI](https://pypi.org/project/sarif-tools/) -- v3.0.5, Microsoft SARIF tools (not recommended for use, but referenced)
- [clippy-sarif crate](https://crates.io/crates/clippy-sarif) -- v0.8.0, Rust clippy-to-SARIF converter
- [shellcheck-sarif crate](https://crates.io/crates/shellcheck-sarif) -- v0.8.0, shellcheck-to-SARIF (not needed -- parse JSON directly)
- [SonarQube new code baseline](https://docs.sonarsource.com/sonarqube-server/2025.2/project-administration/configuring-new-code-calculation) -- conceptual model for delta computation

### Tertiary (LOW confidence)
- None -- all findings were verified via system tools or official documentation.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all tools verified on system, versions confirmed
- Architecture: HIGH -- design doc is detailed, patterns are well-established
- Pitfalls: HIGH -- verified against actual tool behavior on system

**Research date:** 2026-05-15
**Valid until:** 2026-06-15 (tools are stable; ruff iterates fast but output format is stable)
