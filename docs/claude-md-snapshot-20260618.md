<!-- GSD:project-start source:PROJECT.md -->
## Project

**Forge**

A 5-step code review pipeline for AI coding assistants that enforces minimum 9 static review passes before any commit. Forge treats code review as a state machine with cycle-counter logic, hook enforcement, and anti-hallucination gates. It serves both developers using AI coding tools (Claude Code, Cursor, Copilot) who want rigorous review, and AI tool builders who want to integrate review pipelines into their products.

**Core Value:** No code ships without surviving three consecutive clean review cycles from three independent perspectives. The cycle counter resets on any finding -- quality is non-negotiable.

### Constraints

- **Language**: All documentation and skill files in English
- **Dependencies**: bash assertion primitives require only jq; skills require Claude Code or compatible AI coding assistant
- **Compatibility**: Must work with Claude Code skill discovery (SKILL.md in ~/.claude/skills/<name>/)
- **No non-ASCII in code**: typographic characters (em dash, smart quotes) must be ASCII equivalents
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Category 1: AI Coding Assistants
### GitHub Copilot Code Review
- Correctness (logic errors, off-by-one, null dereference)
- Security (injection, hardcoded secrets, missing input validation)
- Performance (unnecessary allocations, N+1, blocking async)
- Best practice violations (deprecated APIs, missing error handling)
- Context window limits clip analysis on large PRs and monorepos with deep dependency chains
- No business domain understanding -- mechanical first pass only
- 29% of reviews produce zero feedback (silent rather than noisy)
- No cycle-counter or convergence mechanism -- single pass, single perspective
- **Deterministic + LLM hybrid:** Copilot blends CodeQL/ESLint deterministic detections with
- **Auto-review on PR open:** configurable automatic trigger. Forge currently requires
- **Coding agent handoff:** review findings passed directly to coding agent for fix PRs.
### Cursor (Bugbot / Background Agents)
- Bug detection (logic errors, edge cases, security)
- Lint error detection and auto-fix
- Code quality via AI-driven suspicious code highlighting
- PR review via Bugbot (graduated to "fixer", Feb 2026)
- Agent Mode sometimes edits unintended files (forge handles this with scope verification)
- Inconsistent outputs on complex tasks -- needs multiple retries
- No formal multi-pass convergence -- single-pass review
- Linting is AI-driven, not deterministic -- can miss what static tools catch
- **Bugbot's fix-and-verify loop:** detects bug, writes fix, runs tests, proposes fix on PR.
- **Supermaven autocomplete as review signal:** fastest autocomplete engine in market uses
- **Background agents:** long-running cloud agents that work for tens of minutes on
### Aider
- Not a review tool per se -- a code generation tool with review-adjacent features
- Built-in linting (auto-lint after every change, auto-fix loop)
- Test integration (run tests after changes, capture failures, propose fixes)
- Git-native traceability (every AI edit becomes a reviewable commit)
- No adversarial review of its own changes -- it trusts its own output
- No security-specific analysis
- No architectural or SOLID analysis
- Lint+test loop can over-fit implementation to broken tests
- No multi-perspective review (single model, single pass)
- **Lint-fix loop with configurable retry limit:** Aider's "change -> lint -> fix -> re-lint"
- **Watch mode with in-editor markers:** `AI!` and `AI?` comment markers that trigger
- **Architect mode (plan/implement split):** uses a smart model for planning and fast model
### OpenAI Codex
- Full autonomous workflow: read codebase, plan, implement, test, create PR
- Code review as part of the autonomous loop (not a standalone review tool)
- Security vulnerability resolution (integrated with static analysis output)
- Review is embedded in generation, not adversarial -- reviewing its own output
- Requires clear requirements; struggles with ambiguity
- No formal multi-pass convergence mechanism
- No separation between "author" and "reviewer" perspectives
- **Sandboxed execution environment:** Codex runs in isolated containers with full toolchain.
- **Integration with static analysis output:** Devin/Codex excel at taking SonarQube or
### Devin
- "Devin Review" feature: logic errors, missing edge cases, style violations
- Full autonomous workflow with PR generation
- Codebase learning (indexes patterns, conventions, tribal knowledge)
- Functions as "junior engineer" -- needs clear requirements, struggles with ambiguous tasks
- Human review still required for unit testing logic and code review output
- No formal multi-pass or convergence mechanism
- 67% PR merge rate means 33% of output is rejected
- **Playbooks and knowledge docs:** Devin supports creating custom playbooks that teach it
- **Fleet parallelism:** multiple Devin instances executing in parallel across repos. Forge
- **Desktop computer-use for testing:** Devin 2.2 can launch and interact with GUI
### Windsurf (Cascade)
- PR review workflows via GitHub app
- AI-assisted code review with team prompts for consistency
- Merge conflict resolution
- Code quality via Cascade's agentic planning
- Acquired by Cognition AI (Dec 2025), then by OpenAI (May 2025) -- future uncertain
- Review is a secondary feature, not the core product
- No formal multi-pass convergence
- Limited to supported IDE workflow
- **Memories system:** Cascade auto-saves context about codebase and workflow between
- **Team prompts for consistent review:** shared team-level instructions ensure consistent
## Category 2: Traditional Static Analysis
### SonarQube
- Bugs (reliability)
- Vulnerabilities (security) with OWASP Top 10, CWE Top 25, SANS Top 25 mapping
- Code smells (maintainability)
- Security hotspots (requires manual triage)
- Secrets detection (400+ patterns)
- SCA (third-party dependency vulnerabilities, malicious packages) -- Enterprise
- AI-generated code detection and enhanced verification (2025)
- SAST only -- no DAST or runtime analysis
- No architectural/design review
- No cross-file semantic analysis beyond taint tracking
- Often deployed as code-quality rather than security tool -- security rules underutilized
- High false positive rate on security hotspots (requires manual triage)
- No adversarial or multi-perspective review
- Cannot detect business logic errors
- **Quality gate with baseline:** marks current state, tracks only new problems. Pre-existing
- **Standards mapping (OWASP, CWE, PCI DSS):** findings mapped to industry standards.
- **AI Code Assurance:** automatic detection of AI-generated code with enhanced verification
- **Severity + type classification system:** bugs vs vulnerabilities vs code smells vs
### Semgrep
- Security vulnerabilities (OWASP Top 10, CWE patterns)
- Custom rule enforcement (rules look like source code)
- Cross-file and cross-function dataflow analysis (Pro)
- Secrets detection
- Infrastructure-as-code security (Terraform, K8s, Dockerfiles)
- Community edition is single-file only -- cross-file requires Pro
- No architectural or design review
- No review of business logic or correctness beyond security patterns
- Custom rules require manual creation -- learning curve for team-specific patterns
- Infrastructure focus may miss application-level logic bugs
- **Rules-as-code that look like source code:** Semgrep rules are readable YAML that resemble
- **Semgrep Skills for AI agents:** curated security knowledge packages that AI coding agents
- **Semgrep Memories:** learns from security decisions and applies that knowledge to future
- **Semgrep Workflows (Python):** custom security policies encoded as automated pipelines.
### Qodana (JetBrains)
- Code quality (3,000+ IDE inspections)
- Security vulnerabilities (taint analysis for 700+ entries)
- Technical debt tracking
- 60+ language support
- Configuration-as-code (inspection profiles in repository)
- Taint analysis currently limited to PHP and JVM linters
- No LLM-based review -- purely deterministic
- No architectural or design review
- No adversarial or multi-perspective review
- IDE-centric philosophy may not cover all CI/CD patterns
- Smaller rule base than SonarQube (3,000 vs 6,500)
- **IDE-consistent inspections:** same analysis in IDE and CI/CD. No surprises at build time.
- **Configuration-as-code with version control:** inspection profiles stored in repo, changes
- **Two-tier auto-fix (CLEANUP vs APPLY):** safe vs risky auto-fix distinction. Forge fixes
- **Global project configuration distribution:** shared inspection files distributed across
### CodeRabbit
- Readability, maintainability, security, potential bugs (PR-level)
- Integrated static analysis (ESLint, Ruff, Pylint, golangci-lint, Clippy, TruffleHog,
- Conversational review (ask for explanations, test generation, docstrings in PR comments)
- Multi-repo analysis (downstream breakage detection for shared APIs, Mar 2026)
- Code graph analysis (dependency understanding, 2026)
- 2-4 week tuning period before reviews reach peak relevance
- Most "talkative" tool -- highest comments per PR, including noise
- January 2026 benchmark: caught all hidden bugs but scored 1/5 on completeness
- Surfaces issues fast but misses architectural reasoning
- Not a replacement for human architectural judgment
- No multi-pass convergence mechanism
- **MCP context integration:** pulls business context from Slack, Confluence, Notion,
- **Multi-repo analysis:** checks downstream repos when shared APIs change. Forge reviews
- **Issue Planner (Feb 2026):** auto-generates coding plans from Jira/Linear issues before
- **Conversational review in PR:** natural language interaction within PR comments. Forge's
- **Sandboxed static analysis:** runs linters in ephemeral containers. Forge runs linters
### CodeClimate (now Qlty)
- Maintainability scoring
- Test coverage tracking
- Technical debt assessment
- Duplication detection
- Complexity metrics
- No LLM-based review
- No security-specific deep analysis (no taint analysis)
- No architectural review
- Struggled with scale at hundreds of repositories (limited filters/search)
- Primarily maintainability-focused, not security-focused
- **Maintainability scoring as a KPI:** "Code Score" provides a trackable quality metric
- **Open engine architecture:** anyone can write static analysis engines following a
- **Hot spot identification:** identifies frequently changed files that need more coverage.
### Greptile
- Deep codebase-aware analysis via repository knowledge graph
- Multi-hop investigation (trace dependencies, check git history, follow leads across files)
- Logic bugs, edge cases, performance issues, security vulnerabilities
- Highest false positive rate in independent evaluations
- Noise level is a significant tradeoff for depth
- $30/dev/month is expensive
- Enterprise-focused -- may not fit individual developer workflows
- **Repository knowledge graph:** full codebase indexed with function, dependency, and
- **Multi-hop investigation:** traces dependencies 2-3+ levels deep autonomously. Forge's
- **Git history context:** uses historical changes to inform review. Forge reviews only
## Category 3: Open-Source Upstream Practices
### Linux Kernel
- **Coding style** (checkpatch.pl -- regex-based pattern matching)
- **Semantic patterns and API usage** (Coccinelle -- AST-level semantic patches)
- **Type checking, lock checking, value range** (Sparse)
- **Logic mistakes** (Smatch -- missing breaks, unused return values, null derefs, leaks)
- **Human review dimensions:** design, security, style, plus long-term maintainability
| Tool | Method | Dimension |
|------|--------|-----------|
| checkpatch.pl | Regex pattern matching | Style, formatting, trivial violations |
| Coccinelle | AST semantic patches | API usage, mass refactoring, macro bugs |
| Sparse | Static analysis | Types, locks, address spaces, value ranges |
| Smatch | Static analysis | Logic bugs, leaks, null derefs, overflows |
| Human (LKML) | Expert review | Design, architecture, long-term maintainability |
- 73.87% of patches not reviewed in the past 10 years (scale problem)
- checkpatch.pl has no global view -- cannot detect cross-fragment inconsistencies
- Coccinelle has steep learning curve, hard to incorporate into patch acceptance workflow
- Sparse/Smatch are C-specific
- Human review is bottlenecked on maintainer availability
- No formal convergence mechanism -- review continues until maintainer is satisfied
- **Coccinelle's semantic patches:** pattern-matching that works before the preprocessor,
- **Multi-tool pipeline with complementary dimensions:** kernel's checkpatch + Coccinelle +
- **Review tags with semantic meaning:** `Reviewed-by:` vs `Acked-by:` vs `Tested-by:`
- **Long-term maintainability as explicit review dimension:** "what will it be like to
- **Patch self-containment:** "include the complete patch description and justification --
### LLVM
- Readability and maintainability
- Robustness and defect prevention
- Leveraging reviewer experience
- Mentorship of new contributors
- Post-commit review allowed for smaller patches -- risks regression
- clang-tidy-diff.py only reports diagnostics for changed lines, may miss issues that
- RFC process is informal -- no formal convergence mechanism
- Review depends on maintainer expertise and availability
- **User feedback on automated checks:** "Not Useful" button on automated comments feeds
- **RFC for design-level changes:** separates design review from code review. Forge reviews
- **Provisional approval with feedback monitoring:** new checks are turned on provisionally
### Chromium
- Code correctness (human review on Gerrit)
- Style and lint (clang-tidy automated on every CL)
- Build and test (trybots + CQ dry run)
- OWNERS-based review (domain expertise enforcement)
| Component | Method | Dimension |
|-----------|--------|-----------|
| Gerrit | Human review | Correctness, design, architecture |
| clang-tidy | Automated, curated checks | C++ lint, patterns |
| CQ Dry Run | CI builder subset | Build, test on affected platforms |
| CQ Submit | Full CI | Comprehensive test on all platforms |
| Mega CQ | All CI builders + CI-only tests | Maximum test coverage for risky CLs |
| OWNERS | Human, domain-enforced | Domain expertise sign-off |
- Median trybot cycle time must be under 40 minutes -- limits test depth
- Google-internal builders only run in full CQ (not dry runs) for non-Googlers
- clang-tidy balance between signal/noise means many checks are disabled
- OWNERS system can bottleneck on specific reviewers
- **Tiered CI based on risk:** CQ Dry Run (quick), CQ Submit (full), Mega CQ (everything).
- **OWNERS-based domain expertise:** specific reviewers required for specific code areas.
- **CL dependency management (Cq-Depend):** coordinates multi-repo merges in order. Forge
- **Gerrit trybot status visualization:** color-coded bubbles (gray/yellow/purple/red) for
### Rust Compiler
- Code correctness (human review via GitHub PRs)
- Full test suite before merge (bors queue)
- Performance regression detection (perf.rust-lang.org benchmarks)
- Reviewer expertise enforcement (r? @username, handoff for unfamiliar code)
| Component | Method | Dimension |
|-----------|--------|-----------|
| Human review | GitHub PR review | Correctness, design |
| bors | Merge queue bot | Pre-merge test execution |
| perf.rust-lang.org | CI benchmark suite | Performance regression detection |
| rustbot | Auto-assignment | Reviewer routing by file path |
| Try build | Subset test run | Quick smoke test while queuing |
- Serialized merge queue bottleneck -- 2+ hour test suite, can take days for patches to land
- bors privileges are binary (no per-area granularity)
- No formal review checklist or structured dimensions -- relies on reviewer expertise
- No automated static analysis beyond clippy/rustfmt
- **Pre-merge test execution (not post-merge):** bors tests as-if-merged before committing
- **Performance regression benchmarking as review gate:** perf.rust-lang.org runs
- **Reviewer expertise routing:** rustbot auto-assigns reviewers based on file paths.
- **Revert-first policy:** meaningful regressions get reverted fast, re-landed with fix
- **Try build as smoke test during queue wait:** quick smoke test runs while PR waits in
## Cross-Cutting Analysis
### Review Dimensions Matrix
| Dimension | Forge | Copilot | CodeRabbit | SonarQube | Semgrep | Kernel | LLVM | Chromium | Rust |
|-----------|-------|---------|------------|-----------|---------|--------|------|----------|------|
| Correctness/Logic | Yes (3 passes) | Yes | Yes | Yes (rules) | Partial | Yes | Yes | Yes | Yes |
| Security | Yes (dim 4) | Yes | Yes | Yes (deep) | Yes (core) | Yes (human) | Partial | Partial | No |
| Performance | Yes (dim 10) | Yes | Partial | Partial | No | No | No | No | Yes (benchmarks) |
| Architecture/SOLID | Yes (pass 2) | No | No | No | No | Yes (human) | Yes (human) | Yes (human) | Yes (human) |
| Style/Formatting | Yes (step 0) | No | Yes (linters) | Yes | No | Yes (checkpatch) | Yes (clang-tidy) | Yes (clang-tidy) | Yes (rustfmt) |
| Concurrency | Yes (dim 5) | Partial | No | No | No | Yes (Sparse locks) | No | No | No |
| API/Contract | Yes (dim 6) | No | Yes (multi-repo) | No | No | Yes (Coccinelle) | No | No | No |
| Bidirectional | Yes (dim 7) | No | No | No | No | No | No | No | No |
| Graceful degradation | Yes (dim 8) | No | No | No | No | No | No | No | No |
| Convention adherence | Yes (dim 9) | No | No | Partial | No | Yes (checkpatch) | Partial | Partial | No |
| Test quality | Yes (dim 11) | No | No | Yes (coverage) | No | Yes (human) | No | No | Yes (human) |
| AI code smells | Yes (dim 12) | No | No | Yes (2025) | No | No | No | No | No |
| Multi-pass convergence | Yes (3 cycles) | No | No | No | No | No | No | No | No |
| Anti-hallucination | Yes (3 gates) | No | No | N/A | N/A | N/A | N/A | N/A | N/A |
| Runtime verification | Yes (step 4) | No | No | No | No | No | No | Yes (CQ) | Yes (bors) |
| Performance benchmarks | No | No | No | No | No | No | No | No | Yes |
| Deterministic rules | Yes (step 0) | Yes (CodeQL) | Yes (linters) | Yes (core) | Yes (core) | Yes (4 tools) | Yes (clang-tidy) | Yes (clang-tidy) | Yes (clippy) |
| Cross-repo impact | No | No | Yes | No | No | No | No | Yes (Cq-Depend) | No |
| Feedback learning | No | No | Yes | No | Yes (Memories) | No | Yes ("Not Useful") | No | No |
| Long-term maintainability | No | No | No | Yes (tech debt) | No | Yes (10-year) | Yes | No | No |
### What Forge Covers That Nobody Else Does

**LIVE (shipped):**
- **Multi-pass convergence with cycle-counter reset.** Three consecutive clean cycles from three independent perspectives (static parsers + LLM adversarial review + anti-hallucination gates). Any finding resets the counter. No other tool enforces convergence -- they are single-pass, single-perspective.
- **Anti-hallucination gates (3 gates).** Forge treats AI review output as untrusted claims: (1) parser-deterministic L0 findings auto-confirmed, (2) LLM L1 findings require falsification before disposition, (3) step-4 smoke test runs the actual code. Prompt-only mitigations cap out at 15% hallucination reduction; tool grounding achieves 65-80%.
- **Real test commit gate (R1).** A real `.git/hooks/pre-commit` that runs the test suite and blocks on NEW failures vs a baseline. Gates on diff content and test results, not a self-claimed marker. Closes the terminal/IDE bypass that PreToolUse hooks cannot reach.
- **Bidirectional review (dim 7) and graceful degradation (dim 8).** Review dimensions no other tool in the matrix covers.
- **Mutation-gated review (R2).** Diff-scoped mutation as a pipeline step, not a commit gate. Each mutant introduced into the changed code is run against the test suite; a surviving mutant flags tests that cannot catch the change. This runs after static review and before the verdict, so toothless tests block the same cycle that finds the code defect.
- **Cross-component coverage heuristic (R3).** Detects diffs that span multiple source areas with a changed function signature and warns (advisory). Opt-in components mapping enables a stronger trigger: when a hub and a dependent are both modified in the same diff and no integration test under the dependent's paths matches the configured test patterns, forge raises an uncertain finding -- pausing the pipeline -- with an escape hatch for components that intentionally have no integration coverage. The trigger checks artifact presence, not coverage proof; a present-but-irrelevant test passes the gate.

### What Forge Is Missing

From the Review Dimensions Matrix above, forge does not cover:
- **Cross-repo impact.** Forge reviews a single repository. Multi-repo dependency analysis (e.g., "this API change breaks a downstream consumer") requires CodeRabbit's multi-repo feature or Chromium's Cq-Depend.
- **Feedback learning.** Forge does not learn from dismissed findings or developer preferences. CodeRabbit, Semgrep Memories, and LLVM's "Not Useful" button adapt over time; forge treats each review as independent.
- **Long-term maintainability.** Forge does not assess technical debt accumulation or "what will this be like to maintain in 10 years" (the Linux kernel's explicit review dimension). SonarQube's tech debt tracking is the closest automated approximation.
- **Performance benchmarks.** Forge does not run performance regression suites. Rust's perf.rust-lang.org is the gold standard for automated performance gates.

**Honest assessment:** Static review passes (parsers + 3-cycle convergence) are one layer. Passes-count is not a quality guarantee -- forge learned this from its own Phase 2 experience where 9 static review passes + 639 mock tests missed 3 bugs that dynamic verification caught. Verification grounding (run suite + mutation + e2e) is forge's thesis, and all three legs have now shipped. One ceiling remains honest: the cross-component coverage check (R3) confirms that an integration test file is present under the expected path -- it does not verify that the test exercises the specific code that changed. A present-but-stale test passes the gate. The test commit gate (R1), mutation runner (R2), and coverage heuristic (R3) together form the dynamic layer; static review alone is not enough, and forge no longer ships as if it were.

## AI Code Review: Hallucination and False Positive Data
- **5-15% false positive rate** is typical for AI code review platforms.
- **29-45% of AI-generated code** contains security vulnerabilities.
- **65% of code hallucination errors** are inventing plausible-but-nonexistent symbols.
- **Multi-model consensus** (3 LLMs in parallel, flag when 2+ agree) filters model-specific
- **Retrieval grounding (RAG)** reduces hallucinations by 75-90% -- strongest mitigation.
- **Tool grounding** reduces by 65-80%.
- **Prompt-only mitigations** cap out at 15% reduction.
- **Reasoning models hallucinate more on factual benchmarks** (o3: 33%, o4-mini: 48% on
- **Confidence paradox:** models are 34% more likely to use confident language when
## Sources
### AI Coding Assistants
- [GitHub Copilot Code Review - Official Docs](https://docs.github.com/en/copilot/concepts/agents/code-review)
- [Copilot Code Review Complete Guide 2026](https://dev.to/rahulxsingh/github-copilot-code-review-complete-guide-2026-255h)
- [Copilot Code Review New Features Discussion](https://github.com/orgs/community/discussions/177790)
- [Best AI Coding Agents 2026 - Faros](https://www.faros.ai/blog/best-ai-coding-agents-2026)
- [AI Coding Agents Comparison - Artificial Analysis](https://artificialanalysis.ai/agents/coding)
- [Cursor AI Review 2026 - NxCode](https://www.nxcode.io/resources/news/cursor-ai-review-2026-features-pricing-worth-it)
- [Aider Linting and Testing Docs](https://aider.chat/docs/usage/lint-test.html)
- [Aider GitHub Repository](https://github.com/aider-ai/aider)
- [Devin 2025 Performance Review - Cognition](https://cognition.ai/blog/devin-annual-performance-review-2025)
- [Windsurf Cascade](https://windsurf.com/cascade)
### Traditional Static Analysis
- [SonarQube 2025 Year in Review](https://www.sonarsource.com/blog/sonarqube-2025-year-in-review)
- [SonarQube Analysis Overview](https://docs.sonarsource.com/sonarqube-server/2025.1/analyzing-source-code/analysis-overview)
- [Semgrep Community Edition](https://semgrep.dev/products/community-edition/)
- [Semgrep Multimodal Launch](https://www.helpnetsecurity.com/2026/03/20/semgrep-multimodal-code-security/)
- [Semgrep GitHub Repository](https://github.com/semgrep/semgrep)
- [Qodana Official Site](https://www.jetbrains.com/qodana/)
- [Qodana vs SonarQube](https://blog.jetbrains.com/qodana/2026/03/sonarqube-vs-qodana/)
- [CodeRabbit Documentation](https://docs.coderabbit.ai/)
- [CodeRabbit Review 2026](https://ucstrategies.com/news/coderabbit-review-2026-fast-ai-code-reviews-but-a-critical-gap-enterprises-can-ignore/)
- [Greptile Benchmarks 2025](https://www.greptile.com/benchmarks)
- [State of AI Code Review 2025](https://www.devtoolsacademy.com/blog/state-of-ai-code-review-tools-2025/)
- [CodeClimate / Qlty](https://codeclimate.com/quality)
### Open-Source Upstream Practices
- [Linux Kernel Checkpatch Docs](https://docs.kernel.org/dev-tools/checkpatch.html)
- [Linux Kernel Coccinelle Docs](https://docs.kernel.org/dev-tools/coccinelle.html)
- [Linux Kernel Testing Overview](https://docs.kernel.org/dev-tools/testing-overview.html)
- [Linux Kernel Submitting Patches](https://docs.kernel.org/process/submitting-patches.html)
- [LLVM Code Review Policy](https://llvm.org/docs/CodeReview.html)
- [Chromium CQ Docs](https://chromium.googlesource.com/chromium/src/+/main/docs/infra/cq.md)
- [Chromium clang-tidy Docs](https://github.com/chromium/chromium/blob/main/docs/clang_tidy.md)
- [Rust Compiler Review Policy](https://forge.rust-lang.org/compiler/reviews.html)
- [Rust CI Testing Guide](https://rustc-dev-guide.rust-lang.org/tests/ci.html)
- [Rust bors Repository](https://github.com/rust-lang/bors)
### Hallucination and False Positive Data
- [LLM Hallucinations in AI Code Review - diffray](https://diffray.ai/blog/llm-hallucinations-code-review/)
- [AI Code Review False Positives - CodeAnt](https://www.codeant.ai/blogs/ai-code-review-false-positives)
- [AI Hallucination Rates 2026 - Suprmind](https://suprmind.ai/hub/ai-hallucination-rates-and-benchmarks/)
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

## Forge Code Review (run before the Phase Wrap-Up Protocol)

`/gsd:execute-phase` and the GSD verifier do NOT run the forge review pipeline --
the verifier only checks must-haves and requirement coverage. The 3-cycle static
review that the global CLAUDE.md mandates is a SEPARATE obligation: run it on the
changed files before the wrap-up accounting and before declaring the phase done.
(Phase 17 was marked Complete pre-review; the post-hoc review then took 5 cycles
to clear real bugs.)

**WHO -- author != reviewer, always two sub-sessions.** Never let one agent both
implement and review: an impl agent under context pressure self-justifies
skipping review every time (observed 3/3, each time a supplemental review found
real contract-violation bugs). Topology:
  1. impl agent (Sonnet) -- code + tests + Step 0.
  2. SEPARATE review agent -- independent 3-cycle review, fresh context.
  3. main session -- ONE verification round (tests pass, scan the diff); it does
     NOT repeat the full 3 cycles. Commit only if it finds nothing new.
When the user delegates "subagent forge review", auto-chain review -> fix (relay
findings to a separate impl agent; the read-only reviewer cannot Edit) ->
re-review. Do not stop at the read-only report and wait for a second "and fix".

**WHERE -- inline/hot, never a cold subagent.** The 9 passes must run in the
session that already holds the diff and source hot. A cold subagent re-derives
everything and truncates mid-review (~65K of 128K); two attempts failed this way.
If the orchestrator is low on context, offload to an external model via the CN
api backend (outlet=cli + type=api) -- never a cold Claude subagent.

**WHAT -- the real Skill, not a self-labeling code-reviewer.** Run review through
the actual `code-forge` Skill (or `aicc` for multi-model). A `code-reviewer`
subagent writing "Pass 1 -- qodo-style: ..." is self-describing, not invoking the
skill. The orchestrator -- not the review agent -- runs the AI-smell grep and the
non-ASCII check before `git commit`; it cannot trust that a sub-agent ran them.

**GATE -- inline forge green is advisory, not proof.** forge's inline outlet
(editor mode / Path C) is `return Verdict.PASS`: it runs no passes and verifies
nothing, so a shirking model prints CLEAN without reading and nothing catches it.
forge itself admits its receipts "cannot distinguish a diligent clean review from
a fabricated one." The un-fakeable layer is the EXTERNAL deterministic gates: the
R1 pre-commit test gate (live since Phase 18.1 -- runs the real suite, blocks on
new failures), Step-0 linters, danger-score, semgrep. Lean the verdict on those;
treat inline LLM passes as advisory until externally checked.

**Sub-session dispatch (forge v2.1+).** Sub-sessions follow three rules
(see `feedback_forge_subsession_rules.md`):
  - **No auto-merge.** Sub-session commits only inside a `git worktree`. The
    final output is `branch X at SHA Y, N tests green, ready for host ff-merge`.
    GSD `execute-phase` auto-merges by default; sub-sessions do not have that
    authority. Auto-merge of feat-01-04-r4 over a 4000-line stale main has
    already shipped once and had to be cleared retroactively.
  - **impl != reviewer.** Implementation and forge review (3 cycles, 9 passes)
    must be separate sub-sessions or subagents. Combined agents skip review
    100% of the time (3/3 observed in v2.0). After impl commits, dispatch a
    SEPARATE reviewer subagent.
  - **Topology-verify before merge.** Sub-session must report branch+SHA+diff
    stat. Host runs `git merge-base main <branch>` and
    `git diff main...<branch> --stat` before ff-merging. GSD has rebuilt main
    between sessions and made the work "ready to merge" framing wrong.

**Verdict trust: do not trust a green Exit 0.** forge's own backend
failure modes produce false-green verdicts that bypass the gate. Four
known traps (see `feedback_forge_cli_llm_timeout.md`,
`feedback_forge_false_green_large_diff.md`,
`feedback_forge_nongit_coverage_gate.md`,
`feedback_deepseek_forge_review_nonconvergence_small_diff.md`):
  - **No-backend silently degrades to `claude -p`** -- `claude` binary is
    always on PATH inside a session, so `claude auth status` probe passes
    and the resolver picks `outlet=cli` by default; the subprocess then
    cold-starts CLAUDE.md/hooks/MCP + does inference, routinely blowing
    past the 120s hard timeout. The correct no-backend behavior is FAIL
    FAST (clear error + 3 named outlets: `gate.yaml` backend /
    `--backend-*` flags / `FORGE_OUTLET=inline`), NOT a silent fall-through
    to `claude -p` and NOT auto-inline (the latter burns the main/Pro
    quota on review work).
  - **Passes dismissed on JSON parse error = false-green.** A backend that
    chokes on a large diff returns unparseable JSON; forge treats it as
    "no findings" (fail-open). On a 3560-line diff this silently defeated
    the gate. Mitigation: confirm the passes produced 0 findings vs were
    dismissed on an API/JSON error -- check pass logs for parse errors
    before trusting Exit 0.
  - **Non-git / empty-diff / unmatched-pattern files = L1 silent gap.**
    `baseline.py` sets `git_diff=None` on non-git reviews and `factories.py`
    returns `[]` when diff text is empty, so the L1 semantic pass never
    runs. Files unmatched by any L0 tool (e.g. shell files when
    `tools.yaml` has no `shellcheck`) then become UNCERTAIN COVERAGE
    findings -> CI FAIL, LOCAL HOLD (shipped in commit `2d44b11`).
    Distrust "tool not installed" theories until `which <tool>` proves it.
  - **deepseek-backend rounds 3+ on a small clean diff = non-convergent
    false-positive spin.** deepseek-chat finds 2 real bugs in rounds 1-2
    then oscillates, self-contradicts, and asserts provable factual errors
    to justify a non-empty verdict. Use deepseek as a 1-2 round bug-finding
    SWEEP, then circuit-break; verify every finding against the code
    (rejecting a finding is itself a claim); for a high-precision second
    opinion or clean-verdict gate prefer mimo-pro or inline Opus.

The un-fakeable layer is still the external deterministic gates (R1
pre-commit test gate live since Phase 18.1, Step-0 linters, danger-score,
semgrep). Lean the verdict on those.

**Reliability beyond forge (cross-phase principles).** Five rules the
review pipeline itself cannot enforce -- enforce them in the orchestrator.
  - **Three anti-shirk layers, strongest first.** Objective gates (R1-R3,
    Step-0, lint, mutation) > receipt + spot-check > human anchor. Opus
    4.6 faked a forge 9-pass review by self-printing CLEAN with 1 Read +
    1 grep; only two hands-on checks caught it (user read the transcript,
    main session re-read the diff). Systematize the hands-on check; do not
    hope for honesty. See `feedback_antishirk_system_over_trust.md`.
  - **Independence over intelligence.** The author of a change cannot
    review it: a context that just wrote the code re-runs the same
    reasoning and re-confirms it -- not shirking, a structural
    false-negative. The ICMPv6 selftest missed `convert_int(32)` (label is
    20 bits) inside a self-narrated 2-cycle inline PASS; an external
    reviewer (sashiko) caught it. Offload solo-authored reviews to an
    EXTERNAL backend (CN / gemini), never a cold Claude subagent (it
    truncates). See `feedback_author_cannot_self_review.md`.
  - **Cross-artifact verification.** A root cause corrected in one
    artifact (RESEARCH) leaves peers (SECURITY, CONTEXT, ROADMAP) silently
    stale. Phase 15/16: F3 false-green corrected in 16-RESEARCH.md but
    15-SECURITY.md T-15-02 still asserted the disproven threat-closed
    claim, flipping a sign-off to false `threats_open: 0`. When a
    decision is REVISED, grep every peer (CONTEXT, RESEARCH, SECURITY,
    VERIFICATION, UAT, ROADMAP, PROJECT) for the old claim and old fix
    location. See `feedback_correction_propagation_peer_artifacts.md`.
  - **Rejecting a finding is itself a claim.** Verify the assumption a
    rejection rests on (grep the namespace, read the source) before
    rejecting; a rejection from intuition is as dangerous as acceptance
    from intuition. Same family: forge TRUST-NN tags are REQUIREMENT IDs
    from planning -- never coin a new one in SKILL.md or prose. See
    `feedback_rejecting_a_finding_is_a_claim.md`.
  - **Aggregate signals do not verify.** A `440 passed` total proves
    liveness, not coverage; `git show --stat` shows line counts, not what
    the lines say; a tool's `N passes` is not the orchestrator running
    the tool. Before stating PASS/verified: list every changed file and
    confirm each was read or its assertions inspected; read full commit
    messages; re-run lint yourself. Correct-by-luck = process failure.
    See `feedback_review_evidence_completeness.md`.
  - **Verify plan version baseline before claiming deviation.** A "X
    deviates from plan" report compared against a superseded v1 will
    flag faithful v3 work as drift. Read the FINAL plan + actual changed
    files; map each claimed deviation to the plan version that actually
    governs. See `feedback_deviation_table_plan_version.md`.

## Phase Wrap-Up Protocol (mandatory before reporting "ready for next phase")

After a phase's code is merged to main AND verified by the main session, the
accounting half must be completed before declaring the phase done. This step is
skipped when running `/gsd:execute-phase` (the skill handles it internally).
It is NOT skipped when work was done manually or via a sub-session dispatch.

**Required in order:**

1. **ROADMAP.md**
   - Milestone list: `[ ] Phase N` --> `[x] Phase N -- completed YYYY-MM-DD`
   - Phase Details heading: append `-- completed YYYY-MM-DD`
   - Phase Details body: add `[x]` checklist for every plan under the phase,
     using real filenames from `.planning/phases/<N>-*/` (NOT invented names)
   - Progress table: `0/? Not started -` --> `Actual/Total Complete YYYY-MM-DD`

2. **STATE.md**
   - frontmatter: recount `completed_phases` and `completed_plans` from actual
     `[x]` marks in ROADMAP (do not trust old values -- they drift)
   - frontmatter: update `total_plans` if new plans were added since last sync
   - frontmatter: recalculate `percent` (completed_phases / total_phases x 100)
   - frontmatter: update `stopped_at`, `last_updated`, `last_activity`
   - body `Current focus`: advance to next phase
   - body `Current Position / Phase`: reflect actual next step
   - body `Total plans completed` (Performance Metrics): sync to frontmatter value
   - body `Session Continuity`: update `Stopped at` + `Resume file` to next plan

3. **Pre-existing drift -- verify, do NOT blind-flip:**
   If ROADMAP marks a plan `[ ]` but the phase is declared Complete elsewhere,
   check `git log --oneline` for the plan's scope (commit messages, `--stat`).
   If the work landed: flip to `[x]`. If it did NOT land: correct the
   "Complete" claim instead. Report which way and the evidence.

4. **Snapshot**: `bash .git/snapshot-planning.sh` -- take a fresh planning-local
   snapshot. Report the new hash in the wrap-up summary.

**Do NOT report "can authorize /gsd:execute-phase N+1" until steps 1-4 are done.**
The main session re-verifies from ground truth before authorizing execution.

## Commit Message Rules (forge-specific)

Follow the global CLAUDE.md format (`<subsystem>/<case>: <summary>` + body + Signed-off-by).
Additionally, forge commit messages MUST NOT contain review-internal vocabulary:

**Banned terms** (opaque to future git readers, AI smell):
- Severity labels: P0, P1, P2, P3, "blocker", "nit", "style nit"
- Review process refs: "review cycle", "completeness guard", "forge review"
- Bullet inventories: "Changes:" lists, "Added:", "Fixed:" enumerations
- Wave/task labels as subject nouns: "Wave 0 changes", "Task 1 fix"

**Write WHY, not WHAT.** A git reader who never saw the review must understand
why the change was necessary from the commit message alone.

Good: "declare DELEGATED when inline outlet runs" + body explaining the problem
Bad:  "fix P3 style nits in Wave 0 changes"

Good test subject: "extend exit code uniqueness guard to cover EXIT_DELEGATED"
Bad test subject: "extend EXIT_* completeness guards to cover EXIT_DELEGATED"

## Branch Hygiene

Feature and fix branches MUST be deleted immediately after the wave/phase is
merged to main and verified by the main session. Stale branches accumulate fast
and create false impressions of in-flight work.

**Rule:** after every `git merge --ff-only <branch>` + main-session CLEAN
verdict, delete the source branch before declaring the wave done:

```bash
git branch -D <branch>          # local
git push origin --delete <branch>  # remote, only if it was pushed
```

`protect_git_worktree.sh` blocks `git branch -D` in AI Bash tool calls.
The user must run the delete manually (or with `!` prefix in Claude Code).
AI agents must proactively remind the user to delete and provide the exact
command -- do not silently leave stale branches.

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->

## Planning File Persistence (local-only, never push)

`.planning/` is gitignored (`.gitignore` line 23): disk-only, never committed.
`CLAUDE.md` is gitignored (`.gitignore` line 27): disk-only, never committed.
History leaked to public origin, purged 2026-06-04 via filter-repo + repo
recreate. Both files are local-only going forward. (2026-06-11: 4 Phase 17
SUMMARY files re-leaked into main via a GSD executor commit; caught before
push, purged via `git filter-repo --path .planning --invert-paths --refs main`.
The `pre-commit` guard below was added to block this at commit time.)

To keep GSD planning files recoverable from `git clean -fdx` / accidental
deletion WITHOUT exposing them:

- Snapshots live in a local orphan branch `planning-local`, written by
  `.git/snapshot-planning.sh` through an isolated index (main's worktree,
  index, and HEAD are never touched). Run `git snapshot-planning` after GSD
  writes a phase -- no need to ask first.
- NEVER push `planning-local` or any `.planning/` content to a remote. main
  never contains `.planning`; keep it that way. The `.git/hooks/pre-push`
  guard refuses any push whose tip carries `.planning/`.
- The `.git/hooks/pre-commit` guard refuses any commit that STAGES `.planning/`
  or `CLAUDE.md` -- one layer earlier than pre-push (which only checks the
  pushed tip). It catches force-adds, `git checkout -- <path>`, and
  GSD-executor staging before they enter history. The snapshot is unaffected:
  it commits via `git commit-tree` plumbing, which does not run pre-commit
  hooks. Recreate (then `chmod 755`):
```sh
#!/bin/sh
leak=$(git diff --cached --name-only | grep -E '^\.planning/|(^|/)CLAUDE\.md$')
if [ -n "$leak" ]; then
    echo "pre-commit BLOCKED: staged paths must never enter history:" >&2
    printf '%s\n' "$leak" | sed 's/^/  /' >&2
    exit 1
fi
exit 0
```
- Restore: `git checkout planning-local -- .planning/<path>`. History:
  `git log planning-local --oneline`.
- The script and both hooks (`pre-push` + `pre-commit`) live under `.git/`
  (survive `git clean`, never pushed). A fresh clone has none of them --
  recreate them from this section.

## Business / Monetization Stance

Strategy decisions for forge monetization live in memory, not here. See the
`project_forge_monetization_stance.md` memory. One-line summary: forge is a
credibility asset, not the income plan -- sell expertise (kernel/QE/upstream)
now; do NOT change the license to monetize at ~zero adoption (a license
captures demand, it cannot create it); if preserving a future commercial
option, AGPL-3.0 + CLA is cheapest while still solo + sole copyright. Deciding
question: is forge the product you sell, or the proof that gets you hired?
