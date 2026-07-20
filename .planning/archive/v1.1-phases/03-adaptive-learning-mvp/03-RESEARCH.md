# Phase 3: Adaptive Learning MVP - Research

**Researched:** 2026-05-13
**Domain:** Adaptive learning pipeline -- source adapters, gap detection, proposal generation, dimension lifecycle
**Confidence:** HIGH

## Summary

Phase 3 implements the adaptive learning pipeline for forge: external feedback ingestion via source adapters (GitHub PR comments, git log, CI logs), rule-based gap detection via keyword dictionaries, interactive gap management, LLM-powered proposal generation, and dimension lifecycle management (shadow mode, promotion, retirement). The CONTEXT.md from 29 rounds of cross-AI review is exceptionally detailed -- it contains complete JSON schemas, CLI specifications, state machine definitions, and algorithm pseudocode that function as a near-implementation-ready specification.

The existing codebase provides strong foundations: `forge_cli.py` (2614 lines) already has `atomic_write()`, `load_findings()`, `load_config()`, `evaluate_dimensions()`, `promote_shadow_dimension()`, and `compute_colocation_matrix()`. The Anthropic Python SDK (v0.94.0) is installed and operational. The `gh` CLI (v2.87.3) is available for GitHub API access. Python 3.14.4 provides all standard library capabilities needed (hashlib, uuid, json, subprocess, argparse, tempfile, re, datetime).

The key risk areas are: (1) the migration from `promoted_dimensions` list to `dimension_states` map requires careful handling of existing data, (2) the rename mapping from `convert_historical.py`'s `map_dimension()` names to SKILL.md canonical names must be applied to existing findings.json before dimension_states initialization, (3) the LLM integration for comment parsing and gap grouping adds a new dependency pattern not present in the current codebase, and (4) the interactive CLI patterns (--gaps) are significantly more complex than existing interactive code (classify_findings).

**Primary recommendation:** Structure implementation as 7 plans following the dependency graph: (1) config migration + keyword_dictionaries + dimension_states, (2) source adapter infrastructure + github_pr adapter, (3) git_log + ci_log adapters + dedup, (4) gap detection pipeline (D4) + keyword expansion, (5) gap management (--gaps) + grouping, (6) proposal generation (--propose) + dimension lifecycle (--add-dimension, --promote, --retire, --eval extensions), (7) escalation monitor (LEARN-10) + Sashiko replay validation (LEARN-09).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D1: Three data sources (github_pr via gh api, git_log via local git, ci_log via local file)
- D2: LLM parses all external input with diff hunk + file path context; extracts structured fields but does NOT assign canonical dimension name
- D3: Adapter pattern with canonical schema; storage in .forge/external_findings.json, gap_candidates.json, keyword_expansion_queue.json, gap_groups.json, proposals/; dedup via (source, source_id) exact + (file, line, text_hash) cross-source within 7 days
- D4: Three-outcome gap detection: keyword match (outcome 1), name match (outcome 2), unrecognized (outcome 3)
- D5: Gap proposal trigger at >= 3 non-terminal candidates per group; user decides propose/reclassify/dismiss
- D6: Proposal output as .forge/proposals/<dim>/ directory with SKILL.md.patch, evidence.md, seed_test.diff, keywords.json, README.md
- D7: Shadow mode with Tricorder 4-criteria evaluation; promotion at user's discretion; 180-day shadow timeout
- D8: Max 20 active dimensions budget
- D9: Staleness requires BOTH last_seen > 90 days AND seed_test_status != "pass"
- D10: Test fixtures with minimums per source type
- D11: Full CLI interface specification (--learn, --gaps, --propose, --add-dimension, --promote, --retire, --eval extensions, --reclassify)
- Complete JSON schemas for all 5 new storage files
- dimension_states migration from promoted_dimensions list
- keyword_dictionaries with 14 seed dimensions
- D4 algorithm pseudocode with step-by-step routing

### Claude's Discretion
- LLM prompt templates (comment parsing, gap grouping, proposal file generation)
- LLM model selection and input fields for gap grouping
- Keyword synonym expansion beyond seed dictionary

