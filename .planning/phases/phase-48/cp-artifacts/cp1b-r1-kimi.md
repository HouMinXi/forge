作为 forge Phase 48 计划的对抗评审员，基于已读代码（`llm_invoke.py`、`factories.py`、`progress.py`、`machine.py`、`gate.yaml`、`mcp_server.py`）和计划全文，找出以下 5 条会导致执行失败或实现跑偏的缺陷。

---

1. **[T4-计数] 测试期望要求恢复成功不重置 breaker，但计划未声明修改 fold 的 record_success 触发条件** @T4 Test 1 / D-1 / `factories.py:424-425`。失败场景：D-1 声明 `record_success()` 在 clean 调用时 reset count，而 T4 Test 1 期望 calls 1-4 每次 recovery 后 count 累计到 1..4 并在 call 5 trip；若 fold 仍对所有成功调用 `record_success()`，每次 recovery 都会把 count 归零，call 5 的 truncation 只让 count=1，测试红且 `mock.call_count == 9` 的断言不成立。修法：在 T4 行动项中明确修改 fold，使 `continuation_breaker.record_success()` 仅在 `result.is_truncated is False` 时调用；或改写 T4 Test 1 为“连续 5 次未恢复 truncation”才 trip。

2. **[T3-None] `_continue_truncated` 未防护 continuation 返回 `content=None`** @T3 helper / `_continue_truncated`。失败场景：continuation 请求返回 `content=None` 且 `finish_reason="stop"` 时，helper 执行 `truncated.content + cont` 会触发 `TypeError`，而不是按预期计为一次失败尝试。修法：在 helper 拼接前将 `cont` 归一化为字符串（`None -> ""`），并把空/None continuation 视为 parse failure 扣减 budget。

3. **[T0-A1] T0 probe 无法可靠验证 `finish_reason=length` 假设** @T0 / D-3。失败场景：`max_attempts=1` 不会禁用 continuation（budget 仍为 2），`sn-deepseek-flash` 触发 length 后可能被恢复成成功，probe 输出 `unexpected_success` 而非 `kind=truncated`，无法直接检测 A1 漂移。修法：probe 显式禁用 continuation（例如传入 `threshold=0` 的 `TruncationBreaker` 让首次 truncation 即 trip，或新增 `budget=0` 路径），确保直接观察到原始截断错误类别。

4. **[T3-逃逸] continuation 内的非截断可重试错误会逃逸到外层 `max_attempts` 循环** @T3 helper / `_invoke_api` except handler。失败场景：continuation 请求遇到 `URLError`/5xx 等 retryable 错误时，helper 只捕获 `_TruncatedResponse`，其它 `LLMInvokeError` 直接抛出外层，导致原始完整 prompt 按 `max_attempts` 重试，而不是把 continuation 失败计入手自己的 budget。修法：在 `_continue_truncated` 内统一捕获 `LLMInvokeError`，将非 truncation 错误视为本次 continuation 失败并扣减 budget，或在文档/测试中显式声明此行为。

5. **[D-11-防御] usage 求和未归一化 `usage_data` 类型** @D-11 / T3 helper。失败场景：测试或异常网关构造的 `_TruncatedResponse(usage_data=None)` 进入 helper 后，`.get()` 调用触发 `AttributeError`。修法：在求和前对 `truncated.usage_data` 和每次 `usage_c` 执行 `or {}` 归一化。

---

额外说明：关于 (b) 截断续写与 `f91605b` json-retry 的相互作用，计划已正确识别“检测到的 truncation 在 parse 之前 raise，不会进入 no_json retry”，且未检测到的 truncation（provider 报 `stop`）走 no_json 重试是已记录的已知 gap，不算新 bug；(c) breaker 的 lock + `check_tripped` 语义本身设计正确，缺陷在于 fold 如何触发 `record_success`；(e) 三种格式键名求和映射与 `_invoke_api:1072-1091` 一致，正确；(f) 主要 file:line 引用与当前磁盘状态一致，未发现明显漂移。
