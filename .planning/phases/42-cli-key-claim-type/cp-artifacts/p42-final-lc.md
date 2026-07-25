## [Internal] Review — Phase 42 (Final)

验证每个 plan 中的 file:line 引用和逻辑声明。

### 源文件交叉验证

| 声明 | 实际代码 | 结果 |
|------|----------|------|
| Plan 01: 现有 guard 在 cli.py:2396-2400 | 2396-2400 正是 `if backend.format != "vertex" and backend.api_key_env:` 块 | ✓ |
| Plan 01: state_dir 创建在 cli.py:2402 | 2402 = `state_dir = cwd / ".code-forge"` | ✓ |
| Plan 01: llm_invoke.py:840-851 运行时 key 解析 | 840-851 正是 `if backend.api_key_file:` 读取 + 空检查块 | ✓ |
| Plan 01: BackendConfig api_key_file/credentials_path/format | backend.py:86 (format), 89 (api_key_file), 96 (credentials_path) | ✓ |
| Plan 01: backend.py:310-320 XOR 强制 | 310 行 `if api_key_env and api_key_file: raise` + 315 行 `if not ... and not ...: raise` | ✓ |
| Plan 01: backend.py:281-300 vertex 解析 api_key_env=None | 294 行 `api_key_env=None` 且无 api_key_file（默认 None） | ✓ |
| Plan 02: machine.py:1211 axis_claim="review" 硬编码 | 1211 行正是 `axis_claim="review",` | ✓ |
| Plan 02: machine.py:1204-1216 LedgerRow 构造 | 完全匹配 | ✓ |
| Plan 02: ledger.py:40-54 LedgerRow 字段 | 11 字段，无 version_sensitive，全部无默认值 | ✓ |
| Plan 02: state.py:77 source Literal 7 值 | `Literal["L0","L1","MUTANT","E2E_CHECK","COVERAGE","INFRA","FIXVAL"]` | ✓ |
| Plan 02: cli.py:1321 axis_claim="manual" | 1321 行正是 `axis_claim="manual",` | ✓ |
| Plan 02: ledger.py iter_rows 反序列化 | 101-113 行 LedgerRow 构造，无 version_sensitive | ✓ |

### 逻辑验证

- **XOR 链式正确性**：非 vertex 后端强制 api_key_env XOR api_key_file，`elif backend.api_key_file` 接在 `if api_key_env` 之后逻辑完备。✓
- **vertex 独立 `if`**：vertex 后端 api_key_env=None、api_key_file=None，第一个 if/elif 都不触发，独立 `if format == "vertex" and credentials_path` 正确。✓
- **claim_type 映射完整**：StateFinding.source 7 值全部映射到 _SOURCE_TO_CLAIM，未知 source 抛 ValueError。✓
- **version_sensitive 放末尾**：LedgerRow 所有现有字段无默认值，`version_sensitive: bool = False` 必须放末尾，否则 "non-default argument follows default argument"。计划正确。✓
- **iter_rows 向后兼容**：`data.get("version_sensitive", False)` 让旧行默认 False。✓

### Bug-injection 验证

- Plan 01: 删除 elif → 测试 1-2 FAIL；删除 vertex if → 测试 4 FAIL；不动 api_key_env guard。✓
- Plan 02: 从 _SOURCE_TO_CLAIM 移除 "L1" → ValueError → 测试 FAIL；machine.py 重硬编码 "review" → 测试 13 source 断言捕获（测试 9-10 绕过 machine.py 不捕获）。✓

### 导入路径

- `from code_forge.cli import _check_backend_credentials, CliError` — CliError 存在于 cli.py:2398。✓
- `from code_forge.ledger import LedgerRow, TerminalState, append_row, iter_rows` — 全部在 ledger.py 中定义。✓
- `from .claim import derive_claim_type` — 新模块，相对导入正确。✓

### 范围

- Plan 01 严格在 F8（fast-fail guard 扩展）。✓
- Plan 02 严格在 claim_type 派生。✓

### Findings

| # | Severity | Plan | Finding |
|---|----------|------|---------|
| — | — | — | 无 |

### Verdict: CLEAN — 0B/0H/0M/0L

所有 file:line 引用精确匹配源码，guard 扩展逻辑正确，claim_type 映射覆盖全部 7 个 source 值，bug-injection 证明针对每个新站点，version_sensitive 末尾放置避免 frozen dataclass 默认值顺序陷阱，向后兼容处理得当，导入路径可解析，范围不越界。
