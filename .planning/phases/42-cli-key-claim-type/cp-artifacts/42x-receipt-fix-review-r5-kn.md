备份完好。全部取证结束，交付 Round 5 报告。

---

# Round 5 审查报告：receipt-load crash guard

**代码结论：五个轴全部 CLEAN，无新发现。两条观察（均非 blocking)。**
**程序结论：审查进行期间，被审 diff 从 worktree 中消失——触发 STOP gate，见末节取证。**

---

## 轴 1:schema 是否过严（独立重打）——CLEAN

我没有继承 round 4 的 `git log -p receipt.py` 证据，而是做了三件独立的事：

**(a) 找到第二个 writer。** round 4 说"receipt.py 历史形状从未变化"——属实（我逐 commit 核对 a979338→25b063e:cycle/pass/findings_count 恒为 int,diff_sha256/timestamp 恒为 str)，**但作为证据不完整**:4 个 string 形状 receipt(c2p2/c2p3/c3p2/c3p3）不来自 receipt.py 的任何版本，而来自 `skills/code-forge/SKILL.md:1332` 的 "Receipt schema" 文档流——AI 按 SKILL 手写 receipt。schema 的注释（verify.py:59）只说"write_receipts() always produces"，这正是 P3 那次单源推导的同款盲区。schema 本身没错，注释的来源标注窄了（观察 1)。

**(b) 全 fleet 实测，而非单仓库。** 本机 12 个消费仓库共 123 个 receipt，逐一过 `_validate_receipt_schema`:

```
total=123 schema-ok=121 parse-fail=2 schema-reject=0
```

121 个健康 receipt **零误拒**；仅有的 2 个失败正是事故文件（c2p1/c3p1，正确地倒在 `json.loads` 守卫而非 schema)。多样性已确认：2 种 key 集合（±pass_status)、covered_line_ranges 两种形状并存（185 个 dict 项 + 38 个 str 项）、timestamp 两种格式（Z / +00:00)。

**(c) 枚举"旧代码 PASS、新代码 REJECT"的全部理论路径。** 唯一收紧的是 **bool 型 int 字段**:`start_line: true` 旧代码能当 line 1 流过 STEP 0(isinstance(True, int) 为 True),`findings_count: true` 配 1 条 finding 旧代码能过计数检查（True==1)。新 schema 拒绝它们——蓄意、有注释、且数据本身就是类型损坏，fail-closed 正确。其余路径（缺 key、错类型、float、NaN）旧代码要么 FAIL 要么 CRASH，不存在健康 receipt 被新拒的情况。

## 轴 2：攻击测试套件——9 针注入全部按预期杀死，每针还原后 md5 校验

| 针 | 注入（复现的历史错误） | 结果 |
|---|---|---|
| A | 删非 dict 守卫 | 5/5 non-object 测试 FAIL(AttributeError 真实崩溃） |
| B | `_is_type` 放行 bool | 恰好只有 `cycle=True` 参数例 FAIL |
| C | except 收窄为 JSONDecodeError+UnicodeDecodeError（第 1/2 轮的枚举错误） | oversized-int、deep-nest、unreadable 三个测试崩溃；子类覆盖的测试保持绿——精确钉住元组语义 |
| D | 单独删 OSError(P1 复现） | 仅 unreadable-entry FAIL，带真实 IsADirectoryError traceback |
| E | 嵌套 schema 删 start_line | 对应参数例 + 改写后的 `test_missing_field_fail` 双双 FAIL |
| F | covered_line_ranges 断言 dict 形状（P3 复现） | string 形状回归测试 ERROR;**再对 7 个真实健康 receipt 重放：c2p2 被拒**——回归测试与真实事故一一对应 |
| G | 删 run_verify 的 try/except | 30/35 corrupt+schema 测试 ERROR（剩 5 个是直调 validator 和健康集，合理） |
| H | 非 list 字段清洗为 [] 而非报告（P2 复现） | `test_malformed_anchors_no_longer_silently_passes` FAIL——损坏数据真的 PASS 了 verify，假阳性当场演示 |
| I | 跳过坏文件的诱人错修 | `not_reported_as_missing` FAIL |

对 `test_real_covered_line_ranges_shapes_are_accepted` 的专项质疑：它直调 validator、无 assert 语句，但 F 针证明"schema 拒绝 string 形状 → 测试 ERROR"，断言有实义；其 docstring 声明的局限（不过 run_verify 端到端）我也验了——legacy 分支 `_covered` 的崩溃确为先在问题，生产路径不可达。

## 轴 3:round 4 清洁判决的审计——其承重声明全部独立复验成立

- "每个被索引字段都有断言（除 `_covered` 的 start/end)"——我自己枚举了 verify.py 全部字段访问，结论一致（anchors 的 file 走 `.get` 默认，covered_line_ranges 仅 `_covered` 索引）。
- "eager validation 不掩盖篡改"——实验：过 schema 的语义篡改仍倒在具名检查（`findings_count mismatch c2p1`；矩阵重复篡改 → `duplicate receipt c1p1`)。
- "`_covered` 生产不可达"——`grep -n hardened cli.py` 无命中（exit 1),cli.py:1513 是唯一生产调用方且恒传 `diff_text`。
- 对其证据框架的一处修正：receipt.py 历史无变化属实但不完整，string 形状的真正来源是 SKILL.md 手写流（见轴 1a)。结论不变。

