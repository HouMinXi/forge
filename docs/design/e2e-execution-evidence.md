# R3 execution evidence: a runtime record of what executed

Status: agreed for a first implementation. Supersedes two earlier drafts: the
first sealed a current HEAD onto an old report and let a record suppress the
P2; the second generated its own execution id, offered a JUnit converter, and
reported through review findings. All three are wrong and are replaced below.

Scope: one narrow slice of #33 capability 5. Nothing here claims the capability
is complete.

## What R3 does today

`run_e2e_check` (`src/code_forge/e2e_check.py:565`) runs two layers. Layer 2 is
opt-in through `.code-forge/components.yaml` and fires a P2 when a hub and a
dependent are both touched and no e2e artifact is found under the dependent's
paths. "Found" is `os.path.isfile` on a glob match (`find_e2e_artifacts`,
`:311`), reached through `_artifact_satisfies_pair` (`:361`).

A file being present suppresses the finding. Whether anything in it ran is
outside the check. `README.md:476` says so: "R3 is artifact-presence, not
coverage proof."

`_run_e2e_check_cmd` (`cli.py:5807`) already prints DISMISSED findings from
`E2E_CHECK` as "ADVISORY (non-blocking)" and exits 0 (`:5892`-`:5905`), so an
advisory channel exists. This version does not use it. A finding carries a
fingerprint that later rounds compare, and adding one here would change what
rounds are matched against, which is not the neutral report the first version
should be.

## What capability 5 asks for

> bind source, test and actual execution; reject stale, wrong-version,
> unrelated or unexecuted artifacts; test that key assertions discriminate.
> File presence, test execution, target-path execution and assertion validity
> are each proven separately.

Four proofs. This slice delivers one of them, test execution.

| proof | owner today | this slice |
| --- | --- | --- |
| file presence | `find_e2e_artifacts` | unchanged |
| test execution | nothing | a record of the files that reached the call phase, at a named commit |
| target-path execution | coverage, for unit tests; e2e has no equivalent | out of scope, not claimed |
| assertion validity | mutation (`src/code_forge/mutation.py`) | out of scope, not claimed |

## Three contracts

1. Evidence comes from a runner or operator the reviewer trusts, not from the
   reviewed change. The record is written by the test run, at run time, to a
   path the operator names. A record found inside the review root is refused.
2. Binding happens at run time. The record names the snapshot it tested, so a
   converter cannot seal a fresh commit onto an old report.
3. This slice only reports. The P2 logic and every exit code stay as they are.
   An UNCERTAIN finding fails the CLI (`cli.py:5879`, `return EXIT_FAIL` at `:5887`), so suppressing the P2
   would change gate results. This design does not suppress it.

No signing, no remote attestation in this version. The trust is the operator and
the runner, stated plainly rather than approximated by a hash.

Three things the first version does not do, because each would let an old or
unrelated result pass as evidence:

- It does not accept a record whose execution id differs from the one the caller
  asked about. A fresh id inside the record would still accept an older run of
  the same commit, so the run entry point allocates the id and the caller hands
  the expected value to the reader.
- It does not convert existing JUnit XML. Nothing in an old report says which
  run produced it or which commit it tested, so a converted report cannot be
  called bound.
- It does not report through review findings. A finding carries a fingerprint
  that later rounds compare, so adding one here would change what rounds are
  matched against. The reader is a standalone command with its own output.

## Measured constraints

One file holding a passing test, a `pytest.mark.skip`, a fixture that raises in
setup, and a failing assertion, run on this machine with
`-o junit_family=xunit1`:

| testcase | `file` | children |
| --- | --- | --- |
| test_pass | `tests/e2e/test_mix.py` | none |
| test_skip | `tests/e2e/test_mix.py` | `<skipped>` |
| test_setup_fail | `tests/e2e/test_mix.py` | `<error>` |
| test_fail | `tests/e2e/test_mix.py` | `<failure>` |

