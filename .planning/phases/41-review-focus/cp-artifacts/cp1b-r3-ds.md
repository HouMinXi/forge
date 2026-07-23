基于对实际代码（cli.py、trust.py、mcp_server.py、factories.py、git.py、legacy.py、cross_repo.py、test_git.py、test_legacy.py）与当前计划（提交的 Plan 文本）的对照审查，以下是审查结果。

先将各验证点总结如下：

**H1 修复验证（Task 3a-3）** — 正确。`_load_gate_yaml_raw` 是一个独立于 `_load_gate_backends` 的原始解析函数，没有信任门控。聚焦读取的每个地方都使用它，而 `_load_gate_backends` 保持不变。当后端未受信任但 review_focus 受信任时，`## Review Focus` 仍会注入。所有 6 个验证点均已确认。

**先前轮次的修复** — 所有内容均符合当前计划，并已针对实际代码验证（B1、已取代的 3b-3/4/5 标记、D5.7、H3 阶段门控、M1 合并优先的 dict、M2 `dict` 签名、M3 存储键、M4 环境变量测试）。未发现回归。

**新发现：**

**M1 Task 3a-1：`_merge_focus_spec` 中缺少 yaml/文件分隔符**

- **位置**：Plan 第 3a-1 小节（约第 301-310 行）
- **描述**：Plan 指定 `_merge_focus_spec` 将 `yaml_focus` 和 `file_content` "连接"起来。`_merge_contract_spec`（cli.py:1855-1889）在两者都存在时使用 `(merged + "\n\n" if merged else "")` 在它们之间插入 `\n\n`。对于焦点，此分隔符未被指定。一个空的 YAML 值（`review_focus: short text`，不含尾随 `\n`）和一个空的文件内容（`--focus FILE`，以文本开头）将被拼接成 `"short textFile content"` — 一个连续无间隔的字符串。Plan 将"更简单"的特性明确列举为：无 LLM 摘要、无 Do Not Flag 拆分、无确认偏误指令。分隔符的省略未被列为刻意的简化。最安全的实现（与合约路径一致）将添加 `\n\n`。
- **建议修复**：在 3a-1 的伪代码中添加 `merged = (merged + "\n\n" if merged else "") + file_content`，使其与 `_merge_contract_spec` 的模式一致。或者，如果故意省略，则将该省略明确列为刻意的简化。

**L2 Task 3b replan (b)：三个消费者元组更新未在指定测试中得到验证**

- **位置**：Plan replan (b) + (e)（约第 570-605 行和第 650-720 行）
- **描述**：Plan 正确要求在所有三个消费者（`snapshot_tempfile_paths` mcp_jobs.py:124、`_wait_for_job` finally :308、`_evict_stale` :353）的迭代元组中添加 `"focus_tempfile_path"`。Bug 注入要求如下："将 `"focus_tempfile_path"` 从一个消费者的迭代元组中删除 → 该消费者的泄漏测试必须失败。分别对每个位置进行注入。" 但 (e) 中指定的五个测试仅针对 `_dispatch_cli` 场景（任务成功保留 focus、无 focus 时无 tmpfile、运行抛出异常时取消链接、取消运行时取消链接、start_job 抛出异常时取消链接）。这些测试并未分别针对 _evict_stale 或 snapshot_tempfile_paths 进行覆盖。除非添加至少一个针对每个消费者的显式泄漏测试，否则这三个消费者之一上面丢失的元组条目将悄无声息地被测试套件忽略。
- **建议修复**：在 (e) 中添加以下内容：(i) 一个测试，通过 TTL 超时触发 _evict_stale 清理，并断言 focus_tempfile_path 已取消链接；(ii) 一个测试，断言 snapshot_tempfile_paths 返回 focus_tempfile_path；(iii) 一个分别针对每个消费者的 bug 注入说明，指定使用哪个测试来检测它。

`SUMMARY: B=0 H=0 M=1 L=1`