## 轴 4：错误契约——CLEAN，一条观察

- 全部失败路径 `VerifyResult(False, "corrupt receipt: <file>: <detail>", 1, 0)`。解析/schema 错误只含 basename(round 1 的拒绝成立）;**但 OSError 分支的 OS 错误串内嵌完整路径**(`[Errno 21] Is a directory: '/tmp/.../receipt-c2p1.json'`)——与"只给 basename"的选择略冗余，无害，操作员下一步动作不变（观察 2)。
- `checks_run=1` 的诚实性：`checks_run` 在 verify.py 之外**零消费者**(cli 只打印 reason)，且与先在的 missing-receipts 路径同约定。纯装饰性问题，不算发现。
- 非 dict 消息的 `type(obj).__name__` 只暴露 JSON 类型名（"got list")，无内部名泄露。

## 轴 5：从假设规格写出的残留——一处，即观察 1

verify.py:59 注释把 schema 的权威性单独归于 `write_receipts()`，实际它必须与**两个** writer 一致（receipt.py + SKILL.md 手写流）。我测量的就是这两者：SKILL.md 文档格式逐字段比对 + 121/121 实测。建议（非要求）把注释改为同时引用 SKILL.md，消除下一次单源推导的入口。

---

## STOP gate：被审 diff 在审查中途从 worktree 消失

**事实时间线（全部有命令输出存档）:**

| 时刻 | 状态 |
|---|---|
| 09:19 | `git status`:`M `×3(errors/verify/test_verify，已暂存）;Read verify.py 见完整修复 |
| 09:25–09:40 | 备份 + 9 针注入，每针还原后 md5 校验 `beff577b…` 一致 |
| 09:42–09:50 | 全量套件后台运行 |
| 09:55 | `git status`：三文件**从 status 消失**，内容与 HEAD(f7bd6ad，基线）逐字节一致，**mtime 显示 7月12日/7月1日/6月19日**；同时出现 ` M llm_invoke.py`、`M  test_fast_fail.py`、`?? .mcp.json` |

**关键取证:**
- reflog 最新条目 `reset: moving to HEAD` 时间戳 **2026-07-27 00:30:02**（昨天，与 test_fast_fail.py mtime 同秒）——昨天另一个会话（做 OmniRoute stream-flag/凭证修复，与 llm_invoke.py diff 内容吻合）在此 worktree 执行过 reset。
- 三文件当前 mtime 与**主仓库**对应文件的 mtime 一致到纳秒。今天 09:40 后某外部进程用带时间戳保留的方式（`cp -p` 类）把基线内容覆盖进了 worktree——这不是我能产生的操作：我最后写 worktree 是 09:38 的注入还原（纯 cp,mtime 应为当时，且 md5 当场校验为修复版）。
- 存在另一个活动 claude 会话（PID 866159，今天 03:28 起 `--resume`)。我没有动它，也没有自行恢复文件——不排除对方有意收回修复，单边覆盖属 friendly fire。
- **修复内容未丢失**:`/tmp/verify_backup_r5.py`(md5 `beff577b…`，含完整修复版 verify.py);errors.py 与 test_verify.py 的修复版全文在审查简报 diff 中，可完整重建。
- 连带警告：全量套件（2944 passed）收集数比简报少 ~35，与新增测试类规模吻合，**它很可能跑到的是基线版 test_verify.py**[INFERRED]。但 09:31 我已对 md5 校验过的修复版单独跑过 `tests/test_verify.py` 46 passed,ruff 与 non-ASCII 门也在当时验证为空——修复自身的测试证据不受影响。

**环境披露（S1)**:`tests/test_taint_rule.py::test_semgrep_validate` 今日在本机稳定超时（30s 测试上限 vs 实测 `semgrep --validate` 墙钟 1m57s,user 仅 1.1s，等网络）;**主 worktree（无 diff）同样失败**——先在环境性问题，与本 diff 无关。

**我碰过的文件**：仅 `src/code_forge/verify.py`(9 针注入，每针已还原并 md5 校验；我最后的写入使它处于修复版 `beff577b`)。从未执行 `git add/commit/stash/reset`，未碰另两个文件。

**当前 `git status --porcelain`（与预期的三个 `M ` 不符，原因如上）:**
```
 M src/code_forge/llm_invoke.py
M  tests/test_fast_fail.py
?? .coverage
?? .mcp.json
```

需要你裁决：是另一个会话有意收回修复，还是事故？若为事故，`cp /tmp/verify_backup_r5.py src/code_forge/verify.py` + 按简报 diff 重建另两个文件即可恢复，我可以执行但等你发话。