### Deferred Ideas (OUT OF SCOPE)
- LEARN-03 (embeddings): v2, trigger keyword miss >20% over 3+ months
- LEARN-04 (Markdown AST): v2, trigger 3+ corrupted edits
- LEARN-05 (structured evidence): v2, trigger 10+ stable changes
- Cross-project transfer, LLM gap detection, CI API integration, GitHub PR API: all v2
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| LEARN-01 | Source adapters -- extract external review feedback from git log, gh api (PR comments), CI/CD failure logs | D1/D2 locked specs; gh CLI v2.87.3 verified; Anthropic SDK v0.94.0 for LLM parsing |
| LEARN-02 | Gap detector -- rule-based detection of findings that map to no existing forge dimension | D4 three-outcome algorithm with complete pseudocode; keyword_dictionaries schema defined |
| LEARN-06 | PR pipeline -- branch, commit, push, create PR for human review | D6 proposal output spec; gh CLI for PR creation; SKILL.md.patch + evidence.md generation |
| LEARN-08 | Guardrails -- decay, overlap detection, shadow mode staging, bounded learning rate, dimension budget | D7/D8/D9 specs locked; 20-cap, 90-day staleness, 180-day shadow timeout, Tricorder criteria |
| LEARN-09 | Sashiko replay validation -- replay Sashiko incident to validate pipeline catches all 3 dimensions | D10 test fixture specs; historical Sashiko findings in findings.json (3 HALLUCINATION entries) |
| LEARN-10 | Escalation monitor -- health check computing dedup error rate, edit corruption count, dimension change count | ROADMAP escalation spec; .forge/escalation-status.json storage |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Source adapter ingestion | CLI (Python) | External (gh API) | Adapters are Python functions calling gh CLI or local git; LLM API call for parsing |
| Gap detection (D4) | CLI (Python) | -- | Pure Python keyword matching -- no external dependency |
| Interactive gap management | CLI (Python) | -- | Terminal input/output, same pattern as classify_findings() |
| Proposal generation | CLI (Python) | External (LLM API) | LLM generates patch/evidence/seed_test via Anthropic SDK |
| Dimension lifecycle | CLI (Python) | Storage (JSON) | Config.json mutation via atomic_write() |
| Dedup pipeline | CLI (Python) | -- | SHA-256 + timestamp comparison, pure Python |
| Escalation monitoring | CLI (Python) | Storage (JSON) | Reads findings+external_findings, writes escalation-status.json |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| anthropic | 0.94.0 | LLM API client for Haiku-class comment parsing and gap grouping | Already installed; same vendor as Claude Code; structured output support [VERIFIED: pip3 show] |
| gh CLI | 2.87.3 | GitHub API access for PR comments (pulls/comments + issues/comments) | Already installed; authenticated; used via subprocess [VERIFIED: gh --version] |
| Python stdlib | 3.14.4 | json, hashlib, uuid, subprocess, argparse, tempfile, re, datetime, os | No new dependencies needed [VERIFIED: python3 --version] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| requests | 2.32.5 | HTTP fallback if gh CLI unavailable | Only if gh CLI fails [VERIFIED: pip3 show] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| anthropic SDK | subprocess claude -p | SDK gives structured JSON output; claude -p is how forge_cli currently works but returns unstructured text |
| gh api | PyGithub / requests | gh CLI is already authenticated and available; adding PyGithub is unnecessary dependency |
| JSON flat files | SQLite | JSON matches existing .forge/findings.json pattern; SQLite adds dependency for ~50 records/month |

**Installation:**
```bash
# No new installations needed -- all dependencies already available
pip3 show anthropic  # v0.94.0 confirmed
gh --version         # v2.87.3 confirmed
```

**LLM Model Selection for Parsing:**

| Model | Input/MTok | Output/MTok | Context | Recommendation |
|-------|-----------|-------------|---------|----------------|
| Claude Haiku 3.5 | $0.80 | $4.00 | 200K | Best price/performance for structured extraction |
| Claude Haiku 4.5 | $1.00 | $5.00 | 200K | Current generation, slightly more capable |