All four carry `file`, so a path in a report proves nothing about execution.
Without that option none of them carry `file`: `_NodeReporter.record_testreport`
sets `"file": location[0]` and then drops it for families that do not allow the
attribute.

`<error>` is ambiguous. A setup failure and a teardown failure both produce it,
so element inspection cannot answer "did the call phase run". That is why the
primary producer is a pytest hook and JUnit is only a fallback with a
conservative rule.

CTRF carries `suite`, `name` and `line` on its test object and no file field,
so a path from it would be a guess. Not used.

## The record

Written by `src/code_forge/e2e_record.py`, a pytest plugin loaded with
`-p code_forge.e2e_record`:

```json
{
  "schema": "forge-e2e-execution-v1",
  "execution_id": "5f0f0e6c1f5b4a0e9d2f3c7a1b8e4d60",
  "started_at": "2026-11-23T15:41:02-05:00",
  "finished_at": "2026-11-23T15:41:19-05:00",
  "command": "python -m pytest tests/e2e -p code_forge.e2e_record",
  "complete": true,
  "session_exitstatus": 1,
  "snapshot": {
    "commit": "b620f5063a1c9d34f8e1a5c9b7d2e4f60718293a",
    "dirty": false
  },
  "executed_files": ["tests/e2e/test_flow.py"]
}
```

- The plugin reads its output path from `FORGE_E2E_RECORD` and its run identity
  from `FORGE_E2E_EXECUTION_ID`, both allocated by whoever started the run. With
  either missing it writes nothing.
- It writes once, at `pytest_sessionfinish`, through a temporary file and a
  rename, and only when the session exit status is 0 or 1. An interrupt,
  internal error, usage error or empty collection leaves no record at all.
- `snapshot` is captured at `pytest_sessionstart`: `git rev-parse HEAD`, and
  whether `git status --porcelain` was empty.
- `execution_id` is the value the caller allocated for this run, copied
  verbatim. It is not a signature. Its job is to tell this run from an earlier
  one of the same commit, and the reader refuses any other value.
- `executed_files` takes a file when at least one test in it produced a
  `pytest_runtest_logreport` with `when == "call"` and `outcome` in
  `{"passed", "failed"}`. A test that skips inside its body reports
  `when == "call", outcome == "skipped"` and is not counted, because the run
  cannot say how much of it executed.
- No env var, no plugin, no record: nothing changes.

The record must never be committed. It is named by the operator and kept
outside the tree; a path that resolves inside the review root is refused, which
also blocks a PR that ships its own record.

## Binding

The record is usable only when every one of these holds. Otherwise the run says
which one failed and counts nothing.

| state | condition |
| --- | --- |
| `absent` | no record path configured, or no such file |
| `in_tree` | the record path resolves inside the reviewed tree |
| `unusable` | unreadable, not JSON, not an object, wrong field types, a bad path entry, or over 2 MiB |
| `unbound_review` | no 40-hex commit was named, or the named commit is not the clean HEAD |
| `foreign` | `schema` is not `forge-e2e-execution-v1` |
| `incomplete` | `complete` is not true, so the run did not finish |
| `wrong_execution` | `execution_id` differs from the one the caller asked about |
| `dirty_run` | `snapshot.dirty` is true |
| `stale` | `snapshot.commit` is not 40-hex, or differs from the reviewed commit |
| `bound` | all of the above hold |

The record is read before the tree is judged, so a record sitting inside the
tree reports `in_tree` rather than a dirty-tree message. It disqualifies itself
either way, and the sharper answer is the useful one.

`unbound_review` is what the second draft missed. `e2e-check` reads either an
external diff (`--diff`, `cli.py:5824`) or `git diff HEAD` (`:5844`), and
neither is a commit. Comparing the record to HEAD would pass a review of
unrelated content. The reader therefore does not accept a commit as a label on
a diff: it reads HEAD and the working tree itself, and requires the named
commit to be the clean HEAD. A dirty tree means the reviewed content is not one
commit, and a differing HEAD means the parameter was attached to something else.
A diff reviewed from a file cannot be bound at all in this version.

