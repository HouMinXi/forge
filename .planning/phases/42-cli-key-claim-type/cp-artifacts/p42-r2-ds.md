## DeepSeek 审查 — Phase 42 计划（第 2 轮）

### 发现

| # | 严重级别 | 计划 | 发现 |
|---|----------|------|------|
| — | — | — | 无 |

### 已验证的独立审查核对清单

**1. 代码准确性** — 所有 `file:line` 引用均与实际代码匹配：
- `cli.py:2396-2400`：现有保护代码（`backend.format != "vertex" and backend.api_key_env` → `os.environ.get`）、`cli.py:2402`（`state_dir` 创建）、`cli.py:1321`（`axis_claim="manual"`）✅
- `backend.py:80-113`：BackendConfig 中 `api_key_file`（第 89 行）和 `credentials_path`（第 96 行）✅
- `backend.py:308-320`：配置解析器在配置时强制执行 `api_key_env XOR api_key_file`（第 310 行：互斥检查；第 315 行：缺少检查）✅
- `backend.py:600-668`：`_probe_api` 的逻辑（文件验证 + 权限检查 + ADC 回退）已正确引用 ✅
- `llm_invoke.py:838-862`：运行时密钥解析（第 840 行 `api_key_file`、第 852 行 `api_key_env`）✅
- `machine.py:1204-1216`：硬编码的 `axis_claim="review"` 位于第 1211 行 ✅
- `ledger.py:40-54`：没有 `version_sensitive` 字段的 LedgerRow；`iter_rows`（第 101-113 行）通过 `data["field_name"]` 显式反序列化（而不是 `data.get()`） ✅
- `state.py:66-86`：包含所有 7 个字面量的 `StateFinding`（"L0"、"L1"、"MUTANT"、"E2E_CHECK"、"COVERAGE"、"INFRA"、"FIXVAL"） ✅

**2. 逻辑正确性**：
- 计划 01 的 `elif backend.api_key_file`：因配置分析器强制执行互斥，故可正确链接。非 Vertex 后端 + `api_key_file` → `elif` 执行。Vertex 后端 + `credentials_path` → 单独的 `if` 处理 ✅
- 计划 02 的映射覆盖了所有 7 个 `StateFinding.source` 字面量，对未知来源引发 `ValueError` ✅
- `version_sensitive=True` 仅适用于 L1 和 MUTANT（有证据负担的类别）；其他字段为 `False` ✅

**3. 测试覆盖** — 所有接受标准均具体且可通过机械方式验证：
- 计划 01：6 项测试（各 3 种通过/失败模式 + Vertex + 旧版 env var），验证命令为 `pytest -x -v` ✅
- 计划 02：13 项测试（8 项推导 + 2 项声明+分类账整合 + 1 项手动标记 + 1 项向后兼容性 + 1 项源码接线），所有验证均可通过 `pytest` 或 `grep` 实现 ✅

**4. 错误注入正确性（第 1 轮修复 — 测试 13）**：
- 第 1 轮 HIGH 问题：测试 9-10 之前构建了 LedgerRow 但未使用 `machine.py`。已通过在 `machine.py` 中检查硬编码 `"review"` 是否消失的源码断言测试 13 修复 ✅
- 计划 01 的注入：移除 `elif` 块 → 测试 1-2 失败；移除 Vertex 块 → 测试 4 失败（正确：注入点正确，不在旧版保护代码中） ✅
- 计划 02 的注入：从映射中移除 `"L1"` → ValueError（可检测）；在 `_write_ledger_rows` 中硬编码 `"review"` → L0 测试失败（即使在机器外部通过测试依然可检测到） ✅

**5. 向后兼容性**：
- `version_sensitive: bool = False` 作为 dataclass 字段默认值 ✅
- `iter_rows` 必须使用 `data.get("version_sensitive", False)` 而非 `data["version_sensitive"]` — 计划 02 第 351 行明确指出这一点 ✅

**6. 导入/模块路径**：
- `from .claim import derive_claim_type` → 包内正确相对导入 ✅
- `code_forge.claim.ClaimType` → 包根目录下可解析 ✅

**7. 范围**：
- 计划 01：`cli.py` + `test_fast_fail.py` — 仅限于 F8 保护扩展 ✅
- 计划 02：`claim.py` + `machine.py` + `ledger.py` + `test_claim_type.py` — 仅限于 claim_type 推导；`cli.py:1321`（手动标记）保持未修改 ✅
- 两个计划均未接触对方文件或无调用的基础设施 ✅

### 裁定：通过 — 0B/0H/0M/0L
