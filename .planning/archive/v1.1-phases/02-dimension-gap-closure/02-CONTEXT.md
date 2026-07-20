# Phase 2: Dimension Gap Closure - Context

**Gathered:** 2026-05-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Close dimension gaps in forge's 12 existing review dimensions using Phase 1b effectiveness data. Add new dimensions only where data confirms gaps. Merge overlapping dimensions using finding co-location analysis. Move deterministic-checkable dimensions to Step 0b. Support project-specific custom rules. Every change follows evidence-gated addition, not speculative expansion.

</domain>

<decisions>
## Implementation Decisions

### D1: Zero-Data Dimensions -- Keep All + Seed Test

**Decision:** Retain all 12 existing dimensions regardless of data volume. Dimensions with zero FP data get a seed test to verify the SKILL.md prompt actually produces findings for that dimension.

**Rationale:** Dropping dimensions with zero data is actively avoiding coverage gaps rather than measuring them. Zero data may mean zero bugs in that dimension (good) or zero detection capability (bad). A seed test distinguishes the two cases.

**Seed test design:**
- For each dimension with <5 findings, craft a synthetic diff that SHOULD trigger a finding
- Run forge review against it
- If finding is produced: prompt works, dimension stays provisional until real data arrives
- If no finding: prompt needs improvement before Phase 2 adds anything new

**Affected dimensions (zero data as of Phase 1b start):** performance (dim 10), concurrency_safety (dim 5), api_contract_consistency (dim 6), graceful_degradation (dim 8), test_quality (dim 11), ai_code_smell (dim 12), error_handling_completeness (dim 3)

**Evidence:** Google Tricorder deploys all checks immediately with provisional status. Removing untested checks guarantees blind spots.

### D2: New Dimension Criteria -- Three-Layer Filter + Routing Strategy

**Decision:** New dimensions must pass a three-layer filter before addition. Existing ROADMAP dimensions (DIM-01 through DIM-07) are routed through this filter with specific outcomes.

**Three-layer filter:**

| Layer | Gate | Reject Condition |
|-------|------|------------------|
| 1. Gap Evidence | Proof that existing dimensions miss this class of issue | No documented miss = no new dimension |
| 2. Deterministic-First | Can a deterministic tool (regex, AST, metrics) detect this? | Yes -> Step 0b, not LLM dimension |
| 3. Tricorder 4 Criteria | Understandable, Actionable, <10% FP, Significant Impact | Fails any criterion -> fix before deploying |

**Dimension routing (DIM-01 through DIM-07):**

| Requirement | Route | Rationale |
|-------------|-------|-----------|
| DIM-01 (documentation completeness) | New LLM dimension + shadow mode | Cannot be deterministic (semantic judgment of "completeness"). Deploy in Monitor mode, promote after <10% FP confirmed |
| DIM-02 (naming quality) | Absorb into dim 9 (convention adherence) | Naming IS convention. ESLint anti-overlap policy: "overlapping rules confuse end users." Extend dim 9 prompt to cover naming intent clarity |
| DIM-03 (complexity measurement) | Step 0b deterministic | radon CC >= 15, cognitive complexity >= 15, lizard CCN >= 15. ICSE 2026 (arXiv:2509.19117): pure metrics match LLM performance. Keep LLM review for semantic complexity (coupling, domain-required complexity) in Steps 1-3 |
| DIM-04 (change scope) | New LLM dimension + shadow mode | Single-concern diff judgment requires semantic understanding. Deploy in Monitor mode |
| DIM-05 (readability) | Absorb into dim 9 (convention adherence) | Readability is a function of naming + formatting + structure. Extend dim 9 to include readability signals (nesting depth, function length) |
| DIM-06 (merge overlapping) | Data-driven (see D3) | Finding co-location analysis, not upfront judgment |
| DIM-07 (project custom rules) | New capability (see D4) | YAML frontmatter + Markdown body format |

