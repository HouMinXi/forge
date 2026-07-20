# Phase 18: Taint + Provenance - Research

**Researched:** 2026-06-10
**Requirement IDs:** REVIEW-TRUST-01

## Executive Summary

Phase 18 delivers three sub-capabilities. Research findings per sub-capability:

1. **Danger-score (L0 blocking)**: Reuse `trust.py::find_dangerous_fields` applied to diff new-lines. Output as direct StateFinding (source=L0, CONFIRMED). Low complexity -- mostly wiring.
2. **Taint advisory axis**: Semgrep CE supports `mode: taint` rules with `pattern-sources` / `pattern-sinks` in YAML. Intraprocedural only in CE (cross-function requires Pro). CLI: `semgrep scan --config rules.yaml --sarif <files>`. SARIF output parsed by existing `_sarif.py`. Taint findings include `dataflow_trace` with `taint_source`/`taint_sink`/`intermediate_vars`.
3. **Adversarial provenance**: Append question to `pass3-adversarial.md`. Zero code change -- purely prompt modification.

## Research Area 1: Semgrep Custom Taint Rule Syntax

### forge-taint.yaml Structure

Semgrep taint rules use `mode: taint` with `pattern-sources` and `pattern-sinks`:

```yaml
rules:
  - id: forge-taint-config-to-subprocess
    severity: WARNING
    languages: [python]
    message: >
      Tainted data from $SOURCE flows to subprocess sink.
      Intraprocedural only -- cross-function flows not detected.
    mode: taint
    pattern-sources:
      - pattern: os.environ[...]
      - pattern: os.environ.get(...)
      - pattern: os.getenv(...)
      - pattern: yaml.safe_load(...)
      - pattern: yaml.load(...)
      - pattern: json.load(...)
      - pattern: open(...)
    pattern-sinks:
      - pattern: subprocess.run(...)
      - pattern: subprocess.Popen(...)
      - pattern: subprocess.call(...)
      - pattern: os.system(...)
      - pattern: os.popen(...)
      - pattern: urllib.request.urlopen(...)
      - pattern: requests.get(...)
      - pattern: requests.post(...)
```

Key findings from semgrep docs (2026-05-11):
- `pattern-sources` and `pattern-sinks` act as `pattern-either` operators (any match counts)
- Intraprocedural is the default for CE -- no `--pro` flag needed
- `pattern-propagators` require Semgrep Pro for cross-function; CE only tracks within a single function
- `taint_assume_safe_functions: true` option reduces noise by treating opaque calls as non-propagating
- Multiple rules can coexist in one YAML file under `rules:` array

### Source/Sink Pairs (from D-12)

| Sources | Sinks |
|---------|-------|
| `os.environ[...]` | `subprocess.run(...)` |
| `os.environ.get(...)` | `subprocess.Popen(...)` |
| `os.getenv(...)` | `subprocess.call(...)` |
| `yaml.safe_load(...)` | `os.system(...)` |
| `yaml.load(...)` | `os.popen(...)` |
| `json.load(...)` | `urllib.request.urlopen(...)` |
| `open(<config>)` | `requests.get(...)` / `requests.post(...)` |
| | `open(...)` (write-mode) |

### Semgrep Rule Testing (from semgrep/skills + official docs)

Semgrep has a built-in test framework using inline annotations:

```python
# ruleid: forge-taint-config-to-subprocess
val = os.environ["SECRET"]
subprocess.run(val, shell=True)

# ok: forge-taint-config-to-subprocess
subprocess.run(["ls"], shell=False)
```

Commands:
- `semgrep --validate --config rule.yaml` -- validate YAML syntax
- `semgrep --test --config rule.yaml test-file` -- run test annotations
- `semgrep --dataflow-traces -f rule.yaml file` -- debug taint flow

File convention: `rule-id.yaml` + `rule-id.py` (test file) in same directory or use `--config` + positional target.

