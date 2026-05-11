# Ground Truth Verification

Test infrastructure itself must be verified via ground truth bug injection, not just static analysis.

## The Principle

Assertion libraries, test frameworks, and smoke-test primitives must pass a bidirectional test: inject a known bug -> verify the infrastructure reports FAIL -> revert the bug -> verify the infrastructure reports PASS.

Static analysis (`bash -n`, `shellcheck`) is insufficient because it cannot detect faulty assumptions in the assertion logic itself.

## Evidence

The bash smoke-test primitives library passed all syntax checks, but ground truth verification revealed three critical bugs:

1. `/proc` glob scaled with system PID count, causing zombie detection timeouts under load
2. Bash auto-reaps direct children, making zombie detection structurally impossible without a non-bash intermediate process
3. `jq -e` output polluted assertion result strings with JSON data mixed into PASS/FAIL lines

None of these were caught by shellcheck or `bash -n`.

## Recommendation

Any new test infrastructure, assertion library, or smoke-test framework must include ground truth verification: write buggy fixture -> fail -> fixed fixture -> pass. Plan for this as an explicit validation phase before release.
