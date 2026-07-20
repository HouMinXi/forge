# Phase 18: Taint + Provenance - Context

**Gathered:** 2026-06-10
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 18 delivers REVIEW-TRUST-01: three sub-capabilities wired into the
review pipeline -- one blocking L0 finding (danger-score), one advisory axis
(taint), one adversarial-pass prompt injection (provenance).

(a) **Config danger-score**: when the diff under review adds dangerous fields
to gate.yaml / .code-forge/ config files, emit a TRUST finding and HOLD.

(b) **Taint advisory axis**: semgrep intraprocedural taint analysis on changed
source files detects config/env/file sources flowing into
subprocess/shell/urlopen/network sinks (open write-mode sink deferred per
D-12 self-loop constraint). Taint findings are advisory (emitted,
displayed, never HOLD, never reset the cycle counter). Semgrep absent ->
loud infra note on stderr + state.infra_errors, never HOLD, never silent.

(c) **Adversarial provenance**: hard-wired question in the adversarial review
pass asking "for each external input, who controls the source and what is the
worst attacker value?" -- always runs, no diff content check.

Danger-score produces L0 blocking findings (directly CONFIRMED, no LLM
falsification). Taint produces AdvisoryFindings via the Phase 17 advisory
AxisRunner path (emitted, displayed, never HOLD). Provenance is a prompt
injection into the adversarial pass (output flows through the normal LLM
pass, not a deterministic finding).
Non-git mode: danger-score loud-skips (needs diff new-lines per D-03);
taint runs (uses source_files list, no git dependency per D-09);
provenance runs unconditionally (D-08).

</domain>

<decisions>
## Implementation Decisions

### Danger-score (sub-capability a)

- **D-01:** Scan scope: only gate.yaml and files inside `.code-forge/`. No
  broader config file scanning to keep false-positive rate low.

