# Phase 28 v2 Cross-AI Review Consolidation

**Date:** 2026-06-24
**Round:** 2 (post-replan)
**Reviewers:** DeepSeek V4 Pro, MiMo v2.5 Pro, Kimi K2.7 Code, Gemini 3.1 Pro + 2 cold agents (plan-checker, code-reviewer)

## Overall Verdicts

| Reviewer | Verdict | Blockers | Warnings | Notes |
|----------|---------|----------|----------|-------|
| DeepSeek V4 Pro | REQUEST_CHANGES | 6 | 4 | 0 |
| MiMo v2.5 Pro | REQUEST_CHANGES | 2 | 5 | 1 |
| Kimi K2.7 Code | REQUEST_CHANGES | 7 | 5 | 3 |
| Gemini 3.1 Pro | REQUEST_CHANGES | 4 | 2 | 1 |
| Cold plan-checker | ISSUES FOUND | 3 | 5 | 0 |
| Cold code-reviewer | BLOCK | 2 (1 FP) | 5 | 2 |

## First-Round MUST-FIX Verification

All 3 models confirmed MF-1 through MF-7 are formally addressed. Two items have residual defects discovered this round:
- MF-1 error handling: addressed but validate_reviewer_json misuse creates a bypass (see MF2-1)
- SF-1 diff mode: addressed but args.mode is execution mode not diff scope -- fix is dead code (see MF2-4)

---

## MUST-FIX (consensus blockers -- fix before next replan)

### MF2-1: validate_reviewer_json contract mismatch defeats canary gate

**Consensus:** 5/5 (DS B-1, MiMo B-01, Kimi B-01, Gemini B-1, cold plan-checker BLOCKER-1)
**Location:** Plan 01 Task 2 dispatch_canary_review
**Finding:** validate_reviewer_json requires top-level {findings, code_excerpts} with non-empty code_excerpts and P0-P3 severity enum. Canary reviewer produces neither code_excerpts nor P0-P3 severities. Rubber-stamp path (findings=0, code_excerpts=[]) triggers ValueError, caught by try/except, degrades to DELEGATED instead of UNRELIABLE. The canary gate is completely bypassed.
**Fix:** Do NOT use validate_reviewer_json for canary dispatch. Create a lightweight validate_canary_findings(findings: list[dict]) -> list[dict] that checks only {file, line, severity, description} per finding, skips envelope and code_excerpts requirements. Severity: accept non-empty strings or constrain prompt to P0-P3.

### MF2-2: Verdict("UNRELIABLE") fallback crashes + Wave 1 implicit dependency

**Consensus:** 4/5 (DS B-2+B-6, Kimi B-02+W-05, Gemini B-4, cold plan-checker BLOCKER-3; MiMo acknowledges but suggests depends_on)
**Location:** Plan 01 Task 2 run_inline_canary; Plan 01 frontmatter depends_on: []
**Finding:** Verdict("UNRELIABLE") on a str enum without the member raises ValueError. Plan 01 and Plan 02 are both Wave 1 with depends_on: []. If Plan 01 runs first, all UNRELIABLE-path tests crash.
**Fix:** Add depends_on: [28-02] to Plan 01 frontmatter. This makes Plan 01 effectively Wave 1.5 (runs after Plan 02 within Wave 1). Remove the hasattr/fallback conditional entirely since Plan 02 is now guaranteed to complete first.

### MF2-3: _load_canary_config re-reads gate.yaml bypassing trust guard

**Consensus:** 6/6 (DS B-4, MiMo WARNING, Kimi B-06, Gemini W-1, both cold agents)
**Location:** Plan 03 Task 1 _load_canary_config
**Finding:** cli.py:1295-1298 explicitly says "Never re-read gate.yaml raw after this point -- a second read bypasses the trust check." Plan 03's _load_canary_config does exactly that. Additionally, Plan 03's rationale ("load_gate_config returns only the test section") is factually wrong -- load_gate_config (gate_check.py:134) returns the full data dict.
**Fix:** Modify _load_gate_backends to return tuple (cfgs, gate_data). Caller unpacks: cfgs, gate_data = _load_gate_backends(...). Extract canary config from gate_data.get("canary"). No re-read. _load_gate_backends is private, called once at cli.py:1298, safe to change return type.

### MF2-4: args.mode is execution mode (local/ci), not diff scope -- SF-1 fix is dead code

