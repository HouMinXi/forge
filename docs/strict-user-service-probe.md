# Existing-user-service topology probe (local review candidate)

This is **not** production admission or strict Linux qualification. It observes
whether an existing ordinary-user manager can start a short-lived service whose
own identity/topology satisfies necessary cgroup prerequisites. It never imports
Forge tests/capability modules, launches bwrap or a product payload, creates a
product cgroup, enables controllers, requests delegation, changes host ownership,
loads profiles, installs packages, sets sysctls, enables linger or starts a missing
manager. No workflow or dispatch is included.

## Execution boundary and authorization

The parent entry point requires `--allow-transient-service`. It creates one
randomly named ordinary transient service, a mode-0700 temporary directory under
the verified existing `/run/user/UID`, and a private Unix socket. This is a limited
state-changing operation, **not wholly read-only**. External execution needs
separate authorization; implementation and offline tests do not authorize it.

The service uses verified fixed systemd/python tooling, argv without a shell,
Python isolated/no-site/no-bytecode mode, and a minimal explicit client environment.
User services otherwise inherit their manager environment: the probe takes a
bounded snapshot, validates every name, and passes **all snapshot names** in
UnsetEnvironment (including LD_PRELOAD/LD_LIBRARY_PATH when present). It compares
an internal snapshot digest after launch and acknowledgment; values and digests
never enter receipts. This detects ordinary concurrent changes, not malicious
same-UID/privileged ABA changes. It is not a race-free clean-environment proof.
Systemd may still supply its own runtime variables such as INVOCATION_ID,
SYSTEMD_EXEC_PID, JOURNAL_STREAM, USER, LOGNAME, HOME, SHELL, PATH and version-
dependent memory-pressure notification variables; this is not an empty environment.
The source's canonical path/ancestors must not be writable by other users. The
existing manager must already be active and delegated. Missing tools, policy,
permissions or prerequisites result in STOP, not host repair.

Fixed budgets: preliminary manager observations 5 seconds, read-only configuration
gate 5 seconds (both snapshots share this deadline), service startup 5 seconds,
exchange 5 seconds, and stop/verification 5 seconds. Each command shares its stage deadline and bounded
16-KiB stdout; stderr's first byte is rejected and never retained. Each direct
command child has an additional one-second SIGKILL/reap bound. WNOWAIT keeps its
PID reserved until group signalling. Service RuntimeMaxSec is 15 seconds,
TimeoutStopSec 2 seconds and KillMode control-group. These are reviewed upper
bounds, **not measured native timing/headroom**. Expiry does not prove cleanup.
Reads and output are byte-bounded; kernel/filesystem calls cannot be guaranteed
wall-clock interruptible in a stuck kernel. External CI must retain a whole-job
watchdog and collect a terminal receipt; none is supplied in this local stage.

## Identity and meaning

An unpredictable launch nonce and independent live challenge bind the exchange.
The parent reconciles SO_PEERCRED with fresh MainPID, boot ID, proc start time,
`/proc/PID/cgroup`, a live pidfd before/after acknowledgment, and repeated unit
metadata. The service checks the parent socket peer and collector hash itself.
Unique unit name, nonce-hash Description, Transient=yes and InvocationID identify
the owned unit; ownership is rechecked immediately before stop. The nonce itself
is omitted from the receipt, and service stdout/stderr go to null.

Run/attempt/source SHA are **declared context**, not provider-authenticated truth.
The collector's bytes are hash-bound on both sides and rechecked after exchange.
A privileged host or malicious same-UID actor is not excluded by this protocol;
provider reconciliation and exact source checkout remain external requirements.

Only the service computes its source, delegated destination, common ancestor,
memory/pids controller availability and effective write prerequisites. The
outside hosted-agent cgroup does not substitute for that observation. Access
checks do not prove placement; no placement write occurs. OBSERVED means a live
bound exchange, necessary topology observations, and verified owned cleanup—not
namespace isolation, runtime containment, product correctness, or qualification.
The receipt always retains boundary=NOT_RUN and qualification=NOT_RUN.

## Cleanup and failures

The exact owned unit alone is stopped. The entire user manager and sibling units
are never stopped. Stopped-state verification requires inactive/dead/MainPID=0,
plus the recorded exact unit cgroup's absence or bounded recursive empty-procs
and populated-zero inspection. Not-found **without previously captured ownership
and cgroup identity is insufficient**, including ambiguous launch failure.
No sibling product cgroups exist at this stage; future production teardown must
separately track/reap its actual children and every owned sibling cgroup.

Only after stopped-state/cgroup verification are the exact directory/socket
inode identities rechecked and removed. Cleanup uncertainty is hard STOP and
leaves those private paths for authorized inspection. The primary failure and
secondary cleanup failure remain separate finite records; nested child_cleanup
retains a launcher kill/reap/close failure without replacing the primary. Finite
probe_stage/observation_status fields distinguish UNKNOWN from NOT_RUN. The
parent-observed binding and terminal cleanup summaries are separate from the
service-reported observation and retain InvocationID, MainPID, peer credentials,
starttime, mount/cgroup namespace inode identities, control group and liveness/ack checks without nonce.
Socket-close failures are recorded separately and cannot skip owned-unit stop. Records include errno/exit/signal
when available. No arbitrary subprocess output is saved. Receipt parsing rejects
duplicate/extra keys, noncanonical JSON, booleans-as-integers, impossible status
claims, unsafe paths and over-size data. A missing observation is unknown, not a
negative proof; qualification stays NOT_RUN even for an OBSERVED topology.

