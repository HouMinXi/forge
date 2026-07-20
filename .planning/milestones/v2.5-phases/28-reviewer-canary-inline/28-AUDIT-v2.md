# Phase 28 v2 实施计划独立审计报告

**审计范围**：forge 项目 Phase 28 "Reviewer Canary for the Inline Outlet" v2 修订版实施计划（`28-01-PLAN.md` ~ `28-04-PLAN.md`）  
**审计日期**：2026-06-24  
**审计方法**：plan-review PBR 8-pass 自检 + 三轮冷 agent 交叉评审（DeepSeek / MiMo / Kimi）+ 实际源码 file:line 地面验证  
**总体裁决**：**REQUEST_CHANGES**

---

## 1. 首轮 7 项 MUST-FIX 落实情况

| # | 首轮要求 | 计划中的修复 | 是否真正解决 | 说明 |
|---|----------|--------------|--------------|------|
| MF-1 | dispatch try/except 降级 | Plan 01 Task 2 + Plan 03 Task 1 双层 try/except | 形式解决，存在交互缺陷 | 异常会被捕获并降级为 DELEGATED，但 B-01 的 `validate_reviewer_json` 误用会让正常门控路径也被吞掉 |
| MF-2 | `CanaryProvider` 签名简化 | 改为 `(diff_text: str) -> list[dict]` | 已解决 | `28-01-PLAN.md:152` |
| MF-3 | `n` 范围 3..5 | `validate_canary_config` 限制 3..5 | 已解决 | `28-02-PLAN.md:197,216-217` |
| MF-4 | Plan 03 接入 `canary_provider` | `_canary_provider` 闭包接入 | 结构解决，运行时仍失败 | prompt 缺少 `original` 字段，见 B-03 |
| MF-5 | partition 先于 cite-verify | 步骤 9→10 顺序正确 | 已解决 | `28-01-PLAN.md:227-230` |
| MF-6 | `_source_lookup` 路径穿越防护 | 已加 `realpath` + `commonpath` 检查 | 表面修复 | `startswith` 前缀判断可被同名目录绕过，见 B-04 |
| MF-7 | `threshold_ratio: 0.0` 崩溃 | 验证器拒绝 0.0，运行时再加 clamp | 已解决 | 但 clamp 与验证器语义重复，见 W-04 |

---

## 2. BLOCKER（执行前必须修复）

### B-01 `validate_reviewer_json` 被错误地用于校验单条 finding，导致 canary 门控被绕过

- **严重性**：BLOCKER
- **位置**：`28-01-PLAN.md` Task 2 `dispatch_canary_review`（行 213-217）；`28-03-PLAN.md` Task 1 `_review_provider`（行 225）
- **描述**：计划要求把 LLM 返回的每个 finding 单独传给 `validate_reviewer_json`。但 `src/code_forge/reviewer_json.py:12,35-49,69-73` 的契约是校验**顶层信封**（必须同时存在 `findings` 和 `code_excerpts`），并且对空 findings + 空 excerpts 抛 `ValueError`。单条 finding dict 缺少顶层字段会全部被拒；即使结构正确， rubber-stamp 零 finding 也会触发 `ValueError`，被外层 try/except 捕获后降级为 `DELEGATED`，门控完全失效。此外 `reviewer_json.py:50` 要求 severity 必须是 `P0/P1/P2/P3`，而 canary prompt 未指定该枚举，LLM 返回 "high" 等合法字符串也会被拒。
- **证据**：
  - `reviewer_json.py:12` `_REQUIRED_FIELDS = {"findings", "code_excerpts"}`
  - `reviewer_json.py:69-73` 对 `len(findings)==0 and len(code_excerpts)==0` 抛 `ValueError`
  - `28-01-PLAN.md:217` "Passes each finding through `validate_reviewer_json`"
- **建议**：新增专用 `validate_canary_findings(findings: list[dict]) -> list[dict]`，只检查每条 finding 的 `{file, line, severity, description}`，不检查顶层 envelope，不要求 `code_excerpts`；severity 放宽为非空字符串或在 prompt 中明确允许值。

### B-02 `Verdict("UNRELIABLE")` 回退在 Wave 1 并行场景下崩溃