`wrong_execution` is what the third draft added. The id is allocated at run time
by whoever starts the run, copied into the record, and handed to the reader by
the caller. Without comparing it, an older run of the same commit would be
accepted as evidence for this one.

Only a tracked file that differs from the commit refuses a review, and both
sides apply the same rule. Untracked files do not count, because running a suite
leaves caches and reports behind and none of that is part of the commit;
counting them would mark every run after the first one dirty and make the record
unusable in the tree it was written for. The producer reads the same way, at the
moment the run starts.

The limit that buys: an untracked file that changes what the tests do, such as a
stray `conftest.py`, is not detected. Naming it here is the alternative to a
check that refuses nearly every honest run.

Path rules for `executed_files`: POSIX separators, no leading `/`, no `..`
segment, no backslash, no empty entry. One bad entry makes the record
`unusable`.

## What the reader reports

Nothing here touches the review pipeline. The reader is a standalone command:

```
python -m code_forge.e2e_evidence --record /var/tmp/run.json \
    --execution-id 5f0f0e6c1f5b4a0e9d2f3c7a1b8e4d60 \
    --reviewed-commit b620f5063a1c9d34f8e1a5c9b7d2e4f60718293a --root .
```

It prints one JSON object (`state`, `detail`, `bound`, `executed_files`,
`review_commit`, `record`) and exits 0 when bound, 1 when not, 2 on a usage
error. Nothing is added to `state.json`, no fingerprint is created, and no
round is matched differently than before.

The P2 in `check_layer_2` is also untouched, in this version and by this
document. Suppressing it stays out of scope, and no claim is made that gate
results are unaffected by anything here, because the reader is not wired into
the gate in the first place.

## Config

None in the repository. The record path comes from `FORGE_E2E_RECORD` or the
CLI flag, never from `components.yaml`, because that file sits inside the
reviewed change and a PR could point it at its own record.
`load_components_yaml` (`:162`) is not touched by this slice.

## Not in this slice

- Suppressing, downgrading or re-prioritising the P2. This document does not
  claim the gate is unaffected by anything it does.
- Running tests. forge reads a record; the check gains no subprocess.
- Target-path coverage for e2e. Unproven, unpublished.
- Assertion discrimination. Mutation owns that evidence; this slice neither
  restates it nor borrows its credit.
- Signing, remote attestation, protection against replay across machines.
- Multi-hop, cross-repo or history work.

## Risks and dispositions

| risk | disposition |
| --- | --- |
| A PR ships its own record | `in_tree` refusal, and the path never comes from repo config |
| A record from another commit | `stale` |
| HEAD compared against a review of unrelated content | `unbound_review`; the named commit must be the clean HEAD, which a parameter alone cannot fake |
| An older run of the same commit | `wrong_execution`; the id is allocated per run by the caller and compared |
| A record left by a run that was interrupted | no record is written unless the session finished, and `incomplete` refuses one that says otherwise |
| A run started on a dirty tree | `dirty_run` |
| A skipped or setup-failed test counted as coverage | not possible: pytest emits no call report for either, and only call-phase passed or failed is recorded |
| A hand-written record claiming files that never ran | Out of reach. The record is trusted input from the operator, and this version adds no signing. The limit is stated here and in the README. |
| A huge record eats memory | 2 MiB cap before parsing |
| An untracked file changes what the tests do, such as a stray `conftest.py` | Not detected. Both sides look at tracked files only, and the reason is in the binding section. |
| Tests counted under a different interpreter or dependency set than the review assumes | Not detected. A limit, named rather than glossed. |

## Exit conditions

Acceptance is four contrasts on the same diff:

Four measured contrasts, each a real pytest run in a throwaway repository
followed by a real reader invocation:

1. The record from this run, at this commit, is accepted and names the file that
   ran.
2. The record from an earlier run of the same commit, judged with the current
   run's id, is refused as `wrong_execution` even though its commit matches.
