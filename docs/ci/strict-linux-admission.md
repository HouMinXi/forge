# Strict Linux observations: preliminary admission evidence

`strict-linux-admission.yml` is a separate **static observation** job for issue
#31. It runs for same-repository pull requests targeting `main` only when the A
workflow, collector script or its test file changes. Fork pull requests are
skipped; documentation-only changes do not trigger it. Manual dispatch remains a
fallback once the workflow is present on the default branch. This job does not
change the existing Linux test lane,
start a service, run a namespace probe, or execute the product/test suite. It does
not install dependencies, change AppArmor, adjust cgroup ownership, or run product
code as root.

The stdlib collector records bounded kernel/cgroup/tool/policy metadata and makes
one fixed, read-only `systemctl --system show` query for the existing ordinary
user manager. It first rejects a symlinked, non-root-owned or writable systemctl
path/ancestor and a set-id executable. The child receives a closed environment,
not inherited loader, D-Bus or pager overrides. Its stdout has a byte cap and its
first stderr byte is a refusal; a deadline and separate process group bound the
query. Cleanup signals that group even if the leader has exited and reaps the
direct child. That is not certification of arbitrary descendant quiescence.

## Meaning of the receipt

- `OBSERVED`: the required static observations are available. **Not admitted,
  qualified, or permission to run the suite.** A writable destination does not
  prove a permitted source-to-destination move or a supported live topology.
- `STOP`: one or more listed observations are missing, ambiguous or unsuitable
  for the current caller. Null means unknown, not zero, false, absence, or a
  successful check. A hosted agent outside the delegated tree can correctly STOP
  on common-ancestor access even when a future manager-child launch is possible.
- `boundary` and `qualification` are always `NOT_RUN`.
- Run/workflow/source values are explicitly `UNVERIFIED_DECLARED_CONTEXT`; the
  logical job name is not an authenticated GitHub API job ID. PR checkout and
  declared source SHA both select the actual `pull_request.head.sha`, not the
  synthetic merge SHA. Manual dispatch uses `github.sha` for both. The workflow
  ref/SHA remains a distinct identity (which can describe a PR merge-ref workflow)
  and is never relabeled as the checked-out head. This collector does not
  independently authenticate the whole source tree or the provider's job metadata. The collector's own bytes
  have a SHA-256. A canonical JSON receipt is not a signed attestation.
- Bwrap metadata is observed without executing its version or any payload;
  `version` remains null. Metadata alone is not executable/runtime qualification.

Receipts use a closed, bounded, canonical JSON schema. Unknown keys, duplicate
keys, invalid finite types, inconsistent derived status, and qualification claims
are rejected. Only finite metadata labels are retained; neither arbitrary
subprocess stderr nor the environment is dumped. Output creation is exclusive,
so a later failure cannot overwrite an earlier observation. Expected CLI errors
use a fixed message without raw paths or traceback. The workflow preserves STOP
receipts with `always()`; failure before receipt creation is still a failed job,
not a successful observation.

## What remains before strict qualification

A separately reviewed live verifier must authenticate source/dependencies/run
identity, bind the existing ordinary-user manager's actual child and private
control channel, recheck the source/destination/common ancestor, and execute the
unchanged production boundary under existing host policy. Unknown topology,
AppArmor applicability, ancestor identity/TOCTOU or permissions cannot be waived
by this receipt. No security/permission repair or blind rerun is authorized.

The full qualification must then execute the exact current mandatory roster and
unchanged complete phases, reject mandatory skips/xfails, reconcile phase/JUnit
and subtest records, record startup versus complete cleanup and phase timings,
and pass final source/policy and exact-owned-cleanup checks in the same qualifying
job. Successful measured headroom and actual CI speed improvements are separate
from an observed setup, a failed duration, or a prediction. This preliminary job
alone does not close #31.