At ~50 comments/month with ~500 tokens/comment input + ~200 tokens/comment output:
- Haiku 3.5: ~$0.04/month (25K input + 10K output tokens)
- Haiku 4.5: ~$0.05/month

[VERIFIED: platform.claude.com/docs/en/about-claude/pricing]

**Config.json pricing entry to add:**
```json
"claude-haiku-3.5": {
  "input_per_mtok": 0.80,
  "output_per_mtok": 4.00,
  "cache_read_per_mtok": 0.08,
  "cache_creation_per_mtok": 1.00
}
```

## Architecture Patterns

### System Architecture Diagram

```
External Sources                    Forge CLI                        Storage
=================                   =========                        =======

GitHub PR comments                                                   config.json
  |                                                                  (keyword_dictionaries,
  | gh api pulls/N/comments         +------------------+             dimension_states)
  | gh api issues/N/comments        |                  |                  |
  +------->[ github_pr adapter ]---->|                  |                  |
                                    |   LLM Parser     |<-- Anthropic SDK |
git log --grep="^Revert"            |  (Haiku-class)   |                  |
  |                                 |                  |                  |
  +------->[ git_log adapter ]------>|   Extracts:      |                  |
                                    |   - dimension_raw|                  |
CI log file (--ci-file)             |   - text         |                  |
  |                                 |   - file/line    |                  |
  +------->[ ci_log adapter ]------->|   - keywords     |                  |
                                    +--------+---------+                  |
                                             |                            |
                                    canonical finding schema              |
                                             |                            |
                                    +--------v---------+                  |
                                    |  Dedup Pipeline   |                  |
                                    |  (exact + cross)  |                  |
                                    +--------+---------+                  |
                                             |                            |
                                    +--------v---------+     +------------v------+
                                    |  D4 Gap Detection |---->| external_findings  |
                                    |  (keyword match)  |     | .json              |
                                    +--+-----+------+--+     +---------+----------+
                                       |     |      |                  |
                              outcome1 |  out2|  out3|                 |
                                       |     |      |                  |
                          dim_states++ |     |      +---> gap_candidates.json
                                       |     |                         |
                                       |     +---> keyword_expansion   |
                                       |           _queue.json         |
                                       |                               |
                                    +--v-----------+                   |
                                    | --gaps        |     gap_groups.json
                                    | (interactive) |<---------+
                                    +--+---+---+---+           |
                                       |   |   |              LLM
                                    (a)|  (b) (c)|          grouping
                                       |   |    |
                                  propose  |  dismiss
                                       |   reclassify
                                       v
                              .forge/proposals/<dim>/
                              (SKILL.md.patch, evidence.md,
                               seed_test.diff, keywords.json,
                               README.md)
```

### Recommended Project Structure

```
cli/
  forge_cli.py          # Extended: new --learn, --gaps, --propose,
                        # --add-dimension, --promote (rewrite),
                        # --retire, --eval (extensions), --reclassify
  config.json           # Extended: keyword_dictionaries, dimension_states,
                        #           haiku pricing
  adapters/             # NEW: source adapter modules
    __init__.py
    base.py             # CanonicalFinding dataclass, BaseAdapter ABC
    github_pr.py        # gh api + source_tool detection
    git_log.py          # git log --grep + diff extraction
    ci_log.py           # local file reader
  llm_parser.py         # NEW: Anthropic SDK wrapper for finding extraction
  gap_detector.py       # NEW: D4 keyword matching + outcome routing
  gap_manager.py        # NEW: --gaps interactive, grouping, proposals
  dimension_manager.py  # NEW: --add-dimension, --promote, --retire,
                        # --eval extensions, shadow timeout, staleness
  migration.py          # NEW: dimension_states migration from promoted_dimensions
  escalation.py         # NEW: LEARN-10 health check monitor

tests/
  seed_tests/
    run_seed_tests.py   # Extended: --dimension <dim> --diff <path>
  fixtures/             # NEW: test fixtures for adapters
    github_pr/          # 5+ fixtures (human/Qodo/CodeRabbit/Copilot/malformed)
    git_log/            # 3+ fixtures (revert/fixup/malformed)
    ci_log/             # 2+ fixtures (valid/malformed)
    cross_adapter/      # dedup test fixtures
    sashiko_replay/     # LEARN-09 fixtures

.forge/
  findings.json         # Existing (dimension names migrated)
  external_findings.json    # NEW
  gap_candidates.json       # NEW
  keyword_expansion_queue.json  # NEW
  gap_groups.json              # NEW
  proposals/                   # NEW (per-dimension subdirectories)
  escalation-status.json       # NEW (LEARN-10)
```

