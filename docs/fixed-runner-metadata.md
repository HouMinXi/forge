# Fixed runner metadata diagnostic (not admission)

This is a separate, explicitly opted-in, one-run diagnostic. It does not import
or run the user-service collector, start a service, replace its ordinary-user
configuration gate, fix the unresolved dependency activation boundary, or qualify
a production isolation boundary. Those remain `NOT_RUN` / `NOT_REPLACED`.

## Entry and source

The already registered `strict-linux-admission.yml` dispatcher has a boolean
`fixed_metadata_read`, default false, and `expected_source_sha`. Only explicit
manual dispatch with the opt-in selects the new job. PR and push events cannot
select it; the original static observer keeps its default behavior.

Before sudo, the standalone ordinary-user program requires repository
`HouMinXi/forge`, branch `forge-fixed-metadata-observer-20261102`, attempt 1, the
fixed job/workflow identity and a valid expected SHA equal to both source and
workflow SHAs. Bad input produces a nonzero STOP and finite artifact rather than
two skipped jobs looking successful. A new dispatch is technically possible:
attempt 1 is not a durable once-only lock, and it does not authorize a second run.
The parent must bind the reviewed source and explicitly release this one run.

The program compares its own file and workflow file to regular Git tree blobs
and SHA256 hashes, using fixed `rev-parse`/`ls-tree` with global/system Git config
and fsmonitor disabled. It does not claim the entire worktree is clean. Source
and tool metadata/hashes are rechecked before and after observations. These
self-declared fields are not execution provenance; GitHub run/job/attempt,
artifact digest, source and actual workflow bytes must be reconciled externally.

## Exact privileged reads

The manager must already be active. Ordinary fixed systemctl reads the system manager's active `user@UID.service`
MainPID. Kernel process UID/starttime/pidfd and a fixed existing private-socket
peer independently cross-check that PID and UID. The observer holds a pidfd and checks start time/liveness around the reads. It never calls LoadUnit,
show-environment, starts a manager or reads environment files.

There are at most four privileged attempts (a pre-spawn failure can leave a
conservative UNKNOWN attempt record): two numeric identity snapshots
and two known-profile presence checks. All use this fixed prefix:

`sudo -n -- /usr/bin/timeout --signal=TERM --kill-after=1s -- 2s`

- Root-owned `/usr/bin/stat` reads only the identified PID's mnt namespace, cgroup
  namespace and root device/inode pairs. The three paths and numeric printf
  format are generated internally; no arbitrary path, PID or format is accepted.
- Root-owned `/usr/bin/grep -E -q` checks only exact `bwrap` and `unpriv_bwrap`
  profile-name lines in the kernel's loaded-profile view. Exit 0/1 becomes a
  matching-line boolean. No list, profile text, mode string or stderr is emitted.
  A name match proves neither vendor policy contents nor executable confinement.
  Exit 1 is interpreted only under the supported normal sudo/timeout/grep chain
  semantics, with no diagnostic output and both surrounding stat reads valid.
  It is not independent authentication that grep ran: a silent frontend/plugin
  failure cannot be distinguished solely from that code. Any diagnostic output
  or other chain error is UNKNOWN/STOP; no sudo validation call is added.

The timeout, stat, grep and supporting tools must be root-owned ELF files under
fixed canonical paths with safe ancestors. Only sudo's expected setuid bit is
accepted. Ordinary `timeout --version` must report GNU coreutils 9.4; no
`sudo --version` is executed. Tool SHA256 is recorded, but root ownership and a
version string are not vendor-package binary attestation.

The environment passed to sudo contains only a newly constructed PATH/LANG/
LC_ALL. Sudo/PAM/plugins may generate additional environment internally; the
observer does not inspect it. Stdin is `/dev/null`, cwd is `/`. No root Python,
shell, env dump, package install, policy load, permission change, controller
write, bwrap or product invocation is included. Sudo may write normal audit
records: this is not a claim of zero host side effects.

## Budgets, uncertainty and evidence

Preflight commands each have a two-second ceiling. The four privileged reads
and their post-observation identity/source checks share one 15-second deadline.
Each privileged call has an outer ceiling of four seconds, clamped to remaining
budget; inner timeout has two seconds plus one-second KILL grace. These are
ceilings, not measured native timings or hard real-time guarantees.