## Validation status and next gate

Local tests exercise offline systemd/topology fixtures, real Unix socketpair
framing/peer credentials and timeout/close/oversize failures, real pidfd
liveness/exit, command bounds, ownership mismatch, descendant cleanup failure and
strict receipt claims. This executor has no usable cgroup mount and denies
AF_UNIX listener creation with EPERM (socketpair works). The orchestration tests
therefore explicitly fake listener/path plumbing as well as systemd/topology;
they **do not establish a native positive or native negative**.

Before any external launch: complete independent six-lens review (identity,
topology, boundedness, exact cleanup, scope/policy, tests/evidence), obtain the
separate temporary-service execution authorization, wait until the four-run CI
performance comparison window ends, and independently bind the provider source
and run. The implemented configuration gate below performs the previously missing
prelaunch drop-in/hook check automatically; unsupported real configuration stops
without asking the user to manually inspect or clear it. Actual systemd-255
compatibility, native timing/headroom and native cleanup still need authorized
validation. Namespace and production-boundary stages remain later gates.

## Read-only prelaunch configuration gate

Before creating the private socket or service, the gate pins the existing user
manager's bus unique owner, PID/starttime/pidfd, namespace and root identities.
Verified `/usr/bin/busctl call` uses typed JSON, no auto-activation and no
interactive authorization. Properties.Get supplies the manager's actual UnitPath;
caller HOME/XDG is not used to infer it. The initial compatibility profile is
systemd 255, including Ubuntu package suffixes. Unsupported versions fail closed.

A successful typed ListUnitsByPatterns(`*.service`) roster plus every returned
unit's Names array covers canonical and alias collisions. No failed command or
stderr string means absence. No GetUnit, ListUnitsByNames, LoadUnit, Set or Reload
call is used by the gate.

At every UnitPath, descriptor-based scans cover generic `service`, both
`forge-.service` / `forge-topology-.service` prefixes, and the exact selected name;
each includes `.d`, `.wants`, `.requires`, `.upholds`. Existing exact fragments or
aliases STOP. All candidates are considered without attempting precedence or
mask resolution. Missing/empty directories and safe regular comment-only files
can pass; all active directives, EnvironmentFile, Exec hooks, injected dependency
entries, masks, arbitrary symlinks, unreadable paths and unsupported syntax STOP.
The gate does not open environment-file targets or execute configuration.
Effective DropInPaths may be a subset of the fully inspected comment-only files
when systemd shadows duplicate filenames; unscanned effective paths still STOP.

Limits: 32 UnitPath entries, 64 service roster objects, 64 Names per object,
128 directory entries, 64 comment files, 16 KiB per file / 256 KiB cumulative,
64 KiB per bus reply. It checks a second full view and rechecks the manager environment before launch. Effective
postlaunch hooks, environment files, names, drop-ins, ExecStart argv, deadline /
kill properties and dependencies are independently checked through the same
manager. Only the verified owned transient fragment (and its newly created
transient directory if initially absent) is an expected filesystem delta. It is
not a blanket filename exclusion: the fragment must actually exist and its
v255 serializer grammar must contain the exact Description, quoted ExecStart
reset+assignment pair, unset names, type/output/kill settings and 15s/2s limits.
Empty/missing/partial or differently assigned fragments fail. Other differences
STOP and invoke exact cleanup.

The receipt separates configuration-gate evidence from service observations.
A clean fixture is an achievable `OBSERVED_CLEAN` subset, not a native runner
claim. Host/same-UID malicious ABA attacks remain excluded; pre/post comparisons
are ordinary-change detection, not race-free host authentication. Existing
policy is neither reset nor bypassed to obtain a passing result.

Tests cover the original 84 controls plus clean/comment-only/missing-path cases,
generic/prefix/exact policy injection, alias-only collisions, typed reply shapes,
read errors/links/masks/bounds, manager/file changes, effective postlaunch hooks,
and unchanged exact cleanup after gate or close failures. Bus JSON fixtures are
source-derived from systemd v255, not captured native results.

Reference semantics: [systemctl show](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html)
uses explicit properties and `--all` to retain empty fields; [systemd v255 source](https://github.com/systemd/systemd/blob/v255/src/systemctl/systemctl-show.c)
is relevant to the target Ubuntu manager. Host version compatibility still needs
a native check rather than extrapolation from a fixture.

Serializer references: [v255 execution serialization](https://github.com/systemd/systemd/blob/v255/src/core/dbus-execute.c), [quoted argv construction](https://github.com/systemd/systemd/blob/v255/src/core/unit.c), and [time serialization](https://github.com/systemd/systemd/blob/v255/src/core/dbus-util.c). Source-derived tests are not execution provenance or provider-bound evidence.
