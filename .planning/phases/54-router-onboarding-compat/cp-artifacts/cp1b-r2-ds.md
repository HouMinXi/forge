已完成全部验证：我通读了 briefing、PLAN、CONTEXT（D-01..D-12）、REQUIREMENTS（ROUTER-02..05），并逐一核对了计划中引用的源码锚点（workspace.py、cli.py、doctor.py、backend.py、llm_invoke.py、user_config.py、contract_loader.py、mcp_server.py、gate.schema.json、conftest.py、6 个测试文件、README）。以下是按分配角度（implementer-readiness + acceptance checkability + coverage）的审查结论。

## 已验证的关键锚点（个人核实，非计划自述）

- `resolve_workspace` 签名与优先级（workspace.py:19-51）✓；FORGE_PROJECT_DIR 优先级 1、$HOME 跳过、回退 cwd 均属实
- `_run_trust` 全部分支（cli.py:1297-1420）：:1315 直接 join、:1337 contracts、:1348-1367 status、:1370-1382 revoke、:1384-1397 空配置 guard、:1408 record_trust ✓
- `resolve_contract_specs(config_path, cwd)` 第二参是相对路径基准（contract_loader.py:294，经 `_resolve_repo_path`）——Task 2 传 workspace 的理由成立 ✓
- `_check_backends`（doctor.py:118-162）仅被 run_doctor 和 6 个直接三参调用（tests/test_doctor.py:137/151/171/186/209/228）——live 参数需带默认值，由现有测试强制，无需计划明说
- `llm_invoke.py` 全部锚点：`_TruncatedResponse` 续传（:1485）先于 attempt 检查（:1497）✓；超时优先级 backend.timeout_s > caller（:580-581）✓；cap 解析 `max_completion_tokens or max_tokens`（:269-280）✓；thinking/reasoning_effort 空串即省略（:283-296）✓；`_format_error_message`（:691-715）确实丢弃 body_excerpt ✓；vertex 已内联 body（:1915）✓；凭据 raise 位点 :1332/:1337/:1343/:1347 + :1857/:1862/:1871、google-auth ImportError :1839 kindless ✓；`kind="` 基线 grep = 16，+15（6 conn + 7 credentials + 2 parse）= 31，算术成立 ✓
- `.kind` 消费者仅 mcp_server.py:958 白名单（dead_code.py 的 `.kind` 是 SQL，无关）——加性安全成立 ✓
- conftest.py:19-30 只 patch `load_user_backends`（陷阱属实）；backend.py:30 无 `replace` 绑定；backend.py:812-813 cli 直通；_probe_api 无网络保证 ✓
- 表单器既有测试确为 substring/prefix 风格（tests/test_llm_invoke.py:3264-3288），Step 1.5 的追加形式三例全绿 ✓
- test_mcp_server.py:919 负向测试断言 `match="Sampling failed"` + `mock_cli.assert_not_called()`——kind="conn" 扩展可落地 ✓；test_cli_integration.py:181 `test_main_returns_int` 存在，test_doctor.py 无 main/argv 模式（g 的理由成立）✓
- schema 导航路径 `$defs.backendEntry.properties.base_url.description` 可达 ✓

## 独立 CONFIRMING（对应 briefing 已结项、非新发现）

- 基线 16→31 的 grep 计数与两 raise 臂形状条款（内部 R1 #7）——实测基线恰为 16 ✓
- kimi F-1（formatter 丢弃 excerpt）与 F 位置声明——源码与测试风格双双证实 ✓
- L-2 的措辞修正（"no live probe applies"）——backend.py:812-813 行为与修正后文案一致 ✓
- mcp 白名单内联不可导入（:958-960）→ 走真 dispatch 测试而非复制元组（内部 R1 #1）✓

## 位置裁定（A–F，显式裁定）

