Now I have all the ground truth needed. Here is the adversarial review.

---

## Phase 48 PLAN.md Adversarial Review

**1. Finding 1 — `_continue_truncated` will crash on vertex format with TypeError**

位置: T3 action (GREEN), `_continue_truncated` dispatch by `backend.format`; also D-4 "one shared helper"

缺陷性质: 代码签名不匹配 — 计划声称的"格式无关辅助函数"实际上需要格式感知的参数分支，但计划未描述该分支。

失败场景: 用户配置 vertex 格式的 backend，某次调用触发 truncation，`_continue_truncated` 按计划文案 dispatch `_invoke_vertex(prompt_c, backend, api_key, timeout_s)` — 但 `_invoke_vertex` 只接受 3 个位置参数 `(prompt, backend, timeout_s)`（确认于 `llm_invoke.py:1475-1479`），`api_key` 是第 4 个参数，Python 抛出 `TypeError: _invoke_vertex() takes 3 positional arguments but 4 were given`。这个 TypeError 不是 LLMInvokeError，会落入 `_invoke_api` except handler 的 UNEXPECTED 分支（`factories.py:378-395`），被当作 bug 而非 truncation 处理。

修法: T3 action 中的 dispatch 逻辑必须显式按 `backend.format` 分支：openai/anthropic 传 `api_key`，vertex 不传。D-4 的"格式无关"表述需要修正为"一个辅助函数，内部按 format 分支 dispatch"。同时需要一个 vertex 格式的测试用例覆盖该路径（当前 Test plan 中无 vertex continuation 测试）。

**2. Finding 2 — 计划 T3 action 的 dispatch 文案与 D-4 的签名声明互相矛盾**

位置: D-4 ("_continue_truncated is format-agnostic and re-invokes the same per-format function") vs T3 action ("dispatch by backend.format calling _invoke_openai(prompt_c, backend, api_key, timeout_s) / _invoke_anthropic(...) / _invoke_vertex(prompt_c, backend, timeout_s)")

缺陷性质: 自相矛盾 — D-4 说"格式无关"，T3 action 实际写了三个不同的调用签名（vertex 少一个参数）。实现者若按 D-4 理解会写出统一调用签名（crash）；若按 T3 action 理解则需要自己推断分支逻辑，增加实现偏差风险。

失败场景: 实现者遵循 D-4 的"format-agnostic"描述，写出 `fn(prompt_c, backend, api_key, timeout_s)` 统一调用，vertex 路径 TypeError。

修法: D-4 改为明确声明：辅助函数是单一入口，内部按 `backend.format` 分支 dispatch，vertex 路径不传 `api_key`。T3 action 的 dispatch 文案保持现状即可。

**3. Finding 3 — T4 Test 1 未验证 breaker 在 _continue_truncated 内部 trip 的传播路径**

位置: T4 Test 1 (test_breaker_trips_across_calls)

缺陷性质: 测试只验证了 trip 后的行为（call_count 不变），但未验证 trip 发生时 `_invoke_api` 的 except handler 中 `isinstance(exc, _TruncatedResponse)` 分支的执行顺序 — 即 `_continue_truncated` 内部 raise 的 `TruncationBreakerError` 是否真的在 continuation request 发出之前就传播出去。

失败场景: 如果实现者将 `breaker.record_truncation()` 放在 `_continue_truncated` 的 loop 内部（budget 循环里）而非 loop 入口之前，第 5 次 truncation 事件仍然会在 loop 内触发 trip，但此时如果 budget=2 且前一次 iteration 已经发出了一个 continuation request，breaker trip 就晚了一个请求。T4 Test 1 的 mock.call_count == 9 仍然通过（因为 4 次 recovery × 2 + 1 × 1 = 9），但语义上多发了一个不该发的 continuation request。

修法: T3 action 应明确 `breaker.record_truncation()` 放在 `_continue_truncated` 的 budget loop **之前**（D-1 的"record the event"），且 T3 Test 7 (test_pre_tripped_breaker_raises_before_dispatch) 已覆盖 pre-trip 场景。但建议在 T4 Test 1 中增加一个断言：当 breaker 在第 5 次 truncation trip 时，`_invoke_openai` 的 call_count 恰好是 9（不多不少），且第 9 次调用就是触发 trip 的那一次 `record_truncation` 之前的 dispatch。当前 mock.call_count == 9 的断言已隐式覆盖，但加一条注释说明 9 = 4×2 + 1 的推导会增强可审计性。

**4. Finding 4 — T3 Test 2 (test_continuation_exhausted) 的 call_count 推导未写明**

位置: T3 Test 2

缺陷性质: 计划声称 call_count == 3，但 side_effect 列表只描述了 3 个元素（raise, raise, raise），未说明为什么第 1 次 llm_invoke call 内部会产生 2 次 _invoke_openai 调用（初始 truncation + 1 次 continuation）加上第 2 次 continuation 的 total = 3。实际上：第一次 llm_invoke → _invoke_openai (call 1, truncation) → _continue_truncated loop iteration 1 (_invoke_openai call 2, truncation again) → budget=2 还剩 1 → loop iteration 2 (_invoke_openai call 3, truncation again) → budget exhausted → raise。所以 call_count=3 的推导是正确的，但计划未写明这个推导。