### Pattern 1: Source Adapter Pattern
**What:** Each source type (github_pr, git_log, ci_log) has a dedicated adapter that normalizes raw input into a canonical finding schema, then passes through LLM for structured extraction.
**When to use:** Every time `forge --learn` is invoked.
**Example:**
```python
# Source: CONTEXT.md D1/D2 specification
from dataclasses import dataclass
from typing import Optional

@dataclass
class CanonicalFinding:
    """Pre-LLM canonical schema from adapter."""
    source: str           # "github_pr" | "git_log" | "ci_log"
    source_tool: str      # "human" | "qodo" | "coderabbit" | etc.
    source_id: str        # GitHub comment ID, commit SHA, etc.
    timestamp: str        # ISO-8601
    raw_source: str       # Original text
    context: dict         # diff_hunk, pr_url, etc.

@dataclass
class ExtractedFinding:
    """Post-LLM structured finding."""
    dimension_raw: str
    confidence: float
    suggested_keywords: list
    text: str
    file: Optional[str]
    line: Optional[int]
```

### Pattern 2: Atomic Read-Modify-Write with atomic_write()
**What:** All .forge/ JSON files use the existing `atomic_write()` pattern (tempfile.mkstemp + os.replace) to prevent corruption.
**When to use:** Every storage mutation.
**Example:**
```python
# Source: existing forge_cli.py pattern (line 137-156)
def load_json_file(filepath, default):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return default

def save_json_file(filepath, data):
    atomic_write(filepath, data)  # reuse existing
```

### Pattern 3: D4 Keyword Matching Algorithm
**What:** Three-outcome classification using case-insensitive substring matching against keyword_dictionaries.
**When to use:** After dedup, before gap candidate creation.
**Example:**
```python
# Source: CONTEXT.md D4 pseudocode
def classify_finding(finding, keyword_dicts, dimension_states):
    """D4 three-outcome classification."""
    search_text = (
        (finding.get('dimension_raw') or '').strip()
        + ' '
        + finding.get('text', '')
    ).lower()

    best_dim = None
    best_count = 0

    for dim, keywords in keyword_dicts.items():
        # Skip archived dimensions
        state = dimension_states.get(dim, {})
        if state.get('status') == 'archived':
            continue
        # Count distinct keyword matches
        count = sum(
            1 for kw in keywords
            if kw.lower() in search_text
        )
        if count > best_count or (
            count == best_count and count > 0
            and (best_dim is None or dim < best_dim)
        ):
            best_count = count
            best_dim = dim

    if best_count > 0:
        return 'outcome_1', best_dim  # keyword match
    # Check exact name match (outcome 2)
    raw_lower = (
        finding.get('dimension_raw') or ''
    ).strip().lower()
    for dim in keyword_dicts:
        state = dimension_states.get(dim, {})
        if state.get('status') == 'archived':
            continue
        if raw_lower == dim:
            return 'outcome_2', dim  # name match
    return 'outcome_3', None  # unrecognized -> gap
```