- **A（D-08 解释）ACCEPT**：`resolved != cwd.resolve()` 是可实现且精确的条件；唯一偏离 D-08 字面（非 git 目录但恰是 workspace 根时不警告）正是"信任完全正确"的情形，且无祖先的残留情形由既有 not-found 错误（test (e) 钉住）覆盖，与 ROUTER-03 的 ADR-0009 框架自洽。
- **B（32-token 上限）ACCEPT**：机制在源码中证实（:1485 续传先于 :1497 attempt 检查）；thinking_type=""/budget=0 使 thinking 块整段省略（:283-288），零化是生效的。
- **C（仅 api 型 live 探测）ACCEPT**：这是对 D-03 "ALL backends" 的窄化，但有依据——cli 型 live 探测意味着执行任意配置命令，违背 `_probe_api` 的 no-subprocess 保证（backend.py:896-927）；行文案已诚实声明"no live probe applies"，不谎报验证。
- **D（六任务单计划）ACCEPT**：D-12 锁定。
- **E（不加 .git probe）ACCEPT**：D-07 的恒打印路径即消歧器，且每路径打印在计划中均落位。
- **F（HTTP 错误消息形态变化）ACCEPT**：三个既有 formatter 测试均为 substring/prefix 断言，追加形式不破坏；形态变化为计划自有的决策（Step 1.5 明确"owned"）。

## Findings

**L-1 — Task 2 的 warn 作用域未钉死，两处计划文本互相拉扯（implementer-readiness / two-reader divergence）**
- 位置：54-01-PLAN.md Task 2 action 段（"after resolution, if `workspace != cwd.resolve()`, print a one-line warning"）vs 同任务 test (f)（"--status output unchanged"）与 CONTEXT.md D-07（"--status stays as-is"）。
- 证据（已核实）：`_run_trust` 中 resolution 之后、gate.yaml open 之前是全局插入点（cli.py:1315-1316 之间的唯一自然位置）；status/revoke/bare 三支路共享该前缀。照 action 字面放在 resolution 后，warn 会对 `--status`/`--revoke` 也触发；照 test (f) 与 D-07 的"stays as-is"读，warn 只应作用于 mutating 路径。两个实现者会产出不同的发布行为（walk-up 场景下 `trust --status` 是否警告），而现有测试均从根 cwd 运行，任何验收准则都测不出这个分歧。
- 建议：把 warn 位置钉到一处（推荐：仅 bare/--revoke 两条 mutating 路径，与 D-07 的 "--status stays as-is" 一致），并让 test (f) 明示"从子目录运行 status 无 warn 行"或反之。

**L-2 — 第六个分类标签（fallback class）未钉字符串，而 headline 404（错误 /v1）情形正落在它身上（coverage / acceptance）**
- 位置：54-01-PLAN.md Task 4 behavior（"unknown kind -> fallback class"）与 Task 5 test (d)（只覆盖五个 D-04 类）。
- 证据（已核实）：错误 /v1 路由返回 404 时，openai/anthropic HTTP handler 构造的 `LLMInvokeError` exit_code=404、kind=""（llm_invoke.py:1595-1604 / :1734-1744 现状）→ 分类函数按计划顺序（is_timeout → 401/403 或 credentials → conn/sse_body/bad_body → fallback）落入 fallback 类。五个 D-04 标签在 behavior 列表中全部钉死（"timeout"/"credential-rejected"/"connection-refused"/"SSE-mixed"/"JSON-malformed"），唯独 fallback 标签留给实现者发明；而 Task 6 smoke 检查 3 的预期文本只列了五类中的三类，真实 404 路由器呈现的将是未钉死的第六标签。
- 评估：CONTEXT.md 的 Claude's Discretion 条款确将"error taxonomy strings"委托给实现者，此条不构成 BLOCKER；但计划已选择钉死五个而漏掉第六个，且第六个恰是 headline 故障的用户可见标签。T4 分类层测试覆盖了 fallback 分支，T5 行层测试（test (d)）不覆盖它。
- 建议：为 fallback 类钉一个字符串（如 "http-error" 或 "unclassified"）并纳入 test (d) 或 T6 smoke 预期文本，消除该分歧。

## Coverage 总结

ROUTER-02→T1、ROUTER-03→T2、ROUTER-04→T4+T5（含"debug-loop value"再论证：Step 1.5 把 wrong-/v1 的 404 body 呈到医生行）、ROUTER-05→T3 全部映射且有任务 action 落地；D-01..D-12 全部有对应实现，唯 D-03 经 C 窄化（已裁定接受）、D-08 经 A 重释（已裁定接受）。未发现"映射了但 action 文本未实现"的情形。历史已结项（内部 R1/R2/R3、deepseek R1、kimi R1）无一重复提出。

SCORECARD: B=0 H=0 M=0 L=2
