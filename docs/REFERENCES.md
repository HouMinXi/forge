# References

[中文版](REFERENCES.zh-CN.md)

The papers and datasets code-forge's design leans on, and what each one is
used for here. This is not a survey. A paper is listed only if some
decision in the tree traces to it, and the paragraph says which decision.

Every arXiv id below was checked with `curl -sI https://arxiv.org/abs/<id>`
on 2026-09-05 and returned HTTP 200.

## Autorubric

Delip Rao, "Autorubric: A Unifying Framework for Rubric-Based LLM
Evaluation on Non-Verifiable Tasks", arXiv [2603.00077](https://arxiv.org/abs/2603.00077), 2026.

Autorubric catalogues the ways an LLM judge fails: position bias,
stochastic inconsistency, criterion conflation, forced judgments under
uncertainty, model-dependent calibration. Its remedy for conflation is to
ask one binary question per criterion in its own call instead of one
holistic question that mixes several. It also insists that judge
reliability be measured on a calibration set rather than assumed.

code-forge's falsification step is the holistic shape the paper warns
about: one prompt carries a ten-step protocol, a three-way verdict and an
optional receipt, and the model has to understand, decide and prove in the
same breath. The v3.1 judgment line takes two things from Autorubric. First,
a falsifier calibration set drawn from the evaluation ledgers, so the gate's
own accuracy is a measured number before its prompt is changed. Second,
conditional on that number, splitting the falsification prompt into
separate yes/no questions (is the path reachable, does the symbol exist,
would the failure occur), each answered in its own call. The paper's own
caveat also applies: "some constructs resist binary categorization". A
reachability question can be binary; "is this design good" cannot, and the
split is only applied where the question allows it.

Autorubric's ensemble judging (k judges voting) is deliberately not
adopted. A majority of three penalises the hard defects the tool exists to
catch, and in normal use a review runs once.

## AACR-Bench

Lei Zhang et al., "AACR-Bench: Evaluating Automatic Code Review with
Holistic Repository-Level Context", arXiv [2601.19494](https://arxiv.org/abs/2601.19494), 2026.

A multi-language code review benchmark with full cross-file context and an
expert-verified answer key, which the authors use to compare context
strategies: no context, BM25 retrieval, embedding retrieval, and an agent
that retrieves on its own. The headline result is that context is a
precision lever (the paper's figures for one frontier model run from single
digits without context to roughly 40% with agent-retrieved context) while
naive retrieval lowers F1 below the no-context baseline, and the
high-precision agent mode pays for it with recall near 10%.

code-forge takes the framing rather than a technique: what reaches the
reviewer should be a small number of source-attributed structured facts,
not more text. That is the design rule behind `context_sources.py` in the
v3.1 evidence line, where every fact carries its source and its snapshot
commit, and a provider indexed at the wrong commit is refused. The recall
collapse in the paper's agent mode is the failure mode the evidence line is
built to avoid, which is why the reviewer is fed facts from a configured
source rather than allowed to go looking. Note that for AACR-Bench only the
abstract and a summary were read when the v3.1 plan was written; the
per-table figures are cited second-hand and should be re-read from the PDF
before being used as a decision basis on their own.

## RARe

Qianru Meng et al., "When More Retrieval Hurts: Retrieval-Augmented Code
Review Generation", arXiv [2511.05302](https://arxiv.org/abs/2511.05302), 2025.

RARe conditions a review-generation model on retrieved historical review
comments as in-context examples. The finding code-forge uses is in the
title: the best result comes from a single retrieved example, and adding a
third or fifth degrades it.

This is the second piece of evidence, alongside AACR-Bench, that context
density matters more than context volume. It is the reason the context
source design carries a hard token budget per source, and it is part of
why forge does not retrieve prior review comments at all. RARe's own
objective is comment style, measured by BLEU, which is not the same thing
as defect recall.

## CR-Bench

Kristen Pereira, Neelabh Sinha, Rajat Ghosh, Debojyoti Dutta, "CR-Bench:
Evaluating the Real-World Utility of AI Code Review Agents", arXiv
[2603.11078](https://arxiv.org/abs/2603.11078), 2026.

CR-Bench derives a code review corpus from SWE-bench: for each instance,
the lines the fix patch removes are traced back with `git blame` to the
pull request that introduced them, so the corpus is a set of real PRs each
containing a defect that was later fixed. The paper evaluates a single-shot
agent and a Reflexion-style iterative agent, and reports that pushing an
agent to find every hidden issue lowers its signal-to-noise ratio.

This paper is the reason the v3.0 milestone exists. code-forge's
three-cycle convergence loop is a Reflexion-shaped architecture, so the
paper predicts a failure mode for it, and the falsification gate is the
tool's defence against that prediction. Before v3.0 neither the prediction
nor the defence had been measured on this tool. The evaluation corpus takes
CR-Bench's construction idea (SWE-bench defects with the fix as answer key)
and its metric set (precision, recall, F1, signal-to-noise); it does not use
CR-Bench's dataset, which had not been published at the time, and it takes
SWE-bench Verified's curation as the filter instead of re-running the blame
walk. CR-Bench-Verified's 174 instances were the size reference for the
150-entry corpus.

## SWE-bench and SWE-bench Verified

Carlos E. Jimenez et al., "SWE-bench: Can Language Models Resolve
Real-World GitHub Issues?", arXiv [2310.06770](https://arxiv.org/abs/2310.06770), 2023.
The Verified subset: `princeton-nlp/SWE-bench_Verified` on the Hugging Face
Hub, 500 instances screened by human annotators for solvable, well-specified
issues.

Every entry in code-forge's evaluation corpus comes from SWE-bench Verified.
Each instance carries the repository, the base commit, the fix patch and a
human-written problem statement. `code_forge.eval.build_corpus` reverses the
fix to produce a defect diff and applies it forward to produce a clean
control; the answer key is the fix's file and line range plus the first
line of the problem statement. See [EVALUATION.md](EVALUATION.md) for the
selection rules and what the corpus can and cannot say.

## Consulted, not adopted

Three more papers were read while planning v3.1 and shaped what forge does
not do. They are listed so the omissions read as decisions.

- Yuxin Zhang et al., "LAURA: Enhancing Code Review Generation with
  Context-Enriched Retrieval-Augmented LLM", arXiv
  [2512.01356](https://arxiv.org/abs/2512.01356), 2025. Retrieval of
  review exemplars for comment generation. Not adopted: it improves
  comment style, not defect recall.
- John Naulty et al., "Bugdar: AI-Augmented Secure Code Review for GitHub
  Pull Requests", arXiv [2503.17302](https://arxiv.org/abs/2503.17302),
  2025. Reports that retrieval helps precision and recall on security
  findings by a few points while lowering precision on explanatory
  findings. Read as further evidence that retrieval effects are
  category-specific.
- Imen Jaoua et al., "Combining Large Language Models with Static
  Analyzers for Code Review Generation", arXiv
  [2502.06633](https://arxiv.org/abs/2502.06633), 2025. Static analyser
  output fed into the prompt outperforms appending it afterwards. Consistent
  with how forge already orders its lint layer ahead of the model passes.