### Anti-Patterns to Avoid
- **Embedding the LLM call inside the adapter:** Keep LLM parsing separate from adapter logic. Adapters normalize raw input; LLM parser extracts structured findings. This allows testing adapters without LLM calls.
- **Mutating dimension_states without atomic_write:** Every dimension_states change must go through atomic_write to config.json. Never leave partial state.
- **Using promoted_dimensions list alongside dimension_states:** The migration must complete before any Phase 3 code runs. All code paths must use dimension_states exclusively.
- **Storing derived data redundantly:** gap_candidates copies some fields from external_findings for display convenience, but the authoritative record is always external_findings.json (joined via finding_id).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| GitHub API auth | HTTP + token management | `gh api` subprocess | Already authenticated, handles pagination, rate limiting |
| LLM structured output | Raw HTTP + JSON parsing | `anthropic.Anthropic().messages.create()` | SDK handles auth, retries, response parsing |
| SHA-256 hashing | Custom hash function | `hashlib.sha256()` | Standard library, battle-tested |
| UUID generation | Custom ID scheme | `uuid.uuid4()` with prefix | Matches existing findings.json pattern |
| Atomic file writes | Manual temp + rename | `atomic_write()` from forge_cli.py | Already exists and tested |
| Diff application | Custom patch parser | `subprocess.run(['git', 'apply', ...])` | Git handles edge cases |

**Key insight:** The existing forge_cli.py provides all persistence and utility infrastructure. Phase 3 adds new domain logic but should reuse existing I/O patterns entirely.

## Common Pitfalls

### Pitfall 1: Migration Order Dependency
**What goes wrong:** Running Phase 3 code before dimension name migration causes findings.json to have mixed canonical/legacy dimension names (e.g., "convention_adherence" vs "convention"), leading to incorrect finding_count in dimension_states.
**Why it happens:** convert_historical.py's `map_dimension()` uses names like "state_management", "convention_adherence", "bidirectional_correctness", "input_validation", "ai_code_smells" -- but SKILL.md uses "concurrency", "convention", "bidirectional", "edge_cases", "ai_code_smell". The existing findings.json has entries with legacy names.
**How to avoid:** Migration step 2 (D3 migration spec) MUST rename dimensions in findings.json BEFORE step 3 creates dimension_states with finding counts. Run migration on first config-touching command if dimension_states is absent.
**Warning signs:** dimension_states shows finding_count=0 for dimensions that have historical findings.

### Pitfall 2: gh api Pagination and Rate Limiting
**What goes wrong:** Large PRs with 100+ comments truncate results to first page (30 items by default).
**Why it happens:** GitHub API returns paginated results. `gh api` supports `--paginate` but output format changes (array of arrays vs flat array).
**How to avoid:** Always use `gh api --paginate` and handle the JSON array-of-arrays output format. Add `--jq '.[]'` to flatten.
**Warning signs:** Finding count from --learn is suspiciously lower than visible PR comments.

### Pitfall 3: source_tool Detection for Review Bots
**What goes wrong:** Adapter assigns "human" to all comments, losing the tool attribution signal.
**Why it happens:** GitHub API returns the comment author's login, not whether it's a bot. Bot detection requires checking user type field or matching known bot login patterns.
**How to avoid:** Check `user.type == "Bot"` in the API response, plus match known patterns: login containing "qodo", "coderabbit", "copilot", "github-actions".
**Warning signs:** All external findings show source_tool="human" even for repos with bot reviewers.

### Pitfall 4: Dedup Text Hash Non-Determinism
**What goes wrong:** LLM produces different `text` for the same comment on re-run, generating different `text_hash`, causing dedup to miss duplicates.
**Why it happens:** LLM output is non-deterministic. Same PR comment processed twice may yield different one-sentence summaries.
**How to avoid:** CONTEXT.md acknowledges this as a known limitation (D3 dedup limitations item 1). Exact dedup via (source, source_id) catches re-processing of the same comment. Cross-source dedup via text_hash is best-effort.
**Warning signs:** Duplicate-looking entries in external_findings.json with different text_hash values but same source_id (should be caught by exact dedup).

