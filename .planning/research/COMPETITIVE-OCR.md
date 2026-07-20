# Competitive Analysis: open-code-review (OCR) vs forge

Status: final, evidence-grounded. Author-verified from source, 2026-07-01.
Scope: user-facing capability gaps only. Not a feature parity checklist.

## Provenance and trust

This document supersedes the raw sub-session gap-verify report. That report was
directionally useful but had two wrong verdicts on its highest-value points,
caused by finder agents that never located the OCR repo (searched "OCR" as a
generic term). Every claim below was re-verified by reading OCR Go source and
forge Python source directly. Where the sub-session was wrong, the correction is
logged in "Corrections to the raw report" and must be honored: do not re-cite the
raw report's P4/P7 verdicts.

OCR repo: github.com/alibaba/open-code-review (Go, Apache-2.0, created 2026-05-18,
~9.7k stars at check time). Read via raw.githubusercontent.com.
Forge: local, src/code_forge/.

## One-line positioning

OCR = team/CI line-level comment generator that scans and posts inline PR review
comments. forge = local blocking gate that runs real tests plus deterministic
static analysis and refuses to let code ship until three clean review cycles pass.
They are complementary, not substitutes. Neither is a superset of the other.

## Determinism: same word, different halves (sharpest distinction)

Both projects market "deterministic" review. It is not the same claim, and this
is the most load-bearing point for positioning -- do not let "we are both
deterministic" collapse it.

- OCR = ORCHESTRATION determinism. Engineering (not the LLM) fixes which files
  are selected, how they bundle into isolated-context sub-agents, which
  per-language rule doc applies, and comment relocation/reflection. But the
  bug-finding itself is 100% LLM. OCR's determinism is the scaffolding AROUND the
  model. Evidence: README "Deterministic Engineering x Agent Hybrid"; batch.go:42;
  system_rules.go:127; relocation.go:19.
- forge = ANALYSIS determinism. L0 linters (semgrep/ruff/clippy/checkpatch), L2
  mutation gate, and R1 real-test execution find real defects with NO LLM in the
  loop, and the gate blocks on them. forge's determinism is the analysis itself.

Consequence: the two solve different halves of the same problem. OCR makes the
LLM's review stable and well-targeted; forge adds non-LLM ground truth (tests
actually run, linters actually fire) and refuses to ship on it. This is why
"close the gap" never means "become OCR" -- forge's determinism is the half OCR
structurally does not have.

## Verified capability gaps (forge lacks; worth considering)

Each row: OCR evidence (file:line) | forge evidence (file:line or confirmed absence)
| verdict. "CONFIRMED" = I read both sides.

| Gap | OCR evidence | forge state | Verdict |
|-----|--------------|-------------|---------|
| Whole-repo / whole-dir auto-scan | scan_cmd.go:51,249 (--path default: whole repo; "Scan the entire repository") | cli.py:329 --whole-file is explicit PATHs only (nargs="+"); :2307 _resolve_whole_file_specs; no auto-traversal | CONFIRMED (narrow: forge has explicit-path whole-file, lacks auto repo/dir traversal) |
| Batching by language / directory | batch.go:42 groupBatches; :15-20 strategies none/by-language/by-directory; :60 sort.Strings determinism | grep of review path: no grouping | CONFIRMED |
| Review concurrency (worker pool) | agent.go:448-452 semaphore, default 8; :478/486/488 goroutine dispatch | none in review path (asyncio present only in mcp_jobs.py/mcp_server.py/factories.py = MCP job lifecycle, not review) | CONFIRMED |
| Token budget with mid-dispatch gating | scan_cmd.go:65 --max-tokens-budget; :39 0=unlimited; agent.go:442-448 stops launching subtasks once budget exceeded, bounded overrun | none | CONFIRMED |
| Cross-comment DEDUP pass | scan_cmd.go:62 --no-dedup ("per-batch DEDUP_TASK"; one LLM call per batch to dedup raw comments) | none | CONFIRMED |
| Per-file tool-call round budget | scan_cmd.go:58 --max-tools | none (forge does not expose a per-file agentic tool budget) | CONFIRMED |
| Curated per-language LLM review rulebook + false-positive suppression | system_rules.json (glob->doc map); rule_docs/java.md:28-41 thread-safety flag/don't-flag matrix; ts_js_tsx_jsx.md:34-40 security section (XSS/eval/innerHTML/secrets); mapper_dao_xml.md:17-33 SQLi (${} vs #{} binding); "prefer false negatives over false positives" | forge relies on semgrep (deterministic) + generic LLM prompt; no curated per-language LLM checklist library | CONFIRMED (prompt-shaped, not a deterministic engine; see P7 correction) |
| 4-layer rule override | system_rules.go:241-242 custom > project > global > system | gate.yaml is single-layer | CONFIRMED |
| Comment relocation / reflection pass | diff/relocation.go:19 ReLocateComment (regenerate existing_code via LLM when text match fails, retry ResolveComment at :64); prompts re_location_task_{system,user}.md | forge emits SARIF/verdict; no line-correction retry pass | CONFIRMED |
| PR/MR review bot (inline comments, @mention trigger) | examples/github_actions/ocr-review.yml (createReview, /open-code-review + @open-code-review triggers, --format json) | forge is local git-hook + MCP gate; no PR-comment path | CONFIRMED |
| Interactive multi-provider onboarding + connectivity self-test | ocr llm providers / config set / llm test; presets anthropic/openai/dashscope/deepseek/z-ai | forge has backend system + forge_resolve_outlet diagnostic, but no interactive wizard and no llm test | CONFIRMED (overlaps Phase 36 onboarding scope) |
| i18n review-output language | configurable output language; docs in multiple languages | forge output English-only | CONFIRMED |
| Public benchmark | OCR README self-report (50 repos / 200 PRs / 1505 labels; claims ~1/9 token, higher F1) | none | VENDOR-CLAIM, unreproduced. Do not launder into fact. |
| WebUI session viewer | ocr viewer (localhost) | none | CONFIRMED (see roadmap: declined) |
| OpenTelemetry spans/metrics | OTLP export | none | CONFIRMED (see roadmap: declined) |
| Single-binary / npm / curl distribution | Go single binary | forge is pip/Python | CONFIRMED (see roadmap: declined) |

