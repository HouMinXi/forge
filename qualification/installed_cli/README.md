# Private preenabled CLI capture tools

The executable capture entrypoint and private worker compose the test-only
prelaunch collector and passive native-event probe. A separate strict syscall
parser remains available without being imported by the executable capture. All runtime outputs are private. This subset
provides no publication-chain reader, ACK-consistency verdict, public runtime
summary, qualification runner or qualification certificate. It does not repair
or waive any separate reader or qualification blocker. Historical run08 remains
UNKNOWN; installed qualification remains NOT_ASSESSED.

## Capture one installed normal invocation

Run on a supported, authorized Linux host using an existing controller Python
with `os.pidfd_open`, `os.P_PIDFD` and `signal.pidfd_send_signal`. The target
interpreter remains the original interpreter named by the installed console.
No installation or rebuild is performed. Both modes use a **private, empty
bytecode-cache policy**, not historical warm-cache timings. A separate controller
can be needed:
for example, an existing target Python 3.12 may lack bindings present in the
host's Python 3.14. Neither executable is substituted for the other.

```sh
umask 077
/usr/bin/python3 -I -B scripts/capture_installed_cli.py \
  --wheel-evidence /absolute/installed-evidence \
  --mode baseline --output /absolute/private/new-baseline
/usr/bin/python3 -I -B scripts/capture_installed_cli.py \
  --wheel-evidence /absolute/installed-evidence \
  --mode diagnostic --output /absolute/private/new-diagnostic
```

The parent of each output must already be owned by the caller and mode 0700.
Every invocation needs a different nonexistent output directory. Paths containing
symlinks, reuse, or missing evidence fail closed; partial output is retained.
All raw files remain private. Do not upload them or print them in CI logs.
This command is a local diagnostic entrypoint, not CI integration or an installed
qualification runner. Final test-identity registration and release gates remain
separate. Historical run08 is UNKNOWN and qualification is NOT_ASSESSED.

The evidence tree must contain `build-proof.json`, `source.tar`, the named wheel
under `dist/`, and its existing noneditable installation under `venv/`. The
entrypoint derives hashes from the actual bytes instead of requiring a hand-made
binding file. It checks physical source archive, wheel, installed product sources,
metadata, direct URL, RECORD, console entrypoint and shebang. It records controller
and target interpreter paths, versions and binary hashes independently, plus owner,
imports and capture hashes. The target's isolated sys-only metadata supplies its
actual standard-library search roots and extension suffixes; controller search
paths are not substituted for them. The archive's Git PAX comment and build
record are RECORDED_BUILD_PROVENANCE, not independently verified Git-object proof. An older
wheel remains associated with its recorded source commit, never with this capture
checkout's newer base merely because their product trees happen to agree.

Before normal console startup, admission rejects competing top-level launcher
packages, legacy bytecode and extension modules, and sitecustomize/usercustomize
candidates in the original console directory, target standard-library roots and
physical site-packages. A present standard-library ZIP is inspected as data;
missing expected ZIP paths are recorded and must remain missing at worker
admission. No candidate is imported or unmarshalled. The worker repeats this
admission against the bound observations before launching the target.

The supported config is only prefix/pyvenv.cfg. Any bin/pyvenv.cfg candidate,
including a dangling link or identical-content file, is rejected because CPython
would give it higher priority. A shared parser follows CPython's UTF-8 text-file
CR/LF/CRLF iteration and strips/lowercases each key before use. Every duplicate
normalized key is rejected; include-system-site-packages must be explicitly
false. Values such as the home path preserve their case. The exact prefix config
bytes are bound even without a uv alias, and rechecked at worker admission,
immediately before console launch, and after owner-confirmed quiescence. No
alternative config resolver or startup hook is executed.

One existing uv base-prefix alias layout is supported without changing startup
selection: cpython-MAJOR.MINOR-PLATFORM may point directly to its matching
cpython-MAJOR.MINOR.PATCH-PLATFORM sibling. The target's isolated metadata must
report that base prefix and full version, its base-exec prefix must agree, and
the original pyvenv.cfg home must name that alias's bin directory. Relative and
absolute direct sibling links are recognized; other aliases remain unsupported.
Admission records the original paths, link text and identity, parent identity,
physical destination identity and configuration hash, then inventories the
canonical standard-library roots. Parents and physical descendants must contain
no other links; the alias, its parent and physical destination must belong to
the current effective user and group, and the directories must not be group/world
writable. Ownership checks use that same user/group role inside the existing
user namespace, where numeric IDs change; original controller IDs are recorded
separately. Aliases owned or grouped by a different host account are not admitted. Missing expected
ZIP roots remain bound to the same alias and canonical path.