Stat stdout is capped at 256 bytes; grep stdout must be empty. Stderr is discarded
and only a saturated count is retained. Overflow is drained rather than killing
only an ordinary parent and pretending root cleanup succeeded. Errors stop later
calls; no retry, root pkill or killall is available. The kernel profile input is
time-bounded, not claimed to have a byte limit.

GNU timeout waits for its monitored child, not an arbitrary descendant tree;
its KILL grace may kill the monitor itself. Sudo can involve pty/plugin processes.
A terminal status and direct sudo reaping do **not** prove all privileged
descendants are gone. Evidence always says `NOT_INDEPENDENTLY_OBSERVED` for that
fact. An uncertain direct wait is `UNKNOWN`; timeout/signal/error remains STOP.
A stable snapshot differing from the caller can still be reported as a diagnostic
comparison; it never substitutes for an ordinary gate or authorizes service start.

The private artifact contains only closed context/tool identifiers, numeric
metadata, known-name booleans, finite reasons/statuses and bounded counts. Invalid
context is not echoed. Raw paths, environment values, profile text and exception
messages are not persisted. Malicious host/root/same-UID races and ABA remain
outside the model; these checks are not host attestation.

Local controls mock all privileged commands; an autouse spy fails any attempted
local sudo subprocess even if the failure is caught. Real local controls use ordinary
pidfds, temporary files and harmless child processes. They do not establish
native success. The diagnostic remains separate from any future reviewed runner
baseline and from the unresolved #376 dependency boundary.

## Ordinary-command failure diagnostics

Receipt schema `forge-fixed-metadata-v3` retains `ordinary_failure`, null unless an
ordinary command fails. Its operation is one of `source_head`, `source_tree`,
`timeout_version`, or `systemctl_manager`. It retains only exit code or signal, a finite
capture/failure reason, errno, captured stdout length, saturated stderr count,
and direct-child reaping state. Captured stdout length is not total output size
when the cap is exceeded. Exceptions before a capture result have zero available
counts and no asserted reaping; raw output and exception text are never stored.
The same failure stops subsequent operations; success predicates, root command
arguments and deadlines are unchanged.

The first dispatched v1 receipt stopped during ordinary manager identification,
before any sudo attempt. Its generic `command` reason cannot identify which of
the three manager commands failed. New labels cannot retrospectively attribute
that old observation, and no host-policy cause has been established.

Operation and exit status identify the failed call, not necessarily its cause.
A nonzero command exit with counted stderr can still leave the host mechanism
unknown; this diagnostic does not parse stderr into authoritative D-Bus errors.

## Existing private-peer identity contract

Version 3 replaces the session-bus-name dependency, not its failure explanation.
There is no fallback between identity paths. The sole endpoint is the already
existing `/run/user/UID/systemd/private`; missing/unsafe directories, symlinks,
non-socket endpoint, mismatching peer PID/UID, changed endpoint metadata or
process identity all STOP. Runtime and systemd directory ownership/modes and
root-owned ancestors are checked before and after connecting. The restrictive
runtime directory limits access even if the socket's own write bits are broad.

A bounded AF_UNIX stream connection reads only kernel SO_PEERCRED: numeric
PID/UID/GID with exact frame length. The GID is the peer's observed GID, never
substituted from the caller or used as an authorization grant. No D-Bus messages,
authentication bytes or other protocol payload are sent or read. Connecting does
create a short-lived server connection: this is not zero host side effects.
The client socket is closed, and the held pidfd is closed on every owned exit;
close failures retain finite secondary labels without replacing a primary error.

After the fixed metadata reads, system MainPID and private-peer identity are
checked again within the existing shared deadline. The receipt records
`manager.private_peer`, not `bus_owner`; v1/v2 session-bus observations retain
their old meaning and cannot be reinterpreted as private-peer evidence.
The v2 run's GetNameOwner failure mechanism remains unknown. This replacement
only identifies the target of the approved metadata reads; it is not a service
admission fix, permission bypass claim, supported runner qualification or a
resolution of the #376 dependency boundary. Native compatibility remains untested
until a separately released exact-source run.

Endpoint stability compares the complete recorded metadata fingerprint, including
ancestor directory size and timestamps, not just inode identity. An unrelated
directory update can conservatively STOP; that alone does not establish endpoint
replacement or a hostile change.

Finite `identity_failure` labels distinguish private_path, private_connect,
private_credentials, identity_recheck and manager_process, with errno and a
timeout flag. Nested errors retain the most specific observed operation. These
labels do not include endpoint strings or exception messages and do not infer
a policy cause.