## Where forge is ahead (do NOT treat these as gaps to close)

| forge capability | OCR state |
|------------------|-----------|
| Blocking pre-commit gate (git hooks + cycle-counter state machine + verdict states) | OCR generates comments; blocks nothing locally |
| Deterministic layers: L0 linters (ruff/pylint/clippy/shellcheck/semgrep/checkpatch), L2 mutation gate, R1 real-test gate (runs the suite, blocks new failures), R3 cross-component coverage | OCR has none of these |
| MCP sampling (borrow client model, no API key) | OCR always needs its own LLM key |
| SARIF output; multi-model cross-review; kernel-C/checkpatch; gate.yaml SHA-256 trust model | OCR has none |

## Corrections to the raw report (must honor)

- P4 (relocation/reflection module): raw report said UNVERIFIABLE ("no
  locator/reflect module found"). WRONG. Module exists as diff/relocation.go
  (ReLocateComment). The finder searched the wrong keyword. Verdict corrected to
  CONFIRMED. A "cannot verify" is itself a claim; here it was a false negative.
- P7 (security ruleset): raw report said REFUTED ("same as forge, LLM+prompt").
  Half wrong. REFUTED is correct only for the "deterministic rule engine" reading
  (system_rules.go is a doublestar glob->prompt router, embed.FS, not an AST/regex
  engine). But OCR does ship a curated per-language LLM rulebook with security
  sections (NPE/thread-safety/XSS/SQLi) and engineered FP-suppression, which forge
  lacks. Net verdict: PARTIAL, two-dimensional -- see the gap table. Do not cite
  the flat REFUTED.

## Roadmap impact (schedule)

Milestone context: current is v2.7 Provider Capability (Phase 36 next, not
started). Gap-closing work below is proposed for a new milestone after v2.7.
Priority is by fit-to-forge-identity (precision and blocking gate), NOT by
"OCR has it." Each build item still owes the standard forge pipeline before any
code: scope-challenge (does it need to exist; 3 real consumers or demand signal;
cost of do-nothing) -> plan -> external multi-model review -> user approval ->
execute. This document schedules intent, not execution.

### Tier 1 -- build (fits identity, real user pain)

- Batching + concurrency + token budget for the existing review path.
  Rationale: forge's single-context review can exhaust context/tokens on a large
  changeset. This is the "large-changeset stability" gap and it serves forge's
  reliability, not just throughput. Consumers: any forge run over a large diff or
  --whole-file across a module. Scope note: the batching/concurrency/budget for
  large DIFFS is in-scope; whole-repo TRAVERSAL (audit mode) is a separate new
  mode and is deferred to Tier 3 pending a demand signal.
  Proposed slot: new milestone, first phase.

### Tier 2 -- build (precision multiplier, serves "honest green")

- Curated per-language review rulebook + FP-suppression matrices.
  Rationale: directly serves forge's high-precision / anti-noise value. The
  don't-flag matrices (java.md thread-safety, mapper #{} binding, "prefer false
  negatives") are exactly forge's philosophy, expressed as reusable per-language
  guidance. Consumers: every forge review in a supported language. Arguably higher
  value than Tier 1 for forge's identity (precision > throughput for a blocking
  gate). Scope note: adopt the CURATION pattern; do not copy OCR's docs verbatim.
- Comment relocation / reflection pass (right-line correction before emit).
  Rationale: fewer wrong-line findings = higher trust in a blocking gate. Medium
  effort, clear identity fit.

### Tier 3 -- consider, gated on demand signal

- PR/MR review bot (inline comments). Big adoption lever but it is CI comment
  generation -- OCR's core turf and somewhat counter to forge's local-blocking
  identity. forge already has MCP + git-hook surfaces. Build only on a concrete
  request. Document as optional integration meanwhile.
- Whole-repo audit mode (traversal, not just batching of a diff). New use case
  distinct from the pre-commit gate. Needs a named consumer.
- i18n review-output language. Low effort, real for non-English users. Note:
  forge repo docs stay English-only; this is about review-comment output only.

### Tier 4 -- decline + document (anti-identity or YAGNI)

- WebUI viewer: counter to CLI-first identity. Decline.
- OpenTelemetry: enterprise telemetry on a local dev tool. YAGNI until enterprise
  demand. Decline.
- Single-binary distribution: forge is Python; a rewrite for a single binary is a
  large cost with pipx already covering distribution. Decline; document pipx as
  the answer.
- Marketplace plugins: forge already ships setup docs for Cursor/Copilot/VSCode/
  PyCharm. Marketplace packaging is polish, not capability. Low priority.

## Open verification debt

None outstanding for the gap table. P6 remains a vendor self-report by design.
If the PR-bot Tier-3 item is ever picked up, re-read examples/gitlab_ci/ to
confirm the GitLab MR inline path (only the GitHub Actions path was read).
