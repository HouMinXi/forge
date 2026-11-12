# Stock Ubuntu 26.04 and Python 3.14 qualification

The target contract is CPython 3.14 with pytest 9.2.0 both outside and inside
mutation sandboxes. This candidate does not change the minimum Python version
for installing Forge or discard previously qualified reporter pairs.

## Status and scope

This promotion candidate puts the stock Ubuntu 26.04 / CPython 3.14 jobs in
`linux-tests.yml` itself. It removes the temporary duplicated workflow and
pilot routing switch. The historical final check name remains `linux-tests`;
its seven-runner graph still consists of plan, process contracts, four
service-free shards and the final exact-node aggregate. No automatic or manual
route changes AppArmor settings or falls back to the old sysctl lane.

The original collection, ownership, cgroup, local, service-free shard and
aggregate receipt gates remain. A prerequisite probe alone cannot satisfy
them. Failed, cancelled, skipped or missing required phases must fail the
aggregate rather than produce a green final check. The workflow name and job
names do not themselves establish server-side branch protection.

The preceding composed pilot at
`b3df2cfa0f80efca16c6da669d3df25370257176` passed
[hosted run 43161468460, attempt 1](https://github.com/HouMinXi/forge/actions/runs/43161468460)
on seven stock runners. Its downloaded public receipts passed the original
aggregate verifier, including all seven real bind-mount cases and 161 audit
cleanup cases. That result covers that exact commit only. It does not qualify
the later #468 diagnostic additions, this workflow promotion, or installed
wheels on Python 3.14. Those changes require their own applicable validation.

The existing runtime/collection skip policy is unchanged. In particular, real
isolation and patch-corpus tests cannot turn into accepted skips. The previous
CPython 3.12/pytest 9.2.0 and 3.12/9.1.1 reporter cases remain provisioned as
compatibility controls; they are not the interpreter used by the candidate
suite or mutation payloads.

## Source binding for ordinary and manual runs

Every Linux job explicitly checks out `github.sha` and validates the request
before package setup. Plan and execution receipts use that same source SHA.
The event modes are separate, fail-closed contracts:

- A pull request targeting main tests the event's synthetic merge commit,
  not a moving branch or the PR head alone. A new base produces a new tested
  merge SHA and requires new evidence.
- A push to main tests that event's full SHA. The checkout must match it.
- A manual run requires a full `expected_source_sha` input equal to both the
  event SHA and checked-out HEAD. Missing or mismatched input fails; it never
  selects an automatic fallback.

Unsupported events and wrong automatic refs are rejected. There is no
`pull_request_target` route, inherited secret or new write permission.
Manual and ordinary concurrency groups remain distinct so an explicit
qualification does not cancel unrelated ordinary CI.

The already registered `linux-tests.yml` entry can run a reviewed candidate
ref without merging it merely to register a workflow. Its only manual input
is `expected_source_sha`; the retired `stock_runner_pilot` switch is not used.
Local routing and source-binding tests are not hosted execution evidence.

## Installed-wheel compatibility controls

The installed-wheel workflow retains Ubuntu 24.04 / Python 3.12 and adds
Ubuntu 26.04 / Python 3.14 as separate matrix entries. Each entry builds and
installs its own wheel offline, outside the checkout, and executes the same
frozen 22-control roster with no skips, errors or failures. It preserves the
source/archive, wheel, imported-module and RECORD checks and publishes only
the original verified wheel and fixed public summary. Artifacts distinguish
the fixed runtime label and run attempt; one runtime cannot overwrite the
other's evidence.

A separate fail-closed aggregate retains the historical `installed-wheel`
check name and requires the matrix result to be success. This uses three
packaging runners in addition to the seven Linux runners; lint and CodeQL
are separate jobs. A one-shot seven-runner pilot authorization does not cover
these additional executions.

These are partial packaging/runtime controls, not complete installed native
qualification. The #468 installed-CLI capture tools remain separate diagnostics;
their original `unshare` route is not qualified by the stock `bwrap` result.
No diagnostic tool is wired into CI and no native acceptance claim is added.

## Why this route

Ubuntu 26.04 is a generally available GitHub runner image. Its distro AppArmor
package supplies `bwrap-userns-restrict`. The profile permits bubblewrap to set
up namespaces, then stacks a capability-denying profile onto the payload.
The candidate requires the existing restriction to remain enabled, the child
to report the enforced stock profile stack, no effective/permitted/ambient
capabilities, no-new-privileges, and distinct user/mount/PID/network/IPC/UTS
namespaces. It fails if those facts cannot be observed. It never repairs the
host by changing sysctls, profiles, capabilities, setuid bits, or container
security settings.

Stock image documentation is a reason to test this route, not proof that the
current image supports every Forge operation. The real cgroup and isolation
phases remain necessary. A passing prerequisite probe is not full qualification.

## Runtime alternatives considered

- **Retain a 3.12 sandbox on a 26.04 host:** setup-python's toolcache is outside
  the paths mounted by the current isolation supervisor. A different command
  path alone is insufficient. This would need deliberate read-only runtime
  binds, relocated interpreter/dynamic-library validation, and the same real
  isolation controls. It also conflicts with the chosen all-3.14 target.
- **3.14 with pytest 9.1.0:** already qualified and useful as an independent
  reporter control. Downgrading the main dev pin would not preserve the
  existing 9.2 contract, so it is not the chosen migration.
- **3.14 with pytest 9.2.0:** preserves the current dev dependency pin. The new
  pair uses the already reviewed pytest 9.2 source hashes, but hashes alone are
  insufficient. Existing real runtime, snapshot and abort-path cases must pass
  with the actual 3.14 interpreter before the pair is accepted.

Local CPython 3.14.10/pytest 9.2.0 ran all 27 existing parameterized qualification
cases for the new pair without skips or failures. The previously qualified
3.14/9.1.0 pair also passed all 27 cases as a control. These results do not prove
Ubuntu's system Python build, stock AppArmor behavior, or complete CI coverage.

## Explicit JSON depth compatibility change

Python 3.14 accepts some deeply nested JSON that previously reached Python
3.12's decoder recursion limit. Two existing contracts relied on that refusal:
MCP's optional SARIF projection and the L0 ShellCheck JSON parser. They now
share an explicit **512-container** whole-input limit, with the root container
counted as level one. This is a newly specified tightening, not a claim that
512 was the old interpreter's fixed threshold. Previously accepted unknown
metadata deeper than 512 may now be rejected by these two readers.

The check is iterative and runs before decoding. Delimiters inside strings,
including escaped quotes and backslashes, do not count. Empty containers do
count, and sibling width does not accumulate depth. The standard JSON decoder
still validates syntax and values. A depth failure follows the existing
`RecursionError` route: MCP leaves the projection unknown and keeps raw output;
L0 records an unexpected-parser failure and cannot turn it into a clean run.

MCP's separate 1,048,576-character cutoff and its 64-container projected
findings/audit limits are unchanged. The existing positive control for 300
levels in unprojected metadata remains valid. Boundary tests require whole
input depth 512 to be accepted and 513 rejected on both Python 3.12 and 3.14,
alongside the original projection 64/65 controls and malformed-input cases.

No other JSON reader acquires this limit. In particular, MCP's separate report
classification reader still reparses raw output under its existing contract.
This is not a claim that all MCP or all L0 JSON decoding is depth-bounded.

## Promotion gates

1. Review the exact composed candidate and complete workflow, source-binding,
   identity and publication delta. Retain all applicable compatibility controls.
2. Validate both local runtimes and the actual offline installed-wheel harness,
   binding every result to its source commit and distinguishing it from hosted
   evidence. Source-tree contract tests are not installed-wheel execution.
3. When separately authorized, require all original stock coverage phases and
   aggregate receipts to pass on the exact new source, with no added skip
   allowance or `continue-on-error` fallback. Qualify hosted Python 3.14 wheels
   separately and retain the Python 3.12 compatibility control.
4. Before opening the promotion PR, verify that its ordinary route is already
   the stock implementation and cannot run the retired security-setting steps.
   A draft PR still triggers configured automatic workflows.
5. Verify the actual PR test-merge SHA, `linux-tests` and `installed-wheel`
   check identities, all applicable checks and any server-side required
   contexts. Earlier pilot or local results cannot replace those new checks.

Publication, hosted execution, branch-rule changes and merging are separate
from local preparation and require their applicable authorization. Older
experimental branches may still carry the AppArmor-changing workflow; this
candidate does not modify their runs or grant permission to rerun them.

## Sources

- [GitHub Ubuntu 26.04 GA announcement](https://github.com/actions/runner-images/issues/14747)
- [Current Ubuntu 26.04 runner inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2604-Readme.md)
- [Ubuntu 26.04 AppArmor package files](https://packages.ubuntu.com/resolute/amd64/apparmor/filelist)
- [Ubuntu's restrictive bubblewrap profile design](https://documentation.ubuntu.com/release-notes/25.04/#apparmor-profile-for-bwrap)
- [Upstream bubblewrap security model](https://github.com/containers/bubblewrap#sandbox-security)