The worker checks the alias before and after startup inventory and again after
fixture setup immediately before the synthetic console launch. The controller
checks it after owner-confirmed quiescence. Retargeting, replacement, dangling
links, configuration changes and new competing startup candidates fail closed.
These are bounded identity observations, not a lock against a concurrently
hostile owner or kernel. The genuine installed console and interpreter still run
through their original paths; no target sys.path rewriting, installation edits,
cache changes or interpreter copies occur. Private output, source files, console
and site-packages retain their ordinary strict no-symlink rules.

Each mode exclusively creates its own empty mode 0700 PYTHONPYCACHEPREFIX inside
its new private worker output. PYTHONDONTWRITEBYTECODE remains enabled. For
source-backed imports that use CPython's cache-prefix mechanism, this redirects
bytecode-cache lookup away from old source caches, without deleting, renaming,
reading or rewriting those cache contents. The genuine generated console and
target source/sys.path selection remain unchanged. The prefix must be empty and
have the same identity before launch, after the child, and after owner-confirmed
quiescence. Missing, reused, symlinked, changed or nonempty prefixes fail closed.

Unknown .pth hooks are rejected. The sole supported distutils-precedence.pth must
match its exact recognized bytes. Both modes set SETUPTOOLS_USE_DISTUTILS=stdlib,
so that hook's optional _distutils_hack import does not run. This is an explicit
controlled environment choice, not evidence that arbitrary startup plugins are
safe. The target interpreter and its standard library remain trusted runtime
inputs; their startup search roots are observed, not replaced.

Bypassing existing source-backed bytecode caches can change startup cost and
timing. This does not flush OS/page caches, make filesystem or disk caches cold,
or disable every loader's bytecode storage. The execution binding covers the
verified top-level launcher source under the admitted startup routes. Compare
baseline and diagnostic only under this shared private-cache policy. Neither is
claimed to reproduce earlier warm-cache timing or recover run08, and no product deadline is relaxed to compensate. Source/cache
admission is not a native outcome or durability verdict.

The controller loads only exact verified installed `_mutation_process` and
`_mutation_imports` source bytes in a private package. It does not import the
installed product package or add target site-packages to its own sys.path.
The unchanged `run_owned_command` owns the worker and descendants. Admission
checks both self and an exact live synthetic child's process-children view.
Absent pidfd bindings or unsupported child visibility stops before a CLI launch.

The worker runs through existing `unshare --user --map-root-user --net`, without
a PID namespace. It must observe a different network namespace before invoking
existing `ip` to bring up loopback, and reject any non-loopback interface. Denied
or uncertain containment stops; there is no host-network fallback. Diagnostic
mode first checks the selected tracer with one bounded sys-only child in that
same namespace. A denied or incomplete capability check stops without attempting
the real CLI. No security settings are changed to make tracing work.

Only a new synthetic Git project, synthetic credential, bounded local HTTP peer
and two ordinary JSON responses are used. Baseline invokes the genuine installed
launcher directly, without a shim, probe or tracer. Diagnostic enables the
unchanged collector before the first normal installed product import. No model,
provider account, actual user repository or alternate installed-audit suite is
used. Run the two modes separately; tracing adds overhead and is not a baseline.

The product's two-second limit is unchanged. The harness uses a 20-second CLI
wait, 35-second worker deadline through the existing owner, 1 GiB per-process
address-space limit, 25-second worker/child CPU limit, 120-second controller CPU
limit, and 8 MiB per-file limit. Owner cleanup keeps its existing bounded grace
periods. Request headers total at most 32 KiB, bodies at most 1 MiB, at most two
ordinary exchanges, and each request has a fixed three-second read deadline.
These are explicit per-process/per-file bounds, not aggregate memory or disk
quotas. The input archive/wheel read bound is 64 MiB. Capture sources remain
hash-bound. Existing source-backed launcher and probe caches are bypassed by the
fresh prefix rather than validated or removed.

The private worker receipt separates the signed exact child returncode, status
observed before cleanup, first body exception, cleanup-induced termination,
collector observations, HTTP transport and worker receipt delivery. Worker exit 0
means only that its receipt was delivered. The controller preserves an ordinary
CLI exit 7 as shell exit 7. A negative child status maps explicitly to 128 + signal.
An earlier timeout remains 124 and cancellation remains 130 rather than becoming
its cleanup-induced SIGKILL. A successful child with missing/failed required
capture or cleanup returns harness status 73. Missing/truncated receipts never
manufacture success, and later metadata failure does not erase an observed 7.

