# Re-run the #33 performance threshold experiment

This is a manual experiment for the fixed `parse_diff_files` pilot, not a CI
gate. It asks whether repeated measurements can separate the same two
perturbations around the existing **+50%** threshold on another runner.
Nothing here changes the comparator or its threshold.

## Run on a separate Linux runner

Use a dedicated or otherwise isolated Linux runner with CPU affinity available.
Avoid other jobs on the measured CPU. A five-second utilization sample is
recorded, but does not certify isolation. Note whether the host was exclusive or
shared; report results from a shared host as such. For the closest replay, use
CPython 3.12.14. The script records the actual interpreter and machine
signature, so a version difference is visible. Create a new private output
directory for each attempted run.

From the Forge checkout containing this draft and the pinned commit:

```bash
set -euo pipefail
umask 077
repo=$(git rev-parse --show-toplevel)
subject=0e87b3c61c0cd1e1a5408b76c030044dc20b7259
run=$(mktemp -d)
mkdir "$run/fixed"
git --no-replace-objects -C "$repo" archive "$subject" | tar -x -C "$run/fixed"
python3.12 -m venv "$run/venv"
"$run/venv/bin/python" -m pip install "$run/fixed"
"$run/venv/bin/python" -m pip freeze > "$run/dependencies.txt"
(
  cd "$run"
  "$run/venv/bin/python" "$repo/docs/eval/perf-threshold-replay/replay.py"
)
printf 'Results: %s\n' "$run"
```

Dependency installation is setup, outside the measurements. If using a
prepared offline environment, record its dependency versions instead. If
the pinned commit isn't available locally, obtain it through the normal
repository checkout before running. Don't substitute current `main`.
If the run exits early, keep the error and any partial records; don't call
that a measured pass or quietly replace it with another run.

## What counts

- Each of the six non-overlapping groups contains nine blocks. Each block
  measures a fresh clean reference, a second clean control, and the two fixed
  changes, in balanced order. Each CLI measurement is a seven-call median in
  a new Python process; the block divides each measurement by its fresh
  reference.
- The lower change adds a header-prefix and length check per parser line; the
  upper change adds the same header-prefix check and a second prefix check.
  No amount is fitted to the new machine. The script checks that the imported
  files and the fixed workload match, then traces the parser separately
  to confirm the added checks run and output is unchanged.
- A group is **within** when at least eight ratios are at or below 1.5,
  **slower** when at least eight are strictly above 1.5, and **uncertain**
  otherwise. An uncertain group is not a pass. For this experiment to meet
  its criterion, all six groups must report clean and lower within, upper
  slower. A clean exit means the script finished, not that this criterion
  passed; read `summary.json`.
- Send `design.json`, `runner-preflight.json`, `summary.json`, all six
  `window-*.json`, and the dependency versions. Include any errors and the
  runner's isolation conditions. Do not cherry-pick groups or claim the
  processes establish statistical independence.

The earlier shared-runner run recorded clean **6/6 within**, lower **2 within /
4 uncertain**, and upper **3 slower / 3 uncertain**. This is a reference
result, not an expected output fixture. The arm names are inherited from the
prior CPU calibration; this launcher measures wall time and does not
re-estimate the CPU bands. A successful independent replay would qualify
only this fixed workload and those runner conditions; it would not qualify a
generic performance gate or close #33.
