# Phase 3: Adaptive Learning MVP - Context

**Gathered:** 2026-05-13
**Status:** Ready for planning
**Review history:** 28 rounds cross-AI review (DeepSeek v4-pro + Kimi K2.6 + Mimo + Sonnet 4.6), R29

<domain>
## Phase Boundary

Forge detects review gaps from external feedback and generates human-gated dimension proposals. Pipeline: source adapters ingest external comments, LLM extracts structured findings, keyword dictionary validates classification, unmatched findings accumulate as gap candidates, clustered gaps generate proposal bundles. PDCA cycle with progressive escalation (Level 1 = rule-based gap detection; LLM permitted only for natural language parsing). Guardrails: dimension budget, staleness decay, deduplication, shadow mode evaluation.

</domain>

<decisions>
## Implementation Decisions

### D1: Data Sources

| Source | `source` value | Method | Signal | `source_id` | `source_tool` |
|--------|---------------|--------|--------|-------------|---------------|
| GitHub PR comments | `"github_pr"` | `gh api` | Reviewer feedback | GitHub comment ID | Detected by adapter: `"human"` / `"qodo"` / `"coderabbit"` / `"copilot"` / `"unknown"` |
| git log | `"git_log"` | local git | Revert / fixup! / squash! only | Commit SHA | `"git"` |
| CI logs | `"ci_log"` | local file | Build/test failures | `sha256(content[:1024])-<index>` | `"ci"` |

Regular commit messages excluded (author signal). Reverts matched by `git log --grep="^Revert "`. fixup!/squash! matched literally. CI adapter reads from user-provided local path (`--ci-file <path>`); LLM may extract multiple findings per file, each with unique source_id via the index suffix.

`source` is the ingestion channel. `source_tool` is the entity that authored the comment (set by adapter logic, not by LLM). Both are stored in external_findings.json. D3 dedup tie-breaker uses `source` values: `github_pr > git_log > ci_log`.

### D2: Source Adapter Parsing

LLM parses all external input with diff hunk + file path context. Extracts structured finding but does NOT assign a canonical dimension name (free-text only; D4 outcome 2 handles the rare case where LLM outputs an exact canonical name).

**Why LLM**: arXiv:2604.23667 (EASE 2026) -- classification F1 0.36-0.37 without context. Implicit findings (e.g., SSRF chain in business language) require diff context.

**Level 1 boundary**: "rule-based" applies to gap detection (D4), not parsing. Escalation trigger: `(outcome_2 + outcome_3) / total_non_dup_findings > 20%` over 90-day windows.

**Cost**: ~50 comments/month, Haiku-class model, ~$0.04/month.

**LLM output fields** (per-finding, stored in external_findings.json alongside adapter-set fields):
- `dimension_raw`: free-text concern description
- `confidence`: extraction faithfulness 0-1 (audit only, not used in routing)
- `suggested_keywords`: 2-5 keywords characterizing the concern
- `text`: one-sentence structured finding description
- `file`, `line`: code location (null if general)

Adapter-set fields (not LLM output): `source`, `source_tool`, `source_id`, `timestamp`, `context`, `raw_source`.

**Initial field values at storage** (before D4 routing): `id=new UUID (ext- prefix)`, `validated_dimension=null`, `gap=false`, `dedup_of=null`, `text_hash=SHA-256(text)`. D4 overwrites `validated_dimension` and `gap` based on outcome.

### D3: Architecture and Storage

**Adapter pattern**: per-source adapter outputting canonical schema. New tool = new adapter.

**Storage layout**:

| File | Writers | Readers |
|------|---------|---------|
| `.forge/findings.json` | forge review | evaluate_dimensions(), update_dimension_states() |
| `.forge/external_findings.json` | `--learn`, `--gaps`, `--reclassify` | D4, `--eval --external`, `--eval --shadow` |
| `.forge/gap_candidates.json` | `--learn`, `--gaps`, `--reclassify` | `--gaps` |
| `.forge/keyword_expansion_queue.json` | `--learn`, `--gaps`, `--reclassify` | `--gaps` |
| `.forge/gap_groups.json` | `--gaps` | `--propose` |
| `.forge/proposals/<dim>/` | `--propose` | user |
| `config.json` | `--learn`, `--add-dimension`, `--promote`, `--retire`, `--gaps`, `--reclassify`, `run_seed_tests.py`, forge review | D4, staleness |

**Deduplication** (during `--learn`, before D4):
1. **Exact dedup**: key `(source, source_id)`. Match = skip entirely, not stored, no audit trail for this layer.
2. **Cross-source dedup**: key `(file, line, text_hash)`. Match = any existing finding within 7 days before the new finding's timestamp. Store with `dedup_of` pointing to original (audit trail preserved via `raw_source`), but do NOT pass to D4. Original = earliest `timestamp`; ties = higher-priority source (`github_pr > git_log > ci_log`).

