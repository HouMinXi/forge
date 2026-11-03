# Existing-user-service topology probe (local review candidate)

This is **not** production admission or strict Linux qualification. It observes
whether an existing ordinary-user manager can start a short-lived service whose
own identity/topology satisfies necessary cgroup prerequisites. It never imports
Forge tests/capability modules, launches bwrap or a product payload, creates a
product cgroup, enables controllers, requests delegation, changes host ownership,
loads profiles, installs packages, sets sysctls, enables linger or starts a missing
manager. The dedicated workflow below is manual-only.

## Unresolved dependency boundary

This probe still lacks a proven prelaunch dependency/effect closure for an
arbitrary existing manager. Its direct service configuration checks do not prove
that allowed base targets/slices, reverse relationships, handlers or cleanup-time
transactions have no other effects. An active base, zero jobs or a bounded
snapshot does not establish that proof. The execution/publication changes below
address accidental invocation and diagnostic disclosure, not this defect or #31
qualification. Keep this work draft and do not treat explicit launch consent as
technical admission.

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
never enter receipts, logs, artifacts, files or exception text. Values are read
only transiently inside the bounded process for name extraction/digest, not
exposed to the model or persisted. This detects ordinary concurrent changes, not malicious
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

## Dedicated workflow entry point

`strict-linux-user-service.yml` has only `workflow_dispatch`; pull requests never
schedule this native workflow. Offline collector/wrapper/publication tests stay
in the ordinary test suite. This does not make all PR CI side-effect-free:
`linux-tests.yml` retains `systemd-run --user --scope` and its existing
privileged setup/security changes despite its separate diagnostic-publication
repair. Those are separate from this
transient-service workflow and have not been executed by this local repair.
A manual run defaults to no service authorization:
`allow_transient_service=false` produces STOP without loading the collector or
running any tool. Native observation requires separate one-run authorization,
explicit opt-in, and the exact reviewed `expected_source_sha` matching the
actual dispatched checkout on its first run attempt. Workflow reruns cannot reuse
the earlier opt-in; another native observation needs a fresh explicitly authorized
dispatch. Wrong events, missing opt-in, unsupported values or
missing/mismatching source stay nonzero; these declarations are not authenticated
proof of who authorized a run. The unresolved dependency boundary still needs
its own accepted contract before an actual launch can be called safe.

The wrapper CLI also requires `--allow-transient-service` and
`--expected-source-sha`. Calling it with only `--output-dir` no longer starts a
service. Programmatic calls default to no authorization. The collector's
existing explicit flag remains required. These guards prevent accidental
execution; they are not an access-control barrier against malicious source or
an actor who can directly invoke arbitrary processes as the same user.

The workflow uses Ubuntu 24.04, a read-only contents token, no workflow-injected
secrets, pinned checkout with credentials not persisted, and a three-minute job
watchdog. Concurrency does not automatically cancel an older run in cleanup.
Publication of a PR no longer schedules this service job. Manual dispatch or
service execution is not performed by the offline tests.

The stdlib wrapper binds the declared head and four execution/publication sources
(collector, wrapper, workflow, public-summary helper) to regular Git tree blobs
before loading the collector. It uses only fixed Git `rev-parse`/`ls-tree`, not
status/diff, external filters or textconv. System/global Git configuration and
fsmonitor are disabled for its child commands. The wrapper compiles exactly
hash-verified captured collector bytes into a non-main module; it does not load
an unverified `.pyc` cache. Both Git blob IDs and SHA256 content hashes are
checked. SHA1 is used only to reproduce this repository's Git object IDs.
Unrelated working-tree files are not claimed clean.

Collector stdout is capped at 16 KiB; stderr is discarded with a saturated byte
count. Overflow does not immediately kill the collector while it may be cleaning
up. A 90-second last-resort wrapper deadline signals only its direct child
through a pidfd, with two-second TERM/KILL reap windows. Timeout, invalid receipt,
wrong UID/context, source changes or reap uncertainty remain STOP with cleanup
UNKNOWN unless a valid matching terminal receipt supports its cleanup claim.
The service timer alone never proves cleanup.

### Public evidence boundary

Only `forge-user-service-public/summary.json` is eligible for upload. It contains
fixed schema/mode labels, OBSERVED/STOP/UNKNOWN, a finite reason, boundary and
qualification NOT_RUN, and raw_evidence NOT_PUBLISHED. No original key/value is
copied into public output: no unit name, path, PID, context, source hash,
dependency edge, raw diagnostic, arbitrary error text, environment or journal.
No count field is published by this service-specific projection.

The projection reads only the wrapper status, with a 16-KiB input cap, no-follow
regular-file checks, duplicate-key/invalid-number rejection and before/after
metadata checks. It does not import a collector, inspect host state or run
commands. OBSERVED additionally requires wrapper success, a valid terminal
receipt, observed cleanup and the source recheck. A flushed OBSERVED status
followed by a nonzero exit or kill is publicly STOP. Missing, malformed or
unsupported status is UNKNOWN and fails the step, even if the wrapper exited 0.
These labels summarize declared evidence, not authenticated qualification.

A fresh private directory is created within the observation shell and is never
exported to subsequent steps. Wrapper stdout/stderr, helper stderr and the
shell's own signal/redirection diagnostics remain there. Tracing is disabled;
initial setup errors are suppressed before a private stderr file is available.
Only a literal publication-ready flag crosses steps. The exact relative upload
path has no raw fallback or wildcard. Empty, missing, linked, nonregular or
oversized summary output cannot set that flag, including interpreter failures
that happen to return the same exit code as a normal UNKNOWN summary.

The original wrapper failure code wins over projection/publication failures.
If the wrapper succeeds but safe summary production fails, the step fails.
STOP is never converted to a green run. Cancellation, runner loss or hard timeout
may prevent any summary; absence is not success or proof of cleanup. The public
summary omits forensic detail deliberately. Temporary raw files are not a
supported private archive, and this change does not retract prior artifacts.

This boundary assumes the reviewed workflow/helper and trusted runner code are
what execute. It is not confidentiality against malicious workflow code, a
compromised runner or a same-user actor. Provider run/job/source and actual
workflow-definition identity still require external reconciliation; checked-out
source hashes do not authenticate the executed workflow. Native service route,
serializer compatibility, timing, dependency effects and cleanup remain
unqualified by offline fixtures or ordinary CI.


### Configuration I/O diagnostics

Configuration I/O failures use a closed set of operation reasons (manager/caller
mount and cgroup namespace reads, manager process/start-time/pidfd/root checks,
caller root, verified bus tool/transport, directory walk and entry
stat/open/metadata/list/file-read). They preserve the original errno and internal
exception cause/cleanup detail while replacing the exception type with a finite
probe error. No OS error text, path, configuration bytes or environment values
are serialized. Existing semantic STOP predicates and missing-entry handling are
unchanged. The first native run's generic configuration EACCES cannot be
retroactively assigned a more specific cause; only a newly collected result can
supply the operation label.

Only directly propagated `OSError` instances receive the finer label. Errors
already classified by the bounded command helper retain their original reason,
errno, cause and cleanup detail, including an existing generic `io_error`.
Operation-boundary fault injection tests are not native syscall observations.
