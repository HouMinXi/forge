# Charter: review telemetry -- ledger attribution enrichment (SCHEDULED-PENDING)

Slot: hard AFTER the 38.1-5/6 fix package merges (factories.py overlap);
parallel-safe with the W3 slow-drip mini-phase (llm_invoke.py only).
Ratified scope: user picked "A: minimal recording phase" 2026-07-08.
Origin: user request 2026-07-08 -- per review, record which MODEL found
which defects in which LANGUAGES with which auxiliary context, and what
the precision / false-positive rates are.

## Scope-challenge

(a) Does this need to exist?
    Mostly it ALREADY does -- Phase 43 LEDGER (merged 8f7cdd6) records
    per-finding fingerprint/file/line/axis_claim/pass_provenance/
    terminal_state (FIXED / DISPROVED / DUPLICATE / ESCAPED) + base/head
    SHAs. What is missing is MODEL attribution (pass_provenance names the
    pass role, not the backend; StateFinding carries no backend field, so
    the machine.py:1109 writer cannot know) and per-review context flags.
    This charter is an enrichment, NOT a new telemetry system. Building a
    second record would be duplicate infrastructure (banned).

(b) 3 real consumers:
    1. gate.yaml backend tuning -- which model earns its seat per
       language/axis (the user's stated want).
    2. H2 forge-bot canary floor (fleet obligation): per-model precision
       baseline, FROZEN before the first qwen dual-run result exists
       (pre-registration law). This telemetry is the canary instrument.
    3. Phase 44 EVAL-ON-DUTY + Phase 47 registry promotion/demotion --
       the certified learning lane consumes per-finding outcome data;
       model attribution makes its fitness signal per-backend.
    (+) rigor for future model evals -- the 2026-07 pilot (memory
       project_model_eval_pilot) had method flaws this data side fixes.

(c) Cost of do-nothing:
    IRREVERSIBLE SAMPLE LOSS. Every review that runs without the backend
    field is an attribution sample that can never be reconstructed.
    Reports/aggregation can be added any time; recording cannot be
    retroactive. This is why the minimal phase ships fields first.

## Ratified scope (option A -- minimal recording)

1. LedgerRow schema v1.1, ADDITIVE:
   - backend: str ("" for human-entered mark rows)
   - ctx_graph_triage: bool, ctx_contract: bool, ctx_whole_file: bool,
     ctx_canary: bool  (per-review pipeline-internal aux inputs)
   REQUIRED design point: iter_rows currently hard-KeyErrors unknown
   schema -> v1 rows would be SKIPPED as invalid. New fields MUST be
   read tolerantly (dict.get with defaults) so old rows stay readable.
   Synthetic example row (v1.1):
     {"fingerprint":"...","repo_root":"...","base_sha":"...",
      "head_sha":"...","file":"src/x.py","line":42,"axis_claim":"...",
      "pass_provenance":"expert","terminal_state":"FIXED",
      "evidence_class":"...","ts":"2026-07-08T00:00:00Z",
      "backend":"mimo-pro","ctx_graph_triage":true,
      "ctx_contract":false,"ctx_whole_file":false,"ctx_canary":false}
2. Threading: factories.py bname (in scope at :300) -> StateFinding
   gains OPTIONAL backend field (additive, default None) -> machine.py
   :1109 ledger writer fills LedgerRow.backend. cli.py:1299 mark path
   writes backend="".
3. Absorbs A3 (LOW, from the surflare acceptance): the "L1 invoke
   failed" INFRA descriptions at factories.py:357/:382 gain bname --
   same code region, same threading, one commit.
4. Language: DERIVED from file extension at analysis time; NOT stored
   (no redundant fields).
5. Report: documented jq recipe only, e.g.
     jq -r '[.backend, .terminal_state] | @tsv' \
       .code-forge/ledger.jsonl | sort | uniq -c
   A `code-forge ledger stats` subcommand is DEFERRED until Phase 44
   needs programmatic consumption (option B territory, not bought).

## Honesty boundaries

- Precision ladder -- every reported rate names its rung:
  Rung 1 (automatic): FIXED/(FIXED+DISPROVED) from the falsification
    machine. Within-pipeline proxy; falsifier errors count as truth.
  Rung 2 (manual): `ledger mark` human adjudication (ESCAPED/DUPLICATE).
  Rung 3 (future, Phase 45): automated escape intake.
  Any GATE use of these numbers (H2 canary floor) triggers the
  pre-registration law: freeze floors before results exist.
- MCP visibility: forge records only ITS OWN pipeline's aux inputs
  (graph triage, contract, whole-file, canary). Client-side MCP usage
  (what the calling agent consulted) is invisible to forge and is NOT
  claimed. The user's "which MCPs helped" is answerable only for
  forge-internal context; stated here so the report never overclaims.

## Non-overlap with the certified v2.9 lane (checked)

44 = ledger-driven case generation; 51 = basis sub-fields
(falsification_survived, convergence_rounds); 52 = env manifest tiers.
None of their certified specs claim the LedgerRow schema fields above;
this enrichment feeds them without touching their scope. AMENDMENT 1
rev 2 is not reopened.

## Process

Logic-bearing (schema + threading + writer) -> worktree Phase 0,
3-cycle review, bug-inject proof for the tolerant-read path (feed a v1
row + a v1.1 row; old row must parse, not skip). Plan external review
optional per the R2 proportionality precedent -- user decides at
dispatch. Two consumers of iter_rows exist today (cli.py ledger
subcommand); grep all iter_rows callers at plan time.

## Not doing (explicit)

- No stats/dashboard subcommand (deferred to demand).
- No second telemetry store; the ledger is the one record.
- No client-side MCP attribution (unobservable).
- No language field in the schema (derivable).
- No true-precision adjudication workflow (Phase 45 territory).