**Consensus:** 4/4 (DS B-3 strongest analysis, MiMo WARNING, Kimi B-05, Gemini W-2)
**Location:** Plan 03 Task 1 step 4 diff computation
**Finding:** cli.py:206 defines --mode with choices=["local", "ci"]. Plan 03's condition `args.mode in ("unstaged", "whole-file")` is DEAD CODE -- args.mode is never "unstaged" or "whole-file". The SF-1 fix from round 1 was built on a fundamental misunderstanding. Additionally, git diff HEAD returns staged+unstaged combined, not unstaged-only.
**Fix:** Remove the mode-based conditional entirely. For v1: use git diff HEAD (matches cli.py:1088 main review path) or git diff --cached (staged only). Document the choice. If --whole-file support is needed, check args.whole_file (the actual flag name), not args.mode.

### MF2-5: _canary_provider prompt missing "original" field

**Consensus:** 1/3 (Kimi B-03 solo) but valid -- runtime KeyError
**Location:** Plan 03 Task 1 _canary_provider closure; Plan 01 Task 1 generate_canaries
**Finding:** generate_canaries calls is_non_equivalent(result["original"], result["code"]) for each LLM-returned mutation. But _canary_provider's prompt only requests {file, line, code, description} -- no "original" field. LLM won't return it, causing KeyError, caught by try/except, template fallback every time. LLM generation is silently dead.
**Fix:** Add "original" to the prompt: {"mutations": [{"file": "...", "line": N, "original": "...", "code": "...", "description": "..."}]}. Document that "original" is the unmodified code snippet that "code" replaces.

### MF2-6: ast.parse on indented code snippets throws IndentationError -- all canary mutations silently rejected

**Consensus:** 1/5 (Gemini B-3 solo) but valid -- kills the entire generation pipeline
**Location:** Plan 01 Task 1 is_non_equivalent
**Finding:** Templates and LLM-generated mutations preserve source indentation (e.g. "    x = 1" with 4-space indent). ast.parse("    x = 1") raises IndentationError (a SyntaxError subclass). The plan catches SyntaxError and returns False (reject mutation). Result: ALL indented mutations fail non-equivalence check, get discarded, generate_canaries returns fewer than 2 verified canaries, run_inline_canary returns CanarySkip. The canary gate never fires.
**Fix:** Apply textwrap.dedent() to both original and mutated snippets before passing to ast.parse. Add a test: is_non_equivalent("    x = 1", "    x = 2") must return True.

### MF2-7: inject_canaries_into_diff uses synthetic appended hunks, violating D-28-02

**Consensus:** 2/5 (Kimi B-07, Gemini B-2; DS/MiMo/plan-checker did not flag)
**Location:** Plan 01 Task 1 template library + Task 2 inject_canaries_into_diff
**Finding:** D-28-02 says "in-place SEMANTIC mutation of the real diff, NOT a synthetic appended file." Templates use uuid-based filenames and append new hunks to the diff copy. This is structurally a synthetic appended file, the exact thing D-28-02 forbids. Gemini adds: modern code review models can easily detect structurally anomalous tail hunks.
**Fix:** Two options: (a) change injection to parse existing Python hunks in the diff and replace real changed lines with mutated versions (true in-place mutation), or (b) amend D-28-02 in CONTEXT.md to explicitly allow template fallback as a degraded-quality path. Option (a) is correct but harder; option (b) is pragmatic for v1.

---

## SHOULD-FIX (consensus warnings)

### SF2-1: epilog missing exit codes 5 (DELEGATED) and 6 (TIMEOUT)

**Consensus:** 4/5 (DS B-5, Kimi W-02, Gemini N-1, cold plan-checker BLOCKER-2; MiMo downgrades to WARNING)
**Location:** Plan 03 Task 1 step 2; cli.py:162-169 (root epilog) and 190-202 (review epilog)
**Finding:** Both epilogs currently show 0-4 only. Plan 03 adds 7 but skips 5 and 6. Users see 0,1,2,3,4,7 -- a confusing gap. DS also discovered the root parser epilog needs the same fix.
**Fix:** Update BOTH epilogs to include all 8 exit codes: 0-PASS, 1-FAIL, 2-CLI_ERROR, 3-BUSY, 4-ESCALATED, 5-DELEGATED, 6-TIMEOUT, 7-UNRELIABLE.

