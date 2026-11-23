# Maintainability signal: explicit `# noqa` sites

`scripts/noqa_suppressions.py` answers one question: between two commits, which
explicit suppression comments were added, removed, moved to a different rule, or
paired with a different category. It is the maintainability-tracking slice of
Forge #33 and nothing more.

## What the number means

The headline count is **explicit sites only** — comments shaped `# noqa: RULE`
for one or more rule codes. Blanket `# noqa`, file-level `# ruff: noqa` and
directives with a shape the scanner cannot classify are counted separately and
never folded into the headline.

Every *measured* snapshot records the fingerprint that decides comparability:
`definition`, `scope`, `python` and `path_inventory_sha256`. An `unavailable`
snapshot carries `status`, `revision`, a `reason` and, when individual paths
failed, the `failures` list — it measured nothing, so it has no fingerprint. A
comparison whose fingerprints differ is reported as `incomparable` rather than
subtracted.

## What this does not measure

- Not Ruff's suppression behaviour. A comment may sit on a line that produces no
  diagnostic at all; this report cannot tell the difference and does not try.
- Not technical debt, quality, a score, or a ranking.
- Not a gate. Nothing in CI reads this output; the exit codes exist for scripts
  that choose to call it.
- Not the current working tree. Both sides are read from Git objects at fixed
  revisions, so an uncommitted edit cannot enter a comparison.

## Why suppressions are paired by source anchor

Line numbers move. Pairing by line would report a block insertion as a flurry of
edits. Instead each suppression is anchored to the code text to its left, then to
one and two lines of surrounding context. When that text exists on both sides but
cannot be identified — it is repeated, or it appears only inside a string — the
site is reported under `unmatched` with a reason (`repeated_source_anchor`,
`no_code_anchor_on_other_side`, `no_source_anchor`) instead of being guessed. A
repeated anchor stays unmatched even when the other side drops the comment: the
text is still there, only no longer unique, and nothing says which copy the
comment belonged to. A directive on its own line has no code anchor at all,
so `no_source_anchor` is reported on both sides even when nothing changed. A run
that changes nothing can therefore still print a non-zero `unmatched` count when
the file holds duplicate anchors or standalone directives.

When the anchor text is absent from the other side but that side still holds a
suppression of its own, the line was rewritten. Nothing proves the new line is
the same site moved, so both sides are reported under `unmatched` with
`no_code_anchor_on_other_side` rather than counted as a removal plus an
addition. A site is a confirmed `added` or `removed` only when the other side
has no suppression left to account for, which is the shape of code written or
deleted together with its directive. When the code text survives and only the
rule codes or the category move, the site is reported under `changed` instead.

## Failure states

| Status | Meaning |
|---|---|
| `measured` | One side's snapshot; carries the fingerprint and the counts |
| `comparable` | Fingerprints agree; `delta` and per-file details are trustworthy |
| `incomparable` | Fingerprint drift (definition, scope, interpreter, path inventory) or an `unclassified` directive on either side; no net delta is reported, common-path detail still is |
| `unavailable` | A commit or blob could not be read or parsed, or both sides measured nothing (`empty_scope_on_both_sides`, which means the scope holds no tracked Python file at all — a wrong `--repo` is more often reported as `commit_unreadable`); every failing path is listed, never zero-filled |

A symlink, a gitlink, an unsupported file mode or an undecodable path makes the
whole side `unavailable`; there is no partial measurement. That check runs only on
the Python entries that survive the scope filter, so a symlinked `notes.txt` is
simply not part of the measurement.

Exit codes: `0` comparable, `2` unavailable, `3` incomparable.

## Known limits

- One `git cat-file` process per blob (about 2.3 ms each), so runtime tracks the
  number of paths rather than total size: 185 paths here take about 2 s idle and
  about 3 s under load, and 4,000 distinct one-line files take 7-15 s depending
  on host load.
- Each blob is read and tokenized whole, and the cost follows tokens rather than
  lines: a 50,000-line file of single assignments peaked near 190 MB on an idle
  host, and the same 50,000 lines written as calls with six arguments peaked
  between 0.5 and 1 GB depending on how deeply the arguments nest. A blob that
  exhausts the address space is reported as `blob_too_large`, not as a traceback
  — including when the read itself fails. A cgroup memory limit tight enough to
  trigger the kernel OOM killer instead takes the whole process down; that is
  the kernel, not this tool, and it cannot be reported.
- The per-command timeout is 8 s and is not aggregated; a run over many slow
  reads has no overall deadline.
- `--no-lazy-fetch` needs Git 2.45 or newer: 2.44 and earlier reject the flag,
  which makes every side `unavailable`. It appears in `git --help` only from
  2.46.

## Reproducing the published sample

```sh
python3 scripts/noqa_suppressions.py --repo <local forge checkout> \
  12fbfac533f19c8d167500aa372543e4d2696089 \
  05b6aaafe2c1a35c7513299914845631ce20e41c
```

Both revisions hold 185 paths and 341 explicit sites with identical rule
aggregates. Two Python blobs differ (`factories.py`, `machine.py`); neither has a
confirmed suppression change under this matching definition — for example
`machine.py`'s BLE001 site shifts from line 3644 to 3651, which the anchor
pairing reports as unchanged rather than removed-and-added.

The `python` field records the interpreter that produced a report, so interpreter
drift can only appear when `compare()` is called directly on two stored
*snapshots*; the CLI always compares two revisions in one run. When a comparison
is `incomparable`, `--summary` names the mismatched fingerprint fields, so the
two causes are distinguishable without reading the JSON.

## Test status

`tests/test_noqa_suppressions.py` holds 58 tests. Each one pins a way the
comparison could otherwise lie: a directive inside a string, a repeated anchor, a
changed path inventory, an unreadable Git object, an unclassifiable directive, a
symlink inside the scanned tree, a lone-CR source, an unreadable blob, an
out-of-memory blob, and an algorithm change that would otherwise compare across
two definitions. The tests build their sample repositories in a temporary
directory, so they do not depend on this repository's own history.
