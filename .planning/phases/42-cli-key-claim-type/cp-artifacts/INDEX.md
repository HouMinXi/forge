# Phase 42 CP1b artifact index

Archived 2026-07-25 by the PM session from /tmp (Fleet Law S2: gate-relevant
evaluation evidence must live next to its results, not in volatile /tmp).
All 57 files copied byte-identical and verified with cmp; they are immutable
records (S2b). Reproduction re-runs write elsewhere and diff against these.

Naming deviation, stated openly: forge CLAUDE.md prescribes
`cp1b-r{round}-{model}.md`. These keep their original /tmp basenames instead.
Renaming 57 files across four rounds with retry suffixes (v2/v3/v4/k3) risked
mis-mapping a result onto the wrong model or round, which would corrupt the
record the convention exists to protect. This index supplies the mapping the
convention would have encoded.

## Why this index exists

Three file properties in here are actively misleading without it:

- Zero-byte results are dispatch failures, NOT clean verdicts. An empty file
  is the absence of a review, and reading it as 0 findings is a false green.
- Some outputs carry a header naming a different model than the file does
  (p42-r3-ds.md is headed "kimi Review"). The aicc dispatch .log files are
  ground truth for which model ran; the in-body self-label is not.
- The four p42-review-*.md files are PROMPTS, not results. Round 1 produced
  no results on disk.

## Rounds

| Round | Files | ds | kimi | longcat | gemini |
|-------|-------|----|------|---------|--------|
| R1 | p42-review-*.md | prompt only | prompt only | prompt only | prompt only |
| R2 | p42-r2-* | CLEAN 0/0/0/0 | NOT CLEAN 1B/1H/2M/1L | CLEAN 0/0/0/0 (v2; v1 errored) | 3 dispatch failures |
| R3 | p42-r3-* | CLEAN 0/0/0/0 | NOT CLEAN (k3 0B/1H/2M/2L; v3, v4 retries) | CLEAN 0B/0H/0M/1L | dispatch failure |
| Final | p42-final-*, p42-gemini-* | CLEAN 0/0/0/0 (08:30) | NOT CLEAN 0B/0H/2M/2L (v2, 08:42) | CLEAN 0/0/0/0 (08:32) | CLEAN 0/0/0/0 (09:10, manual relay) |

The R2 kimi BLOCKER was real and empirically reproduced: inserting
`version_sensitive` mid-dataclass raises TypeError (non-default argument
follows default argument) and would have broken collection of the whole
suite. It is the highest-value finding in this set.

## Artifact version boundary (important)

The plan files on disk were written 08:44 (42-02) and 08:52 (42-01).

- ds (08:30), longcat (08:32) and kimi (08:42) reviewed the version BEFORE
  those writes. Their clean verdicts do not cover the current files.
- gemini reviewed the current files via manual relay: R1 found one LOW
  (`p.read_text()` needed try/except OSError), the fix landed in 42-01, and
  R2 at 09:10 returned CLEAN.

So exactly one model has reviewed the artifact version now on disk. CP1b's
exit condition is ALL models 0/0/0/0 on the same version; that is not met.
Closing it needs one confirmatory round of ds/longcat/kimi against the
current files, not a redo.

## gemini dispatch failure (root cause on record)

`aicc gm` failed in R2, R3 and Final with "Gemini 3 Pro is no longer
available". OmniRoute's default-route combo falls back to the deprecated
antigravity/gemini-3.1-pro-high. The user hand-carried the prompt to Gemini
3.1 Pro (High) instead; forge CLAUDE.md already designates gemini as a
manual-relay panel member, so this is the documented path, not a deviation.
Trace: p42-gemini-manual-relay-trace.md. Prompt: p42-gemini-manual-prompt.md.
Fixing the automation is tracked in the router onboarding compat batch.

## File groups

- `p42-review-*.md` -- R1 dispatch prompts (no results exist for R1).
- `p42-external-review-prompt.md`, `p42-r3-prompt.md`, `p42-final-prompt.md`
  -- prompts for R2, R3 and Final.
- `p42-r{2,3}-{ds,kimi,lc,gm}*.md` -- per-round results; `-v2`/`-v3`/`-v4`/
  `-k3` suffixes are retries after a failed or empty first attempt.
- `p42-final-*` -- final round results.
- `p42-gemini-*` -- manual relay prompt, trace, and result.
- `*.log` -- aicc dispatch logs. Authoritative for which model actually ran.
- `*.err` -- stderr. The recurring 192-byte one is a benign warning that
  claude.ai connectors are disabled because ANTHROPIC_API_KEY takes
  precedence; it does not indicate review failure.
- `*-pids.txt` -- background dispatch process ids.