### SF2-2: gate.schema.json canary additionalProperties: false breaks forward compatibility

**Consensus:** 3/3 (DS W-1, MiMo NOTE, Kimi W-01)
**Location:** Plan 02 Task 2 gate.schema.json canary object
**Finding:** Every existing schema section uses additionalProperties: true. Canary section uses false, blocking future fields without schema migration.
**Fix:** Change to additionalProperties: true. The validator code is the authoritative enforcer.

### SF2-3: _canary_provider silently swallows all exceptions

**Consensus:** 2/3 (DS W-3, Kimi W-03)
**Location:** Plan 03 Task 1 _canary_provider closure
**Finding:** except Exception: return [] hides all generation failures. User gets template fallback with no signal that LLM generation failed.
**Fix:** Log warning to stderr before returning empty list: "canary generation failed: {exc}, falling back to templates".

### SF2-4: Template fallback synthetic filenames leak canary intent

**Consensus:** 1/3 (DS W-4 solo) but valid UX concern
**Location:** Plan 01 Task 1 _TEMPLATE_LIBRARY
**Finding:** uuid.uuid4().hex[:8] produces filenames like canary_a3f2b1c0.py. A careful reviewer could detect the canary pattern.
**Fix:** Use generic filenames (helpers.py, config.py, utils.py) derived from existing Python files in the diff.

---

## INFORMATIONAL (non-blocking, noted for context)

### INFO-1: Template fallback is synthetic appended file vs D-28-02 spirit

**Source:** Kimi B-07 solo
**Assessment:** D-28-02 targets the primary LLM generation path. Template fallback is documented as a degraded-quality backup. If the team considers this a violation, D-28-02 needs amendment. Not blocking execution.

### INFO-2: Real-model smoke test still not in any plan

**Source:** Kimi N-03
**Assessment:** CONTEXT.md sec 8.5 and RESEARCH.md explicitly assign this to main-session acceptance, not plan deliverables. Documented, not a plan gap.

### INFO-3: threshold_ratio runtime clamp redundant with validator

**Source:** Kimi W-04
**Assessment:** Defense-in-depth. Validator rejects 0.0 at config time; clamp catches direct API calls. Add comment in code noting the clamp is defensive.

---

## Action Items Summary

| # | Severity | Finding | Consensus | Plans Affected |
|---|----------|---------|-----------|----------------|
| MF2-1 | MUST-FIX | validate_reviewer_json defeats canary gate | 5/5 | 01 |
| MF2-2 | MUST-FIX | Verdict fallback crash + Wave 1 dependency | 4/5 | 01 |
| MF2-3 | MUST-FIX | trust guard bypass on gate.yaml re-read | 6/6 | 03 |
| MF2-4 | MUST-FIX | args.mode dead code (SF-1 fix wrong) | 4/4 | 03 |
| MF2-5 | MUST-FIX | _canary_provider prompt missing "original" | 1/4 | 03 |
| MF2-6 | MUST-FIX | ast.parse IndentationError kills all mutations | 1/5 | 01 |
| MF2-7 | MUST-FIX | Synthetic appended hunks violate D-28-02 | 2/5 | 01 |
| SF2-1 | SHOULD-FIX | epilog missing exit codes 5/6 | 4/5 | 03 |
| SF2-2 | SHOULD-FIX | schema additionalProperties: false | 3/4 | 02 |
| SF2-3 | SHOULD-FIX | _canary_provider silent exception | 2/4 | 03 |
| SF2-4 | SHOULD-FIX | Template filenames leak canary intent | 1/4 | 01 |

---

## False Positives Excluded

| Source | Claim | Why False |
|--------|-------|-----------|
| Cold code-reviewer BLOCKER-01 | M1 infrastructure doesn't exist | M1 is on branch forge/near-perfect-inline @ c515db7, needs cherry-pick per CONTEXT.md sec 9 |
| Kimi B-04 | startswith prefix bypass with sibling dir | Plan uses cwd_real + os.sep, so /tmp/foobar does NOT match /tmp/foo/ |
| MiMo NEW-BLOCKER-2 | Verdict fallback "INVALID" | MiMo acknowledges the ordering issue exists but frames it differently; other 3 sources confirm it as a real problem |

---

*Round 2 complete. 7 MUST-FIX + 4 SHOULD-FIX. Next: /gsd:plan-phase 28 --reviews to replan.*