失败场景: 实现者如果误解 call_count 的含义（以为是 llm_invoke 的调用次数而非 _invoke_openai 的调用次数），可能写出错误的 mock 或断言。

修法: 在 T3 Test 2 的 behavior 描述中加一句 "Total _invoke_openai call count == 3: initial truncation + 2 continuation attempts (budget=2 exhausted)"。

**5. Finding 5 — T3 Test 5 (test_combined_parse_failure_counts_as_attempt) 的行为描述含糊**

位置: T3 Test 5

缺陷性质: 计划说 "first response truncated partial; continuation returns prose that makes partial + tail unparseable and with no '{'-extractable envelope. That counts as a failed continuation (budget decrement); a second same-shape attempt then raises 'continuation exhausted after 2 attempts'; call count == 3."

但这里有一个未说明的语义问题：continuation 返回的 "prose" 是 `_invoke_openai` 的返回值（一个成功的 tuple），不是 raise。`_continue_truncated` 拿到 `(cont, usage_c)` 后做 `combined = truncated.content + cont`，然后 `_strip_fences + json.loads + _extract_json_from_text(combined, expected_keys)` 全部失败。此时 `_continue_truncated` 将这次 parse failure 计为 budget decrement 并 continue。第二次 iteration 同样失败，budget 耗尽，raise exhaustion。call_count = 1 (initial) + 2 (continuation) = 3。推导正确，但 "prose that makes partial + tail unparseable" 的表述可能让实现者误以为是 _invoke_openai raise 了。

修法: 明确写 "continuation _invoke_openai returns successfully but the combined string fails all three parse stages (_strip_fences + json.loads + _extract_json_from_text returns None). This is a failed continuation counted against the budget, not a raise."

**6. Finding 6 — 计划未覆盖 vertex 格式的 continuation 测试**

位置: T3 behavior (tests 1-7), Test plan

缺陷性质: 所有 7 个 T3 测试都使用 openai 格式的 fixture（`_truncated_openai_body`）。没有一个测试验证 anthropic 或 vertex 格式的 continuation 路径。D-4 声称 "all three formats recover through the same code path"，但没有任何测试证明这一点。

失败场景: 实现者为 vertex 路径写了错误的 dispatch（如 Finding 1 所述），没有测试会捕获。vertex 格式的 `_invoke_vertex` 不接受 `api_key`，如果实现者写 `_invoke_vertex(prompt_c, backend, api_key, timeout_s)`，只有运行 vertex 格式的端到端测试才会暴露，而计划中没有。

修法: 至少增加一个 vertex 格式的 continuation 测试（mock `_invoke_vertex` 的 side_effect），验证 helper 在 vertex 路径上不传 `api_key`。同理可增加一个 anthropic 测试（当前 anthropic truncation 检测只有一处 raise，但 continuation 路径未被测试覆盖）。这是 D-4 "the marginal cost is one fixture per format" 承诺的测试，但计划的实际 test plan 未兑现。

**7. Finding 7 — 计划 D-8 的 None-safe guard 措辞遗漏了 `content` 为非字符串类型的情况**

位置: D-8, T3 behavior (zero-output guard)

缺陷性质: D-8 声称 guard 是 `not truncated.content or not truncated.content.strip() or "{" not in truncated.content`。但 `_invoke_openai` 的 `_openai_body` fixture 可以构造 `content=None`（tests:1726-1743），而 `LLMInvokeError` 的 content 字段也可能收到非字符串值（如 int，tests:1745-1757）。`None` 被 `not truncated.content` 捕获（正确），但 `123` 不会触发 `not truncated.content`（`not 123` 是 False），然后 `123.strip()` 抛出 `AttributeError`。

失败场景: 如果某个格式的 truncation 检测代码将非字符串的 content 传入 `_TruncatedResponse`（如 `resolved_cap` 被误传为 content），guard 会在 `.strip()` 处 AttributeError。

修法: guard 应该先检查 `isinstance(truncated.content, str)`，或者在 D-8 的描述中明确写 `not isinstance(truncated.content, str) or not truncated.content.strip() or "{" not in truncated.content`。实际上，三个 truncation raise 站点都从 response dict 的 `choices[0].message.content` 取 content，这个值在正常情况下是 str 或 None，所以风险较低。但防御性编程要求 guard 处理非 str 类型。

---

**总结**: 7 条发现。其中 Finding 1（vertex TypeError）是会导致生产 crash 的实现缺陷；Finding 2（D-4 自相矛盾）会增加实现偏差风险；Finding 3-5 是测试描述的可审计性问题；Finding 6（vertex 测试缺失）是验收覆盖缺口；Finding 7（None-safe guard 措辞）是防御性编程的遗漏。