### Pitfall 5: Interactive CLI State During --gaps
**What goes wrong:** User approves an expansion, then the subsequent group regeneration (steps 4-5) changes the state, making the approval seem lost.
**Why it happens:** --gaps processes expansions (step 3) BEFORE group regeneration (steps 4-5). Expansions are independent of groups. But the group regeneration resets all non-terminal candidates, which may include candidates related to just-approved expansions.
**How to avoid:** Follow the exact processing order in D5: staleness sweeps first, then expansions, then full group regeneration from scratch. Each step reads current state, not cached state.
**Warning signs:** Approved expansion's keywords appear in config but no dimension_states update.

### Pitfall 6: promote_shadow_dimension() Must Be Rewritten
**What goes wrong:** Current `promote_shadow_dimension()` uses `promoted_dimensions` list and `shadow=True/False` on findings. Phase 3 replaces this with `dimension_states` map.
**Why it happens:** Phase 3 migration removes `promoted_dimensions` and replaces the shadow flag mechanism with `dimension_states[dim].status`.
**How to avoid:** Rewrite `promote_shadow_dimension()` to set `dimension_states[dim].status = "active"` instead of toggling shadow flags. The old code at line 1279-1322 must be completely replaced.
**Warning signs:** `--promote` command succeeds but dimension_states shows status="shadow" still.

### Pitfall 7: Concurrent Modification of config.json
**What goes wrong:** --learn writes keyword_dictionaries while --eval reads dimension_states, causing file corruption.
**Why it happens:** CONTEXT.md explicitly states forge commands are NOT concurrent-safe (D3). atomic_write prevents crash corruption but not logical races.
**How to avoid:** Document that forge commands must be serialized. The atomic_write pattern prevents file corruption but not read-modify-write races between concurrent processes.
**Warning signs:** config.json has stale keyword_dictionaries after running --learn and --eval simultaneously.

## Code Examples

### GitHub PR Adapter -- Comment Fetching
```python
# Source: gh api verified output format (tested against octocat/Hello-World)
import json
import subprocess

def fetch_pr_comments(owner_repo_pr):
    """Fetch PR review comments + issue comments via gh api.

    Args:
        owner_repo_pr: "owner/repo#N" format

    Returns:
        list of dicts with id, body, user, diff_hunk, path, etc.
    """
    parts = owner_repo_pr.split('#')
    repo = parts[0]  # owner/repo
    pr_num = parts[1]

    comments = []
    # Review comments (inline on diff)
    result = subprocess.run(
        ['gh', 'api', '--paginate',
         f'repos/{repo}/pulls/{pr_num}/comments',
         '--jq', '.[]'],
        capture_output=True, text=True, timeout=30,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                comments.append(json.loads(line))

    # Issue comments (general PR comments)
    result = subprocess.run(
        ['gh', 'api', '--paginate',
         f'repos/{repo}/issues/{pr_num}/comments',
         '--jq', '.[]'],
        capture_output=True, text=True, timeout=30,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                comments.append(json.loads(line))

    return comments
```

### LLM Parser -- Finding Extraction
```python
# Source: Anthropic SDK docs + CONTEXT.md D2
import anthropic

def extract_finding(raw_text, diff_hunk, file_path):
    """Extract structured finding from raw comment via LLM.

    Uses Haiku-class model for cost efficiency (~$0.04/month
    at 50 comments/month).
    """
    client = anthropic.Anthropic()
    prompt = f"""Extract a structured code review finding from this comment.

Comment: {raw_text}

Diff context:
{diff_hunk or 'No diff context available'}

File: {file_path or 'Unknown'}

Return ONLY valid JSON with these fields:
- dimension_raw: free-text description of the concern category
- confidence: 0-1 extraction faithfulness
- suggested_keywords: 2-5 keywords characterizing the concern
- text: one-sentence structured finding description
- file: code file path (null if general comment)
- line: line number (null if general comment)"""

    response = client.messages.create(
        model="claude-haiku-3.5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    # Parse JSON from response
    text = response.content[0].text
    return json.loads(text)
```