- **严重性**：BLOCKER
- **位置**：`28-01-PLAN.md` Task 2 `run_inline_canary`（行 233）
- **描述**：计划使用条件回退 `if hasattr(Verdict, "UNRELIABLE") then Verdict.UNRELIABLE else Verdict("UNRELIABLE")`。当 `UNRELIABLE` 尚未定义时，构造 `Verdict("UNRELIABLE")` 会抛 `ValueError: 'UNRELIABLE' is not a valid Verdict`，比它想防范的问题更严重。`28-01` 与 `28-02` 都声明 `depends_on: []` 且同处 Wave 1，并行执行时此路径确实会触发。
- **证据**：
  - `src/code_forge/state.py:29-35` 当前 Verdict 只有 `PASS/FAIL/ESCALATED/PENDING/DELEGATED`
  - `28-01-PLAN.md:233` 脆弱回退逻辑
- **建议**：在 `28-01-PLAN.md` 的 `depends_on` 中加入 `28-02`；或让 `run_inline_canary` 返回字符串 `"UNRELIABLE"`，由 Plan 03（已依赖 Plan 02）转换为枚举。

### B-03 `_canary_provider` prompt 缺少 `original` 字段，导致 `generate_canaries` 运行时 KeyError

- **严重性**：BLOCKER
- **位置**：`28-03-PLAN.md` Task 1 `_canary_provider` 闭包（行 195-209）
- **描述**：`_canary_provider` 的 prompt 要求 LLM 返回 `{"file", "line", "code", "description"}`，但 Plan 01 的 `generate_canaries` 在校验非等价性时需要 `result["original"]`（`28-01-PLAN.md:164`）。prompt 未要求 `original`，LLM 大概率不返回，校验阶段会抛 `KeyError`，同样被 try/except 吞为 `DELEGATED`。
- **证据**：
  - `28-01-PLAN.md:164` "For each result, verify `is_non_equivalent(result['original'], result['code'])`"
  - `28-03-PLAN.md:199-207` prompt 未包含 `"original"`
- **建议**：prompt 中明确要求 `"original"` 字段：`{"mutations": [{"file": "...", "line": N, "code": "...", "original": "...", "description": "..."}]}`。

### B-04 `_source_lookup` 的路径穿越防护可被同名前缀目录绕过

- **严重性**：BLOCKER
- **位置**：`28-03-PLAN.md` Task 1 `_source_lookup`（行 229-243）
- **描述**：containment 检查使用 `full.startswith(cwd_real + os.sep)`。若 `cwd_real` 为 `/tmp/foo`，攻击者可用相对路径 `foobar/etc/passwd`，其绝对路径 `/tmp/foobar/etc/passwd` 仍满足 `startswith('/tmp/foo/')`，从而穿越到兄弟目录。
- **证据**：`28-03-PLAN.md:234-237` `if not full.startswith(cwd_real + os.sep) and full != cwd_real`
- **建议**：使用 `os.path.commonpath([cwd_real, full]) == cwd_real` 做真正的包含判断，并保留 `ValueError` 的盘符异常处理。

### B-05 diff 计算误用 `args.mode`，无法覆盖实际 review 模式

- **严重性**：BLOCKER
- **位置**：`28-03-PLAN.md` Task 1 inline branch（行 180-186）
- **描述**：计划按 `args.mode in ("staged", "unstaged", "whole-file")` 选择 `git diff --cached` 或 `git diff HEAD`。但 `src/code_forge/cli.py:206` 中 `--mode` 的合法值只有 `["local", "ci"]`，所以该条件恒为假，canary 总是拿 `--cached`；`--whole-file` 等场景直接错误。DeepSeek 进一步指出 `git diff HEAD` 包含 staged + unstaged，不是仅 unstaged。
- **证据**：
  - `28-03-PLAN.md:183` `if hasattr(args, "mode") and args.mode in ("unstaged", "whole-file")`
  - `src/code_forge/cli.py:206` `--mode` choices = `["local", "ci"]`
- **建议**：不要重新实现 diff 逻辑，复用现有 `resolve_mode` / `baseline` / `head` / `--whole-file` 的 diff 源；若必须内联，则使用真实标志：`staged -> git diff --cached`，`unstaged -> git diff`，`whole-file -> git diff --cached -U9999`。

### B-06 `_load_canary_config` 重新读取 `gate.yaml`，违反 trust guard