**Shadow mode deployment process (for DIM-01, DIM-04):**
1. Deploy in Monitor mode -- findings logged to findings.json but NOT shown to user in review output
2. After 20+ findings accumulated, compute FP rate
3. FP < 10% -> promote to active (shown in review output)
4. FP >= 10% -> improve SKILL.md prompt, reset count, re-monitor

**Evidence:**
- Google Tricorder (CACM 2018): 4 criteria gate, "Not Useful" feedback, analyzers exceeding 10% FP disabled
- Chromium clang-tidy: provisional approval + 1 month "Not Useful" monitoring (0-3.6% rate across 5 checks)
- Facebook Infer (CACM 2019): same analysis, same FP rate -- batch 0% fix rate vs diff-time 70%+ fix rate
- Semgrep: explicit Monitor -> Comment -> Block pipeline with noise detection
- ICSE 2026 MLSEC (arXiv:2509.19117): deterministic metrics match LLM for vulnerability/complexity detection
- arXiv:2502.20747: LLM review results vary even at temperature=0; deterministic tools are reproducible
- MSR 2025 (Jaoua et al.): "static analyzers maintain highest percentage of accurate reviews due to deterministic nature"
- KNighter (arXiv, 2025): derives new checkers from historical patch evidence, not speculation
- Google FindBugs failure: ungated addition -> 84% of bugs never fixed

### D3: Dimension Merging -- Data-Driven via Finding Co-Location

**Decision:** Merge decisions based on finding co-location data from findings.json, not upfront semantic judgment. Two dimensions are merge candidates when they frequently flag the same (file, line_number) pair.

**Process:**
1. Compute co-location matrix: for each dimension pair, count findings that share (file, line) coordinates
2. Co-location rate > 30% -> merge candidate (same issue detected by both dimensions)
3. Analyze reject_reason breakdown for co-located findings -- if both have similar FP patterns, merging reduces noise without losing coverage
4. Generate merge recommendation with evidence
5. User approves via PR

**Why not upfront merging:**
- Pecorelli et al. (EMSE 2022): overlapping detectors produce overlapping FALSE POSITIVES too -- merging doesn't automatically improve precision
- SonarQube 10.2+ didn't delete categories, they added cross-mapping (MQR mode) -- one rule can now map to multiple quality dimensions
- ESLint history shows both merge (10 rules -> eslint-plugin-node) AND split (space-in-brackets -> 2 rules) based on empirical data

**Minimum data for merge analysis:** 20+ co-located findings per dimension pair (same threshold as D4 Tricorder provisional -> confirmed)

**Evidence:**
- Miller (1956) / Cowan: working memory 3-7 chunks; more dimensions = cognitive overload
- Hick-Hyman (1952): decision time increases logarithmically with choices
- Bacchelli & Bird (ICSE 2013, Microsoft): review benefits cluster into 3-4 outcomes, not 20 categories
- SmartBear/Cisco (2006): structured checklist +66.7% defect detection, but time constraints demand fewer focused items
- IEEE TSE 2023 (Guo et al.): overlapping rules worsen FP rate
- LinearB (2025, 6.1M PRs): cognitive overload -> more comments but fewer actionable
- DeepSource: "2,000 precise rules > 10,000 noisy ones"

### D4: Project Custom Rules -- YAML Frontmatter + Markdown Body

**Decision:** Users define project-specific review rules in `forge-rules.md` (or `.forge/rules/*.md` for multiple rules). Format: YAML frontmatter for structured metadata + Markdown body for natural language rule description consumed by LLM.

**Format:**
```markdown
---
name: no-raw-sql
severity: high
dimension: security
scope:
  - "**/*.py"
  - "**/*.go"
enabled: true
---

# No Raw SQL Queries

Do not use raw SQL string concatenation or f-string interpolation for database queries.
Always use parameterized queries or ORM methods.

## Bad
```python
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
```

## Good
```python
cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
```

## Why
SQL injection is OWASP Top 1. String interpolation bypasses parameter escaping.
```

