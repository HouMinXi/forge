核对完毕，所有计划声明已逐一对照真实代码验证。以下是评审结果。

---

## 验证为准确的部分（无需修改）

**42-01:**
- 守卫位置 cli.py:2396-2400 精确 ✓;`Path` 已在 cli.py:18 导入 ✓;XOR 校验 backend.py:310-320 与计划引用完全一致（api 后端恰好二选一，`elif` 链接安全；vertex 分支 backend.py:281-299 永不设置 `api_key_file`,`elif` 不会误触发）✓
- llm_invoke.py:838-862 运行时解析与计划描述一致 ✓;read_first 引用 backend.py:600-668 确为 `_probe_api` ✓

**42-02:**
- machine.py:1211 硬编码 `axis_claim="review"` 精确 ✓（全文件唯一出现，grep 验收可行）;cli.py:1321 `axis_claim="manual"` ✓（唯一出现，正确排除在外）
- LedgerRow frozen + 全部字段无默认值 ✓，计划"必须放末尾"的 IMPORTANT 注释正确；`append_row` 用 `asdict`(ledger.py:69)，新字段自动序列化，计划不碰它是对的；iter_rows 显式键构造（ledger.py:101-113)，计划的 `data.get("version_sensitive", False)` 方案正确
- 仅两个 LedgerRow 写入点（machine 流水线 + cli 手动）,"每个 finding 都有派生 claim_type"的范围声明成立；既有测试不断言 machine 写入行的 axis_claim(test_machine_ledger.py 无此类断言），不会破坏旧测试 ✓
- claim.py 无 code_forge 内部导入，无循环依赖风险 ✓；两计划同属 wave 1 且文件无重叠，可并行 ✓

## 发现

### HIGH-1 (42-01):Test 1/4 会被 outlet 探测层抢先通过，bug 注入协议按现状必然失效

**机制（实测代码路径）:** `resolve_outlet` 的自动路径（无 `--outlet`/`FORGE_OUTLET`/gate.yaml outlet）在到达守卫**之前**就调用 `reachability_fn()` → `_probe_api`(outlet_resolver.py:263-271)，探测失败直接 raise CliError。而 `_probe_api` 已经检查：api_key_file 存在性+权限（backend.py:636-652)、vertex credentials_path 存在性（backend.py:611-619)、api_key_env 存在性（backend.py:654-668)，且报错文案含 "not found"/"not set"——与计划 Test 1/4/6 的 match 字符串吻合。

**后果：** 默认路径下，Test 1（文件缺失）和 Test 4(vertex 凭证缺失）**即使删掉新守卫也照样 PASS**（错误来自探测层而非守卫）。计划 Step 3 的注入预期"tests 1-2 must FAIL / Test 4 must FAIL"对 Test 1、4 不成立——正是 Golden Rule 2 要防的 false green。只有空文件（Test 2）在默认路径下真正需要新守卫（探测只查存在性不查空）。探测仅在显式 outlet 路径被绕过（outlet_resolver.py:222-252)。

**附带 premise 失真：** 计划 objective 称"api_key_file 仍会在重试循环深处失败，产生 3 条相同 INFRA findings"——这在默认自动探测路径下为假（缺失文件在 outlet 解析阶段已 CliError)，仅对显式 outlet 配置和空文件情形为真。

**修复：** 测试必须用 `monkeypatch.setenv("FORGE_OUTLET", "subprocess")` 或 mock `resolve_outlet` 强制走探测绕过路径，并在计划中写明该机制；read_first 应补 outlet_resolver.py:218-271。

### MEDIUM-1 (42-02):Task 2 注入预期事实性错误；接线无运行时测试，仅有源码 grep

- Step 4 称"把 `derive_claim_type(f.source).type` 改回硬编码 `'review'`,L0 的 test_ledger_row 也会 FAIL"——**错误**。Test 9-10 直接用 `derive_claim_type` 输出构造 LedgerRow，完全不经过 machine.py（计划自己在 257、334、410-411 行承认"they bypass machine.py"，前后自相矛盾）。该注入只有 Test 13（源码断言）会 FAIL。
- 验收标准"hardcoding 'review' back causes L0 test to FAIL;removing version_sensitive causes L1 test to FAIL"——同错。Test 9 自行构造行，删 machine.py 的 version_sensitive 只有 Test 13(c) 能抓。
- 根本缺口：接线正确性仅靠 Test 13 的源码 grep，没有任何测试真正执行 `_write_ledger_rows` 断言落盘行的值。tests/test_machine_ledger.py 已有现成 harness(`_make_finding` line 35，直接调 `_write_ledger_rows` 的测试 line 149-270)：补一个 L0→("lint", False)、L1→("review", True) 的运行时断言成本极低，且能让计划所写的注入预期真正成立。grep 断言另有脆性：函数内注释若含 `axis_claim="review"` 字样会误报。

严重度定为 MEDIUM 而非 HIGH：源码 grep 网确实能抓住主回归（重新硬编码），覆盖并非缺失，但计划的完成条件按现状无法达成，执行者会在注入步骤撞上矛盾。

### LOW-1 (42-01)：验证命令引用了不存在的测试类

`<verification>` 中 `pytest tests/test_cli_integration.py::TestMissingEnvVarCliError`——该类不存在。`test_missing_env_var_cli_error`(691 行）位于 `TestLLMInvokeErrorWrapping`(647 行）。照抄命令会在收集阶段报错，触发虚假 STOP。正确节点：`tests/test_cli_integration.py::TestLLMInvokeErrorWrapping::test_missing_env_var_cli_error`。（验收标准按行号引用是对的，仅 pytest 节点路径错。)

### LOW-2 (42-01)：非 UTF-8 的 api_key_file 会抛 UnicodeDecodeError 而非 CliError

`p.read_text(encoding="utf-8")` 遇二进制/损坏文件抛 UnicodeDecodeError（既非 CliError 也非 OSError)，输出 traceback 而非干净错误。llm_invoke.py:842 有同样缺口，行为一致，但守卫的存在意义就是干净的早期报错。建议 `except (OSError, UnicodeDecodeError)` 包成 CliError。

---

**Verdict: 4 findings(1 HIGH / 1 MEDIUM / 2 LOW)**

HIGH-1 必须在执行前修掉：它使 42-01 的核心证明机制（bug 注入）对 6 个测试中的 2 个失效，且修复成本极低（测试内强制 `FORGE_OUTLET=subprocess` + 计划补一句说明）。MEDIUM-1 建议一并修：用 test_machine_ledger.py 现成 harness 补运行时接线断言，计划的注入预期即由假转真。两条 LOW 可在执行中顺手处理。