- **严重性**：BLOCKER
- **位置**：`28-03-PLAN.md` Task 1 `_load_canary_config`（行 164-168）
- **描述**：`cli.py:1295-1298` 明确注释 "Never re-read gate.yaml raw after this point -- a second read bypasses the trust check." Plan 03 却建议 `_load_canary_config` 自己 `yaml.safe_load` 一次；同时 `_load_gate_backends` 只返回 backend 配置，不会调用 `load_gate_config` 的 `validate_canary_config`，非法值会流到运行时。
- **证据**：
  - `src/code_forge/cli.py:1295-1298` trust guard 注释
  - `28-03-PLAN.md:166-168` "prefer re-reading via yaml.safe_load"
- **建议**：修改 `_load_gate_backends` 返回完整 gate dict（或 `load_gate_config` 返回的 data），`_load_canary_config` 从已校验的数据中取 `canary` 段，并调用 `validate_canary_config(canary)`。

### B-07 模板 fallback 仍使用 synthetic appended file，违反 D-28-02

- **严重性**：BLOCKER
- **位置**：`28-01-PLAN.md` Task 1（行 160）与 Task 2（行 213-215）
- **描述**：模板库使用 `uuid.uuid4().hex[:8]` 生成文件名并通过 "append a unified-diff hunk" 注入，本质仍是 synthetic appended file。`28-CONTEXT.md:119-122` 明确要求 "in-place SEMANTIC mutation of the real diff, NOT a synthetic appended file"。首轮 Kimi BLOCKER 已指出此问题，v2 仍未解决。
- **证据**：
  - `28-01-PLAN.md:160` "Use uuid.uuid4().hex[:8] in file names"
  - `28-01-PLAN.md:213-215` "appends a unified-diff hunk to the copy"
  - `28-CONTEXT.md:119-122` D-28-02 锁定决策
- **建议**：模板 fallback 也应解析现有 Python hunk、替换真实变更行并复用真实文件路径；若工程上必须保留 synthetic 路径，应在 `28-CONTEXT.md` 中显式修订 D-28-02 并记录偏差，否则视为 BLOCKER。

---

## 3. WARNING（强烈建议修复）

### W-01 `gate.schema.json` 的 `canary` 对象 `additionalProperties: false` 与 validator 不一致

- **位置**：`28-02-PLAN.md` Task 2（行 229）
- **描述**：`validate_canary_config` 明确允许未知 key（行 199），但 schema 使用 `additionalProperties: false`。`graph_triage`、`daemon_state` 等现有 section 均为 `additionalProperties: true`，此设置破坏前向兼容。
- **证据**：`src/code_forge/gate.schema.json:92,130` `additionalProperties: true`
- **建议**：将 `canary` 对象的 `additionalProperties` 改为 `true`。

### W-02 根解析器与 review 解析器的 epilog 仍缺少 exit code 5/6

- **位置**：`28-03-PLAN.md` Task 1（行 161-162）；`src/code_forge/cli.py:162-169,190-197`
- **描述**：当前根解析器和 review 解析器的 epilog 都只列出 0-4。Plan 03 只追加 7，仍不补 5 (`DELEGATED`) 和 6 (`TIMEOUT`)，用户看到 0,1,2,3,4,7 的断裂序列。
- **证据**：`src/code_forge/cli.py:162-169` 根 epilog；`190-197` review epilog
- **建议**：两个 epilog 同步补全：`5  DELEGATED`、`6  TIMEOUT`、`7  UNRELIABLE`。

### W-03 `_canary_provider` 静默吞掉所有异常

- **位置**：`28-03-PLAN.md` Task 1 `_canary_provider`（行 195-218）
- **描述**：`_canary_provider` 使用 `except Exception: return []` 捕获所有异常。生成失败（如后端返回畸形 JSON）会被悄悄吞掉，用户得到模板回退却无任何信号。
- **建议**：在返回空列表前向 `stderr` 输出警告，例如 `"canary generation failed: {exc}, falling back to templates"`。

### W-04 `threshold_ratio` 运行时 clamp 与验证器语义重复

