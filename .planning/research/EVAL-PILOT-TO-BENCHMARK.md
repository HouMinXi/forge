# Eval: Pilot -> Benchmark Upgrade Checklist

Status: grounded from source, 2026-07-01. Precondition for any forge empirical
paper or tech report. This document locates the pilot's method flaws (file:line)
and lists the ordered work to turn a 2-scenario flawed pilot into a defensible
benchmark.

## Two eval systems exist (do not conflate)

- MANUAL PILOT: eval/results/*.md + eval/buggy/ + eval/ground_truth/ +
  eval/prompts/. A hand-scored multi-model comparison (S1, S5) from
  surflare-watchdog. This is the flawed artifact analyzed below.
- AUTOMATED HARNESS: src/code_forge/eval/{corpus,runner,scorer}.py. A clean,
  reproducible false-green-rate scorer (compute_summary at scorer.py:121;
  caught/missed/correct_pass/false_positive quadrants). The pilot did NOT route
  through this. The upgrade largely = feed the corpus through this harness
  instead of hand-scoring.

forge is NOT starting from zero: it already has a ground-truth method
(injected bug + fix diff + scoring rubric) and an automated scorer. What is
missing is scale, procedural fairness, and statistical rigor.

## The three method flaws (located)

### Flaw 1 -- Corpus: N=2, single codebase, single language, single run
Evidence:
- Only eval/buggy/s1_watchdog.sh and s5_watchdog.sh. Both shell, both from
  surflare-watchdog. One absence-type bug (S1), one presence-type bug (S5).
- final_scorecard.md:24 "conditional on N=1 absence bug + N=1 presence bug";
  :73 "Sample size N=1 ... cannot generalize"; :87-91 "Do NOT route ... until
  N>=3 absence-type + N>=3 presence-type".
- Single run per (model, scenario) cell; no seeds, no variance. scorer.py
  supports multi-run majority (:151, :162) but the pilot used runs=1.
Why it blocks publication: no statistics possible; no generalization beyond
shell/one project; LLM stochasticity unmeasured.

### Flaw 2 -- Prompt / procedure asymmetry across models (the "unfair prompt")
Evidence:
- Standard prompt eval/prompts/s5_input.md (2109 lines, generic 5-point focus)
  = what ds/kimi/mimo received.
- Focused variant eval/prompts_s1_only/s5_focused.md (220 lines) telegraphs the
  target bug in the prompt itself ("server_ips could become empty, blocking ALL
  traffic"; "killswitch after a watchdog restart") and supplies only an excerpt,
  not the full file.
- final_scorecard.md:4 gm was a "manual agy run"; :8-11 gm S5 attempt-1 had
  prior-context contamination (reused its own S1 review), was deleted and re-run;
  :12-13 attempt-2 used "new conversation + explicit anti-bias instructions";
  :55-58 explicit caveat that the nudge suppressed gm's non-target findings
  (2 vs 10-20 for others). gm was manually re-run and anti-bias-nudged; the other
  models were not held to the same procedure.
Why it blocks publication: cross-model / cross-tool scores are not comparable
when the prompt, scope (full file vs excerpt), and re-run policy differ per
subject. Any headline "model/tool X wins" is confounded.

### Flaw 3 -- Manual, subjective, single-rater scoring; automated scorer bypassed
Evidence:
- Scoring hand-written in final_scorecard.md: severity judgment "HIT but severity
  P0->P2 downgrade" (:16-17, :42, :51) and an undefined middle grade "PARTIAL"
  (:40) with no rubric.
- No inter-rater reliability; single scorer.
- The operationalized scorer exists (scorer.py:121 compute_summary;
  :29 advisory_caught keyword match) but the pilot did not use it -- results are
  hand-authored .md.
Why it blocks publication: the central construct ("honest green" / HIT / severity)
has no reproducible, rater-independent definition; another researcher cannot
reproduce the scores.

## Upgrade checklist (ordered by leverage)

Each build item owes forge's standard scope-challenge before starting (does the
benchmark need to exist; who consumes it; cost of do-nothing). For a paper the
answer is yes; the checklist assumes that decision is taken.

1. GROW THE CORPUS (fixes Flaw 1). Target N>=3 absence-type + N>=3 presence-type
   per language, across >=2 languages and >=2 projects (not just surflare shell).
   Source from real git history fix commits (the fix diff IS the ground truth).
   Encode each as a corpus.py CorpusEntry (diff_file + expected_verdict +
   expected_advisory) so it runs through the automated harness. Concrete first
   step named in final_scorecard.md:89-91: 4 more absence + 4 more presence from
   surflare git history -- then diversify beyond shell.

2. FIX THE PROTOCOL (fixes Flaw 2). One prompt template per scenario, identical
   scope (full file OR consistent excerpt) for every subject. No per-model
   anti-bias nudges. No bug-telegraphing focused prompts in the scored set
   (retire prompts_s1_only/ from scoring; keep only as an ablation if studying
   prompt sensitivity). Same re-run policy for all subjects (either allow N runs
   for everyone or none). Pin model versions; record them.

3. AUTOMATE + OPERATIONALIZE SCORING (fixes Flaw 3). Route all scoring through
   scorer.py compute_summary. Replace hand-judged HIT/PARTIAL/severity with a
   written rubric: define "caught" (verdict-match or keyword-match already in
   scorer.py), define severity mapping, drop or formally define PARTIAL. For the
   honest-green claim specifically, report: false-green rate (missed / expected-
   HOLD) and calibration of "not verified" declarations. Add a second rater on a
   sample and report agreement.

4. ADD STATISTICAL RIGOR. runs>=5 per cell (scorer.py majority threshold already
   supports it); report variance and a significance test on the headline claim.

5. ADD BASELINES (the right axis). The pilot compared models INSIDE forge. A
   paper needs forge-the-system vs alternative SYSTEMS on the same corpus: raw
   agent review (Claude Code + skill), OCR, semgrep/SonarQube alone, and human.

6. ABLATIONS. Turn off each layer (L0 / L2 / R1 / R3 / 3-cycle / multi-model /
   falsify_real) and measure the delta, to show which layers carry the result.

7. REPRODUCIBILITY ARTIFACT. Release corpus + harness + pinned model versions +
   seeds. This is also forge's honest-green ethos applied to its own evaluation.

## What already exists (do NOT rebuild)

- Ground-truth method: eval/buggy/ (injected bug) + eval/ground_truth/ (fix diff
  + scoring rubric).
- Automated false-green scorer: src/code_forge/eval/scorer.py (compute_summary).
- Corpus loader: src/code_forge/eval/corpus.py (YAML manifest -> CorpusEntry).
- Runner: src/code_forge/eval/runner.py.
- Finding-verification protocol: src/code_forge/falsify_real.py (10-step anti-
  hallucination). This is a candidate paper contribution in its own right.

## Strategic note

Highest-leverage next step is items 1-3 (corpus + protocol + automated scoring):
they are the shared prefix of both a tech report and a paper, and they convert
the self-admitted "NOT a conclusion" pilot into first real evidence. Items 4-7
are the difference between a credible arXiv tech report and a venue submission.
Recommended sequencing: 1-3 -> tech report / arXiv -> if it lands, add 4-7 and
submit. A tech report captures ~80% of the credibility with no peer-review
gatekeeping and is the paper's Section 4-5 verbatim.
