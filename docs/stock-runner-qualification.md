# Stock Ubuntu 26.04 and Python 3.14 qualification

The target contract is CPython 3.14 with pytest 9.2.0 both outside and inside
mutation sandboxes. This candidate does not change the minimum Python version
for installing Forge or discard previously qualified reporter pairs.

## Status and scope

The independent `linux-stock-qualification.yml` workflow is manual-only and
requires the full reviewed source commit. It has **not** been run on a hosted
runner. It does not replace the required `linux-tests` check or authorize a
claim that the full migration has passed.

The automatic Linux workflow remains on Ubuntu 24.04 with its suite and system
sandbox interpreter on Python 3.12. It additionally provisions Python 3.14 with
pytest 9.2.0 so the new mandatory qualification cases execute there too. Their
absence must fail the existing strict receipt policy, rather than become an
accepted skip. Runner counts, security steps and skip policy are unchanged.

The candidate retains all original collection, ownership, cgroup, local,
service-free shard and final exact-node coverage phases. Its temporary copy of
the current Linux workflow is checked structurally against the original: only
the declared runner, runtime, source-binding and prerequisite changes may
differ. It must be retired after an evidenced promotion rather than maintained
as an indefinite second CI implementation.

The existing runtime/collection skip policy is unchanged. In particular, real
isolation and patch-corpus tests cannot turn into accepted skips. The previous
CPython 3.12/pytest 9.2.0 and 3.12/9.1.1 reporter cases remain provisioned as
compatibility controls; they are not the interpreter used by the candidate
suite or mutation payloads.

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

1. Review the exact candidate commit and its complete workflow delta.
2. Run the manual stock-runner workflow only when separately authorized.
3. Require all original coverage phases and aggregate receipts to pass for that
   exact source, with no added skip allowance or `continue-on-error` fallback.
4. Validate the installed-wheel workflow on the new contract as well; a reporter
   qualification or source-tree test run does not prove installed-wheel behavior.
5. Promote the reviewed runner/runtime changes to the required Linux workflow,
   retaining the qualified compatibility controls and publication checks. Retire
   the temporary workflow, then verify the new exact-head required checks.

Do not publish this candidate as a routine PR without considering the existing
workflow: the unchanged required Linux workflow still runs its old AppArmor
sysctl command. A manual-only candidate does not neutralize other workflows
triggered by a push or PR. Publication and hosted execution are separate from
local preparation and require the applicable authorization.

## Sources

- [GitHub Ubuntu 26.04 GA announcement](https://github.com/actions/runner-images/issues/14747)
- [Current Ubuntu 26.04 runner inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2604-Readme.md)
- [Ubuntu 26.04 AppArmor package files](https://packages.ubuntu.com/resolute/amd64/apparmor/filelist)
- [Ubuntu's restrictive bubblewrap profile design](https://documentation.ubuntu.com/release-notes/25.04/#apparmor-profile-for-bwrap)
- [Upstream bubblewrap security model](https://github.com/containers/bubblewrap#sandbox-security)
