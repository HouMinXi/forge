[aicc] session: anchor-r1-kimi
⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY or another auth source is set and takes precedence over your claude.ai login · Unset it to load your organization's connectors
已核对两个 NIT fix 与真实文件一致：

- `src/code_forge/llm_invoke.py:978` 现在使用 `cause = " ".join(str(exc).split())[:400]`，仅把任意空白符合并为单个空格。实测 OmniRoute 风格的 `"this is OmniRoute's request queue -- not an upstream timeout"` 归一化后仍保留 `"--"`；双连字符等分隔符不会丢失。
- `tests/test_machine_local.py:473` 已重命名为 `test_three_consecutive_rounds_with_a_failed_pass_stop_the_run`，docstring 明确说明计数的是“包含失败 pass 的连续 round”，而不是“连续失败 pass”，与 `machine.py:748` 的实现一致。

最终轮检查项：

1. **early-abort breaker**：`_check_l1_can_still_converge` 仍按 `derive_pass_outcomes` 判定每轮是否有未完成 pass，全 clean 时 `_rounds_with_failed_pass` 清零，连续 3 轮触发 `TimeoutBreaker`，消息文本准确。
2. **check 8 共享**：`verify.py:566` 的 `# 8.` 仍位于 `if hardened ... else ...` 同级之后，对 hardened 与 legacy 路径均生效。
3. **anchor filter**：`receipt.py:127` 仍只在 `anchors` 中过滤 `source == "INFRA"`，findings 与 pass_status 保留，下游 verify check 3 不会被 sentinel 阻塞。
4. **R3_DISPOSITION_EVIDENCE.md**：其驳回 qodo 两个 findings 的论据仍成立；check 8 在 legacy 路径执行，且 coverage 测试与 check 8 测试职责分离。

执行验证：
- `PYTHONPATH=src pytest tests/test_verify.py tests/test_machine_local.py` → 118 passed
- `PYTHONPATH=src pytest tests/test_llm_invoke.py` → 226 passed
- `ruff check` on 6 个改动文件 → passed
- non-ASCII check → 无命中

两个 NIT fix 未引入新问题。

MAJOR:0 MINOR:0 NIT:0

[aicc] session saved: anchor-r1-kimi
[aicc] to resume:  aicc kimi --cont anchor-r1-kimi "continue"
kimi rc=0