Worker-time collection checks are preliminary. The controller repeats narrow
framing checks only after the unchanged owner confirms descendant quiescence.
The resulting `collector_health` is delivery/framing health, not complete trace
semantics or native correctness: stream correlation and ACK consistency remain
NOT_ASSESSED. No parser, publication reader or qualification verdict is imported.
The trace stays raw and private. SIGKILL is not power loss.

## Collect through an existing synthetic harness

Enable collection before the first normal installed import. A later rerun cannot
recover the original failure's trace. Import `traced_child` from
`qualification.installed_cli.diagnostic_run`:

```python
with traced_child(
    argv,
    case_id=case_id,
    cwd=synthetic_project,
    env=synthetic_environment,
    output=new_capture_directory,
    stdout=private_stdout,
    stderr=private_stderr,
) as child:
    returncode = child.wait(timeout=existing_harness_timeout)
```

This is an API fragment, not a process supervisor. The caller must already own a
private isolated synthetic session, watchdog and resource budget. It must reap
its exact child before context exit and clean up any tracer and descendants after
success, timeout, signal or setup failure. The context does not terminate or reap
children. Preserve the original exception or `Popen.returncode`, including
negative signal results, separately from all diagnostics. The product's existing
two-second deadline is unchanged. The collector's bounded two-second post-exit
drain is only a wait for an exact child terminal observation.

The collector requires Linux and an already authorized `strace` supporting
`-D -f -ttt -T -yy`, found on the system default search path. `-D` leaves the
console executable as the exact Popen child. The caller must verify the platform,
tracer capabilities and permissions before use. Stop on unsupported environments;
do not change ptrace, namespace, mount, kernel or security settings to make it run.

- Use the genuine installed console executable with synthetic arguments, outside
  the checkout, and a sanitized environment without a preexisting `PYTHONPATH`.
  Installed-wheel provenance and synthetic transport isolation belong to the
  caller. Never use real repositories, provider accounts or unsanitized inputs.
- The `output` parent must already be private and trusted. `output` must not
  exist. Relative paths are anchored to the caller's current directory, not the
  child's `cwd`; existing output symlinks are rejected. The collector creates
  directories with mode 0700 and files with mode 0600. Partial setup output must
  not be reused as a new capture.
- Pass caller-owned stdout/stderr streams created exclusively with mode 0600 in
  that private tree. The collector does not enforce the permissions of these
  streams. Never inherit public terminal output for a real capture. HTTP bodies,
  native storage and other harness files are private too.
- `case_id` accepts `normal`, `correction`, `partial`, `http_error`, `start`,
  `prefix`. These are fixed synthetic harness labels, not proof that any scenario
  ran or that a roster is complete.
- A private `sitecustomize.py` shim enables observation before ordinary installed
  imports. It neither preimports Forge nor replaces product functions. Normal
  installed-launcher checks remain active. Only the child receives the shim's
  environment.

After launching a child, context exit attempts private `execution.json` even if
closing the events descriptor fails. An active setup/body exception takes
precedence, then a close error, then a metadata error. The events descriptor is
not closed twice. Private stream wrappers are non-owning: their construction or
close failures cannot leak the helper-owned descriptor or cause a second close of
a reused number. A missing footer, unreaped child, missing metadata or false
`collector_terminal_observed` is missing evidence, not product success.

The collector has no raw trace write-size cap. A 32 MiB terminal-drain read bound
is not a disk quota. Bound disk use and process lifetime in the caller's already
authorized harness; do not mistake parser limits for collection limits.
Each drain attempt reads at most 65,536 tail bytes plus one preceding boundary
byte. A terminal must start at a genuine line boundary and include its terminating
LF within that read. A cut record, including a complete-looking prefix without
the observed LF, supplies no terminal evidence; a later attempt may observe it.

## Passive probe observations