- **D-02:** Finding type and blocking: diff-introduced dangerous fields produce
  a TRUST StateFinding (source=L0, directly CONFIRMED). Finding causes HOLD --
  consistent with REVIEW-TRUST-01 "CAN BLOCK" and the founding principle (this
  IS the committed diff's own code). No LLM falsification step.

- **D-03:** Diff scope: scan only new lines (lines starting with `+` in the
  diff). Pre-existing dangerous fields are not re-reported. Aligns with the
  R1 baseline principle (NEW vs baseline delta only).

- **D-04:** Trust-state independence: danger-score runs regardless of whether
  the repo is trusted via `code-forge trust`. Orthogonal checks.

- **D-17:** Granularity: one TRUST finding per dangerous field, fingerprint =
  (file, field_name, line). Precise cycle dirty-checking; user sees exactly
  which fields triggered.

- **D-18:** No suppression mechanism in Phase 18. Danger-score findings are
  factual (a dangerous field in the diff is a fact) and unsuppressible. Taint
  findings are advisory and need no suppression -- they never block. Noise
  management for advisory taint is deferred until real-usage feedback.
  Rationale: a diff-author-controlled inline annotation that could silence a
  CAN-BLOCK gate is an attacker-controllable false-green path; deletion
  removes the attack surface entirely rather than adding countermeasures.

### Taint advisory axis (sub-capability b)

- **D-05:** Semgrep absent behavior: the taint advisory axis cannot run.
  Emit the D-06 stderr message and record infra_error in state.infra_errors.
  Never HOLD, never silent. Rationale: satisfies ROADMAP SC#3 ("loud-fails,
  logs a clear warning, never silently skips"); removes the install-or-stall
  adoption blocker for an alpha package.

- **D-06:** Semgrep absent stderr message:
  `"Taint gate requires semgrep. Install: pip install semgrep"`

- **D-09:** Taint advisory axis input: `resolved_review.source_files` file list from
  the already-resolved diff. No `git diff` invocation; no git hard-dependency
  introduced in Phase 18. Forward-guard honored: semgrep takes file list, not
  git call; non-git trees supported via same path.

- **D-10:** Rule dependency: semgrep CE built-in rules, zero user config.

- **D-11:** Rule level: intraprocedural (single-function) taint. semgrep CE
  free tier. Cross-file/cross-function requires semgrep Pro -- out of scope.

- **D-12:** Source / sink pairing:
  - Sources: `os.environ[...]`, `open(<config>)`, `yaml.safe_load(...)`,
    `yaml.load(...)`, `json.load(...)`
  - Sinks (Phase 18): `subprocess.run(...)`, `subprocess.Popen(...)`,
    `os.system(...)`, `os.popen(...)`, `urllib.request.urlopen(...)`,
    `requests.get/post(...)`
  - Sink DEFERRED: `open(...)` (write-mode) -- deferred because open() is
    also a source; a single taint rule with open as both source and sink
    creates a self-loop producing false positives. A separate rule
    `forge-taint-config-to-file-write` targeting `open($PATH, "w")` /
    `open($PATH, mode="w")` will be added in a future phase if real-usage
    feedback shows demand. ROADMAP SC#2 interpreted as: all listed sources
    flow to subprocess/shell/urlopen/network sinks; open-as-sink deferred
    per self-loop constraint.

- **D-13:** (numbering gap -- ID was never assigned during discuss; no
  decision was withdrawn or merged. Total decisions: 18.)

- **D-19:** Taint rule file: `src/code_forge/rules/forge-taint.yaml`
  bundled with the package. Users do not configure a separate file.

### Adversarial provenance (sub-capability c)

- **D-07:** Integration: question hard-wired into the adversarial pass prompt
  (pass3-adversarial.md skill). Not a separate AxisRunner advisory axis.

- **D-08:** Trigger: every review run, unconditionally. No diff content gate.

### Pipeline architecture

- **D-15:** Danger-score: L0 deterministic tool. Scans diff new-lines directly
  for DANGEROUS_FIELDS via regex, constructs StateFinding(source="L0",
  disposition=CONFIRMED) directly -- no SARIF round-trip (constructing synthetic
  SARIF from diff text only to immediately parse it back adds no information;
  the SARIF path applies to the taint sub-capability which actually invokes
  semgrep and receives real SARIF output). source=L0, directly CONFIRMED, can HOLD.
  Taint: semgrep --sarif output may reuse the same SARIF parser internally,
  but results materialize as AdvisoryFinding (no fingerprint-driven cycle
  dirtying, no fixpoint participation). Surfaced in advisory-findings.json
  + display, exactly like Phase 17 advisory axes.

- **D-16:** Non-git mode degradation: danger-score loud-skips (needs diff
  new-lines; non-git has no baseline). infra_error recorded: "danger-score
  requires a diff -- skipping in non-git mode". Taint advisory axis runs in
  non-git mode (uses resolved_review.source_files per D-09, no git dependency).
  Provenance question runs unconditionally per D-08. Verdict unaffected.
  Forward-guard compliant.

### Regression

- **D-14:** SC#5 regression fixture: eval corpus `gate-yaml-rce` entry
  (already in `tests/eval/corpus/corpus.yaml`, axis_tags=[TRUST, SEC]).
  Acceptance: after Phase 18, gate-yaml-rce shows CAUGHT in
  `code-forge eval --corpus ... --backend <real> --runs 1`.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` section REVIEW-TRUST-01 -- three sub-capabilities,
  success criteria 1-5, CAN BLOCK designation

### Phase 17 foundations (Phase 18 extends these)
- `src/code_forge/trust.py` -- `find_dangerous_fields`, `DANGEROUS_FIELDS`
  frozenset; reuse directly in danger-score
- `.planning/phases/17-trust-gate-eval-scaffold/17-CONTEXT.md` section D-14..D-19
  -- AxisRunner Protocol; advisory vs blocking type separation. Phase 18
  uses BOTH: L0 blocking for danger-score AND the advisory AxisRunner path
  for taint. AxisRunner Protocol reference is now load-bearing.
- `tests/eval/corpus/corpus.yaml` -- gate-yaml-rce regression entry (D-14)

### Semgrep integration
- `src/code_forge/parsers/semgrep.py` -- existing SARIF parser; reuse unchanged
- `src/code_forge/parsers/_sarif.py` -- underlying SARIF parser

### Review pipeline
- `src/code_forge/machine.py` section `_run_l0_phase` -- insertion point for
  danger-score (L0 blocking tool). Taint is wired via the Phase 17 advisory
  AxisRunner path (see D-15), NOT as an L0 tool.
- `src/code_forge/skills/code-forge/SKILL.md` section pass3-adversarial --
  adversarial pass prompt; provenance question (D-07) appended here

### Forward guards
- North star: forge end-state = multi-spacetime tracing gatekeeper.
  Phase 18 introduces NO git hard-dependency. semgrep takes file list
  (resolved_review.source_files), not git invocation. Non-git behavior:
  danger-score loud-skips (needs diff), taint advisory axis runs, provenance runs
  unconditionally. All three behaviors declared in D-16 and must be tested.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `trust.py::find_dangerous_fields(gate_data)` -- detect 7 dangerous fields;
  Phase 18 applies to diff new lines, not full gate_data
- `trust.py::DANGEROUS_FIELDS` frozenset -- reuse, do not redefine
- `parsers/semgrep.py::parse_semgrep(output, exit_code)` -- reuse for taint
- `parsers/_sarif.py::_parse_sarif(...)` -- shared SARIF backend

### Established Patterns
- L0 tool pattern: tool runs, SARIF output parsed, `source="L0"`, directly
  CONFIRMED. Phase 18 danger-score is L0/CONFIRMED but constructs StateFinding
  directly from diff text -- no SARIF round-trip (D-15). Taint does NOT -- it
  materializes as AdvisoryFinding via the Phase 17 advisory AxisRunner path (D-15).
- `_run_l0_phase()` in machine.py: registration point for new L0 tools
- `state.infra_errors`: where loud-skip records absence reason

### Integration Points
- `machine.py::_run_l0_phase()` -- danger-score (L0 blocking) added here.
  Taint advisory axis registered via the Phase 17 AxisRunner path (D-15), not as
  an L0 tool.
- `src/code_forge/skills/code-forge/passes/pass3-adversarial.md` --
  provenance question appended to this pass's prompt
- `src/code_forge/rules/` -- new directory; `forge-taint.yaml` lives here

</code_context>

<specifics>
## Specific Ideas

- Provenance question wording (D-07/D-08): "For each external input in the
  changed code: who controls the source of this data, and what is the worst
  value a malicious caller could inject?"

- Taint finding ceiling note: finding description must state "intraprocedural
  only -- cross-function flows not detected" so users understand the limit.

- Danger-score fingerprint: (file_path, field_name, line_number) -- one
  finding per field even when same field appears in multiple backends.

- Taint advisory finding text should carry an explicit "advisory -- not
  verified" marker, feeding the Phase 20 verdict-honesty declaration pattern
  (green verdicts declare what they did not verify).

</specifics>

<gray_areas>
## Gray Areas (for the planner to resolve)

- **Legitimate-use waiver**: when a user intentionally adds a dangerous field
  to their own trusted repo's config, what is the sanctioned path past the
  unsuppressible danger-score HOLD? Candidates: `code-forge trust` doubles
  as the waiver; or a one-time interactive disposition; or accept re-HOLD per
  change. Must be answered against D-04 (danger-score is trust-state
  independent) -- the waiver mechanism must not silently contradict that.

</gray_areas>

<deferred>
## Deferred Ideas

- Cross-file / cross-function taint: requires semgrep Pro (paid). Deferred
  beyond v2.4.
- Per-project taint rule customization via gate.yaml: YAGNI for Phase 18.
- Extending danger-score to arbitrary .yaml/.json/.toml: deferred.

</deferred>

---

*Phase: 18-Taint + Provenance*
*Context gathered: 2026-06-10*