Annotation rules:
- `# ruleid:` on the line IMMEDIATELY BEFORE the vulnerable code (must match)
- `# ok:` on the line IMMEDIATELY BEFORE safe code (must not match)
- `# todoruleid:` for known limitations (won't fail test)
- `# todook:` for known false positives (won't fail test)

### focus-metavariable for Precise Sink Matching

For sinks like `subprocess.call($CMD, shell=True, ...)`, use `focus-metavariable: $CMD` to match only the command argument, not the entire call:

```yaml
pattern-sinks:
  - pattern: subprocess.call($CMD, shell=True, ...)
    focus-metavariable: $CMD
```

This reduces false positives by only flagging when tainted data flows into the specific argument that matters.

### open() as Both Source and Sink

D-12 lists `open(...)` as both a source (reading config) and a sink (write-mode). In a single taint rule this creates a self-loop. Resolution:
- Source: `open(...)` when reading (produces tainted data)
- Sink: `open(..., "w")` or `open(..., mode="w")` when writing
- Practical approach for CE: keep `open(...)` as source only in the main rule. Write-mode sink is a separate rule if needed (lower priority, higher false-positive rate).

## Research Area 2: Semgrep CLI Invocation

### Command Structure

```bash
semgrep scan \
  --config src/code_forge/rules/forge-taint.yaml \
  --sarif \
  --dataflow-traces \
  file1.py file2.py ...
```

Key flags:
- `--config PATH` -- local rule file (D-19: `src/code_forge/rules/forge-taint.yaml`)
- `--sarif` -- SARIF 2.1.0 output format to stdout
- `--dataflow-traces` -- include taint_source/taint_sink/intermediate_vars in SARIF
- Positional args -- specific files to scan (D-09: `resolved_review.source_files`)
- No `--pro` needed -- intraprocedural taint is CE-only (D-11)

### File List Invocation

Semgrep accepts files as positional args: `semgrep scan --config rules.yaml file1.py file2.py`. Since D-09 specifies `resolved_review.source_files` (already a list), pass them directly.

### Absence Detection

```python
import shutil
semgrep_path = shutil.which("semgrep")
if semgrep_path is None:
    # D-05/D-06: loud-skip with infra_error
```

### Exit Codes

- 0: no findings
- 1: findings found
- 2+: error (config invalid, parse failure, etc.)

For taint advisory: exit code 1 (findings) is the success case. Map to AdvisoryFinding, never HOLD.

## Research Area 3: SARIF Output Structure for Taint

### Standard SARIF Fields (already parsed by _sarif.py)

The existing `_parse_sarif` extracts: `file`, `line`, `end_line`, `column`, `rule_id`, `level`, `message`, `tool_name`. These map directly to `Finding` objects.

### Taint-Specific SARIF Fields

Semgrep taint findings include `dataflow_trace` in `extra` (experimental):
```json
{
  "dataflow_trace": {
    "taint_source": { "location": {...} },
    "intermediate_vars": [ {"location": {...}} ],
    "taint_sink": { "location": {...} }
  }
}
```

### Conversion Path: Finding -> AdvisoryFinding

```
semgrep --sarif output
    |
    v
_parse_sarif() -> list[Finding]  (existing parser, reuse unchanged)
    |
    v
_findings_to_advisories() -> list[AdvisoryFinding]  (new converter in taint.py)
    |
    v
AxisRunner.run() returns list[AdvisoryFinding]
```

Mapping:
- `id`: `f"taint:{finding.file}:{finding.line}:{finding.rule_id}"`
- `axis`: `"taint"`
- `file`: `finding.file`
- `line_range`: `[finding.line, finding.end_line]`
- `description`: `finding.message` + " (intraprocedural only -- cross-function flows not detected)"
- `attribution`: `"semgrep-ce/intraprocedural"`

## Research Area 4: Danger-Score Implementation

### Diff New-Line Extraction

Extract + lines from diff (excluding +++ header):
```python
new_lines = [
    line[1:]
    for line in diff_text.splitlines()
    if line.startswith('+') and not line.startswith('+++')
]
```

### Dangerous Field Detection in Diff

D-01 scope: only gate.yaml and .code-forge/ files.
D-03: only new lines (+ prefix).

Approach: regex scan of new lines for `field_name:` patterns where field_name is in DANGEROUS_FIELDS.

```python
DANGER_PATTERN = re.compile(
    r'^\s*(' + '|'.join(re.escape(f) for f in DANGEROUS_FIELDS) + r')\s*:',
)
```

### StateFinding Construction (not SARIF round-trip)

Danger-score is a deterministic L0 tool. Construct `StateFinding(source="L0", disposition=CONFIRMED)` directly. No SARIF needed.

D-17 fingerprint: `f"danger-score:{file_path}:{field_name}:{line_number}"`

### File-Path Extraction from Diff

Parse `diff --git a/PATH b/PATH` headers to identify which files are in the diff. Filter to files matching `gate.yaml` or `.code-forge/*`.

## Research Area 5: Adversarial Provenance Prompt

### Injection Point

`src/code_forge/skills/code-forge/passes/pass3-adversarial.md` (161 lines). Append to attack dimensions section.

### Proposed Wording (from 18-CONTEXT.md specifics)

```markdown
### External input provenance

For each external input in the changed code: who controls the source of
this data, and what is the worst value a malicious caller could inject?
```

D-07: hard-wired question. D-08: every review, unconditionally.

### No Code Change Required

The adversarial pass is a markdown skill file loaded by the review pipeline. Adding the provenance section is a text edit -- no Python code change.

## Research Area 6: Integration Architecture

### Where Each Sub-Capability Plugs In

| Sub-capability | Integration Point | Type | File |
|----------------|-------------------|------|------|
| Danger-score | `_run_l0_phase()` | L0 blocking | machine.py |
| Taint axis | `advisory_runners` list | Advisory via AxisRunner | machine.py + new taint.py |
| Provenance | pass3-adversarial.md | Prompt text | pass3-adversarial.md |

### New Files

| File | Purpose |
|------|---------|
| `src/code_forge/taint.py` | TaintRunner (AxisRunner impl) + danger_score_from_diff() |
| `src/code_forge/rules/forge-taint.yaml` | Semgrep taint rule definition |
| `tests/test_taint.py` | Tests for TaintRunner + danger-score |

### Wiring in machine.py

1. **Danger-score**: Add call in `_run_l0_phase()` alongside existing `self.l0_runner()`. Returns `list[StateFinding]` with source="L0", disposition=CONFIRMED.

2. **Taint AxisRunner**: Register in `cli.py` when constructing `StateMachine`:
   ```python
   from code_forge.taint import TaintRunner
   advisory_runners = [TaintRunner()]
   ```
   `_run_advisory_axes()` already iterates and dispatches.

### AxisRunner source_files Wiring

Current AxisRunner Protocol: `run(diff_text: str, repo_root: Path) -> list[AdvisoryFinding]`

TaintRunner.run() needs `source_files` not `diff_text`. Recommended approach: TaintRunner receives `source_files` as a constructor parameter or has it set by machine.py before `_run_advisory_axes()` dispatch. The Protocol signature stays narrow (D-11 anti-anchoring invariant preserved).

Implementation: machine.py sets `runner.source_files = resolved_review.source_files` after resolving the review, before dispatching advisory axes. Optional attribute -- Protocol doesn't require it.

### Non-Git Mode (D-16)

- Danger-score: `resolved_review.git_diff` is None. Loud-skip: "danger-score requires a diff -- skipping in non-git mode"
- Taint: uses `resolved_review.source_files` (D-09). Works in non-git mode.
- Provenance: always runs (D-08). Works in non-git mode.

## Research Area 7: Eval Corpus Regression (D-14)

`gate-yaml-rce` corpus entry already exists with `axis_tags: [TRUST, SEC]`. SC#5 requires it stays CAUGHT. No new corpus entry needed.

## Validation Architecture

### Test Strategy

| Component | Test Type | Key Assertions |
|-----------|-----------|----------------|
| danger_score_from_diff | Unit | Detects dangerous fields in + lines; ignores - lines; ignores non-config files |
| TaintRunner.run() | Unit (mock semgrep) | Returns AdvisoryFinding list; handles semgrep absent; handles semgrep error |
| forge-taint.yaml | Semgrep test | `semgrep scan --test` with annotated test file |
| pass3-adversarial.md | Grep | Provenance question text present in file |
| Regression | Full suite | 1317+ tests pass, gate-yaml-rce stays CAUGHT |

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Semgrep CE intraprocedural misses cross-function flows | Low coverage | D-11 accepted; caveat in finding description |
| YAML fragment false positives in danger-score | Extra HOLD on benign changes | Scoped to gate.yaml/.code-forge/ only (D-01) |
| Semgrep version incompatibility | Rule syntax changes | Pin minimum version in D-06 stderr message |
| AxisRunner source_files wiring | Architectural complexity | Set attribute before dispatch; Protocol stays clean |

---

*Phase: 18-Taint + Provenance*
*Research completed: 2026-06-10*
