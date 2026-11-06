# Opt-in real mutmut CI ledger controls

This small source-checkout suite runs the genuine synchronous `run_mutation`
path through `StateMachine.run(Mode.CI)`. It is partial #335 evidence, not a
qualification of every mutation backend or the entire issue.

## Run explicitly

Use Linux with CPython 3.12, readable `/proc/<pid>/task/<tid>/children`, working
pidfds and the repository's current development dependencies. The fixture's
expected native inventory is pinned to mutmut 3.8.1. Install in your own venv:

```bash
python -m pip install -e '.[dev]' 'mutmut==3.8.1'
FORGE_RUN_NATIVE_MUTATION=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest \
  -c pyproject.toml qualification/native_mutation/test_ci_ledger.py -q
```

Only the exact flag value `1` enables execution. Otherwise the explicitly
selected test skips. Once enabled, missing dependencies, mismatched versions,
missing Linux ownership prerequisites, incomplete measurements and failed
cleanup fail the test rather than becoming skips. Python assertions must remain
enabled. This is deliberately outside the default `tests/` collection and the
strict Linux public identity/skip manifests. It neither changes the default CI
roster nor certifies that a hosted workflow ran it. Its single explicit node is:

```text
qualification/native_mutation/test_ci_ledger.py::test_real_mutmut_ci_ledger_controls
```

The existing installed-wheel suite has a separate roster and is unchanged.
Repository-wide Ruff lint/format checks still apply to these files.

## What executes

The test invokes an isolated worker with the actual production process owner.
The worker creates a fresh tiny Git repository, an associated state worktree
and an outside trusted gate in pytest's private temporary directory. It invokes
real baseline tests three times, probes the interpreter for mutmut, then runs
`python -m mutmut run --max-children 1` and `results --all true`.

For `double(x): return x * 0` with the deliberately weak test `double(0) == 0`,
the native inventory contains one killed mutant and one survivor. A seed
measurement is followed by five real CI measurements:

1. Unruled survivor: FAIL and CONFIRMED.
2. Exact DISPROVED fixture ruling: PASS and DISMISSED.
3. Latest UNADJUDICATED revocation: FAIL and CONFIRMED, including reentry on the
   same machine after copying its previous State.
4. Restore A's ruling, change source to `x * 2`, and resolve a fresh review:
   FAIL. Both current survivors, including the previously ruled identity,
   remain CONFIRMED under the changed review.
5. Use the old A review against source B: FAIL with the specific source-binding
   refusal and measurement authority ineligible.

Every verdict, persisted disposition and convergence value is checked.
Native command reports, generated metadata, inventory and result hashes must
agree. A return-event profiler only observes actual calls and copies files
before cleanup; no process owner, Popen, native transport, metadata, parser,
ledger or StateMachine method is replaced. Ledger terminal decisions are
synthetic fixture inputs written/read by the real ledger, not assertions of
semantic equivalence of the mutants. L0 is empty; L1/falsifier/autofixer are the
existing stubs, and coverage is explicitly exempt for this tiny source.

The worker has a clean environment and private HOME/XDG directories. Its PATH
contains only Git so that the separate end-of-CI detached discovery branch does
not launch another measurement. The genuine direct runner prepends the absolute
interpreter's bin in its child environment, where mutmut is installed. The extra
dismissed `mutation-unavailable` PATH-discovery note is expected and checked;
it does not mean the synchronous measurement was replaced or skipped.

## Bounds and cleanup

The outer process owner has a 240-second execution deadline plus its normal
cleanup/report grace. It sets inherited 1-GiB RLIMIT_AS before starting the
worker, so the cap also reaches baseline/probe/results processes. This is a
per-process address-space cap, not an aggregate-memory/cgroup quota. The worker
disables core dumps. Inner phase deadlines are 20 seconds per baseline attempt
(three attempts), 30 seconds for import probing, 60 seconds for mutmut and
10 seconds for results. The mutmut concurrency setting is one child. Idle-host
gating and third-party pytest plugin autoload are explicitly disabled.

The test checks successful outer and inner ownership reports, no remaining
reported owned processes, restored setup/source bytes, removed mutation mirrors
and no recovery/quarantine directories. The complete unlocked owner journal and
two empty marker directories are normal retained product state. Failure is
propagated and uncertain recovery artifacts are not deleted by the harness.

Raw command output, metadata and states remain in pytest's private temporary
files under its normal retention policy; the harness does not upload them or
add them to the repository. They can contain host paths and PIDs. Use a fresh
`--basetemp` when retaining a particular experiment; pytest may clear a supplied
base directory on a later invocation.

## Boundaries

This suite does not test the CLI dispatcher, detached launch/publication,
installed-wheel acquisition, the separate cgroup MutmutAdapter, resource
exhaustion or failure-path cleanup, continuous/ABA lifetime binding, OS-process
restart recovery, full branch coverage or full-repository regression. A local
Mode.CI verdict is not a remote CI workflow result. No #335 checkbox should be
closed solely because this suite passes.

The original exploratory smoke used Python 3.12.14, mutmut 3.8.1 and pytest
9.1.1. That historical result does not establish this suite's required current
pytest pin or its stricter Linux observation preflight.

## Source map and primary references

- `test_ci_ledger.py` owns the opt-in and outer execution bound.
- `worker.py` owns private fixture construction, observation and the five
  controls. It calls existing `mutation.run_mutation`, `machine.StateMachine`,
  `ledger.append_row`, `finding_suppression_key` and `state.load_state`.
- `src/code_forge/_mutation_process.py` owns descendant tracking and cleanup;
  `_mutation_workspace.py` owns config/mirror leases.
- `src/code_forge/mutation_findings.py` validates measurement/review binding and
  applies latest exact rulings.
- [mutmut documentation](https://mutmut.readthedocs.io/en/latest/) describes its
  fork requirement and pytest/configuration model.
- [Python resource documentation](https://docs.python.org/3.12/library/resource.html)
  defines RLIMIT_AS; it is not an aggregate-memory limit.
- [Python profiling API](https://docs.python.org/3.12/library/sys.html#sys.setprofile)
  describes the observational callback, not a sandbox.
