---
phase: 23
slug: daemon-state
created: 2026-06-14
---

# Phase 23: Daemon State - Validation Strategy

## Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x |
| Config file | pyproject.toml [tool.pytest.ini_options] |
| Quick run command | `python3 -m pytest tests/test_daemon_state.py tests/test_daemon_state_drift.py -x` |
| Full suite command | `python3 -m pytest -x` |

## Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STATE-01a | AxisRunner Protocol conformance | unit | `pytest tests/test_daemon_state.py::test_is_advisory -x` | Wave 0 |
| STATE-01b | Empty diff returns [] | unit | `pytest tests/test_daemon_state.py::test_empty_diff -x` | Wave 0 |
| STATE-01c | Heuristic fallback (no gate.yaml) | unit | `pytest tests/test_daemon_state.py::test_heuristic_fallback -x` | Wave 0 |
| STATE-01d | Two-step LLM call (Q1 -> grep -> Q2Q3) | unit | `pytest tests/test_daemon_state.py::test_two_step_llm -x` | Wave 0 |
| STATE-01e | Static conflict rule matching | unit | `pytest tests/test_daemon_state.py::test_static_rules -x` | Wave 0 |
| STATE-01f | RuntimeRunner.last_surfaces storage | unit | `pytest tests/test_runtime.py::test_last_surfaces_stored -x` | Wave 0 |
| STATE-01g | gate.yaml daemon_state validation | unit | `pytest tests/test_gate_check.py::test_daemon_state_validation -x` | Wave 0 |
| STATE-01h | grep sanitization (D-08) | unit | `pytest tests/test_daemon_state.py::test_grep_sanitization -x` | Wave 0 |
| STATE-01i | SKILL.md drift test | unit | `pytest tests/test_daemon_state_drift.py -x` | Wave 0 |
| STATE-01j | LLM failure -> SKIPPED finding | unit | `pytest tests/test_daemon_state.py::test_llm_failure_skipped -x` | Wave 0 |
| STATE-01k | Ordering: DaemonState after Runtime | unit | `pytest tests/test_daemon_state.py::test_ordering -x` | Wave 0 |
| STATE-01l | E8 eval corpus entry | integration | `pytest tests/test_eval_runner.py -x -k E8` | Wave 0 |

## Sampling Rate

- **Per task commit:** `python3 -m pytest tests/test_daemon_state.py tests/test_daemon_state_drift.py -x`
- **Per wave merge:** `python3 -m pytest -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

## Wave 0 Gaps

- [ ] `tests/test_daemon_state.py` -- covers STATE-01a through STATE-01k
- [ ] `tests/test_daemon_state_drift.py` -- covers STATE-01i
- [ ] New test in `tests/test_runtime.py` -- covers STATE-01f (last_surfaces stored)
- [ ] New test in `tests/test_gate_check.py` -- covers STATE-01g (daemon_state validation)
- [ ] `tests/eval/corpus/diffs/E8-killswitch-mark-conflict.diff` -- covers STATE-01l
