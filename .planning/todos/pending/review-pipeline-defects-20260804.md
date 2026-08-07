# Review pipeline defects found in the field (2026-08-04)

Found while running `forge_review` against OmniRoute worktrees. All four were
observed in the same session, on `code-forge 2.7.0`. Ordered by how much they
cost the caller.

Environment: fresh linked git worktree under
`/home/houminxi/code/OmniRoute/.claude/worktrees/`, `.code-forge/` populated by
copying `gate.yaml` / `tools.yaml` / `gate.schema.json` / `contract-template.md`
from the main checkout.

---

## 1. CI mode discards state.json, so a review can never converge

Stderr on every run:

    ignoring prior state.json in CI mode (STATE-09)

Consequence: a finding the caller has already dispositioned comes back
byte-identical on the next round. Measured on a 3-line diff, three consecutive
runs each produced the same fingerprint `5cc902eaf4c06dbd` with
`"converged": false` and `"verdict": "FAIL"` every time. `round_history` grew,
but the dispositions it carried were never consulted.

Why it matters beyond wasted tokens: house rules elsewhere say "never claim
review-complete after fewer than 3 rounds." That rule silently assumes rounds
accumulate state. Under CI mode they cannot, so the caller either burns three
identical runs for ceremony, or marks the work reviewed on fewer rounds and is
technically in breach. Neither is a good option.

Suggested shapes, in preference order:

- Persist dispositions across rounds even in CI mode, keyed by fingerprint,
  and let a dispositioned finding count as resolved for convergence.
- If CI mode must stay stateless, detect the repeat: when round N produces a
  fingerprint set identical to round N-1, say so explicitly in the output
  ("no new findings vs previous round") rather than reporting a bare FAIL that
  looks like fresh signal.
- At minimum, document that CI mode is stateless where the round-count
  guidance lives, so callers stop paying for rounds that cannot converge.

## 2. Untrusted workspace silently swaps the backend

Stderr:

    Untrusted repo backends ignored. Run 'code-forge trust' to enable.

The `gate.yaml` in the worktree declared `deepseek-omni` (default) and
`deepseek-v4`. The run ignored both and used `gemini-omniroute` /
`onmi-gemini3.6` instead. That substitution appears only in the token-cost
block at the very bottom of the SARIF properties; nothing in the verdict, the
findings, or the receipts says the configured backend was not used.

This is the dangerous half: the review still returns findings and a verdict, so
it reads exactly like a successful run against the configured model. A caller
comparing rounds across worktrees would be comparing different models without
knowing it.

**It is not only a different model, it is a much shallower review.** Measured
back-to-back on two worktrees of the same repo, same forge version:

| | untrusted worktree | after `code-forge trust` |
|---|---|---|
| backend | `gemini-omniroute` / `onmi-gemini3.6` | `deepseek-omni` / `sn-deepseek-flash` |
| output tokens | 393 | 8816 |
| converged | false, on all 3 rounds | true, on round 1 |
| infra errors | 2 | 0 |
| finding quality | 1 CONFIRMED with four fabricated line numbers | clean |

An order of magnitude less generated reasoning, and the one finding the shallow
run did produce cited four line numbers that contained nothing resembling the
claim. A caller who does not know the substitution happened will read that as
"forge found a real bug in my diff."

Suggested: refuse to run, or emit a prominent warning in the verdict line,
rather than silently substituting. "Backend X requested, backend Y used" belongs
next to the verdict, not in a cost footer.

## 5. The 3-round convention is unsatisfiable when round 1 passes clean

Round 1 on the 429 diff: `PASS`, `converged: true`, 51 s, 8816 output tokens.
Round 2, same diff untouched: `PASS`, **3.95 s**, and byte-identical token
counts (44235 in / 8816 out) with `durationSeconds` in the properties dropping
from 67.1 to 0.2.

That is a cache hit keyed on `diff_sha256`, and it is the *right* behaviour --
re-running an unchanged diff through a nondeterministic model would be waste.
But it means "run at least 3 rounds before calling the review complete" cannot
be satisfied on a diff that passes clean the first time: rounds 2 and 3 are
replays of round 1, not independent evidence.

The convention implicitly assumes each round follows a fix, so the diff hash
changes. Worth stating that assumption somewhere the guidance lives, or having
forge say "cached result for unchanged diff" instead of reporting what looks
like a fresh second pass.

Note this interacts with defect 1: under CI mode a *failing* diff cannot
converge across rounds, and a *passing* diff cannot produce more than one real
round. Between them there is no configuration where the 3-round convention
does what it was written to do.

## 3. L0 eslint is invoked with the leading slash stripped

From `infra_errors`:

    L0 ToolError tool=eslint msg=Tool exited 2 with no output
    stderr=No files matching the pattern
    "home/houminxi/code/OmniRoute/.claude/worktrees/repro-8779/tests/unit/8779-agy-prefix-credential-lookup.test.ts"
    were found.

The path lost its leading `/`, so eslint got a relative path that does not
exist, exited 2, and the entire L0 lint layer contributed nothing to the review.
It is reported as an infra error rather than a failure, so the run still
produces a verdict.

Looks like a path is being joined or trimmed somewhere that eats the root
slash. Worth checking whether this only bites for newly added (untracked ->
staged) files, since the same run linted other paths without complaint.

## 4. L2 is skipped silently when gate.yaml has no test section

From `infra_errors`:

    L2: gate.yaml missing or test.command not configured: gate.yaml needs an
    active 'test' section for the commit gate.

The stock `gate.yaml` ships without a `test:` section, so the commit gate
skips tests by default and the run still reports a verdict. A caller who has
not read the infra_errors array will believe tests were part of the gate.

Adding the section is easy once you know:

    test:
      command: [node, --import, tsx/esm, --test, tests/unit/<file>.test.ts]
      timeout_seconds: 900

Suggested: either ship a commented-out `test:` stub in the template gate.yaml
so the omission is visible, or make "no test command configured" a warning on
the verdict line rather than an entry in an array most callers never read.

---

## Cross-cutting note

Three of the four share one shape: **the pipeline degrades and still returns a
normal-looking verdict.** L0 crashed, L2 never ran, the backend was swapped --
and the output was a clean-looking `FAIL findings=2 confirmed=1`. Everything
needed to notice was present, but only in `infra_errors` and a cost footer.

A caller who trusts the verdict line gets a review that ran one layer on the
wrong model and does not know it. Surfacing degradation next to the verdict
would fix all three at once, independent of the individual fixes.

---

## Not a forge defect, recorded so it is not re-filed

The adversarial pass on the OmniRoute diff produced a finding whose four cited
line numbers were all wrong (748, 830, 886, 901 -- none contained what was
described), while the underlying conclusion was true at six *different* lines.
That is model behaviour, not pipeline behaviour. Noted only because it is the
nastiest review failure shape to handle: dismissing it for bad citations
discards a real defect, and accepting it on the strength of specific-looking
line numbers accepts fabricated evidence. Any future "verify the reviewer's
anchors" feature would have caught it cheaply.
