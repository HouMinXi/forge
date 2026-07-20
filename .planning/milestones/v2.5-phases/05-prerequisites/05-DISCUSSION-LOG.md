# Phase 5: Prerequisites - Discussion Log

**Date:** 2026-05-30
**Mode:** discuss (default)
**Areas discussed:** 4/4 selected

## Area 1: Auto-detect Strategy (CLI-03/04)

### Q1: Detection scope
- **Options:** Tool-only | Tool + language inference | Tool + config-file aware
- **Selected:** Tool + config-file aware
- **Rationale:** Read pyproject.toml/Cargo.toml/package.json to infer language and declared tools, then verify via shutil.which()

### Q2: Detection failure behavior
- **Options:** Empty template + warning | Error stop | L0 degraded run
- **Selected:** Empty template + warning
- **Revised to:** Error stop (after user review identified silent-PASS trap: load_registry returns {} for empty tools.yaml without error, creating false green on L0)

## Area 2: Auth Probe Design (CLI-05)

### Q1: Probe timing
- **Options:** Every review | First + cache | Only at outlet selection
- **Selected:** Every review (with caching added post-review to avoid 12s+billing repeat)

### Q2: No auth + no explicit Outlet B
- **Options:** Error stop | Degrade to B | Degrade + warning
- **Selected:** Error stop (per CLI-02 FAIL CLOSED)

### Post-review additions (user feedback)
- D-07: Timeout configurable via FORGE_AUTH_TIMEOUT (dead value = false auth failure)
- D-09: Outlet B path never probes (explicit inline short-circuits)
- D-11: Opt-in real API test (@pytest.mark.real_api)

## Area 3: Outlet Selection Logic (BOTH-04)

### Q1: Override form
- **Options:** Env var only | Env var + CLI flag | Env var + gate.yaml
- **Selected:** Env var + gate.yaml

### Q2: SKILL.md handoff
- **Options:** SKILL.md self-runs decision tree | Python subcommand outputs result
- **Selected:** Python subcommand (resolve-outlet) -- user confirmed after initial clarification about what "outlet" means

## Area 4: Integration & Test Strategy

- Covered in user's structured review of the full design
- Three must-fix items identified from code evidence (D-02, D-03, D-09)
- Four minor fixes folded into decisions (D-04, D-05, D-07, D-11)

## Deferred Ideas

- Reviewer Canary implementation (v2.3+)
- Outlet enforcement (Phase 7)
- Multi-language detect beyond Python
- FORGE_LLM_MODEL docs (Phase 8)

---

*Discussion completed: 2026-05-30*
