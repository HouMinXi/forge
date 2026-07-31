# Evidence 08 -- H3 (None base/head SHAs): when it real-triggers, and when it does not

## What this tests

H3 claims `base_sha`/`head_sha` are None in practice, so `machine.py`'s
`if base is None or head is None: return 0` (G4) silently swallows
rows. This calls the real `code_forge.baseline.resolve_baseline`
function directly (not a rewrite) with the same BaselineSpec
combinations the CLI's own `_build_baseline_specs`
(`src/code_forge/cli.py:3093-3138`, read but not modified) constructs
for each real flag combination, to see which ones actually produce None.

Scripts: `exp5_check_h3_none_shas.py` and (correction, see below)
`exp5_check_h3_none_shas_v2.py`.

## Real output

```
--- default review invocation: GitRefBaseline(HEAD) + head=WORKING, real git repo ---
base_sha='f189d153ca115bc66dc1302b67f203353c376483' head_sha='f189d153ca115bc66dc1302b67f203353c376483'

--- --whole-file mode: EmptyBaseline + head=WORKING, real git repo ---
base_sha='4b825dc642cb6eb9a060e54bf8d69288fbee4904' head_sha='f189d153ca115bc66dc1302b67f203353c376483'

--- SnapshotBaseline pointing at a MISSING snapshot file, real git repo ---
base_sha=None head_sha=None mode_hint='git'
```

The fourth case in the first script run had a bug: I nested the
"non-git" directory inside the same tempdir as the git repo it was
built next to, so `is_git_repo()` walked up and found the parent
repo's `.git` -- the result printed `mode_hint='git'`, which is not a
real non-git case and would have been a wrong claim if left in. Caught
by inspecting the printed `mode_hint`, not assumed correct. Corrected
with a genuinely separate tempdir in `check_h3_none_shas_v2.py`:
```
is_git_repo(nogit) = False
EmptyBaseline, head_spec=None, TRULY non-git dir:
  base_sha=None head_sha=None mode_hint='non-git'
```

## Reading

1. The DEFAULT `code-forge review` invocation (no flags, in a git
   repo) never hits None SHAs -- both fields are always populated by
   `_resolve_git` (`baseline.py:129-149`).
2. `--whole-file` mode, in a git repo, ALSO never hits None SHAs --
   `_resolve_empty` takes the branch that fills both fields whenever
   `head_spec is not None`, and the CLI always supplies
   `GitRefBaseline("WORKING")` for `--whole-file` when `in_git` is true
   (`cli.py:3089`). This corrects a plausible-sounding assumption (that
   whole-file mode might be an EmptyBaseline blind spot) that turned
   out false on measurement -- reported as such rather than reconciled
   away, per the dispatch order's instruction.
3. None SHAs are real and reproducible, but only via: (a) a
   `SnapshotBaseline` pointing at a missing snapshot file (falls back
   unconditionally to `head_spec=None` regardless of git-ness --
   `baseline.py:163-165`), or (b) a genuinely non-git working directory
   with no explicit `--head`.
4. Because `_write_ledger_rows` is structurally unreachable in CI mode
   regardless of SHA validity (H1, evidence-02), and the default
   git-mode review path never produces None SHAs anyway, H3 is not a
   contributor to the 26-day-empty ledger for the mainstream invocation
   path. It remains a real, narrow, independently-confirmed defect
   surface specific to snapshot-baseline usage or non-git directories,
   whichever mode is active.
