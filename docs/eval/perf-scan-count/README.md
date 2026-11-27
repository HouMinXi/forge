# Manual regression check: repeated diff traversal

This is a narrow companion to the [wall-time replay](../perf-threshold-replay/).
The check counts how many lines Forge's actual `parse_diff_files` visits
on a fixed workload. It has no timing threshold, production hook, CI
integration, or general performance claim.

From a Forge checkout with its Python runtime dependencies installed:

```bash
python docs/eval/perf-scan-count/probe.py --source . --check
```

The check reports the parser digest and measurements for 20- and 40-file
inputs. It exits 0 when both stay within one observed traversal and 1 when
a valid measurement violates that expectation. An import, fixture, or
parser-output error is not a measured regression or a pass. The script reads
the supplied checkout, not an installed Forge package, and requires the
same workload from the pinned `scripts/perf_regression.py` blob. Use it on
source you trust; it executes the parser locally.

## Qualify the check itself

The control run takes a frozen source tree and modifies only a temporary
copy. From the checkout with this script and the pinned commit available:

```bash
set -euo pipefail
subject=2324abcd9557705bcc4ec69f5aae903e354aeb3a
source=$(mktemp -d)
git --no-replace-objects archive --format=tar "$subject" src scripts/perf_regression.py |
  tar -xf - -C "$source"
python docs/eval/perf-scan-count/probe.py --source "$source"
```

| Control | 20 files | 40 files |
| --- | ---: | ---: |
| One pass, stock parser | 4,060 | 8,120 |
| Second pass over the same list | 8,120 | 16,240 |
| Restored parser | 4,060 | 8,120 |
| Added line predicate, still one pass | 4,060 | 8,120 |

The second pass must fail the count while preserving exact parser output.
Counting only `splitlines()` would miss it: the control calls that method
once and iterates the same result twice. The line-predicate control documents
a real blind spot: more work per line is not another traversal. The observer
sees iterations through the tracked list, not every possible operation on
the input. If a legitimate refactor changes how the parser consumes input,
revisit the measurement instead of treating a changed count as proof it
got slower. This does not satisfy the full #33 performance criterion.
