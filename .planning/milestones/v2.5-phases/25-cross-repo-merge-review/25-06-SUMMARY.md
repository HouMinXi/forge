---
reconstructed: true
provenance: "git show f9bf18c (2026-06-20)"
---

# 25-06 Summary: Grouped Verdict Output

## One-liner

Added format_cross_repo_output() to group findings by repo with section headers.

## Accomplishments

- cross_repo.py: format_cross_repo_output() emits === [label] === header per repo followed by findings in [label] file:line -- description format
- Wired after receipt collection so per_repo_findings is fully populated before formatting
- Handles missing labels (header only, no body), preserves declaration order (primary first, then siblings)
- 57-line test suite added for formatting and edge cases

## Files Changed

- src/code_forge/cross_repo.py (+30 lines)
- tests/test_cross_repo.py (+57 lines)

## Commit

f9bf18c cross-repo: group verdict output by repo with section headers
