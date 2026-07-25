所有声明已对照源码逐条核实。

## LongCat-2.0 Review — Phase 42 Plans (Round 2)

### 代码准确性核实

| 计划 | 声明 | 实际 | 状态 |
|------|------|------|------|
| 01 | cli.py:2396-2400 现有 guard | cli.py:2396-2400 完全匹配 | ✓ |
| 01 | state_dir 创建于 2402 行 | cli.py:2402 `state_dir = cwd / ".code-forge"` | ✓ |
| 01 | backend.py:310-320 XOR 校验 | backend.py:310-320 完全匹配（both→raise, neither→raise） | ✓ |
| 01 | llm_invoke.py:840-851 api_key_file 解析 | llm_invoke.py:840-851 完全匹配 | ✓ |
| 01 | test_cli_integration.py:691-706 现有模式 | 691-706 完全匹配 | ✓ |
| 02 | state.py:66-86 StateFinding | 66-86 完全匹配，source Literal 7 值齐全 | ✓ |
| 02 | ledger.py:40-54 LedgerRow | 40-54 完全匹配 | ✓ |
| 02 | machine.py:1200-1219 _write_ledger_rows | 1200-1219 完全匹配，axis_claim="review" 在 1211 | ✓ |
| 02 | cli.py:1314-1326 manual mark | 1314-1326 完全匹配，axis_claim="manual" 在 1321 | ✓ |

### 逻辑正确性

- **01 elif 链**：`elif backend.api_key_file` 正确链于 `if backend.format != "vertex" and backend.api_key_env`。非 vertex + api_key_file 设置时 if 为 False → elif 触发。vertex 凭据检查作为独立 `if` 处理，允许 ADC 回退。✓
- **02 claim 映射**：7 个 StateFinding.source 值全覆盖；version_sensitive=True 仅 L1+MUTANT（合理：跨版本稳定性敏感）；未知 source 抛 ValueError 强制显式映射。✓
- **02 向后兼容**：`version_sensitive: bool = False` 默认值 + iter_rows 用 `data.get("version_sensitive", False)` 反序列化旧行。✓

### Bug-injection 正确性

- 01：移除 elif → 缺失/空文件不再早抛 → 测试 1-2 FAIL。✓
- 02 Task1：移除 "L1" → ValueError → test_l1 FAIL。✓
- 02 Task2：回硬编码 "review" → L0 测试 axis_claim 错配 FAIL；Test 13 源码断言捕获 machine.py 回退（测试 9-10 绕开 machine.py 的盲点被堵）。✓

### 范围

两个计划严格限定于 F8（guard 扩展）+ claim_type（新模块 + wiring）。cli.py:1321 manual mark 明确不动。✓

### Findings

| # | Severity | Plan | Finding |
|---|----------|------|---------|
| — | — | — | 无 |

All file:line references verified against actual source. Guard logic, claim mapping, backward-compat handling, bug-injection design, and scope boundaries are correct. No discrepancies found.

## VERDICT: CLEAN — 0B/0H/0M/0L