- **位置**：`28-01-PLAN.md` Task 2（行 226）；`28-02-PLAN.md` Task 2（行 198）
- **描述**：Plan 02 的验证器已拒绝 `threshold_ratio: 0.0`，Plan 01 又做 `max(1, ceil(...))` clamp。若验证器生效，clamp 不会触发；若绕过验证器直接调用，clamp 才生效。语义上 0.0 等于禁用门控，应只由验证器负责。
- **建议**：保留 Plan 02 拒绝 0.0 的验证，Plan 01 的 clamp 加注释说明是防御性编程，正常路径不会到达。

### W-05 Wave 1 内部存在隐式运行时依赖，但 `depends_on` 为空

- **位置**：`28-01-PLAN.md:6`；`28-02-PLAN.md:5-6`
- **描述**：`canary_gen.py` 需要 `Verdict.UNRELIABLE` 和 `EXIT_UNRELIABLE`，两者由 `28-02` 提供。两计划都声明 `depends_on: []` 且同为 Wave 1，并行执行时 `28-01` 的测试/导入会先失败。
- **建议**：在 `28-01-PLAN.md` 的 `depends_on` 中加入 `28-02`，或把 UNRELIABLE 基础设施先合并。

---

## 4. NOTE（非阻塞，建议改进）

### N-01 `_canary_provider` 返回类型标注不精确

- **位置**：`28-03-PLAN.md` Task 1（行 195）
- **描述**：函数签名写成 `def _canary_provider(diff_text_arg: str) -> list:`，与 `CanaryProvider` 协议要求的 `list[dict]` 不一致。
- **建议**：改为 `-> list[dict]`。

### N-02 inline branch 内重复实现 git diff，未复用现有 diff 源

- **位置**：`28-03-PLAN.md` Task 1（行 180-186）
- **描述**：正常 review 路径已有 diff 计算逻辑，inline branch 重新调用 `subprocess.run(["git", "diff", ...])`，容易与 `--baseline`、`--head`、`--committed`、`--whole-file` 等标志失步。
- **建议**：抽象出获取 diff text 的公共函数供正常路径和 canary 路径共用。

### N-03 缺少 Plan 05 或主会话来覆盖 real-model smoke test

- **位置**：`28-CONTEXT.md:205-206`；`28-REVIEWS.md:40-44`
- **描述**：CONTEXT 要求 deliverable (g) 包含一次真实模型 smoke test，但四份计划里没有任何一份把它列为可执行任务，仅在 acceptance 中提及。
- **建议**：在 `28-04` 末尾增加一个 Task，或在主会话 wrap-up 中明确记录 real-model smoke 的触发条件与通过标准。

---

## 5. 冷 agent NEW findings 验证

| 编号 | 内容 | 评估 |
|------|------|------|
| NEW-BLOCKER-1 | `validate_reviewer_json` 与 canary reviewer 输出契约不匹配 | **成立**，见 B-01 |
| NEW-BLOCKER-2 | `Verdict("UNRELIABLE")` 回退崩溃 | **成立**，见 B-02 |
| NEW-BLOCKER-3 | epilog 缺少 5/6/7 | **成立**，但根解析器同样缺失，见 W-02 |
| NEW-WARNING-1 | `_load_canary_config` 重读 `gate.yaml` 绕过校验/信任守卫 | **成立**，升级为 BLOCKER，见 B-06 |
| NEW-WARNING-2 | diff mode 逻辑错误 | **成立**，升级为 BLOCKER，见 B-05 |

---

## 6. 最终裁决

**REQUEST_CHANGES**

v2 计划对首轮 7 项 MUST-FIX 做了形式上的响应，但 MF-6 的路径穿越防护、MF-1 与 `validate_reviewer_json` 的交互、MF-4 的 prompt 字段完整性、SF-1 的 diff mode 选择仍存在实质性缺陷；加上 `Verdict("UNRELIABLE")` 回退、`_load_canary_config` 违反 trust guard、synthetic appended file 违反 D-28-02 等问题，当前计划若直接执行会在真实运行路径上产生崩溃或门控被绕过。

**必须在执行前修复的 BLOCKER 共 7 项**：B-01 ~ B-07。  
**强烈建议修复的 WARNING 共 5 项**：W-01 ~ W-05。  
**建议改进的 NOTE 共 3 项**：N-01 ~ N-03。

建议在修复后重新走一轮 plan-review（PBR 8-pass + 至少一轮 cross-AI 收敛），并验证每个 BLOCKER 都有对应的回归测试或验收命令。
