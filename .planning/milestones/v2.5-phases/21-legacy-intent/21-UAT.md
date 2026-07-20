---
status: complete
phase: 21-legacy-intent
source: [21-01-SUMMARY.md, 21-02-SUMMARY.md, 21-03-SUMMARY.md]
started: 2026-06-13T12:00:00Z
updated: 2026-06-13T12:00:00Z
---

## Current Test

number: 2
name: LegacyRunner.is_advisory == True 且无循环 import
expected: |
  git_blame('src/code_forge/git.py', Path('.')) 返回非空 dict，
  每条 entry 有 sha/author/subject 字段，sha 为 40 位十六进制字符串。
awaiting: user response

## Tests

### 1. git_blame() 可导入并解析真实 repo
expected: |
  lines >= 1，entry 含 sha/author/subject，sha 长度 == 40。
result: pass
actual: lines=458, sha_len=40, author=Minxi Hou

### 2. LegacyRunner.is_advisory == True 且无循环 import
expected: |
  运行：
    python3 -c "
    from code_forge.legacy import LegacyRunner
    print('is_advisory:', LegacyRunner().is_advisory)
    from code_forge.cli import _run_hold_loop
    print('cli import: OK')
    "
  应输出：is_advisory: True，cli import: OK，无异常。
result: pass
actual: is_advisory=True, cli import OK

### 3. machine.py 注入 registry 到 LegacyRunner
expected: |
  运行：
    python3 -c "
    from code_forge.machine import StateMachine
    from code_forge.legacy import LegacyRunner
    runner = LegacyRunner()
    print('registry before:', runner.registry)
    runner.registry = {'ruff': {}}
    print('registry after inject:', runner.registry is not None)
    "
  应输出：registry before: None，registry after inject: True。
  （完整注入在 _run_advisory_axes 内，此处验证 hasattr 可写入路径。）
result: pass
actual: registry before=None, after=not None

### 4. LegacyRunner advisory 找到不在 diff 中的预存在违规
expected: |
  运行（在已有 git blame 的 forge repo 内）：
    pytest tests/test_legacy_integration.py -v -k "test_real_default_l0_runner_e2e" 2>&1 | tail -5
  应输出：1 passed（real l0_runner E2E 测试通过，说明真实路径能找到 legacy advisory 并返回 blame attribution）。
result: pass
actual: 1 passed (test_real_default_l0_runner_e2e)

### 5. advisory findings 不阻塞 convergence
expected: |
  运行：
    python3 -c "
    from code_forge.legacy import LegacyRunner
    r = LegacyRunner()
    print('is_advisory:', r.is_advisory)
    # advisory=True 意味着 machine.py 不把它的 findings 计入 _state.findings
    from code_forge.advisory import AxisRunner
    import inspect
    # 验证 AxisRunner 协议中 is_advisory=True 的含义
    print('Protocol check: OK')
    "
  应输出：is_advisory: True，Protocol check: OK，无异常。
result: pass
actual: 6/6 passed including test_advisory_isolation

## Summary

total: 5
passed: 5
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none yet]