**Metadata fields:**

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| name | yes | string | Rule identifier (kebab-case) |
| severity | yes | enum | critical/high/medium/low |
| dimension | no | string | Map to existing dimension or "custom" |
| scope | no | list[glob] | File patterns this rule applies to (default: all) |
| enabled | no | bool | Toggle without deleting (default: true) |

**Loading order:**
1. Load `forge-rules.md` (single file) if exists
2. Load `.forge/rules/*.md` (multi-file) if directory exists
3. Both can coexist; duplicates by name are rejected with error
4. Rules injected into LLM prompt as additional review instructions

**Why this format over alternatives:**
- Semgrep YAML patterns: not applicable (forge doesn't do pattern matching, LLM does)
- Pure YAML: complex natural language descriptions in YAML strings are unreadable
- Pure Markdown: metadata extraction requires fragile parsing
- YAML frontmatter + Markdown: structured metadata for tooling + natural language for LLM consumption. Same pattern as Devin playbooks and Hugo/Jekyll content.

**Evidence:**
- Devin playbooks: teach AI team conventions via structured natural language
- CodeRabbit .coderabbit.yml: review instructions in natural language
- Windsurf team prompts: shared team-level instructions for consistent review
- Semgrep (philosophy): "catch what a senior engineer would catch in code review" -- rules should be human-readable

### Claude's Discretion

- Seed test synthetic diff design (content, file structure)
- Co-location analysis implementation details (SQL vs Python, caching)
- Shadow mode logging format (separate file vs findings.json annotation)
- Custom rule parsing implementation (YAML library choice, error handling)
- Step 0b complexity tool selection priority (radon vs lizard vs both)
- DIM-01/04 prompt wording for new dimensions
- Monitor mode UI/output handling (completely silent vs debug flag)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 1a/1b Artifacts (foundation)
- `.planning/phases/01a-trust-instrumentation/01a-CONTEXT.md` -- D1-D10 decisions
- `.planning/phases/01b-trust-calibration/01b-CONTEXT.md` -- D1-D5 decisions (confidence, tiers, rule improvement, Tricorder criteria)
- `skills/forge/SKILL.md` -- Current state machine, all 12 dimensions, finding persistence schema with confidence_signals
- `cli/forge_cli.py` -- classify_change(), evaluate_dimensions(), generate_recommendation(), show_stats(), backfill_confidence()
- `cli/config.json` -- tier_classification, evaluation sections
- `.forge/findings.json` -- Existing findings data with outcomes and reject_reasons

### Research (Phase 2 specific, from discuss-phase search)
- Google Tricorder CACM 2018: 4 criteria, "Not Useful" rate monitoring, two-tier model (compiler vs code review checks)
- Chromium clang-tidy: provisional approval + monitoring, quantified "Not Useful" rates (0-3.6%)
- Facebook Infer CACM 2019: diff-time vs batch deployment, AL declarative checker language
- Semgrep: Monitor -> Comment -> Block pipeline, anti-overlap philosophy, rule contribution requirements
- SonarQube 10.2+: Clean Code taxonomy reorganization, MQR mode (one rule -> multiple qualities)
- ESLint: "overlapping rules confuse end users" policy, rule deprecation/merge history
- ICSE 2026 MLSEC (arXiv:2509.19117): deterministic metrics match LLM for vulnerability detection
- arXiv:2502.20747: LLM review non-determinism even at temperature=0
- MSR 2025 (Jaoua et al.): hybrid deterministic+LLM review quality
- KNighter (arXiv 2025): deriving checkers from historical patch evidence
- Miller (1956) / Cowan: working memory 3-7 chunks
- Bacchelli & Bird (ICSE 2013): review benefits cluster into 3-4 outcomes
- SmartBear/Cisco (2006): checklist +66.7% defect detection, <400 LOC per review
- IEEE TSE 2023 (Guo et al.): FP mitigation survey, overlapping rules worsen FP
- LinearB (2025, 6.1M PRs): cognitive load cliff in code review

### Requirements
- `.planning/REQUIREMENTS.md` -- DIM-01 through DIM-07 definitions

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `cli/forge_cli.py:evaluate_dimensions()` -- Tricorder 4 criteria evaluation, Wilson CI. Input for D3 merge analysis.
- `cli/forge_cli.py:generate_recommendation()` -- Rule improvement suggestions. Extend for merge/retire recommendations.
- `cli/forge_cli.py:classify_change()` -- Deterministic tier classification. Extend for Step 0b complexity checks (DIM-03).
- `cli/forge_cli.py:run_dry_run()` -- Step 0 runner. Add Step 0b as sub-step for deterministic complexity checks.
- `cli/forge_cli.py:backfill_confidence()` -- Groups findings by (file, line, dimension). Reuse grouping logic for co-location analysis (D3).
- `cli/forge_cli.py:show_stats()` -- Dashboard. Extend for co-location matrix display.
- `cli/config.json` -- Configuration pattern. Add complexity thresholds, custom rule paths.

### Established Patterns
- **Atomic JSON write**: tempfile.mkstemp + os.replace
- **Finding schema**: UUID id, ISO timestamp, dimension, severity, outcome, reject_reason, confidence, confidence_signals
- **Sidecar pattern**: run metadata in .forge/runs/*.json
- **Config pattern**: cli/config.json with .get() defaults
- **Progressive deployment**: Phase 1b tier classification is the model for shadow mode (classification before LLM invocation)

### Integration Points
- `skills/forge/SKILL.md` -- New dimensions (DIM-01, DIM-04) need prompt additions. Existing dim 9 needs expansion (absorb DIM-02, DIM-05).
- `cli/forge_cli.py:run_forge()` -- Step 0b complexity check runs here (after Step 0a syntax, before LLM invocation)
- `cli/forge_cli.py:run_dry_run()` -- Add radon/lizard execution for Python files
- `.forge/findings.json` -- Shadow mode findings need a `shadow: true` flag or separate storage
- `forge-rules.md` / `.forge/rules/*.md` -- New file(s), loaded by forge_cli.py before LLM invocation

</code_context>

<specifics>
## Specific Ideas

### From Discussion
- User insisted on keeping zero-data dimensions ("abandoning them = actively avoiding coverage gaps")
- User required paper/project evidence for ALL four sub-decisions in D2 before accepting
- User selected data-driven merging over upfront semantic judgment ("don't guess, measure")
- User preferred hybrid YAML+Markdown format for custom rules (LLM consumption + structured metadata)

### Research-Backed Decisions
- D1: Google Tricorder provisional deployment pattern
- D2: 16+ papers/production case studies across Google/Chromium/Facebook/Semgrep/SonarQube/ESLint/ICSE/MSR/IEEE TSE
- D3: Miller/Hick-Hyman cognitive science + SonarQube/ESLint tool evolution + ICSE 2013/SmartBear empirical
- D4: Devin playbooks / CodeRabbit / Windsurf team prompts as format precedent

</specifics>

<deferred>
## Deferred Ideas

- **Embedding-based dimension deduplication** (LEARN-03): deferred to v2 with explicit trigger (rule-based dedup error rate >20% over 3+ months)
- **AST-based SKILL.md editing** (LEARN-04): deferred to v2 with trigger (3+ line-based edits corrupt markdown)
- **Cross-project rule sharing**: share custom rules across forge installations (v2 ADV-02)
- **ML-based co-location clustering**: use unsupervised clustering on finding embeddings for merge candidates (v2, if rule-based co-location analysis proves insufficient)

</deferred>

---

*Phase: 02-dimension-gap-closure*
*Context gathered: 2026-05-12*