The collector normally installs `diagnostic_probe.install(destination,
max_events=200_000)` through the shim. Direct probe users must install before
native imports and threads, in an isolated process with no conflicting trace
hook. A path is created exclusively; a supplied private regular descriptor is
duplicated, and the caller retains ownership of its original descriptor. Direct
`Probe(destination, max_events)` construction uses the same destination contract:
all observer state is initialized before a private descriptor is acquired. Setup
failure preserves its primary exception and never retries descriptor close.
Installation is published only after exit-handler registration succeeds. Under the lifecycle preconditions below, a start
or registration failure rolls back the owned descriptor, installed hooks and any
registered callback; its original exception takes precedence over cleanup errors.
A normal uninstall still exposes unexpected cleanup errors; if both finishing and
callback removal fail, the finishing error takes precedence. An ordinary descriptor
close OSError remains contained by the observer, as before. These are tested
specific boundaries, not a guarantee against arbitrary memory exhaustion.
`finish()` emits the observer footer; `uninstall()` also removes the hooks.
Uninstall must run on the thread whose start attempt passed its preconditions,
before that thread exits. After a hook could have been installed, the caller must stop
creating threads and join all other Python threads, including non-native workers,
before uninstall. A still-live thread causes rejection before cleanup because its
inherited per-thread hook cannot be removed by the owner. The guard is a check,
not synchronization against concurrent thread creation. Before any accepted start
attempt, cleanup belongs to the constructing thread. A start rejected by its
precondition checks does not transfer ownership. Wrong-thread uninstall raises
RuntimeError before changing output, descriptors, hooks, callbacks or installed
state; the owning thread must perform cleanup. Ownership uses the actual Thread
object, not an integer identifier that could be reused. This does not make start
idempotent or authorize cleanup from a different thread. Cleanup of an observer
whose start was rejected before hook installation still closes its private FD
even when other threads already exist. Partial installation records possible hook
exposure conservatively; if another thread inherited it, stop/join that thread
before cleanup on the owner.

The probe emits scalar JSONL with generated host, operation, ticket, submission,
worker, wait and publication observations. It uses exact native object references
for ownership, not nearby timestamps or phase-name guesses. It does not replace
product functions or alter their return values, exceptions, deadlines or objects.
Transport ACK state and native acknowledgement fields remain separate raw values.
Recognized native acknowledgement fields must both be booleans; unsupported
shapes are contained as observer errors. Non-ACK results carry paired nulls.

The private footer's `complete` flag describes observer delivery and tracked
lifecycle health only. It is not ACK certification, publication consistency,
product success, or evidence of durable storage. Event limits, emission failure,
unknown objects and incomplete lifecycle observations must not be silently
ignored. `deadline_ns` is a creation-time snapshot, not a later effective deadline.

## Parse private syscall text

`parse_trace(text)` consumes text supplied by the caller. Read only trusted private
inputs, without following untrusted symlinks, and bound reads before allocating
input text. The parser rejects more than 32 MiB of encoded text, 200,000 input
records/events, or 65,536 characters per line. It does not read or persist files.
Only LF separates physical records; CRLF terminators are also accepted. The line
limit excludes that terminator, and real blank records are not discarded. Other
separator characters cannot frame records, but supported quoted field data and
C escapes remain data. A missing final LF still makes parsing incomplete.

<!-- private-parser-example -->
```python
from qualification.installed_cli.trace_parser import parse_trace

private_parse = parse_trace(private_trace_text)
```

Keep the entire result private, including `complete`, `issues` and `events`.
The result is not a public receipt. Do not print it in CI logs or publish captures.
There is no combined reader or verdict API in this subset.

The parser retains fsync/fdatasync, rename variants, directory opens, descriptor
closes, exits and signals. It validates supported syntax, sequence, paired split
calls, terminal chronology and conservative descriptor evidence. Split records
pair by native TID and syscall name; both duration and resumed timestamp constrain
the endpoint. Every sync's `sync_target_kind` stays `unknown`: untraced operations
such as `dup2` can replace a descriptor without changing its decoded path.
`prior_directory_open_event` is only an index into the private event list for
compatible recorded directory-open history, or `None` if absent or ambiguous.
It is not evidence of current descriptor identity, type, or continued ownership.
Observed close/reuse, overlapping operations and tied timestamps conservatively
suppress this historical link across TIDs; untraced operations remain unknown.
The original open/close records and their timing remain available. Unsupported, malformed,
truncated or incomplete syntax fails parsing health rather than implying success.

A private `complete=True` means only these parser checks passed. It can coexist
with a failed syscall or nonzero child exit. It makes no assertion about native
outcomes, publication chains, ACK consistency, provenance or durability. A
successful syscall or late ACK does not establish power-loss survival; SIGKILL
is not power loss.

## Validation boundaries

Tests use synthetic trace text, native in-process fixtures and ordinary local
Python/fake-tracer subprocesses. They do not execute actual `strace`, an installed
Forge CLI, a model API or a provider. Probe and collector assertions inspect raw
fields, native objects/outcomes, file privacy and original returns/exceptions
without a publication reader or a hidden consistency oracle.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q \
  tests/test_cli_diagnostic_probe.py tests/test_cli_diagnostic_run.py \
  tests/test_cli_trace_parser.py
```

Preserve a separately authorized uninstrumented baseline. Instrumentation adds
overhead and may itself cross a deadline; never substitute a diagnostic rerun for
the baseline. Full repository CI, independent review/fault controls and installed
qualification retain their own gates. Safe external reports may describe tool
and test status, but must not contain runtime capture data.
