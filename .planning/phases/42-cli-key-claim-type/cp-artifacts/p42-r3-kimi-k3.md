验证完成。所有引用已逐项对照真实源码，以下是第 3 轮评审结果。

## kimi-k3 Review — Phase 42 Plans (Round 3)

### 已验证无误的关键点（抽查清单）

- `cli.py:2396-2400` 现有 guard 与计划引用逐字一致；`state_dir` 创建在 2402 行，插入位置正确；`Path`(L18)、`CliError`(L40）均已导入。
- `backend.py:310-320` XOR 强制校验**真实存在**(310-314 两者同设报错，315-320 两者皆无报错）,`elif` 链的依据成立；`api_key_file` 在解析时已做 `~` 展开（321-322)。
- `llm_invoke.py:840-851` api_key_file 运行时解析位置精确；vertex 分支（863-864）跳过 key 解析，与 Test 5 "deferred to ADC" 一致。
- `machine.py:1211` 是全文件**唯一** `axis_claim` 出现点，hardcoded `"review"` 确认；`cli.py:1321` `axis_claim="manual"` 确认；machine.py 用相对导入（L39),`from .claim import derive_claim_type` 符合惯例。
- `state.py:77` source Literal 七个值与 `_SOURCE_TO_CLAIM` 映射表完全对齐；`ledger.py:40-54` LedgerRow frozen 且无默认字段确认——R2 修复（字段置于末尾）有真实依据；`iter_rows` 反序列化构造在 100-113 行，`axis_claim=data["axis_claim"]` 正在第 108 行，`data.get("version_sensitive", False)` 的向后兼容指引正确；`append_row` 用 `asdict`，新字段自动序列化。
- R2 全部 6 项修复均已落实：Test 13 含断言（c)、injection #1 引用 Test 13、`files_modified` 含 ledger.py、Test 9-10 导入含 TerminalState。
- `tests/test_fast_fail.py`、`src/code_forge/claim.py`、`tests/test_claim_type.py` 均不存在，"Create" 表述准确。

### Findings

| # | Severity | Plan | Finding |
|---|----------|------|---------|
| 1 | HIGH | 42-01 | `<verification>` 块 pytest node ID 错误：`tests/test_cli_integration.py::TestMissingEnvVarCliError` 这个**类不存在**。test_cli_integration.py:691 的 `test_missing_env_var_cli_error` 实际位于 `TestLLMInvokeErrorWrapping`（类起始于 647 行）。实测 `pytest --collect-only` 退出码 4、"no tests collected"——该机械验证命令按原文执行必然失败。正确 node:`tests/test_cli_integration.py::TestLLMInvokeErrorWrapping::test_missing_env_var_cli_error`。 |
| 2 | MEDIUM | 42-02 | Task 2 Step 1 散文（287-292 行）写 "Create a mock StateFinding, call _write_ledger_rows (or the relevant code path)"，与紧随其后的测试 pattern（直接构造 LedgerRow、绕开 machine.py)、验收 NOTE(257 行 "Tests 9-10 verify claim+ledger data flow (not machine.py wiring)")及 334 行（"bypass machine.py entirely"）三处自相矛盾。这是 R2 重新设计 tests 9-10/13 后未传播干净的残留散文，执行者按第一句会写出与 NOTE 冲突的测试。 |
| 3 | MEDIUM | 42-02 | Task 2 Step 4 第一条注入注记（399-401 行）声称把 machine.py 的 derive 调用换回 hardcoded `"review"` 时 "test_ledger_row for source='L0' will also FAIL"；验收标准 423 行重复此说法（"hardcoding 'review' back causes L0 test to FAIL")。但按计划自身设计，tests 9-10 直接调用 `derive_claim_type` 且不触碰 machine.py，该注入下 L0 测试保持 **GREEN**，只有 test 13 FAIL。计划相邻两处（411 行、验收 424 行）反而写对了（"even though tests 9-10 bypass machine.py")——同一文件内相邻段落互相矛盾，执行者注入后观测到 L0 绿色会无所适从。 |
| 4 | LOW | 42-01 | 新 guard 代码 `p.read_text(encoding="utf-8")` 未捕获 OSError:api_key_file 存在但不可读（权限拒绝）时抛出裸 PermissionError traceback 而非干净的 CliError。运行时路径（llm_invoke.py:843-847）有 `except OSError` 包装。仍在 pipeline 前失败，属健壮性毛边，建议 try/except OSError 转 CliError。 |
| 5 | LOW | 42-01 | objective 称失败发生在 "deep in the retry loop at llm_invoke.py:840-851"——key 解析实际在 `_invoke_api` 顶部（839-862),retry 参数校验（866+）之前，零次重试即抛出。行号引用正确，"retry loop" 措辞不准确。 |

### Verdict: NOT CLEAN — 0B/1H/2M/2L

**核心结论**:R2 的 6 项修复全部真实落地且经源码验证，两份计划的代码引用、映射完整性、向后兼容设计、bug-injection 主体逻辑均正确，无 BLOCKER。剩余问题集中在两处：42-01 验证命令的 pytest node ID 错误（Finding 1，必修，否则机械验收卡死）;42-02 内部三处自相矛盾的残留散文（Findings 2-3，同一根因——R2 重设计未全量传播，建议把 287-292 行散文、399-401 行注记、423 行验收统一改为与 257/411/424 行一致的 "tests 9-10 bypass machine.py" 口径）。Finding 4-5 为 LOW，可顺手修或记录留档。
