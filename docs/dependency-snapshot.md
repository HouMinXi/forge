# Ordinary-user dependency snapshot

Publication update: this workflow now uploads only [bounded public aggregate summaries](public-diagnostic-evidence.md), never the raw receipts described below. Raw details remain ephemeral on the runner; they are not a private artifact archive.

This adds evidence for Forge #31, not an admission gate. Native execution is
**NOT_RUN** for the local runner integration. No deployment, security setting,
baseline matcher, or service-launch logic is added. Publication and native
dispatch remain separate steps after independent reviews and applicable checks.

## Explicit stack and provenance

The original probe files are `scripts/dependency_snapshot.py`,
`tests/test_dependency_snapshot.py`, and this document. They use an
**unchanged baseline dependency**: #378's
`scripts/fixed_runner_metadata.py`, recovered from head `e8bd3e18b68414d6021bca43e6dd4ac1724cae98`, SHA-256
`988503e24b9fd3c42d4ccd2f2a3616e6cbccfa33311a1789c14a56f178c1cfc3`.
The adapter verifies that digest before importing source directly, without
reading bytecode. The source read requires a regular file, rejects final symlinks,
uses nonblocking open and a 64-KiB size/read limit. Its module-level code only declares constants/functions;
`main()` is guarded. The source snapshot was prepared before #378 merged; #378 is now merged.
The original main inspected was `5744def5955b1ef2ad83f8f189492a283164c359`.
#376 inspected: `76817fcd87ed61c3339f9b0abedcf900858773ba`.

## Query and effect boundary

The reviewed identity bootstrap obtains the system manager's authoritative
`user@UID.service` MainPID with `systemctl show`. That existing command performs
GetAll internally and transiently receives unrelated properties before filtering.
Only Id, ActiveState, and MainPID are retained by its parser. The new adapter
**does not claim that the entire process never invokes GetAll**.

The separate user-manager dependency connection has a closed method set:
Manager.ListUnits, Manager.ListJobs, and Properties.Get for fixed Manager/Unit
fields only. It never requests GetAll, service execution, Set, Ref, Reload,
LoadUnit, or bus Hello. Auto-start and interactive authorization are disabled on
every request. The connected socket is bound to the reviewed MainPID/UID,
start time, pidfd and endpoint checks, then that same FD is handed to libsystemd.
There is no address reopening, session-bus fallback, or alternate protocol client.

Property lookup may load manager configuration caches and affect dependency,
notification and garbage-collection bookkeeping. No start/stop transaction is
requested. This is not a claim of zero host effects, complete dependency closure,
absence of concurrent activity, or strict isolation qualification.

## Trusted runtime and finite outcomes

The candidate currently supports the canonical target of the preinstalled
`/usr/lib/x86_64-linux-gnu/libsystemd.so.0` only. It verifies root ownership and
non-writable ancestor/file metadata using the reviewed helper, records SHA-256,
and requires the fixed public nonvariadic ABI symbols. It records
`library_package_version=NOT_OBSERVED`. Manager.Version is independently recorded
and must have major version 255; it is not proof of the library package version.
The trust model excludes hostile root or replacement of the trusted runtime.
No installation, sudo, profile change or permission workaround is performed.

Snapshot decoding limits: 256 listed units, 64 jobs, 64 elements per property
array, and 4096 bytes per string. The 256-unit limit is an operational bound
informed by one later observation of 185 units, not a baseline or guarantee.
The receipt is limited to 256 KiB, with one 25-second worker deadline and a
30-second parent watchdog. The watchdog reaps its direct worker with the reviewed bounded capture
helper; it does not prove descendant cleanup. These output/iteration limits are
**not a cap on libsystemd's internal receive allocation**. If ListUnits exceeds
its snapshot array limit, an optional later, non-atomic count-only query skips at
most 512 typed entries without retaining their values. It reports an exact count
only when the reply ends within that bound; otherwise it reports a validated
512-entry prefix and that more entries exist. Exceeding the unit limit still
produces STOP, and a failed count is omitted. This count is not a qualification or
proof of the triggering roster size; it also does not bound receive allocation.
Library calls use the remaining common timeout. The parent watchdog covers child
library loading, filesystem reads, authentication and queries, subject to
ordinary OS scheduling and process termination limits. Python startup, importing the exact trusted
metadata source, and resolving the script path in the parent precede this
watchdog; no wall-time bound is claimed for that bootstrap. A killed worker
yields STOP, never successful partial data. The sole command-line entry accepts
no arguments; `--worker` is rejected. Internal child invocation uses a fixed
Python command rather than a public unguarded worker flag. Ordinary UID/GID
checks precede source loading and CDLL. STOP exits 2; OBSERVED exits 0.

Manager fields, roster and jobs are compared before and after the property reads;
process and endpoint identity are rechecked. This is not an atomic snapshot and
does not detect a change that is reverted between checks. Unit paths are queried
only from the returned roster. The higher unit cap can retain more unit names
and paths in the artifact. Environment, Exec and configuration contents are not
collected. Description and ListUnits job detail fields are not retained. RequiresMountsFor values are filesystem paths, not unit names.
Environment and Exec fields are outside the dependency whitelist.

Missing fields, permission errors, incompatible signatures, identity drift,
changed roster/jobs, missing ABI/library, timeout or any limit yields STOP with
`complete=false`; partial snapshots are discarded. Finite errors include the
failing whitelisted property when available, never exception or bus error text.
OBSERVED means this bounded observation completed, not approval of the runner.

