# CP1b R4 — deepseek: text-only micro-fix confirmation (Task 5 action + verify)

R4 范围锁定：只核验本轮落地的两处文本微修复（54-01-PLAN.md Task 5 的
<action> 与 <verify> 块），不重审其余计划（R3 已 0/0/0/0 退出确认）。

## 修复 2（verify 追加 tests/test_mcp_server.py）— 干净

计划 :662 已亲眼确认：`<automated>` 命令链末尾追加了
`tests/test_mcp_server.py`。XML 结构完好（标签闭合正常），与
VALIDATION.md 的 quick-run 及 Task 4 的 verify 命令一致。无新问题。

## 修复 1（显式写出导入语句）— 锚点准确，但拼写与所引用风格自指矛盾

核实过程（实时源码，非计划自述）：
- 引用的三个风格锚点行号准确：doctor.py:110（`from code_forge.trust
  import trust_status`）、:126-128（`from code_forge.backend import
  load_backend_configs, probe_backend` 等三行）、:168（`from
  code_forge.outlet_resolver import resolve_outlet`）——均已亲眼读出。
- 但这三处（以及该文件全部函数内导入）是**绝对导入**：
  `grep -c "from \." doctor.py` = 0（全文零相对导入）。
- 而修复文本写的是**相对导入** `from .backend import probe_backend_live`
  （计划 :639），与"match the style at doctor.py:110/126/128/168"的
  声称正好相反——所引用锚点的风格恰恰不是这个拼写。
- 跨任务不一致：Task 3 action（计划 :368）为同一文件的同类函数内导入
  写的是绝对拼写 `from code_forge.user_config import ...`。同一文件、
  同一场景，两个任务给出两种拼写。

### Finding

**L-1 — 修复 1 的导入拼写与其引用的风格锚点相反，且与 Task 3 跨任务
不一致（implementer-readiness / two-reader divergence）**
- 位置：54-01-PLAN.md:639-641（Task 5 action 新增句）。
- 证据（已核实）：doctor.py 全部函数内导入为绝对导入
  （:110/:126-128/:168，`grep -c "from \."` = 0）；计划 :368（Task 3）
  亦用绝对拼写。修复文本写 `from .backend import ...` 却声称匹配
  doctor.py:110/126/128/168 的风格。
- 评估：非功能性错误——`from .backend import probe_backend_live` 在
  code_forge 包内合法且能运行，两种拼写等价。但计划的目标是
  implementer 无需提问即可照抄：此处照引号字面抄得相对导入、照
  "match the style" 指令抄得绝对导入，两个实现者产出不同代码行，
  与 R2 L-1 同类（两处文本拉扯）。
- 建议（最小改动）：把 :639 的 `from .backend import probe_backend_live`
  改为 `from code_forge.backend import probe_backend_live`——与 doctor.py
  既有风格、Task 3 拼写、所引用锚点三者同时一致。（Task 4 的
  `from .llm_invoke import ...` 不受影响：那是 backend.py 包内相对导入，
  且有 llm_invoke.py:29 先例，且未声称匹配 doctor.py 风格。）

SCORECARD: B=0 H=0 M=0 L=1
