两份计划已对照磁盘源码逐条核实。行号引用全部抽查验证通过（cli.py:2396-2400 守卫、machine.py:1211 硬编码、ledger.py:40-54/101-113、state.py:77 的 7 个 source 值、cli.py:1321 manual、backend.py:310-320 XOR、backend.py:281-300 vertex 提前 return）。

## Findings

**MEDIUM-1 (42-02, bug-injection correctness)**: Task 2 Step 4 的注入预测与计划自己的分析自相矛盾。计划称 re-hardcode `"review"` 后 "test_ledger_row for source='L0' will also FAIL"，又称 "removing version_sensitive causes L1 test to FAIL"——但 Test 9/10 自己构造 LedgerRow（显式调用 `derive_claim_type`），完全绕开 machine.py，注入 machine.py 后它们照常 PASS。计划自己在 Test 13 描述里也承认 "claim+ledger tests (9-10) still pass because they bypass machine.py entirely"。真正捕获这两类回归的只有 Test 13（源码断言）。执行者按 acceptance criteria 跑注入时会观察到与计划相反的 PASS，可能误判注入证明失败、甚至去弱化 Test 13。安全网（Test 13）本身成立，但 bug-injection 小节的哪条测试 FAIL 必须改写准确。

**MEDIUM-2 (42-01, test coverage mechanism)**: 守卫内联在 CLI 主函数中段（resolve_backend 之后、state_dir 之前），现有测试无一触及该层——计划 cited 的 test_cli_integration.py:691 测的是 llm_invoke 运行期（LLMInvokeError），不是 CLI 守卫（CliError）。计划对如何调用守卫只写了 "either directly or via a helper"，未给出 main() 级调用 harness（args/inline backend 标志绕过 gate.yaml），也没决定是否抽 helper。执行者需自行发明调用机制，两种走法（重量级 main() mock vs 抽 helper 偏离计划内联代码）都有偏差风险。建议计划补一个最小 main() 调用 pattern。

**LOW-1 (42-01)**: 守卫 `p.read_text(encoding="utf-8")` 不捕获 OSError/UnicodeDecodeError。`is_file()` 通过但读取失败（权限、竞争删除、非 UTF-8 字节）时抛裸 traceback 而非 CliError——llm_invoke.py:841-847 对 OSError 有包装，新守卫反而不如它 pre-empt 的代码健壮，且"启动时干净报错"正是本守卫的存在意义。建议 `try/except OSError -> CliError`。

**LOW-2 (42-01)**: "a backend sets exactly one of api_key_env XOR api_key_file (backend.py:310-320)" 表述不准确——XOR 校验只在非 vertex 分支执行；vertex api backend 两者都不设（backend.py:281-300 提前 return，api_key_env=None 且无 api_key_file），cli 型 backend 也是两者皆无。不影响所提代码正确性（elif 只增不删），但引用表述应改为 "非 vertex api backend"。

**LOW-3 (42-01, scope)**: 守卫只覆盖 subprocess/CLI outlet；MCP outlet 不经此路径，api_key_file 后端在 MCP 下仍会在 retry loop 深层失败。计划未声称覆盖 MCP，但 objective 里 "every backend type gets a single clear CliError" 的表述宜限定 outlet，避免后续误读为全链路。

**LOW-4 (42-02)**: Test 13 为源码文本断言，对格式敏感（`axis_claim = "review"` 带空格或经中间变量 `AC = "review"` 逃逸检测），且无运行时接线测试。属计划明示的取舍（Test 13 描述已论证），可接受，仅记录脆弱性。

## 核实通过项

- 全部行号引用准确（两份计划共 12 处 file:line 断言，零失配）
- `from pathlib import Path` 已在 cli.py:18，无需新增 import
- machine.py 中 `axis_claim="review"` 唯一出现于 1211，grep==0 验收有效；cli.py 当前 `api_key_file`/`credentials_path` 零出现，grep>=1 验收有效
- LedgerRow 末尾加默认字段避免 non-default-after-default TypeError 的分析正确；append_row 走 asdict 自动携带新字段；iter_rows `.get(..., False)` 向后兼容方案正确
- vertex + api_key_file 组合经 config 不可达，elif 链无行为副作用
- 42-01 `requirements: F8` / 42-02 `requirements: "7.1"` 均与 ROADMAP:37 一致
- cli.py:1321 manual 路径保持不变的边界划定正确

**Verdict: 6 findings (0 BLOCKER / 0 HIGH / 2 MEDIUM / 4 LOW)**

两个 MEDIUM 都在可执行性层面：42-02 的注入预测写错失败测试编号，42-01 的测试调用机制留白。修正后即可进入执行。