## Offline validation

Run `python -m unittest discover -s tests -v` from the isolated candidate directory.
Tests use fake ABI functions and anonymous socketpairs, not a system manager,
service, listener, privileged operation or network. They do not establish actual
D-Bus serialization/authentication or runner compatibility. Python compilation
and these targeted tests are separate from full-tree CI. All 30 offline tests
and compilation passed. Targeted Ruff 0.17.3 check and format --check passed
for both new Python files using the current repository configuration and an
isolated uvx cache under /tmp, without a global installation. Formatting
preserved both Python ASTs; the baseline dependency remained byte-identical.
The original #382 head `486ddcbf566971dbb42ce59dac2e14a5c8799e33` subsequently
passed ordinary hosted CI, including these 30 tests. That is not native evidence
and does not establish CI for the runner integration below.

Source contracts: [v255 Unit fields](https://github.com/systemd/systemd/blob/v255/src/core/dbus-unit.c#L785-L866),
[Manager fields](https://github.com/systemd/systemd/blob/v255/src/core/dbus-manager.c#L2719-L2743),
[ABI declarations](https://github.com/systemd/systemd/blob/v255/src/systemd/sd-bus.h#L273-L310),
[FD ownership](https://github.com/systemd/systemd/blob/v255/man/sd_bus_set_fd.xml#L41-L52).


## Explicit runner integration (local, native NOT_RUN)

The already registered `.github/workflows/strict-linux-admission.yml` gains one
manual opt-in, `ordinary_dependency_snapshot`, default false. Its default static
observation is unchanged. The ordinary and existing fixed privileged metadata
modes are mutually exclusive at job selection. Selecting both runs only a small
failure job with a STOP artifact, no checkout, and no diagnostic. PR events
cannot enter either manual job. The privileged script and its command set are
unchanged. The ordinary job never invokes that script's privileged entry point.

This uses the registered workflow path on the dedicated branch
`diag/ordinary-dependency-snapshot-20261103`, rather than adding an unregistered
workflow that cannot be manually dispatched before a default-branch merge.
Before an authorized dispatch, publish the reviewed branch and obtain its full
commit SHA; supply that SHA as `expected_source_sha`. Set only
`ordinary_dependency_snapshot=true`; leave `fixed_metadata_read=false`.
An Actions rerun is rejected (`run_attempt` must be 1). This document grants no
permission to dispatch, push workflow changes, merge, or retry a native run.
The later file-view mode is also default false and mutually exclusive; leave
`dependency_view_read=false` for ordinary observation. This wrapper's dedicated
branch check remains in force after integration into main: dispatching the main
ref yields STOP, not an automatically expanded execution allowance.

The fixed-purpose `scripts/dependency_snapshot_runner.py` requires the exact
repository, branch, job, workflow path, event and both mode values. Expected,
source and workflow SHAs must agree with the checked-out HEAD. Before any native
call, it checks the probe's fixed SHA-256 and the probe checks its existing
metadata dependency hash. It compares all four local sources (wrapper, probe,
metadata helper and workflow) against their regular non-executable HEAD Git blobs using bounded
read-only Git calls. System/global Git configuration and replace objects are
disabled for these reads. It repeats the four-file binding after the probe returns.
The receipt records the exact declared execution context and four SHA-256s.
Context is declared workflow evidence, not independent cryptographic attestation.
The wrapper is itself executed from the reviewed dispatch commit; the expected
SHA is a reviewer/operator binding, not protection against a malicious workflow.

There is no command, module, library or script-path input. The unchanged probe
runs only under the ordinary UID/GID, with isolated stdlib Python and a minimal
child environment. It uses only the preinstalled library already described
above. No sudo, package installation, user-manager creation, service start/stop,
AppArmor/sysctl/delegation change or permission workaround is added.

The wrapper starts with `native=NOT_RUN`, marks an invocation `ATTEMPTED`, and
marks a validated return `RETURNED`. Failure to validate context/source never
enters the probe. STOP remains an unsuccessful diagnostic, not an admission
result. `qualification=NOT_RUN` is preserved for every result. The probe retains
`BOUNDED_NONATOMIC` and `dependency_closure=NOT_ESTABLISHED`. Missing or denied
runner prerequisites remain findings to evaluate, not permission to alter them.

The outer probe capture is limited to 40 seconds and 256 KiB; each fixed Git
capture is limited to 2 seconds and 64 KiB. Source reads are limited to 64 KiB
per file and reject final symlinks/nonregular files. The serialized wrapper
receipt is capped at 300,000 ASCII bytes, and the job at three minutes. These
limits do not turn process termination or initial Python/bootstrap filesystem
work into a strict wall-clock guarantee. The outer watchdog reaps its direct
probe process; no independently verified descendant-cleanup claim is made.
Only the single receipt is uploaded, retained 14 days. Checkout or runner failure
can prevent a valid receipt, and must not be treated as a successful observation.

Offline integration validation: all 340 tests across the snapshot, wrapper,
existing fixed metadata and static admission suites passed locally (including
16 event/mode/repository selection cases). Compilation and full-tree Ruff check
and format check also passed. The isolated pytest environment emitted two
non-failing plugin/configuration warnings; this is not a full project test run. Native behavior remains
NOT_RUN. The tests use mocks and temporary files only, not a real manager or
native bus. They cover exact context, mode guards, SHA mismatch, four-source
binding, source size/symlink rejection, root rejection, STOP/OBSERVED agreement,
invalid output, timeout/error capture and post-run source drift.
