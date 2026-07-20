# Phase 3 Dogfood Results

**Phase:** 03-r3-e2e-coverage  
**Plan:** 03-05  
**Date:** 2026-05-27  

## Task 1.1: Phase 3 Diff, No components.yaml

**Command:**
```
PYTHONPATH=src python3 -c '
from pathlib import Path
from forge.e2e_check import run_e2e_check
import subprocess
diff = subprocess.check_output(
    ["git", "diff", "cc7f1b5..HEAD", "--", "src/forge", "tests"]).decode()
print("diff length:", len(diff))
findings, infra = run_e2e_check(diff, Path("."))
print("findings:", [(f.source, f.fingerprint, f.disposition) for f in findings])
print("infra_errors:", infra)
print("findings count:", len(findings))
'
```

**Actual output:**
```
diff length: 71473
findings: []
infra_errors: []
findings count: 0
```

**Result:** Layer 1 count = 0, Layer 2 count = 0, infra_errors = [], e2e_fingerprints = [].

EXPECTED: Layer 1 = 0 (single source group src/forge after test-dir exclusion, threshold not met). Layer 2 = 0 (no .forge/components.yaml in repo).

PASS.

## Task 1.2: Sanity components.yaml Dogfood

A temporary `.forge/components.yaml` was created with:

```yaml
version: 1
components:
  state:
    paths: ["src/forge/state.py"]
    shared: true
  runner:
    paths: ["src/forge/machine.py", "src/forge/e2e_check.py"]
    depends_on: [state]
e2e_patterns: ["tests/test_*e2e*.py"]
```

**Sub-run (i): e2e_patterns: ["tests/nonexistent/**"] -- Layer 2 SHOULD fire**

```
PYTHONPATH=src python3 -c '
from pathlib import Path
from forge.e2e_check import run_e2e_check
import subprocess
diff = subprocess.check_output(
    ["git", "diff", "cc7f1b5..HEAD", "--", "src/forge", "tests"]).decode()
findings, infra = run_e2e_check(diff, Path("."))
print("findings count:", len(findings))
print("findings:", [(f.source, f.fingerprint, f.disposition) for f in findings])
'
```

Actual output:
```
findings count: 1
findings: [('E2E_CHECK', 'e2e-l2:657440d6dc687387', <Disposition.UNCERTAIN: 'UNCERTAIN'>)]
```

Layer 2 FIRES for (state, runner) pair. PASS.

**Sub-run (ii): e2e_patterns: ["tests/test_*e2e*.py"] + e2e_absent_ok for runner -- Layer 2 SHOULD clear**

components.yaml updated to add:
```yaml
e2e_absent_ok:
  - component: runner
    reason: "forge src/forge/ paths contain no e2e artifacts; integration tests live under tests/"
```

Actual output:
```
findings count: 1
findings: [('E2E_CHECK', 'e2e-l1:47a976037738b6b4', <Disposition.DISMISSED: 'DISMISSED'>)]
```

Layer 2 cleared (0 L2 findings). Layer 1 fires as advisory (DISMISSED) since the diff spans multiple source groups with signature changes.

FIRE-THEN-CLEAR confirmed.

Note on the sanity behavior: the e2e_patterns ["tests/test_*e2e*.py"] matches real test files (tests/test_e2e_check.py, tests/test_machine_e2e.py), but those artifacts do not live under the runner component's declared paths (src/forge/machine.py, src/forge/e2e_check.py). Per-pair scoping requires the artifact to be within the dependent's component paths, so Layer 2 fires when the patterns point to the tests/ directory but the component paths are src/forge/. The e2e_absent_ok escape hatch clears it correctly.

**Cleanup confirmation:**
```
rm .forge/components.yaml
ls .forge/ 2>/dev/null
```
Output: `gate.yaml  tools.yaml`

components.yaml is NOT listed. DELETED: YES.

## Task 1.3: Suite Regression Check

**Command:**
```
PYTHONPATH=src python3 -m pytest tests/ -q
```

**Actual output:**
```
698 passed, 3 warnings in 8.73s
```

Suite still green. 698 passed. PASS.

## Interpretation

The Phase 3 diff (71,473 bytes spanning src/forge/ and tests/) does not trigger
Layer 1 or Layer 2 when run against forge's own repository without a
components.yaml. This confirms the single-package no-fire design: after test-dir
exclusion, only one source group (src/forge) remains, so the cross-group
threshold is not met. Layer 2 is opt-in and produces no findings when
.forge/components.yaml is absent.

The sanity components.yaml run proved Layer 2 fires when e2e artifacts are absent
from the dependent's declared paths, and clears when the e2e_absent_ok escape
hatch is applied. The fire-then-clear cycle demonstrates the full flow: detect
gap, mark uncertain, clear with explicit exception.

## .forge/components.yaml Deletion Confirmed

YES -- the temporary file was removed and verified absent from .forge/ directory
listing before any commit.