**Dedup limitations**: (1) LLM non-determinism may produce different text_hash (false negative dedup). (2) Null file/line general comments may over-deduplicate (false positive dedup). (3) CI `source_id` index depends on LLM extraction order; re-running `--learn` on the same CI file may produce different indexes, causing exact dedup to miss duplicates (cross-source dedup via text_hash catches these).

**Concurrency**: forge commands are NOT concurrent-safe (read-modify-write on shared JSON). Users must serialize.

### D4: Gap Detection Pipeline

Non-duplicate findings stored in external_findings.json, then routed:

```
Finding from D2 (after dedup)
  -> Store in external_findings.json (validated_dimension=null, gap=false)
  -> If dedup_of != null: STOP (audit only)
  -> Keyword dictionary validates:

  Step 1: For each non-archived dim D in keyword_dictionaries
          (D missing from dimension_states = active per fallback):
          count = number of distinct keywords from D that appear as
          case-insensitive substrings in ((dimension_raw or "").strip() + " " + text)
          (each keyword counted at most once regardless of frequency)

  Step 2: No non-archived dims exist -> Outcome 3
  Step 3: max(count) > 0 -> Outcome 1 (highest count, ties = alphabetical)
  Step 4: max(count) == 0 AND (dimension_raw or "").strip().lower()
          exactly equals some non-archived key -> Outcome 2
  Step 5: Otherwise -> Outcome 3

Outcome 1 (keyword match):
  validated_dimension = D, gap = false
  dimension_states[D]: finding_count++, last_seen = now
  (auto-create dimension_states entry if missing, per fallback rule)

Outcome 2 (name matches, keywords don't):
  validated_dimension = null, gap = false (unchanged from initial)
  Create keyword_expansion_queue entry:
    id=new UUID, finding_id=<ext-id>, created_at=now,
    proposed_dimension=<matched dim>, unmatched_text=finding.text,
    text_hash=finding.text_hash, suggested_keywords=finding.suggested_keywords,
    status="pending", reclassified_to=null
  User resolves via --gaps:
    approve -> expansion: status="approved". Keywords added to dim's dictionary.
              External finding: validated_dimension=dim, gap=false.
              dimension_states[dim]: finding_count++, last_seen=now
    reject -> gap=true, validated_dimension="unknown",
              create gap_candidate (see Outcome 3 fields, plus
              reclassified_from=<exp-id>),
              expansion: status="rejected", reclassified_to=<gap-id>

Outcome 3 (unrecognized):
  validated_dimension = "unknown", gap = true
  Create gap_candidate:
    id=new UUID, finding_id=<ext-id>, timestamp=finding.timestamp,
    created_at=now, dimension_raw=finding.dimension_raw, text=finding.text,
    text_hash=finding.text_hash, file=finding.file, line=finding.line,
    source=finding.source, status="pending", group_id=null,
    reclassified_from=null
```

**Substring matching**: intentionally simple for Level 1. Use specific keywords (e.g., "race condition" not "race"). `--reclassify` provides correction.

