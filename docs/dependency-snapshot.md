# Ordinary-user dependency snapshot (local candidate)

This adds evidence for Forge #31, not an admission gate. Native execution is
**NOT_RUN**. No workflow, deployment, security setting, baseline matcher, or
service-launch logic is added. Do not publish or dispatch this candidate until
its independent reviews and applicable checks are complete.

## Explicit stack and provenance

The three new files are `scripts/dependency_snapshot.py`,
`tests/test_dependency_snapshot.py`, and this document. The local workspace also
contains an **unchanged baseline dependency**: #378's
`scripts/fixed_runner_metadata.py`, recovered from head `e8bd3e18b68414d6021bca43e6dd4ac1724cae98`, SHA-256
`988503e24b9fd3c42d4ccd2f2a3616e6cbccfa33311a1789c14a56f178c1cfc3`.
The adapter verifies that digest before importing source directly, without
reading bytecode. The source read requires a regular file, rejects final symlinks,
uses nonblocking open and a 64-KiB size/read limit. Its module-level code only declares constants/functions;
`main()` is guarded. This stack does not imply #378 is merged or available on
main. Main inspected: `5744def5955b1ef2ad83f8f189492a283164c359`.
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

Limits: 64 listed units, 64 jobs, 64 elements per array, 4096 bytes per string,
256 KiB serialized receipt, one 25-second worker deadline and a 30-second parent
watchdog. The watchdog reaps its direct worker with the reviewed bounded capture
helper; it does not prove descendant cleanup. These output/iteration limits are
**not a cap on libsystemd's internal receive allocation**. Library calls use the
remaining common timeout. The parent watchdog covers child library loading,
filesystem reads, authentication and queries, subject to ordinary OS scheduling
and process termination limits. Python startup, importing the exact trusted
metadata source, and resolving the script path in the parent precede this
watchdog; no wall-time bound is claimed for that bootstrap. A killed worker
yields STOP, never successful partial data. The sole command-line entry accepts
no arguments; `--worker` is rejected. Internal child invocation uses a fixed
Python command rather than a public unguarded worker flag. Ordinary UID/GID
checks precede source loading and CDLL. STOP exits 2; OBSERVED exits 0.

Manager fields, roster and jobs are compared before and after the property reads;
process and endpoint identity are rechecked. This is not an atomic snapshot and
does not detect a change that is reverted between checks. Unit paths are queried
only from the returned roster. Description and ListUnits job detail fields are
not retained. RequiresMountsFor values are filesystem paths, not unit names.
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
Full-tree CI and native execution remain NOT_RUN.

Source contracts: [v255 Unit fields](https://github.com/systemd/systemd/blob/v255/src/core/dbus-unit.c#L785-L866),
[Manager fields](https://github.com/systemd/systemd/blob/v255/src/core/dbus-manager.c#L2719-L2743),
[ABI declarations](https://github.com/systemd/systemd/blob/v255/src/systemd/sd-bus.h#L273-L310),
[FD ownership](https://github.com/systemd/systemd/blob/v255/man/sd_bus_set_fd.xml#L41-L52).
