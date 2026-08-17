# Eval corpus findings-extension review -- R1 dispositions, 2026-08-16

Branch fix/eval-corpus-findings (worktree .worktrees/eval-corpus-findings,
base dec413e). Diff: corpus.py + scorer.py + runner.py + new test file.
Review backend: deepseek-direct (v4-pro). Log: /tmp/forge_evalcorpus_r1.log
(copy in .planning/reviews/eval-corpus-r1.log).

## R1 (15 findings, all disposed)

All 15 were legitimate robustness findings on the eval harness. 14 of
them collapse into three substance groups plus one already-fixed item:

Group 1 -- _read_confirmed_findings trusts the JSON shape (qodo 420,
expert 420, adversarial 420): ACCEPTED. Now validates isinstance(data,
dict), findings list, per-item dict; normalizes line_range (two-int
list, bool excluded) and coerces description to str. Injection-verified.

Group 2 -- _parse_expected_findings trusts the manifest shape (qodo
126, expert 126, adversarial 126): ACCEPTED. raw must be a list of
mappings; every failure is a ValueError naming the entry.
Injection-verified.

Group 3 -- line_range validation gaps (qodo 144, expert 150,
adversarial 150, adversarial 144, plus the bool-subclass variants):
ACCEPTED. bool excluded from int checks; range must satisfy
1 <= start <= end. Injection-verified.

Group 4 -- finding_hit unpacks actual_range without validation (qodo
69, expert 69, adversarial 69): ACCEPTED. Malformed actual ranges
fall through to the description rule; non-string descriptions are
coerced. Injection-verified (first attempt was invisible -- the
hardening tests used range-less expectations; re-injected with a
ranged-expected test pair).

EvalSummary breaking change (expert 133): ALREADY FIXED before the
findings arrived -- the full suite had caught the same defect (three
direct-constructor failures in test_cli_eval.py) and the fields were
moved after `results` with 0 defaults. The review and the suite
converged independently on the same issue.

write_json_report omits expected_findings (adversarial 358,
UNCERTAIN): ACCEPTED. The JSON report now serializes the answer key
per entry so results can be audited against the bank.
Injection-verified.

No findings were dismissed: the harness is a measurement instrument,
and every finding above was a real way for hand-edited manifests or
arbitrary run state to crash the eval instead of degrading loudly.

## R2 (9 findings, all disposed)

All nine were substantive refinements of the R1 hardening; none
dismissed.

44. [qodo 71, expert 71, adversarial 71, expert 432, adversarial 432]
    "actual-side line_range lacks semantic validation (0-based,
    inverted) -> false hits." ACCEPTED -- one shared valid_line_range
    helper in scorer.py; runner normalizes through it (no duplicated
    shape checks to drift). Injection-verified.

45. [qodo 138] "whitespace-only file/description passes." ACCEPTED --
    strip before the emptiness check. Injection-verified.

46. [expert 452, UNCERTAIN] "scoring helpers live in runner.py,
    splitting the module." ACCEPTED -- score_findings and
    pick_best_findings moved to scorer.py; runner imports them.

47. [expert 97] "an answer-key description with <2 significant tokens
    can never match." ACCEPTED as a MATCHING-RULE fix, not parse-time
    rejection: a concise expected description (one significant token)
    now matches on one shared token; rejecting such keys at parse
    time would break legitimate concise keys. Injection-verified.

48. [adversarial 465] "one actual finding can inflate hits across
    overlapping answer entries." ACCEPTED -- greedy distinct
    assignment: each actual finding hits at most one expected.
    Injection-verified.

## R3 (12 findings, all disposed; seven substance groups accepted)

49. [qodo 168, expert 138, adversarial 168] "strip validates but the
    unstripped value is stored." ACCEPTED -- stripped values stored.
    Injection-verified (first attempt invisible: the old whitespace
    test only covered all-whitespace, rejected at validation; added a
    padded-valid test).

50. [qodo 126] "explicit empty expected_findings key (None) rejected
    while the absent key defaults to []." ACCEPTED -- None coerced
    to [].

51. [expert 90, adversarial 90] "zero-significant-token descriptions
    are permanently un-hittable." ACCEPTED -- matching fallback: any
    shared alphanumeric token (any length) when the expected key has
    no >=4-char token. Injection-verified.

52. [expert 151] "range validation duplicated, list vs tuple drift."
    ACCEPTED -- valid_line_range moved to corpus.py; scorer imports
    it; the parser reuses it.

53. [expert 421] "UnicodeDecodeError crashes the runner." ACCEPTED --
    caught; and the return contract changed: absent/malformed state
    now returns None, distinguishable from a genuine zero-findings
    run, so evidence-less runs never participate in scoring.
    Injection-verified via a missing-state wiring test.

54. [qodo 438] "empty-file CONFIRMED findings inflate FPs."
    ACCEPTED -- skipped during normalization.

55. [adversarial 130] "greedy first-match can under-count."
    ACCEPTED -- exact maximum bipartite matching (Kuhn augmenting
    paths). Injection-verified against both the under-count
    counterexample and the R2 inflation case.

56. [expert 204] "EvalSummary lacks finding_misses." ACCEPTED --
    field added, aggregated, reported.

## R4 (7 findings, all disposed)

57. [qodo 671, expert 653, adversarial 319] "evidence-less runs:
    per-entry misses=0 vs summary all-missed -- contradiction with the
    'never participates' semantics." ACCEPTED -- EvalResult gains
    findings_evidence (False when no run produced state evidence);
    compute_summary excludes such entries from the findings
    denominator. Infra loss is not the model's performance.
    Injection-verified.

58. [qodo 75] "a programmatically constructed ExpectedFinding with an
    invalid range bypasses the description fallback." ACCEPTED --
    finding_hit now validates the expected range too.
    Injection-verified (first attempt invisible; strengthened the
    test to carry a valid actual range).

59. [expert 115] "docstring still says greedy; implementation is
    Kuhn." ACCEPTED -- docstring corrected.

60. [expert 39] "valid_line_range imported through scorer (transitive
    re-export)." ACCEPTED -- runner imports it from corpus directly.

61. [adversarial 162] "punctuation-only descriptions accepted but
    un-hittable." ACCEPTED -- parser requires at least one
    alphanumeric token. Injection-verified.

## R5 (infra-stalled, zero code findings)

Round 0's three passes all hit the 900s silence detector
(deepseek-direct went silent mid-response, the same CN-afternoon
stall the surflare session documented) before emitting any finding;
the round produced three CONFIRMED INFRA findings and no falsify
events. Zero code findings from the round -- convergence with the
R1-R4 disposition set. Review complete; loop exited on the
substance-free-repeat criterion (the infra stall adds no signal
about the diff).