3. A file holding only a skipped test and a test whose fixture raises in setup
   produces a record that does not name it.
4. A review whose named commit is not the clean HEAD is refused as
   `unbound_review`, and a record from another commit as `stale`.

Plus:

5. Every state row covered by a test.
6. Injections, each red then restored byte-exact: drop the call-phase filter
   (contrast 3 must fail), drop the execution-id comparison (contrast 2 must
   fail), drop the in-tree refusal, drop the dirty check, drop the completion
   check, drop the size cap.
7. No change to any existing test: this version adds no finding, no fingerprint
   and no CLI subcommand to the review path.
8. `README.md:476` rewritten to say which two answers are now separate, which
   two are not, and that nothing here gates.

## Sources

- pytest JUnit XML writer, `file` attribute and family filtering (background
  for why a report cannot answer this, not a producer in this version):
  https://doc.pytest.org/en/stable/_modules/_pytest/junitxml.html
  (14742 stars: https://github.com/pytest-dev/pytest)
- `pytest_runtest_logreport` and its `when` values:
  https://docs.pytest.org/en/stable/reference/reference.html
- CTRF test object, no file field: https://ctrf.io/docs/specification/test
  (104 stars: https://github.com/ctrf-io/ctrf, pushed 2026-11-21)
- Revision-bound artifact provenance as a CI concern:
  https://owasp.github.io/DevSecOpsGuideline/2-Process/2-3-Build/2-3-6-Supply-Chain-Security/
- Measured `testcase` attributes and children: real run in this repository, see
  "Measured constraints".

## What the tests do not pin, and why

The mutation gate runs mutmut over the two modules and counts a surviving
mutant as a finding. Two groups of mutants cannot be killed by any test worth
writing, and the tools in this space say so themselves:

- **Equivalent mutants.** A mutant whose behaviour matches the original for
  every input. Stryker documents the category and ships three ways to handle it:
  exclude the mutator, a `// Stryker disable` comment, or an ignore plugin, with
  the disabled mutant kept in the report as `ignored` instead of removed from
  the score. PIT takes the other route and says its operators "are largely
  designed to be stable ... and minimise the number of equivalent mutations".
  Source: https://stryker-mutator.io/docs/stryker-js/disable-mutants/ and
  https://pitest.org/quickstart/mutators/.
- **Prose.** `argparse`'s `prog`, `description` and `help=` strings change what
  the tool prints when asked for help, and nothing else. cargo-mutants and
  Stryker both offer file- or line-level exclusion for exactly this, and neither
  asks for tests that compare help text.

This module uses mutmut's own mechanism, `# pragma: no mutate`, which the
mutation gate honours because forge drives mutmut for Python
(`src/code_forge/mutation_engines/adapters/python_mutmut.py`). mutmut also
supports `block` / `start` / `end` variants and a `do_not_mutate_patterns`
configuration; the pragma is used here because it sits next to the code it
applies to. Source: https://mutmut.readthedocs.io/en/latest/ and the pragma
parser in `mutmut/mutation/pragma_handling.py`.

Seventeen statements carry a pragma (four in `e2e_record`, thirteen in
`e2e_evidence`), each with its reason inline:

| location | why no test can kill it |
| --- | --- |
| `main`'s `prog` and `description` | prose |
| the four `add_argument` statements | prose; the flag names and `required=` are asserted by the command-line tests |
| `_git`'s `encoding="utf-8"` in both modules | a different encoding changes a byte only under a non-UTF-8 locale |
| `verify`'s `read_text(encoding="utf-8")` | same |
| `Recorder.write`'s `fdopen(..., encoding="utf-8")` | same |
| `_git`'s `return 1` on `OSError` | the reader only asks whether the code is zero |
| `_read_repo_state`'s `head or None` | the caller reports `unbound_review` on that path and drops the head |
| `_valid_paths`' absolute-path clause | an absolute entry also has an empty first segment, which the next guard refuses |
| `verify`'s `(reviewed_commit or "")` | a fallback of `"XXXX"` fails `_is_commit` exactly as `""` does |
| `verify`'s `resolve(strict=False)` | `strict=None` is falsy and takes the same path |
| the module-level `pytest_sessionfinish` | the delegate reads only `exitstatus` |
| the `stacklevel` of the "not written" warning | only the frame the warning is attributed to changes; the message is asserted exactly |

### The diagnostic cannot change what the run reports

When the record is refused, the plugin says so. That message is a warning, and a
project that runs with `-W error` turns every warning into an exception: the
refusal would then surface as a failing run with exit code 1, which is a change
this feature must not make. The message is therefore emitted inside
`warnings.catch_warnings()` with `simplefilter("always")`, which keeps it a
message under any filter setting and restores the caller's filters afterwards.

The message also says which of the ways it declined happened — the run had not
finished, the snapshot could not be read, the snapshot could not be read again
(a git call stopped answering, which is not the same as the tree moving), the
tree no longer matches the commit it started on (naming it), or the record could
not be written — so a caller can tell a refusal from a failure without reading
the code.

The regression tests run pytest in a subprocess twice, with `-W error` and with
`-W ignore::UserWarning`, and assert three things at once in each: exit code 0,
no record, and the full message with its reason still in the output. A unit test
compares `warnings.filters` before and after the hook, so the guard cannot leak
a filter change into the caller.

### Only the test body counts, and the snapshot is re-read at the end

Two things the first shape got wrong, both found by running it:

- **A subtest is not the test body.** A fixture that runs `subtests.test(...)`
  and then skips the body reports `when="call"` for that subtest, so a filter on
  `when` and `outcome` alone records the file as executed while the test never
  ran. pytest wraps a subtest in a `SubtestReport`, so the hook now checks the
  concrete type: `type(report) is not pytest.TestReport` means it is not an
  ordinary test report, and it is ignored.
- **The snapshot is read twice.** The commit and the tracked-tree state are read
  when the session starts, and read again before the record is written; if
  either moved, no record is written and the run says why. A test that edits a
  tracked file and leaves it is the case this catches.

Two equal readings do not prove the tree never moved: a test can change a
tracked file and change it back within the same run, and the second reading
would match the first. The record states that the tree matched at both ends,
which is what it can observe.

### What the plugin does when it cannot leave evidence

The hook only records when both `FORGE_E2E_RECORD` and
`FORGE_E2E_EXECUTION_ID` are set. When they are set and the record still cannot
be written — the run was interrupted, the tree was dirty, the path could not be
created — `pytest_sessionfinish` now emits a warning naming the path instead of
failing silently. The warning does not change the exit status: a refused record
is absence of evidence, not a test failure.

The session root comes from `session.config.rootpath`, falls back to `rootdir`,
and finally to the directory the run started in, so a config object carrying
neither attribute does not end the session with an `AttributeError`.

### Two boundaries the review asked about

- **A symlink inside the tree that points outside it.** `_as_posix_relative`
  judges the path text, not the filesystem: a record naming `link/file`, where
  `link` resolves outside the repository, is accepted as `link/file`. That is
  deliberate and harmless here, because `executed_files` is data the reader
  compares and prints; nothing opens those paths. Resolving them would make the
  judgement depend on the filesystem at review time, so two readers on two
  machines could disagree about the same record.
- **A deselected test emits no report at all**, so it never reaches the call
  phase and never enters the record. That matches the rule the module is built
  on: only tests that actually ran count.

Disabling a statement removes it from the gate, so the claim in the table above
was checked rather than assumed: renaming `--record` to `--recrod` in the parser
breaks six of the command-line tests, and dropping `required=True` from
`--execution-id` breaks two. What the pragmas stopped judging is still guarded.

Everything else is killed: 732 mutants, 0 survivors, over 155 tests. Dead code
found on the way out was deleted rather than pragma'd, which is why the two
unreachable guards in `_as_posix_relative` and the unused `or "unknown"` in the
HEAD comparison are gone.
