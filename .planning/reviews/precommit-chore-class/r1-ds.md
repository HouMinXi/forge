[aicc] session: pc48-r1-ds
⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY or another auth source is set and takes precedence over your claude.ai login · Unset it to load your organization's connectors
# R1 审查报告: FORGE_COMMIT_CLASS 声明类 carve-out

验证记录（全部实测）：85 个受影响测试通过；生成的 hook 通过 `sh -n`；两个 bug 注入实验（已恢复，md5 `791e943e...` 一致）；sharpened 断言用旧断言逻辑实测确认必要。

## 已验证为正确的部分

- **顺序与 docstring 一致**：carveout → declared → attestation → d12 → presubmit → declared-exit → review → chain → exec（install_hooks.py:539-571，两变体均含新块）。
- **POSIX shell 正确**：`sh -n` 通过；`${FORGE_COMMIT_CLASS:-}` 是 POSIX 参数展开；`_FORGE_DECLARED=` 在 case 前初始化，`set -u` 安全；双引号内展开无分词风险。
- **Fail-closed 正确**：无效值（(n) 测试）、未设置（(k) 测试）、空字符串（case 模式不匹配，逻辑验证）均回退完整 gate。
- **Sharpened 断言忠实且必要**：实测旧断言 1 在新内容下失败（新注释含 "presubmit" 一词，`False`）；旧断言 3 匹配到新 echo 行（`line 25`，presubmit 之前）导致 `presubmit<gate` 不成立——sharpening 是被新代码破坏后的必要修复，且新锚点（`"code-forge: presubmit"` / `"exec " + "gate-check"`）精确匹配 presubmit FAILED 行与 `exec /usr/bin/code-forge gate-check` 行，原意图（空条目无块、presubmit 在 gate-check 前）保持。
- **声明路径的 text gates 保留**：(m) 测试实证 non-ASCII gate 在 declared-exit 之前运行。

## Findings

**MINOR 1 — 声明路径跳过 LLM review 无任何测试覆盖**（r1.diff:158，`'  *) exit 0;;'`）
`_make_stub_verify_fails` 与 `_make_stub` 的 `*)` 分支使 review 恒 exit 0。注入实验：将 `declared_exit_block` 移到 `review_block` 之后（声明提交将实际执行 review），14/14 测试全绿。若未来组装顺序被改动或 review 块被移入声明路径，无测试变红。brief 声称 "declared commits skip LLM review" 无测试保障。

**MINOR 2 — 声明路径保留 presubmit 无测试覆盖**（r1.diff:166，`content = generate_hook_content("code-forge gate-check", None)`）
`_install_hook` 传 `presubmit_entries=None` → presubmit 块恒为空；test_hook_carveout.py 中无任何 `presubmit_entries` 引用。注入实验：将 `declared_exit_block` 移到 `presubmit_block` 之前（声明提交将跳过 presubmit linters），14/14 测试全绿。这是 brief 声称的 "presubmit linters still run for declared commits" 的唯一无覆盖点——Golden Rule 2 的注入位置应补在此处。

**MINOR 3 — env 继承可意外扩大跳过范围**（r1.diff:17，docs 示例行）
`export FORGE_COMMIT_CLASS=chore` 后，该 shell 中所有后续提交（含逻辑代码提交）静默跳过 attestation/review/gate-check，无单次使用机制。docs 声称 "declare the class explicitly for this one commit"（r1.diff:14）但实现无法强制。会话侧 hook `check_git_commit_review.sh:244` 只解析命令末尾 marker（`#\s*(post-review-c3|docs|config|chore|wip|humantest)\s*$`），不认识此变量——AI 会话仍受 marker 约束，但终端手动路径无第二道防线。风险面窄于 `git --no-verify`（text gates 与 presubmit 仍运行），但比 marker 隐蔽（env 残留无操作痕迹）。建议：文档警告 env 持久性，或考虑在提交消息中编码类（commit-msg 可读）。

**NIT 4 — echo 消息遗漏 chain**（r1.diff:75-76）
`echo "... skipping verify/review/gate-check"` 未提 chain，但声明提交在 chain 变体中同样跳过已有 hook（注释和 docstring 均提到）。措辞不一致，纯 NIT。

**NIT 5 — "same vocabulary" 文档措辞不精确**（r1.diff:20-21）
会话侧 hook 接受 6 个 marker 值（`post-review-c3|docs|config|chore|wip|humantest`），FORGE_COMMIT_CLASS 只接受 4 个。用户按 `# humantest` 习惯设置 `FORGE_COMMIT_CLASS=humantest` 会静默回退完整 gate——fail-closed 方向正确，但 "the same vocabulary as the trailing marker convention" 的说法不准确。

## 结论

实现本身正确：gate 语义（跳过集合 = 非代码 carve-out 的跳过集合 + 额外保留 text gates 与 presubmit）、fail-closed、POSIX shell、sharpened 断言全部经实测验证。缺口集中在测试层面：声明路径的 review-skip 与 presubmit-preservation 两个核心声称各缺一个 bug 注入位点（注入实验均全绿通过），以及 env 继承的意外扩大风险无缓解。

MAJOR:0 MINOR:3 NIT:2

[aicc] session saved: pc48-r1-ds
[aicc] to resume:  aicc ds --cont pc48-r1-ds "continue"
ds rc=0
