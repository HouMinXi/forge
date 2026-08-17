I've carefully analyzed the revised plan and amendments against each other and the codebase. Here are my findings:

---

## CP1B-R2 Adversarial Review

**1. 注入矩阵缺少 I9 — 实现者可能漏执行 A-17 注入**

位置: Test plan "Injection points" 表格; A-17

缺陷性质: 表格仍列 I1-I8，A-17 新增的 I9（删除 `except TruncationBreakerError: raise` 子句）未出现在矩阵中。

失败场景: 实现者按矩阵执行 I1-I8 后认为注入完成，跳过 I9，trip-swallow 路径无回归保护。

修法: 注入矩阵增加一行 `I9 | DELETE except TruncationBreakerError: raise clause | test_trip_propagates_not_budgeted`，exit criterion 第 10 项改为 "I1-I9 executed"。

**2. T4 测试计划表缺少折叠测试 — 实现者可能只实现 3 个测试**

位置: test_plan "New tests" 表格 TestTruncationBreakerWiring 行; A-12

缺陷性质: 表格列出 3 个测试（test_breaker_trips_across_calls, test_breaker_default_fresh_per_call, test_provider_passes_breaker），A-12 新增的 test_fold_records_success_only_for_non_truncated 未列入。T4 GREEN 声称 "All 4 new tests pass" 但表格只列 3 个。

失败场景: 实现者按表格实现 3 个测试，exit criterion "All 4 new tests pass" 无法通过；或实现者发现不一致后自行补测试但行为未经计划审查。

修法: 表格增加 `test_fold_records_success_only_for_non_truncated | TestTruncationBreakerWiring | recovered truncation does NOT reset count; clean result DOES reset count`。

**3. A-17 理由与 A-19 结构描述存在时间线矛盾 — 但不影响正确性**

位置: A-17 rationale ("except-clause order is behaviorally inert for the reachable trip sites") vs A-19 (two-level try structure)

缺陷性质: A-17 的理由描述的是单层 try 结构下"trip 站点绕过子句"的情况，但 A-19 引入了双层 try 结构后，两个 trip 站点确实到达外层 except 子句。A-17 的注入（删除 re-raise 子句）在双层结构下仍然正确证明了吞没行为，但理由措辞与最终结构不匹配。

失败场景: 不影响实现正确性（注入本身 work），但计划审阅者可能因理由自相矛盾而质疑计划一致性。

修法: A-17 rationale 补充一句："Under the A-19 two-level try shape, both trip sites reach the outer except clauses; deletion of the specific clause lets the broad handler catch the trip, proving swallow." 无需改动注入设计。

**4. D-4 与 A-9/A-13 对 anthropic 测试的要求自相矛盾**

位置: D-4 ("the marginal cost is one fixture per format") vs A-9/A-13 ("an anthropic continuation test is optional")

缺陷性质: D-4 明确声称三种格式的边际成本只是每个格式一个 fixture，暗示三种都应测试。但 A-9/A-13 将 anthropic 标为"optional"，测试计划表中无 anthropic continuation 测试。两种说法矛盾。

失败场景: 实现者按 D-4 理解需要写 anthropic 测试但计划表中没有指导；或按 A-9/A-13 理解跳过但 D-4 的"one fixture per format"承诺落空。anthropic 路径的 dispatch 逻辑（传 api_key）在无测试覆盖下可能有隐蔽 bug。

修法: 二选一：(a) 在测试计划表增加一个 anthropic continuation 测试（mock `_invoke_anthropic` 的 side_effect），或 (b) 在 D-4 中明确 "anthropic continuation tested by the vertex test's shared dispatch logic; a dedicated anthropic test is out of scope because _invoke_anthropic's signature is identical to _invoke_openai's for the continuation path". 选项 (a) 更稳妥。

**5. A-11 的 probe 消息与原始 truncation 消息不同 — 不影响验收但影响可读性**

位置: D-3 + A-11 (T0 probe with threshold=1)

缺陷性质: probe 使用 threshold=1 的 breaker，第一次 truncation 触发 TruncationBreakerError，probe 捕获 LLMInvokeError 打印 kind=truncated。但消息内容是 breaker 的 "backend hit N truncations (>=%d)..." 而非原始的 "backend response truncated (finish_reason=length...)" 消息。A-11 声称"probe observes the raw truncation kind"，kind 确实是 truncated，但消息不是原始的。

失败场景: 操作员看到 probe 输出 "backend hit 1 truncations (>=1)" 可能误以为是 breaker 行为而非原始 truncation 检测。不影响 A1 验证（kind=truncated 已确认），但可读性降低。

修法: 在 probe 的输出格式中额外打印异常的 `__cause__` 链（`exc.__cause__`），这样操作员能看到原始 _TruncatedResponse 的消息。或在 probe 脚本注释中说明 "kind=truncated confirms finish_reason=length detection; the message text is from TruncationBreakerError, not the raw detection"。

---

**总结**: 5 条发现。Finding 1-2 是文档同步遗漏（注入矩阵和测试表未跟上修订），会导致实现者遗漏步骤；Finding 3 是理由措辞与最终结构不匹配（不影响正确性）；Finding 4 是 D-4 与 A-9/A-13 的自相矛盾（需要显式决策 anthropic 测试覆盖范围）；Finding 5 是 probe 可读性问题（不影响验收）。无 fix-on-fix 引入的新代码缺陷。