**Misclassification feedback**: `--reclassify <finding-id> <correct-dim>` (target must exist in keyword_dictionaries AND not be archived):
1. External finding: validated_dimension = correct-dim, gap = false
2. Derived entries (joined via finding_id): gap_candidates and keyword_expansion_queue entries whose `finding_id` matches the target external finding AND whose status is non-terminal: set status="reclassified". Terminal states for gap_candidates = proposed/dismissed/reclassified/auto_dismissed. Terminal states for keyword_expansion_queue = approved/rejected/auto_dismissed/reclassified. Entries already terminal are left unchanged (including auto-dismissed expansions -- their terminal status reflects historical staleness, while the external finding's updated validated_dimension reflects the current correction; this divergence is intentional audit behavior).
3. suggested_keywords merged into correct-dim's keyword_dictionaries entry (deduplicated against existing keywords in that entry)
4. dimension_states[correct-dim]: finding_count++, last_seen = now (auto-create if missing per fallback rule). dimension_states[old-dim]: finding_count-- (clamped to 0). If old-dim is "unknown" or null (no entry): skip decrement. If old-dim == correct-dim: net zero (++ then -- cancels), last_seen updated.
5. Audit: write keyword_expansion_queue entry (status="approved", proposed_dimension=correct-dim, text_hash=finding.text_hash, unmatched_text=finding.text, suggested_keywords=finding.suggested_keywords, finding_id=<ext-id>, created_at=now, id=new UUID, reclassified_to=null). Dedup key (proposed_dimension, text_hash): collision updates existing entry's status to "approved" (overrides prior status regardless of terminal state -- this is a dedup upsert, not a state transition).

**dimension_states update rules** (all paths set last_seen = current timestamp, never the finding's original timestamp):

| Trigger | finding_count | last_seen |
|---------|--------------|-----------|
| D4 Outcome 1 (during --learn) | ++ | now |
| D4 Outcome 2 approved (during --gaps) | ++ on approved dim | now |
| Internal review (update_dimension_states()) | ++ | now |
| D5 option (b) reclassify to dim X | ++ per candidate on X | now |
| --reclassify to correct-dim | ++ on new, -- on old | now on new |

`update_dimension_states()`: called after `atomic_write()` in review pipeline. For each dimension with new findings: if dimension exists in keyword_dictionaries, auto-create dimension_states entry if missing (per fallback rule), then increment. Dimensions not in keyword_dictionaries: skip silently.

### D5: Gap Proposal Trigger

`forge --gaps` processing order:
1. **Staleness sweep on keyword_expansion_queue**: entries with `status=="pending"` AND `now - created_at > 90 days` -> `status="auto_dismissed"`. Associated external finding: if `validated_dimension` is null (always true for pending expansions in practice -- expansions are created at outcome 2 where validated_dimension=null, and all resolution paths set a terminal expansion status before updating validated_dimension): set `gap=true`, `validated_dimension="unknown"`. If `validated_dimension` is non-null (defensive guard for edge cases): leave both `gap` and `validated_dimension` unchanged. Auto-dismissed expansions do NOT create gap candidates (intentional: too stale for actionable gap analysis). The finding remains discoverable via `--eval --external` with `gap=true` as an audit marker, but unlike other `gap=true` findings, no corresponding gap_candidate exists.
2. **Staleness sweep on gap_candidates**: entries with `status` in ("pending", "grouped") AND `now - created_at > 180 days` -> `status="auto_dismissed"`.
3. **Display keyword expansion suggestions** (remaining pending items). User approves or rejects each interactively.
4. **Group regeneration**: ALL non-terminal candidates reset to `status="pending"` and `group_id=null` (applies to both current "pending" and "grouped" candidates). Terminal states (proposed/dismissed/reclassified/auto_dismissed) unaffected -- their stale `group_id` is harmless because terminal candidates are excluded from grouping and display.
5. **LLM groups** all pending candidates. Each grouped candidate: `status="grouped"`, `group_id=<assigned group UUID>`. All groups (including those with < 3 candidates) written to gap_groups.json (full overwrite, not append -- ephemeral, regenerated each run, prior groups not preserved). LLM failure (network/API error) = error message + non-zero exit, no partial groups written.

`--gaps --approve-expansion <id>`: non-interactive single expansion approval (pending status required, exit 0 on approve, non-zero on error or non-pending). Applies same side effects as interactive approve in step 3: expansion status="approved", keywords added, external finding validated_dimension set, dimension_states updated. Does NOT trigger steps 4-5 (no group regeneration). Non-interactive reject deferred to v2.

**Gap grouping**: groups with >= 3 non-terminal candidates = proposal-ready. < 3 = "insufficient evidence" (persisted in gap_groups.json but not actionable via --propose).

**User decides** for each proposal-ready group (interactively within `--gaps`):
- (a) **Propose**: invokes `forge --propose <group-id>` inline (same code path as the standalone CLI command). Candidates + group: status = "proposed" atomically. Each candidate's `group_id` retained as historical reference (terminal, not cleared).
- (b) **Reclassify group to dimension X** (X must exist in keyword_dictionaries AND not be archived): for each candidate in the group: external finding (via finding_id): validated_dimension = X, gap = false. Keywords from suggested_keywords (deduplicated across all candidates, then merged with existing keywords in X's keyword_dictionaries entry) added to X's dictionary. All candidates: status = "reclassified". Group: candidate_ids filtered, count updated; count = 0 -> group status = "dismissed". dimension_states[X]: finding_count incremented once per candidate (not once per group), last_seen = now (auto-create if missing per fallback rule). No decrement on old dimension (candidates always come from outcome 3 or outcome 2 reject, where validated_dimension was "unknown" -- decrement skipped per D4 rule). Audit record per candidate written to keyword_expansion_queue (id=new UUID, finding_id=<ext-id>, created_at=now, status="approved", proposed_dimension=X, text_hash=finding.text_hash, unmatched_text=finding.text, suggested_keywords=finding.suggested_keywords, reclassified_to=null; dedup key collision updates existing entry to "approved").
- (c) **Dismiss group**: sets group status = "dismissed" and ALL remaining non-terminal candidates in the group: status = "dismissed". Terminal.

Per-candidate targeting within a group: use `--reclassify <finding-id> <dim>` (D4 CLI command) to reclassify individual findings. `--reclassify` does NOT update gap_groups.json directly. The `--gaps` session (if active) re-reads candidate statuses before presenting the next group; otherwise, `--propose` re-validates live count, and the next `--gaps` run regenerates groups from scratch. When `--gaps` processes groups: count = 0 -> group status = "dismissed".

### D6: Proposal Output

`forge --propose <group-id>` validates: (1) group exists with status "pending", (2) non-terminal candidate count >= 3 (re-checks candidate statuses in gap_candidates.json -- counts candidates whose status is not in the terminal set). Validation failure = error message + non-zero exit. Then calls LLM to generate:

```
.forge/proposals/<dim-name>/
  SKILL.md.patch       -- LLM-generated unified diff
  evidence.md          -- LLM-generated gap analysis with source findings
  seed_test.diff       -- LLM-generated synthetic diff for seed test
  keywords.json        -- from suggested_keywords of group's external findings
  README.md            -- LLM-generated human-readable summary
```

LLM prompt templates are Claude's Discretion. keywords.json is deterministic: flat array from suggested_keywords of external findings referenced by candidates (via finding_id), deduplicated, sorted.

**Directory naming**: `<dim-name>` = `proposed_dimension` from gap_groups.json, lowercased then sanitized to `[a-z0-9_]` (non-matching chars replaced with underscore), truncated to 40 chars. Empty after sanitization -> `dim_<group-id[:8]>`.

**Crash safety**: write to `.tmp-<dim>/`, rename after all files written. Existing directory overwritten via rmtree + rename (best-effort, not fully atomic -- a crash between rmtree and rename leaves no proposal dir; next `--propose` regenerates). After rename succeeds: candidates status = "proposed", group status = "proposed" (same transitions as D5(a)). A crash between rename and status update leaves proposal files on disk with candidates still pending; next `--propose` overwrites the directory and retries the status update.

**Post-edit validation** (inside `--propose`): apply SKILL.md.patch to an in-memory copy of the current SKILL.md. Validate: (1) result parses as valid markdown (at least one `#` heading), (2) result is not shorter than the original by more than 20% measured in lines. Validation failure = discard patch, error exit. On pass: write patched content to proposal dir.

**User workflow**: (1) review README.md, (2) `git apply SKILL.md.patch`, (3) `forge --add-dimension <dim> --keywords-file keywords.json` (steps 2-3 together), (4) findings accumulate, (5) `forge --promote <dim>` (may need `--retire` first per D8).

`--keywords-file <path>`: reads a flat JSON array of strings (same format as `keywords.json` generated by `--propose`).

### D7: Shadow Mode and Promotion

`forge --add-dimension <dim>` (`<dim>` must match `^[a-z0-9_]+$`):
- If `<dim>` exists in keyword_dictionaries with dimension_states status="archived": error. User must choose a different name or manually edit config.json to remove both `keyword_dictionaries.<dim>` and `dimension_states.<dim>` first.
- If `<dim>` exists in keyword_dictionaries with non-archived status: error (dimension already exists).
- Otherwise: new dimension.

Steps for new dimension:
1. Adds keywords to keyword_dictionaries
2. Creates dimension_states entry: status="shadow", last_seen=null, added_at=now, finding_count=0, consecutive_eval_failures=null, seed_test_status=null
3. If `.forge/proposals/<dim>/seed_test.diff` exists: runs `run_seed_tests.py --dimension <dim> --diff <path>`. `run_seed_tests.py` writes result ("pass" or "fail") directly to `dimension_states[dim].seed_test_status` in config.json. Failure = warning only (does not block dimension creation).

**Tricorder evaluation** (after finding_count >= 20, all sources combined):

| Criterion | Method |
|-----------|--------|
| Understandable | Human review via `--eval --shadow` |
| Actionable | Human review |
| <10% ToolFP | `rejected / (accepted + rejected)` over internal findings classified to this dimension by the normal review pipeline (keyword_dictionaries matching, same algorithm as D4 Step 1). Shadow dims participate in keyword matching alongside active dims. Denominator < 10 -> human review. |
| Significant impact | Human review |

`forge --eval --shadow` is interactive: evaluates each shadow dimension independently. For each shadow dim: presents its findings (from both `.forge/findings.json` and `.forge/external_findings.json`), prompts user for pass/fail on each of the 4 Tricorder criteria. With `--include-archived`: also displays archived-from-shadow dimensions in read-only mode (findings listed, no pass/fail prompts -- archived is terminal). On completion of interactive evaluation:
- Pass all 4: `consecutive_eval_failures` set to 0 (from null on first eval, or reset from prior failures). User can `--promote <dim>`.
- Fail any: `consecutive_eval_failures` incremented (null -> 1, N -> N+1). At 2: `--eval --shadow` auto-archives the dimension (sets status="archived"), prints notification.

`forge --promote <dim>`: validates status == "shadow" (errors on active/archived). Checks 20-cap (D8). Does NOT gate on finding_count or eval completion -- user is trusted to evaluate before promoting. Tricorder evaluation is a recommendation, not an enforced gate.

**Shadow timeout** (180 days from added_at, applies to shadow dimensions only, checked at end of --learn and --eval; skipped when `status != "shadow"`):
- finding_count >= 20: prints warning to stderr recommending `--eval --shadow`. Does not block.
- 0 < finding_count < 20 AND `consecutive_eval_failures is null` (never evaluated): prints warning recommending `--eval --shadow` for early evaluation. If `consecutive_eval_failures` is not null (previously evaluated), the 20-finding threshold is waived -- no warning.
- finding_count == 0: auto-archived at the 180-day check (status change, no warning -- zero findings in 6 months = not useful). This is a write operation, not just a warning.

Active dimensions are NOT subject to shadow timeout (the check only runs when `status == "shadow"`).

### D8: Dimension Budget

Max 20 active dimensions. Shadow/archived do not count. At cap: `--promote` errors with active list + `compute_colocation_matrix()` merge candidates. User must `--retire <dim>` first. Merge = retire one + manually port keywords.

### D9: Staleness Decay

"Stale" in `--eval` output requires BOTH:
1. `now - last_seen > 90 days`. Null last_seen (new shadow dims only) = NOT stale.
2. `seed_test_status != "pass"`. This covers both `"fail"` (broken seed test) and `null` (no seed test defined). `"pass"` = condition NOT satisfied (healthy).

In other words: stale = old findings AND broken/missing seed test. Zero findings with passing seed test = healthy. Zero findings with null seed test and old last_seen = stale. A dimension whose finding_count was decremented to 0 via --reclassify retains its last_seen from the most recent increment; staleness depends on that last_seen age and seed_test_status, same as any other dimension.

"Unhealthy" flag (independent of staleness): any active or shadow dimension with seed_test_status == "fail". Computed at display time, not persisted.

`forge --retire <dim>`: sets status="archived". Works on both active AND shadow dimensions. Archived dimensions filtered from --eval by default, shown with --include-archived. No findings deleted.

### D10: Validation Strategy

| Category | Purpose |
|----------|---------|
| Valid input | Correct classification |
| Gap input | Correct gap detection |
| False-gap negative | Implicit finding routes to existing dim |
| Malformed input | Graceful handling |
| Dedup test | Cross-source dedup prevents false trigger |

Minimums: github_pr >= 5 (human/Qodo/CodeRabbit/Copilot/edge-case-malformed), git_log >= 3 (revert/fixup/malformed), CI >= 2 (valid/malformed). Cross-adapter dedup uses github_pr+git_log scenarios. Test fixtures live in `tests/` (alongside existing `tests/seed_tests/`).

### D11: CLI Interface

| Command | Description |
|---------|-------------|
| `forge --learn --pr <owner/repo#N>` | Ingest PR comments (gh api). Exactly one of --pr/--branch/--ci-file required. Idempotent via dedup. |
| `forge --learn --branch <name>` | Scan reverts/fixup!/squash! vs `git merge-base <name> HEAD` |
| `forge --learn --ci-file <path>` | Ingest CI log from local file |
| `forge --gaps` | Interactive: (1) staleness sweeps, (2) keyword expansion review, (3) gap candidate grouping + propose/reclassify/dismiss per group |
| `forge --gaps --approve-expansion <id>` | Non-interactive single expansion approval (pending only; exit 0/non-zero). Does NOT trigger group regeneration. |
| `forge --propose <group-id>` | Generate proposal bundle (validates pending group + non-terminal count >= 3). Also invoked inline by `--gaps` option (a). |
| `forge --add-dimension <dim> --keywords-file <path>` | Register shadow dimension (dim must match `^[a-z0-9_]+$`; errors if dim already exists or is archived) |
| `forge --promote <dim>` | Shadow to active (validates shadow, checks 20-cap). No finding_count or eval gate. |
| `forge --retire <dim>` | Active or shadow to archived |
| `forge --eval` | Show active dimension evaluation (staleness, unhealthy flags, finding counts) |
| `forge --eval --shadow` | Interactive: present shadow findings (internal + external), prompt pass/fail for Tricorder criteria, update consecutive_eval_failures. Auto-archives at 2 consecutive failures. |
| `forge --eval --external` | External findings as JSON (timestamp desc). By default excludes findings whose validated_dimension maps to an archived dimension. |
| `forge --eval --include-archived` | Include archived in eval output. With `--external`: includes findings whose `validated_dimension` maps to an archived dimension. With `--shadow`: displays archived-from-shadow dimensions read-only (no interactive prompts). |
| `forge --reclassify <finding-id> <dim>` | Correct misclassification (target must be non-archived, must exist in keyword_dictionaries). For per-candidate targeting within groups, use this with the candidate's finding_id. |

`--eval` flags: `--shadow`/`--external` mutually exclusive, `--include-archived` combines with either.

</decisions>

<data_schemas>
## Data Schemas

### config.json additions

```json
{
  "keyword_dictionaries": {
    "correctness": ["off-by-one", "wrong comparison", "inverted condition", "null", "uninitialized"],
    "security": ["injection", "SSRF", "traversal", "authentication", "authorization", "IDOR", "secrets", "credentials"],
    "concurrency": ["race condition", "deadlock", "lock ordering", "unsynchronized", "thread safety"],
    "edge_cases": ["empty", "zero", "negative", "maximum", "unicode", "encoding", "timezone"],
    "error_handling": ["swallowed", "ignored error", "missing rollback", "catch-all", "timeout", "retry"],
    "api_contract": ["breaking change", "wire format", "precondition", "postcondition", "validation"],
    "bidirectional": ["round-trip", "serialize", "deserialize", "parse", "format", "encode", "decode"],
    "graceful_degradation": ["missing dependency", "optional tool", "feature absence", "skip gracefully"],
    "convention": ["naming", "style drift", "helper", "pattern", "consistency", "nesting depth"],
    "performance": ["unbounded", "N+1", "O(n^2)", "hot path", "blocking", "memory leak", "pagination"],
    "test_quality": ["mock", "flaky", "shared state", "negative case", "boundary test", "coverage"],
    "ai_code_smell": ["hallucinated", "over-engineering", "plausible-but-wrong", "TODO", "FIXME", "repetition"],
    "doc_completeness": ["docstring", "changelog", "README", "documentation", "undocumented"],
    "change_scope": ["unrelated", "mixed concerns", "unfocused", "scope"]
  },
  "dimension_states": {
    "security": {
      "status": "active", "last_seen": "2026-05-13T10:30:00Z", "finding_count": 47,
      "added_at": null, "consecutive_eval_failures": null, "seed_test_status": "pass"
    },
    "doc_completeness": {
      "status": "shadow", "last_seen": null, "finding_count": 0,
      "added_at": "2026-05-13T08:00:00Z", "consecutive_eval_failures": null, "seed_test_status": null
    }
  }
}
```

**keyword_dictionaries**: case-insensitive substring matches. All 14 dimensions pre-populated.

**dimension_states fields** (all fields always present on every entry):
- `status`: "active" | "shadow" | "archived". Archived is terminal -- no reactivation path. To reuse the name, remove both `keyword_dictionaries.<dim>` and `dimension_states.<dim>` from config.json manually.
- `last_seen`: ISO-8601 datetime. Set to current timestamp on finding_count increment. Null = new shadow dim, never seen (D9: not stale).
- `finding_count`: integer. See D4 update rules table for all increment/decrement triggers.
- `added_at`: ISO-8601 datetime. Set by --add-dimension (new dims) and migration step 3 (shadow=migration date, active=null). Active dims from migration have null added_at because the 180-day timeout only applies to shadow dimensions.
- `consecutive_eval_failures`: integer or null. Null = never evaluated via --eval --shadow. Set to 0 on first pass (reset on every pass). Incremented on fail. At 2: auto-archived by --eval --shadow.
- `seed_test_status`: "pass" | "fail" | null. Set by --add-dimension (D7 step 3, via run_seed_tests.py writing directly to config.json) and by manual run_seed_tests.py invocations. Checked by --eval for staleness/unhealthy display.

**Fallback rule**: dimension in keyword_dictionaries but missing from dimension_states -> treat as active with finding_count=0, last_seen=current timestamp, added_at=null, consecutive_eval_failures=null, seed_test_status=null. Auto-create on any write (applies to all code paths: D4 Outcome 1, --reclassify, D5 option (b), update_dimension_states()). D4 algorithm: missing entries treated as active (not filtered by archived check). Note: fallback auto-create sets `last_seen=current timestamp` (preventing immediate staleness), while `--add-dimension` (D7) sets `last_seen=null` (D9 treats null as not stale). These are distinct creation paths -- fallback handles legacy/unexpected entries, --add-dimension handles intentional new dimensions. Fallback-created entries with `seed_test_status=null` will become stale after 90 days without new findings (per D9 condition 2), which is correct behavior -- an unexpected dimension should be evaluated.

**Naming convention**: all keys use SKILL.md canonical short names. map_dimension() uses different names -- see migration step 2.

**Migration** (runs on first config-touching command if dimension_states absent):
0. If keyword_dictionaries absent: initialize from seed dictionary above. Preserve if exists.
1. Read SKILL.md [SHADOW] tags.
2. Rename dimension field in .forge/findings.json: convention_adherence->convention, bidirectional_correctness->bidirectional, state_management->concurrency, input_validation->edge_cases, ai_code_smells->ai_code_smell. Already-canonical names untouched (idempotent). Names not in map_dimension() (correctness, doc_completeness, change_scope) already canonical. **Must run before step 3.**
3. For each SKILL.md dimension without existing dimension_states entry: create with status from tag ([SHADOW]->shadow, else active), last_seen=migration date, finding_count from findings.json count (canonical names from step 2), added_at=migration date (shadow) or null (active), consecutive_eval_failures=null, seed_test_status=null.
4. If promoted_dimensions list exists: for dims in that list whose dimension_states entry was just created in step 3, override status to "active". Pre-existing entries not overridden.
5. Delete promoted_dimensions from config.json.
6. Update promote_shadow_dimension() to write dimension_states[dim].status = "active".

### .forge/external_findings.json

```json
{
  "version": 1,
  "findings": [{
    "id": "ext-uuid-1", "timestamp": "2026-05-13T10:00:00Z",
    "source": "github_pr", "source_tool": "human", "source_id": "github-comment-12345",
    "file": "src/auth.py", "line": 42,
    "text": "LLM-extracted description", "dimension_raw": "data exposure via admin endpoint",
    "validated_dimension": "security", "confidence": 0.85, "gap": false,
    "suggested_keywords": ["admin endpoint", "data exposure"],
    "text_hash": "sha256-abc123", "dedup_of": null,
    "context": { "diff_hunk": "...", "pr_url": "https://github.com/org/repo/pull/1" },
    "raw_source": "original comment text"
  }]
}
```

All ID values in JSON examples are illustrative (e.g., "ext-uuid-1"). Actual IDs are UUIDs.

- `id`: UUID prefixed ext-.
- `timestamp`: ISO-8601, original event time (adapter-set).
- `source`: ingestion channel (adapter-set). Values: `"github_pr"`, `"git_log"`, `"ci_log"` (see D1 table).
- `source_tool`: authoring entity (adapter-set, see D1 table).
- `source_id`: per-source identifier (adapter-set, see D1 table).
- `file`, `line`: code location (LLM-extracted). Null if general.
- `text`: LLM one-sentence description.
- `dimension_raw`: LLM free-text concern. Not canonical.
- `validated_dimension`: initially null (set by D2 storage). Updated by D4 outcomes: canonical name (outcome 1), remains null (outcome 2 pending), "unknown" (outcome 3). Also updated by --reclassify and D5(b) to canonical name. May remain null indefinitely if --gaps is never run (user-gated).
- `confidence`: 0-1, audit only (LLM-set).
- `gap`: initially false (set by D2 storage). Set true by D4 outcome 3, outcome 2 rejection, and auto-dismiss. Remains false for outcome 1 and outcome 2 pending. Also set true on auto-dismiss (audit marker only -- no gap_candidate created for auto-dismissed expansions, unlike all other gap=true paths).
- `suggested_keywords`: 2-5 from D2 LLM.
- `text_hash`: SHA-256 of text. Cross-source dedup key.
- `dedup_of`: original finding ID (cross-source dup) or null. Non-null = not processed by D4.
- `context`: freeform (adapter-set). diff_hunk always present (string or null). Per-source: pr_url (github_pr), commit_sha+branch (git_log, diff_hunk = commit diff), ci_output_excerpt (ci_log).
- `raw_source`: unprocessed original text for audit (adapter-set).

### .forge/gap_candidates.json

```json
{
  "version": 1,
  "candidates": [{
    "id": "gap-uuid-1", "finding_id": "ext-uuid-1",
    "timestamp": "2026-05-13T10:00:00Z", "created_at": "2026-05-13T10:00:00Z",
    "dimension_raw": "label", "text": "finding text",
    "text_hash": "sha256-abc", "file": "src/auth.py", "line": 42,
    "source": "github_pr", "status": "pending",
    "group_id": null, "reclassified_from": null
  }]
}
```

- `id`: UUID. `finding_id`: references external_findings.json (for suggested_keywords, source_tool lookup).
- `timestamp`: original finding time (display only, not used in any logic; authoritative time accessible via finding_id -> external_findings.json). `created_at`: when candidate was created (--learn for outcome 3, --gaps for outcome 2 reject). Used for 180-day staleness.
- `dimension_raw`, `text`, `file`, `line`, `source`: copied from external finding for --gaps display.
- `text_hash`: copied from external finding (display and audit only, not used in gap_candidates processing logic).
- `group_id`: from gap_groups.json, set during grouping. Cleared to null by --gaps step 4 for non-terminal candidates. Terminal candidates retain stale group_id (harmless -- excluded from grouping and display).
- `reclassified_from`: keyword_expansion_queue entry ID (outcome 2 reject) or null. Bidirectional with reclassified_to.
- **Status transitions**: pending -> grouped (grouping), grouped -> pending (regeneration reset), pending/grouped -> proposed|dismissed|reclassified|auto_dismissed (all four terminal).

### .forge/gap_groups.json

```json
{
  "version": 1, "generated_at": "2026-05-13T10:00:00Z",
  "groups": [{
    "group_id": "grp-uuid-1", "proposed_dimension": "supply_chain_security",
    "description": "One-line description",
    "candidate_ids": ["gap-uuid-1", "gap-uuid-3"], "count": 2, "status": "pending"
  }]
}
```

- `group_id`: UUID. `proposed_dimension`: LLM-suggested, sanitized [a-z0-9_] for D6.
- `description`: LLM one-sentence. `candidate_ids`: gap candidate IDs (filtered atomically on per-candidate ops, count = len). `generated_at`: informational.
- `status`: pending | proposed | dismissed. Initial status on generation = pending. All three are terminal within a single --gaps session (groups are ephemeral).
- **All groups persisted**: both proposal-ready (>= 3 candidates) and sub-threshold (< 3) groups are written to gap_groups.json (full overwrite each --gaps run). Sub-threshold groups are not actionable via --propose (non-terminal count validation rejects them).
- **Ephemeral**: regenerated each --gaps run (steps 4-5). --propose must follow --gaps without intervening --gaps runs (group IDs change). Validates: group exists + pending + live count >= 3.
- **Stale after --reclassify**: `--reclassify` (CLI command) sets candidate status to terminal but does NOT update gap_groups.json. `--propose` re-validates live pending count by checking candidate statuses, so stale candidate_ids/count do not cause incorrect proposals. Next `--gaps` run regenerates from scratch.

### .forge/keyword_expansion_queue.json

```json
{
  "version": 1,
  "expansions": [{
    "id": "exp-uuid-1", "finding_id": "ext-uuid-1",
    "created_at": "2026-05-13T10:00:00Z", "proposed_dimension": "security",
    "unmatched_text": "admin endpoint copies data",
    "text_hash": "sha256-def456",
    "suggested_keywords": ["admin endpoint", "copies data"],
    "status": "pending", "reclassified_to": null
  }]
}
```

- `id`: UUID. `finding_id`: references external_findings.json.
- `created_at`: --learn execution time. Used for 90-day staleness (pending entries only).
- `proposed_dimension`: matched dim name (outcome 2), or target dim (D5(b) audit / D4 --reclassify audit).
- `unmatched_text`: finding text that failed keyword matching. For audit entries: finding's `text` field.
- `text_hash`: from external finding (SHA-256 of text). Dedup key: (proposed_dimension, text_hash). Collision on audit write (D5(b) or --reclassify) -> update existing entry's status to "approved" (dedup upsert, overrides prior status). Multiple expansion entries per finding across different dimensions are valid (different dedup keys).
- `suggested_keywords`: from external finding.
- `reclassified_to`: gap candidate ID on reject, null otherwise. Creation-time snapshot (not updated on candidate transitions; follow finding_id for current state).
- **Status transitions**: pending -> approved|rejected|auto_dismissed|reclassified (all terminal). Exception: dedup key collision on audit write can override any status to "approved" (see text_hash description above).
- Auto-dismiss: sets external finding gap=true, validated_dimension="unknown" ONLY IF validated_dimension is currently null. Does NOT create gap candidate.
- Approved: keywords added to config.json, external finding validated_dimension set, dimension_states updated.

### .forge/proposals/<dim>/keywords.json

```json
["keyword1", "keyword2"]
```

Flat JSON array of strings. Generated by `--propose` from suggested_keywords of external findings via finding_id. Deduplicated, sorted. `<dim>` in the directory path = `proposed_dimension` from gap_groups.json (sanitized). Consumed by `--add-dimension --keywords-file <path>` (same flat-array format).

### Claude's Discretion

- LLM prompt templates (comment parsing, gap grouping, proposal file generation)
- LLM model selection and input fields for gap grouping
- Keyword synonym expansion beyond seed dictionary

</data_schemas>

<canonical_refs>
## Canonical References

### Existing Infrastructure
- `cli/forge_cli.py` -- load_findings(), atomic_write(), evaluate_dimensions(), promote_shadow_dimension(), co-location, argparse
- `cli/config.json` -- pricing, tier classification, evaluation, complexity, custom_rules, colocation
- `bootstrap/convert_historical.py` -- map_dimension() (dimension names differ from SKILL.md)
- `skills/forge/SKILL.md` -- 14 dimensions (12 active + 2 shadow), shadow mode, persistence
- `tests/seed_tests/` -- run_seed_tests.py + 7 synthetic diffs. Phase 3 extends run_seed_tests.py to accept `--dimension <dim> --diff <path>` for proposal-generated seed tests.

### Prior Phase Decisions
- `01a-CONTEXT.md` -- findings.json schema, FP taxonomy, accept/reject
- `01b-CONTEXT.md` -- Wilson confidence, Tricorder 4 criteria, tier classification
- `02-CONTEXT.md` -- zero-data dims, three-layer filter, co-location merging

### Research Papers
- arXiv:2604.23667 (EASE 2026), arXiv:2510.05450 (ASE 2025), arXiv:2501.15134 (FSE 2025), arXiv:2507.21160v1 (OOD 2025), SARIF v2.1.0, DefectDojo

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- load_findings() / atomic_write() -- persistence for all .forge/ files
- evaluate_dimensions(include_shadow=True) -- extend for D7 Tricorder
- promote_shadow_dimension() -- MUST update to write dimension_states
- compute_colocation_matrix() -- D8 merge candidates

### Integration Points
- New CLI: see D11. Extended: --promote (dimension_states + 20-cap), --eval (new flags)
- New config.json: keyword_dictionaries, dimension_states
- New .forge/: external_findings, gap_candidates, keyword_expansion_queue, gap_groups, proposals/

</code_context>

<specifics>
## Design Drivers

1. **SSRF attack chain**: implicit finding with zero keywords -> full-LLM parsing + D4 three-outcome
2. **Tool-agnostic**: any review tool -> canonical schema + adapter pattern
3. **Sashiko as one test case**: validation must prove source-agnostic gap detection

</specifics>

<deferred>
## Deferred Ideas

- LEARN-03 (embeddings): v2, trigger keyword miss >20% over 3+ months
- LEARN-04 (Markdown AST): v2, trigger 3+ corrupted edits
- LEARN-05 (structured evidence): v2, trigger 10+ stable changes
- Cross-project transfer, LLM gap detection, CI API integration, GitHub PR API: all v2

</deferred>

---

*Phase: 03-adaptive-learning-mvp*
*Context gathered: 2026-05-13 (28 rounds cross-AI review)*
