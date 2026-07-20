# Milestone v2.3 -- Project Summary

**Generated:** 2026-06-09
**Purpose:** Team onboarding and project review

---

## 1. Project Overview

**Forge** is a 5-step code review pipeline for AI coding assistants that enforces
minimum 9 static review passes before any commit. It treats code review as a state
machine with cycle-counter logic, hook enforcement, and anti-hallucination gates.

Three review outlets serve different trust levels:

- **Outlet A** (subprocess): SKILL.md dispatches to `code-forge review` subprocess.
  Untrusted/cheap models run here.
- **Outlet B** (inline): Trusted models run qodo + expert + adversarial merged in
  one SKILL.md section. No external process.
- **Outlet C** (subagent): Session models spawn fresh Agents per pass. Strongest
  reviewer independence guarantee.

**Core value:** No code ships without surviving three consecutive clean review
cycles from three independent perspectives. The cycle counter resets on any
finding -- quality is non-negotiable.

**v2.3 focus:** Wire cheap third-party backends (DeepSeek, MiMo, Kimi, GLM) into
the review pipeline so forge can run on budget APIs, and close anti-shirk gaps in
receipt verification and reviewer independence.

---

## 2. Architecture and Technical Decisions

### Backend System (Phase 12)

- **Decision:** gate.yaml `backends:` block as single config surface (not separate backends.yaml)
  - **Why:** Matches existing outlet_resolver.py pattern; one file to manage
  - **Phase:** 12

- **Decision:** Dict-based backend schema with name injected from YAML key (D-11)
  - **Why:** Backward-incompatible but cleaner than list-of-dicts; name is the key
  - **Phase:** 12

- **Decision:** max_tokens as int field with default 16384 (not Optional)
  - **Why:** Fits all target provider limits (Anthropic 8192 min, OpenAI uncapped)
  - **Phase:** 12

### Vertex Backend (Phase 13.1)

- **Decision:** Native Vertex AI `api` format with google-auth OAuth2 (not CLI wrapper)
  - **Why:** Direct rawPredict POST avoids gcloud CLI dependency; supports SA key + ADC
  - **Phase:** 13.1

- **Decision:** Fail-fast on zero-config: refuse implicit claude -p fallthrough (D-01)
  - **Why:** Silent billing trap -- users run forge without config and get charged on claude Pro
  - **Phase:** 13.1

- **Decision:** Outlet renamed `cli` -> `subprocess` with deprecated alias (D-08)
  - **Why:** `cli` collides with CLI module naming; `subprocess` describes what it does
  - **Phase:** 13.1

### Anti-Shirk (Phases 14-16)

- **Decision:** Outlet C routes through same StateMachine as Outlet A (not standalone)
  - **Why:** Receipt verification, falsification, and consecutive-clean logic reused; no second path
  - **Phase:** 14

- **Decision:** l1_provider 4-tuple (findings, excerpts, Usage, duration)
  - **Why:** Excerpts flow through l1_provider channel, not post_round_hook; no _round_state needed
  - **Phase:** 14

- **Decision:** Conventions resolver for reviewer independence (D-12 spec)
  - **Why:** Fresh Agent per pass needs project conventions seeded without implementation context
  - **Phase:** 15

- **Decision:** Diff-size tiering is relief, not defense (D-07)
  - **Why:** Fewer cycles for small diffs reduces corner-cutting pressure; not a quality compromise
  - **Phase:** 16

- **Decision:** INFRA source tag + falsifier skip guard for F3 fail-closed
  - **Why:** Parse-error findings are infrastructure problems, not code defects; falsifying them is meaningless
  - **Phase:** 16

---

## 3. Phases Delivered

| Phase | Name | Status | One-Liner |
|-------|------|--------|-----------|
| 12 | Backend API Wiring | Complete | Wire gate.yaml backends to cli.py; max_tokens fix; F1/F2/F3 cleanup |
| 13 | Backend Dogfood Verification | Complete | Prove mimo/deepseek/kimi backends work with zero claude tokens |
| 13.1 | Root-fix: Vertex + Ergonomics | Complete | Fail-fast zero-config; Vertex AI backend; outlet rename cli->subprocess |
| 14 | Outlet C Receipt Gap + Verify | Complete | Every review path produces verifiable receipts; hardened verify gate |
| 15 | Reviewer Independence | Complete | Fresh Agent per pass; conventions resolver; no implementation context leak |
| 16 | Relief Mechanisms | Complete | Diff-size adaptive tiering (2/3/4 cycles); F3 INFRA fail-closed fix |

---

## 4. Requirements Coverage

All 9 requirements met:

### Backend Wiring

- BACK-01: gate.yaml backends block wired to cli.py with --backend flag and FORGE_BACKEND env
- BACK-02: max_tokens raised from hardcoded 4096; explicit max_tokens for OpenAI path
- BACK-03: F1/F2/F3 cli.py cleanup (dead loop flattened, whole_file DRYed, --whole-file multi-file)
- BACK-04: Dogfood verified -- mimo/deepseek run with zero claude tokens consumed

### Anti-Shirk

- SHRK-01: Outlet C receipt gap closed -- routes through StateMachine, verifiable receipts
- SHRK-02: Reviewer-not-implementer enforced -- fresh Agent per pass, conventions-only context
- SHRK-03: Diff-size tiering -- 2 cycles (<50 lines), 3 cycles (50-199), 4 cycles (>=200)
- SHRK-04: Hardened verify ceiling -- per-hunk excerpt check, coverage-exempt patterns

### Carry-over