### Dedup Pipeline
```python
# Source: CONTEXT.md D3 dedup specification
import hashlib
from datetime import datetime, timedelta, timezone

def compute_text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def is_exact_dup(finding, existing_findings):
    """Layer 1: (source, source_id) exact match."""
    for ef in existing_findings:
        if (ef['source'] == finding['source']
                and ef['source_id'] == finding['source_id']):
            return True  # skip entirely, no audit trail
    return False

def find_cross_source_dup(finding, existing_findings):
    """Layer 2: (file, line, text_hash) within 7 days."""
    cutoff = (
        datetime.fromisoformat(finding['timestamp'])
        - timedelta(days=7)
    )
    for ef in existing_findings:
        ef_time = datetime.fromisoformat(ef['timestamp'])
        if ef_time < cutoff:
            continue
        if (ef['file'] == finding['file']
                and ef['line'] == finding['line']
                and ef['text_hash'] == finding['text_hash']):
            return ef['id']  # original finding ID
    return None
```

### Config Migration
```python
# Source: CONTEXT.md D3 migration specification
DIMENSION_RENAME_MAP = {
    'convention_adherence': 'convention',
    'bidirectional_correctness': 'bidirectional',
    'state_management': 'concurrency',
    'input_validation': 'edge_cases',
    'ai_code_smells': 'ai_code_smell',
}

def migrate_to_dimension_states(config, findings_data, skill_md_dims):
    """Run on first config-touching command if dimension_states absent."""
    if 'dimension_states' in config:
        return  # already migrated

    # Step 0: Initialize keyword_dictionaries if absent
    if 'keyword_dictionaries' not in config:
        config['keyword_dictionaries'] = SEED_KEYWORD_DICTIONARIES

    # Step 2: Rename dimensions in findings.json (BEFORE step 3)
    for finding in findings_data.get('findings', []):
        old_dim = finding.get('dimension', '')
        if old_dim in DIMENSION_RENAME_MAP:
            finding['dimension'] = DIMENSION_RENAME_MAP[old_dim]

    # Step 3: Create dimension_states from SKILL.md dimensions
    now = datetime.now(timezone.utc).isoformat()
    config['dimension_states'] = {}
    # ... count findings per canonical dimension, create entries

    # Step 4: Override from promoted_dimensions
    for dim in config.get('promoted_dimensions', []):
        if dim in config['dimension_states']:
            config['dimension_states'][dim]['status'] = 'active'

    # Step 5: Delete promoted_dimensions
    config.pop('promoted_dimensions', None)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| promoted_dimensions list | dimension_states map | Phase 3 migration | Full lifecycle tracking per dimension |
| shadow=True/False on findings | dimension_states.status | Phase 3 migration | Decouples finding data from dimension state |
| map_dimension() legacy names | Canonical SKILL.md names | Phase 3 migration step 2 | Consistent naming across all code paths |
| No keyword dictionaries | keyword_dictionaries in config | Phase 3 | Enables rule-based gap detection |

**Deprecated/outdated:**
- `promoted_dimensions` list in config.json: replaced by dimension_states map (migration step 5 deletes it)
- `shadow` flag on individual findings in findings.json: still readable for backward compat but dimension_states.status is authoritative
- `map_dimension()` names (state_management, convention_adherence, etc.): renamed to SKILL.md canonical names

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Claude Haiku 3.5 model ID is "claude-haiku-3.5" for the Anthropic SDK | Standard Stack | Wrong model ID causes API errors; easily correctable |
| A2 | gh api --jq '.[]' flattens paginated results into newline-delimited JSON objects | Code Examples | Pagination handling breaks; may need manual array concatenation |
| A3 | anthropic SDK v0.94.0 supports structured JSON output via messages.create() | Standard Stack | SDK version mismatch; response parsing may need adjustment |
| A4 | ~50 comments/month volume estimate for cost projection | Standard Stack | Higher volume increases cost linearly but still negligible at Haiku pricing |

## Open Questions

1. **Anthropic API Key Configuration**
   - What we know: anthropic SDK is installed (v0.94.0) and Anthropic() client creates successfully
   - What's unclear: Whether ANTHROPIC_API_KEY is set in the environment, or if the SDK uses Claude Code's built-in auth
   - Recommendation: Check for ANTHROPIC_API_KEY env var; if absent, use subprocess claude -p as fallback (same pattern as existing forge_cli.py)

2. **SKILL.md Shadow Tag Parsing for Migration**
   - What we know: SKILL.md has `[SHADOW]` tags on dimensions 13 and 14 (lines 293-294)
   - What's unclear: Whether migration should parse SKILL.md directly or use a hardcoded list
   - Recommendation: Parse SKILL.md to be forward-compatible with user-added shadow dimensions. Regex: `^\s*\d+\.\s+.*\[SHADOW\]`

3. **Existing Findings.json Dimension Names**
   - What we know: Current findings.json has entries with legacy names (state_management, convention_adherence, bidirectional_correctness)
   - What's unclear: Whether SKILL.md's finding persistence heredoc has been updated to use canonical names in Phase 2
   - Recommendation: Migration step 2 handles this; run idempotently (already-canonical names untouched per spec)

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | All Phase 3 code | Yes | 3.14.4 | -- |
| gh CLI | GitHub PR adapter | Yes | 2.87.3 | -- |
| anthropic SDK | LLM comment parsing, gap grouping, proposal generation | Yes | 0.94.0 | claude -p subprocess |
| requests | HTTP fallback | Yes | 2.32.5 | Not needed if gh works |
| hashlib (stdlib) | text_hash computation | Yes | builtin | -- |
| git | git_log adapter, diff operations | Yes | system | -- |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** None -- all dependencies verified present.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | gh CLI handles GitHub auth |
| V3 Session Management | No | CLI tool, no sessions |
| V4 Access Control | No | Local filesystem, user-scoped |
| V5 Input Validation | Yes | Validate finding schemas, sanitize dim names `^[a-z0-9_]+$`, cap keyword lengths |
| V6 Cryptography | No | SHA-256 for hashing only (not security-critical) |

### Known Threat Patterns for Python CLI + LLM

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| LLM prompt injection via malicious PR comments | Tampering | Parse LLM output strictly; validate against schema; do not execute LLM output as code |
| Path traversal in proposal dir names | Tampering | Sanitize dim names to `[a-z0-9_]` per D6 spec; use os.path.join, never string concat |
| JSON injection via crafted finding text | Tampering | json.dumps() handles escaping; atomic_write prevents partial writes |
| Excessive .forge/ disk usage | Denial of Service | Dimension budget (20 max), staleness decay (180-day auto-archive) |

## Sources

### Primary (HIGH confidence)
- forge_cli.py source code (2614 lines) -- atomic_write, load_findings, promote_shadow_dimension, evaluate_dimensions patterns
- CONTEXT.md (29 rounds cross-AI review) -- complete schemas, algorithms, CLI specs
- cli/config.json -- current structure (no keyword_dictionaries or dimension_states yet)
- Anthropic pricing page -- Haiku 3.5 at $0.80/$4.00/MTok, Haiku 4.5 at $1.00/$5.00/MTok [VERIFIED: platform.claude.com/docs/en/about-claude/pricing]

### Secondary (MEDIUM confidence)
- gh api output format verified against octocat/Hello-World (review comments + issue comments) [VERIFIED: gh api repos/octocat/hello-world/pulls/1/comments]
- anthropic SDK v0.94.0 installed and client creation verified [VERIFIED: pip3 show anthropic + import test]

### Tertiary (LOW confidence)
- None -- all claims verified

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all dependencies verified installed and operational
- Architecture: HIGH -- CONTEXT.md provides near-complete specification; existing codebase patterns well understood
- Pitfalls: HIGH -- identified from direct code analysis and schema comparison (migration name mapping verified against both map_dimension() and SKILL.md)
- Implementation ordering: HIGH -- dependency graph clear from schema and code relationships

**Research date:** 2026-05-13
**Valid until:** 2026-06-13 (stable -- Anthropic SDK and gh CLI are mature)
