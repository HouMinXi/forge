# Phase 18: Taint + Provenance - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in 18-CONTEXT.md.

**Date:** 2026-06-10
**Phase:** 18-taint-provenance
**Areas discussed:** Danger-score blocking, Semgrep absent behavior, Provenance
form, Taint input, Taint rule scope, Escape hatch, Regression fixture, Non-git
degradation, Pipeline position, Finding granularity, noqa scope, Rule file location

---

## Danger-score

Scan scope: only gate.yaml / .code-forge/ (not arbitrary config files).
Blocking: HOLD on diff-introduced dangerous fields (CAN BLOCK, L0 CONFIRMED).
Diff scope: new lines only (+ prefix), not pre-existing fields.
Trust independence: runs regardless of repo trust state.

---

## Semgrep absent behavior

HOLD current cycle. Stderr: "Taint gate requires semgrep. Install: pip install semgrep"

---

## Provenance question form

Injected into adversarial pass prompt (always on, every review).
Not a separate AxisRunner advisory axis.

---

## Taint gate input + rule

Input: resolved_review.source_files (no git invocation -- forward-guard honored).
Rules: semgrep CE intraprocedural, bundled at src/code_forge/rules/forge-taint.yaml.
Sources: os.environ / open(config) / yaml.safe_load / json.load.
Sinks: subprocess.run / Popen / os.system / urlopen / requests.get/post.

---

## Escape hatch

# noqa: forge-trust suppresses both danger-score and taint for that line.

---

## Regression fixture

eval corpus gate-yaml-rce entry (already in corpus.yaml, axis_tags=[TRUST, SEC]).

---

## Pipeline position and granularity

L0 deterministic findings (CONFIRMED, no LLM falsification).
One finding per dangerous field / taint flow.
Non-git mode: loud-skip with infra_error, verdict unaffected.

---

## Deferred Ideas

- Cross-file / cross-function taint (semgrep Pro): deferred beyond v2.4
- Per-project taint rule customization: YAGNI Phase 18
- Danger-score for arbitrary config types: deferred