- DETECT-01: Pre-shipped in v2.2 (3a8f276)

---

## 5. Key Decisions Log

| ID | Decision | Phase | Rationale |
|----|----------|-------|-----------|
| D-05 | max_tokens=16384 default | 12 | Fits all provider limits |
| D-07 | F1 for-loop flattened to 4 if-checks | 12 | Clearer than abstraction over heterogeneous flags |
| D-08 | F2 whole_file merged into _resolve_whole_file_specs | 12 | Returns 3-tuple, eliminates duplicate pattern |
| D-09 | F3 --whole-file expanded to nargs='+' | 12 | Multi-file review support |
| D-11 | Dict-based backend schema | 12 | Name from YAML key, cleaner than list-of-dicts |
| D-01 | Fail-fast zero-config | 13.1 | Prevent silent billing trap |
| D-05 | Probe bypass for cli backends | 13.1 | Vertex needs no reachability probe |
| D-07 | code-forge init subcommand | 13.1 | Guided gate.yaml creation with CN API templates |
| D-08 | Outlet renamed cli->subprocess | 13.1 | Resolves naming collision with CLI module |
| D-11 | Vertex native api format | 13.1 | rawPredict POST, google-auth OAuth2, no gcloud dep |
| -- | StateMachine reuse for Outlet C | 14 | One verification path, not two |
| -- | l1_provider 4-tuple | 14 | Excerpts through data channel, not hook |
| D-12 | Conventions resolver (cross-repo) | 15 | 4-source resolver, multi-lang extraction, sha256 cache |
| D-07 | Tiering = relief not defense | 16 | Framing prevents misinterpretation as quality reduction |
| -- | INFRA source tag | 16 | Parse errors skip falsifier; infrastructure, not code |

---

## 6. Tech Debt and Deferred Items

### Deferred to v2.4+

- **SEC-01**: Untrusted gate.yaml credential flow (pre-existing on main)
- **REVIEW-TRUST-01 through REVIEW-SYSTEM-01**: 5 requirements staged for trust-boundary hardening
- **v2.4 eval/benchmark**: Score backends on labeled-bug corpus to retire multi-model voting

### Future Requirements (parked)

- **CANARY-01**: Reviewer Canary (design spec ready from v2.2)
- **PLAT-01**: Windows IDE support (subprocess lifecycle, signal handler portability)
- **ENG-01**: l1_provider parallelization (3 sequential passes)
- **ENG-02**: R5 test layering / threshold-triggered real-dependency regression
- **MULTI-01**: Cross-repo joint scanning

### Hard Non-Goals

- Modifying standalone pass skills (daily kernel review entry point)
- Agentic review depth (incompatible with fixed-pipeline thesis)
- kimi-cli native (YAGNI: all reachable via api format)
- Diff-driven model routing (D-26)

### Lessons Learned (v2.3)

- Multi-model voting converges on mechanical text but diverges on semantic intent -- human review is the gate for strategic correctness
- .planning gitignore is necessary but not sufficient: worktree commits bypass gitignore scope
- STATE.md is a derived artifact: should be reconstructed from ROADMAP.md + git, not maintained independently
- Cross-model plan review exit: R4 panel + human gatekeeper, not unbounded voting

---

## 7. Getting Started

### Run the project

```bash
pip install -e ".[dev]"
code-forge init              # creates .code-forge/gate.yaml with CN API templates
code-forge review             # review current git diff (auto-detects outlet)
code-forge review --backend mimo  # use mimo API backend
code-forge verify             # verify review receipts
```

### Key directories

```
src/code_forge/           # main package (~30 modules, ~12K lines)
  cli.py                  # CLI entry point, outlet dispatch, subcommands
  machine.py              # StateMachine: cycle-counter, convergence logic
  backend.py              # BackendConfig, gate.yaml loader, probe, Vertex
  llm_invoke.py           # LLM API calls (Anthropic, OpenAI, Vertex)
  outlet_c.py             # Outlet C (subagent) orchestrator
  conventions.py          # AST-based naming extractor
  conventions_resolver.py # Cross-repo convention source resolver + cache
  diff.py                 # Diff analysis, line counting, tiering
  detect.py               # Toolchain auto-detection
  verify.py               # Receipt verification (hardened checks 1-6)
  falsify.py / falsify_real.py  # Finding falsification engine
tests/                    # 1,190 tests (~28K lines)
skills/code-forge/        # SKILL.md (Claude Code skill definition)
```

### Tests

```bash
python -m pytest tests/                           # full suite (~1190 tests)
python -m pytest tests/ -m "not real_api"         # skip real API tests
python -m pytest tests/ -k "test_backend"         # backend tests only
```

### Where to look first

1. **cli.py** -- main entry point, outlet dispatch logic (~line 690)
2. **machine.py** -- StateMachine that enforces consecutive-clean convergence
3. **backend.py** -- how gate.yaml backends are loaded and probed
4. **SKILL.md** -- how Claude Code skills invoke forge review

---

## Stats

- **Timeline:** 2026-06-04 to 2026-06-09 (6 days)
- **Phases:** 6/6 complete (100%)
- **Plans:** 19/19 complete
- **Requirements:** 9/9 met
- **Commits:** 28 (v2.2..v2.3 range)
- **Files changed:** 42 (+6,474 / -383)
- **Contributors:** Minxi Hou
- **Test suite:** 1,190 tests passing

---

*Summary generated from v2.3 milestone artifacts (ROADMAP, REQUIREMENTS, SUMMARY, VERIFICATION, RETROSPECTIVE).*
