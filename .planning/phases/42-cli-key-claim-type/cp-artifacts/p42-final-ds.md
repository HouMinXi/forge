所有源代码已验证完毕。以下是最终审查结果。

---

## DeepSeek Review — Phase 42 (Final)

### Findings

| # | Severity | Plan | Finding |
|---|----------|------|---------|
| — | CLEAN | — | 无未解决问题 |

### 逐项验证摘要

**Plan 01 — CLI key fast-fail**

| 维度 | 结果 | 证据 |
|---|---|---|
| `cli.py:2396-2400` guard | ✅ 匹配 | 行 2396-2400 包含确切的 `if backend.format != "vertex" and backend.api_key_env: ... raise CliError` |
| `backend.py:80-113` BackendConfig | ✅ 匹配 | 行 88 `api_key_env`, 行 89 `api_key_file`, 行 96 `credentials_path`, 行 86 `format` |
| `llm_invoke.py:838-862` key 解析 | ✅ 匹配 | 行 840-847 文件读取, 行 848-851 空文件检查, 行 852-857 env var 回退 |
| `backend.py:310-320` XOR | ✅ 匹配 | 行 310-314 拒绝两者同时设置; 行 315-319 要求至少有一个 |
| `backend.py:281-300` vertex 解析 | ✅ 匹配 | 行 292-299 `api_key_env=None`, `credentials_path` 以关键字传入 |
| 在非 vertex api_key_env 后使用 `elif` | ✅ 正确 | 非 vertex api backend 必然恰有二者之一 (XOR); vertex 格式不会触发此分支 |
| vertex `credentials_path` 单独 `if` | ✅ 正确 | vertex 后端可用 ADC(无 credentials_path), 所以不能纳入 elif 链 |
| Bug-injection 只攻击新守卫 | ✅ 正确 | 明确指出在 api_key_file 和 vertex 块注入, 而不是在已存在的 api_key_env 守卫处 |

**Plan 02 — claim_type oracle**

| 维度 | 结果 | 证据 |
|---|---|---|
| `state.py:66-86` StateFinding.source | ✅ 匹配 | 行 77 `source: Literal["L0", "L1", "MUTANT", "E2E_CHECK", "COVERAGE", "INFRA", "FIXVAL"]` |
| `ledger.py:40-54` LedgerRow 定义 | ✅ 匹配 | 行 40 `@dataclass(frozen=True)`, 行 44-54 字段, 无 `version_sensitive` (待添加) |
| `machine.py:1211` 硬编码 | ✅ 匹配 | 行 1211 `axis_claim="review"` — 确如所述 |
| `cli.py:1321` manual 覆盖 | ✅ 匹配 | 行 1321 `axis_claim="manual"` — 完全匹配 |
| `version_sensitive` 放在最后 | ✅ 正确 | 所有现有字段均无默认值; `=False` 必须放在 `ts` 之后 |
| `iter_rows` 反序列化用 `.get()` | ✅ 正确 | 行 101-113 使用关键字参数; `.get("version_sensitive", False)` 与 dataclass 默认值一致 |
| 7 个 source 值映射 | ✅ 完整 | L0→lint, L1→review, MUTANT→mutation, E2E_CHECK→e2e, COVERAGE→coverage, INFRA→infra, FIXVAL→fixval |
| `version_sensitive=True` 仅 L1+MUTANT | ✅ 正确 | _SOURCE_TO_CLAIM 字典: L1=True, MUTANT=True, 其余=False |
| 未知 source 的 ValueError | ✅ 正确 | `_SOURCE_TO_CLAIM.get(source)` + `if ct is None: raise ValueError(...)` |
| 已知 source 无静默回退 | ✅ 正确 | 未使用 `.get(source, default)` 兜底; 显式抛出异常 |
| 相对导入 `from .claim import ...` | ✅ 正确 | `machine.py` 已有多个 `.xxx` 相对导入 (行 32-54) |
| Test 13 捕获接线回归 | ✅ 正确 | 明确 re-hardcoding 不会使 test 9-10 FAIL, 但会被 test 13 的源码断言捕获 |

### Verdict: CLEAN — 0B/0H/0M/0L

两个计划中的所有声明均已通过实际源代码验证。关键检查点：
- **文件:行引用** — 每个引用的位置都精确匹配当前代码
- **逻辑正确性** — `elif` 链在 XOR 约束下正确; vertex 的分离 `if` 块处理了 ADC 场景; `version_sensitive` 放在 `LedgerRow` 末尾避免了 dataclass 错误
- **依赖一致性** — `machine.py` 使用相对导入; `_check_backend_credentials` 和 `derive_claim_type` 的 import 路径可解析
- **后向兼容性** — `iter_rows` 中的 `data.get("version_sensitive", False)` 正确处理了旧版 ledger 行
- **作用域约束** — 两个计划均严格限定在自身范围内 (F8 守卫扩展 + claim_type 推导); `cli.py:1321` 的 manual 覆盖未受影响
