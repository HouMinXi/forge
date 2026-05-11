# Design Iterations

How the bash assertion primitives library evolved across 12 design iterations.

## v1: Primitive Needs

Bash has no standard test framework. AI-generated shell tests were inconsistent  --  every session reinvented assertion patterns with different naming, different output formats, different error handling. A reusable assertion function library was the obvious fix.

## v2-v4: Self-Test + Ground Truth

The library itself needed verification. Static analysis (`bash -n`, `shellcheck`) caught syntax errors but not runtime bugs. Three assertion logic bugs were discovered through bug-injection ground truth verification: inject a known-bad condition -> verify the assertion reports FAIL -> revert -> verify PASS. Static analysis alone would have missed all three.

## v5-v8: Multi-Language Port Attempt

An attempt was made to port the same assertion primitives pattern to Python, Go, and C (~1300 LOC planned). After multi-AI peer review across 3 independent models, the approach was rejected:

- Python has `pytest` (industry standard, AI already fluent)
- Go has `testing.T` + `testify` (industry standard, AI already fluent)
- C has established test harnesses in target environments
- Custom assertion libraries in these languages add learning cost with no benefit

The shell primitives library exists because bash is the exception  --  it has no native test framework.

## v9-v12: Strategy Refinement

The SKILL.md was updated to direct each language to its standard test framework (pytest for Python, go test for Go) and keep shell primitives for bash testing only. Kernel C build verification was separated from functional testing (build runs pre-commit on primitives.sh; functional testing runs pre-merge on the project's existing harness).

Each design iteration started by challenging whether the artifact needs to exist, before listing implementation approaches  --  including do-nothing and do-less as named alternatives.
