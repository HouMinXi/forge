[aicc] session: pc48-r1-ds
⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY or another auth source is set and takes precedence over your claude.ai login · Unset it to load your organization's connectors
# R2 审查报告: FORGE_COMMIT_CLASS 声明类 carve-out（round 2）

## Dispositions 验证（全部实测）

**已确认修复（8/9）**：
- kimi MINOR orphaned "versus the baseline." — 修复 ✓。docs/setup-vscode.md:211-213 现为 item 4 完整句（"no new failures versus the baseline."），新 section 在其后（r2.diff:5-9）。
- kimi MINOR presubmit 未测试 — 修复 ✓。新测试 (o) `test_declared_commit_still_runs_presubmit`（r2.diff:335-354），fixture `_ENTRY_FAILING_SUBMIT`（`command: ["false"]` + `applies_to_grep: ".*"`，r2.diff:188-193）有效：presubmit 块会 `git diff --cached | false` → 失败 → 阻止。**注入 A 实证**（declared_exit 移到 presubmit 前）：`1 failed, 18 passed`，唯一红的是 (o) ✓。
- kimi NIT AI-vocab 未测试 — 修复 ✓。测试 (q)（r2.diff:381-402）："moreover" 在 `_AI_VOCAB_PATTERN` 中（install_hooks.py:196），纯 ASCII 内容先过 non-ASCII gate 再命中 AI-vocab gate；`"AI" in stderr` 只能来自该 gate 的失败消息（其他失败路径的 stderr 均不含 "AI"）。
- kimi NIT wip/chain 未测试 — 修复 ✓。测试 (s) docs 类值（r2.diff:414-431）、测试 (r) chain 变体（r2.diff:404-412，字符串级断言，与既有 (f) 风格一致）。
- ds MINOR 1 review-skip 未测试 — 修复 ✓。测试 (p)（r2.diff:356-379）+ 新 fixture `_make_stub_review_fails`（r2.diff:169-185）：undeclared 提交被 review 失败阻止（证明完整路径运行 review），declared 提交通过（证明声明跳过）。**注入 B 实证**（declared_exit 移到 review 后）：`1 failed, 18 passed`，唯一红的是 (p) ✓。
- ds MINOR 2 — dup，同上。
- ds MINOR 3 env 继承 — 缓解落地 ✓：docs 警告 "Do not `export` it into your shell profile"（r2.diff:26-29）、生成器注释 "One value, one commit"（r2.diff:72）、echo 每提交打印类（r2.diff:80-81）。残余风险陈述（text gates + presubmit 不可被该变量禁用）与实现一致。设计决策（env 是 API）合理。
- ds NIT 4 echo 缺 chain — 修复 ✓。echo 现为 "skipping verify/review/chain/gate-check"（r2.diff:81，实测 install_hooks.py:508）。

**测试计数声称**：96 passed（19 carveout + 71 install_hooks + 6 failclosed）实测一致 ✓。源码注入后已恢复，md5 `6d8efea7...` 一致，96 测试复跑通过 ✓。

## 新发现

**NIT 1 — NIT 5 修复不完整：docstring 残留 "same vocabulary"**（r2.diff:49-50；src/code_forge/install_hooks.py:440-441）
生成器注释已改为 "the class names the session-side trailing # class marker convention uses"（install_hooks.py:499-500），docs 同步（r2.diff:19-20），但 `generate_hook_content` 的 docstring 仍写 "same vocabulary as the session-side trailing marker"——4 类 vs 会话侧 6 个 marker（`post-review-c3|docs|config|chore|wip|humantest`）的不精确表述保留在 docstring 中，与已修复的注释自相矛盾。

**NIT 2 — docs "nothing warns when that happens" 与实现的 echo 矛盾**（r2.diff:26-29；docs/setup-vscode.md:228-229）
export 后每个提交都会触发 case 匹配，hook 每次打印 `code-forge: declared $FORGE_COMMIT_CLASS commit, skipping verify/review/chain/gate-check` 到 stderr（git 默认显示 hook 输出）——并非 "nothing warns"。准确的问题表述应是"没有警告区分主动声明与意外残留"（echo 无法区分两者），而非零警告。

## 结论

Round-1 的两个 MINOR 测试缺口均已由 (o)/(p) 闭合，且各自经过 exactly-one-test-red 注入实证（我独立重注入确认）；两个 NIT 修复（AI-vocab、wip/chain）有效；docs 段落在 item 4 完整句后衔接正确。无新引入的功能性问题。剩余仅两处措辞级不一致（docstring 残留、docs 警告夸大）。

MAJOR:0 MINOR:0 NIT:2

[aicc] session saved: pc48-r1-ds
[aicc] to resume:  aicc ds --cont pc48-r1-ds "continue"
ds rc=0
