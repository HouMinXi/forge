# CP1b R3 (FINAL EXIT) — deepseek: implementer-readiness + acceptance-checkability + coverage

对当前计划文本（54-01-PLAN.md @ main 4087b05，树干净）重跑 R1 分配角度。
R2 之后的变更共三处：我的 L-1/L-2 修复、kimi L-1 的 continuation_breaker
修复（新增第八个钉死标签）。三处全部按"独立核实、不信任计划自述"的原则
对实时源码重验。

## 新增机制声明核实（kimi 修复引入，我 R2 之后进入计划 —— 逐一验证）

- `llm_invoke` 签名确实带 `continuation_breaker` 形参（llm_invoke.py:893，
  类型 `TruncationBreaker | None`）——probe 传 `continuation_breaker=
  TruncationBreaker(threshold=1)` 是可落地的，非计划虚构。
- `TruncationBreaker.__init__(threshold=5)`（:138），`record_truncation`
  先自增后判 `>= threshold`（:143-147）——threshold=1 时第一次记录即抛，
  与计划"first truncation raises"一致。
- 抛出的 `TruncationBreakerError` 是 `LLMInvokeError` 子类且
  `kind="truncated", retryable=False`（:100-118）——probe 的
  `except LLMInvokeError` 能捕获它，分类臂 "truncated-output" 可达。
- 入口事件记录（:1190-1191）在续传预算循环（:1229）之前——计划
  "BEFORE any continuation request" 精确属实；`budget=2`（:1168）属实。
- 传播路径：TruncationBreakerError 从 except 处理器（:1486 的
  `_continue_truncated` 调用）内抛出，穿出 for 循环直达调用方；即使有
  外层吞掉，循环头的 `breaker.check_tripped()`（:1381）会在下一次尝试、
  任何网络调用之前再次抛错——60s 硬边界双重成立。
- 默认新 breaker 兜底（:1367-1371，`else TruncationBreaker()`）与计划
  ":1370" 引用一致；零重试由 :1497（`not retryable or attempt ==
  max_attempts - 1 → raise`）保证。
- cap 解析（:269-280）：`max_completion_tokens or max_tokens` +
  `output_ceiling > 0` 才覆盖——copy 上双零化后 cap=32 成立；thinking
  空串即省略（:283-287）、reasoning_effort 空串即省略（:290-294）属实。
- 基线 `grep -c 'kind="' llm_invoke.py` 实测 = 16（实时执行），16→31
  算术成立；新增 docstring 枚举位点（:71-77）现状无引号拼写（16 个命中
  不含该区），按既有风格扩充不改变计数。

## 我的 R2 修复在现行文本中的落位核实

- **L-1（warn 作用域）**：Task 2 action 明确 "on the two MUTATING paths
  only (bare trust and --revoke)"，并显式警告 "Do NOT place the warn at
  the shared post-resolution prefix"；test (f) 断言从子目录跑 --status
  无 warn 行。两处文本不再拉扯，两个实现者会产出相同行为。
- **L-2（fallback 标签）**："http-error"（exit_code>=400 且无 kind）与
  "unclassified"（其余未知）双双钉死，且分支顺序在 behavior 列表内
  固定（is_timeout → 401/403|credentials → conn → sse_body → bad_body
  → truncated → http-error → unclassified）——headline 404 情形
  （exit_code 置位、kind=""）按顺序正确落入 http-error，401/403 先被
  credential-rejected 拦截，无顺序歧义。

## 第八标签（truncated-output）传播面核实

计划内四处 + VALIDATION.md 一处全部同步：
- 目标行（:87 "eight-class error taxonomy"）
- Task 4 behavior（:468 "All eight labels are pinned by tests"）
- Task 5 test (d)（:616 "each of the eight error classes"）
- 成功准则（:777 "eight pinned classes"）
- VALIDATION.md:46 "8 pinned taxonomy labels (5 D-04 + truncated-output
  + http-error + unclassified)"
全计划扫描无遗留 six/seven 旧计数、无未钉死的 "fallback class" 措辞。

## 锁定决策一致性（coverage 角度）

- D-04（CONTEXT.md:34-36）五类与计划钉死的五个 D-04 标签逐一对应；
  Claude's Discretion（CONTEXT.md:73-76）明确将 taxonomy strings 委托给
  计划者——新增三个标签不构成锁冲突。
- Task 6 smoke 检查 3 现在预期 http-error 行 + Step 1.5 的 excerpt
  见证，检查 2 的 "near 60s (not the 600s cap)" 与 breaker 修复一致。
- 防树漂移复核：`_format_error_message`（llm_invoke.py:691-715）当前
  仍然接收 body_excerpt 而丢弃（返回值 :713-715 不含它）——Step 1.5 的
  前提在现行树上依旧成立。

## 反模式自检

- 未重提任何已裁定项；对两项历史结论（16→31 计数形状、白名单负向
  测试落点）本次实测复核仍成立，属 CONFIRMING 而非新发现。
- 未发现任何已裁定项在当前文本/源码中被改错的情况。
- 声明位置 A-F 自 R1/R2 裁定后无计划变更，维持 ACCEPT。

## Findings

无。三个 R2 遗留（我的 2 个 + kimi 的 1 个）在现行文本中均已正确修复
且全部传播到位；kimi 修复引入的新机制声明逐条经实时源码核实准确。

SCORECARD: B=0 H=0 M=0 L=0
