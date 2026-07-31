调查完成（三条 heredoc 探针内容各不相同，非循环——已到收尾）。所有关键结论均已实证验证。以下是审查报告。

---

# 审查报告：forge verify gate（分支 `defects/verify-gate`）

审查范围：`src/code_forge/verify.py` 与 `tests/test_verify.py` 的 diff。我通读了新文件的完整文本（verify.py 541 行、test_verify.py 1075 行），并用 Python 对每个可疑点做了实证复现（含旧代码对照）。全部 82 个测试通过，与地面真相一致。

## 优先问题答案（先给结论）

**Q1 — 伪造摘录能否通过 STEP C？能。** 构造的输入已实证：新代码通过、旧代码拒绝（详见 FINDING-1）。但需限定范围：伪造摘录只能"搭便车"通过——它无法携带覆盖率（名义区间必须与 all_diff 相交才计分，一旦相交则 shift-0 窗口必与 post-image 重叠，伪造内容必然被拒）。所以整个证明（60% 覆盖率 + 全部 hunk 有见证）仍不能被完全伪造。

**Q2 — (a) 是否放行了"并非三个连续干净周期"的集合？** 未发现超出设计意图的放行。重复 (cycle,pass) 被拒（verify.py:263）；最后三个不连续被拒；顺序由未改动的检查 4 间接强制（通过集合内文件名序 = 周期数序 = 时间序，任何对早期周期 receipt 的重写都会破坏单调性）。"干净"（findings 为空）从来不是该 gate 的检查项，本次改动也未改变这一点——见文末 QUESTION。真正的发现是反方向：(a) 的承诺在两位周期号处失效（FINDING-3，误拒而非误放）。

**Q3 — (b) 是否打开"无真实覆盖即通过"的路径？没有。** STEP A 要求每个非纯删除 hunk 有重叠摘录见证；60% 覆盖率按周期逐个检查 last_three。越出 hunk 的摘录名义区间既不与 all_diff 相交（我已验证 all_diff ⊆ post_image 键集），也不与任何 hunk 相交，无法满足任何一项。该注释（verify.py:357-358）是准确的。

**Q4/Q5** 见下方 findings。

## Findings

```
SEVERITY: MAJOR
FILE:LINE: src/code_forge/verify.py:424-428
CLAIM: 失败判定只取 attempts[0]（shift 0）的 bad_line；当名义窗口与 post-image 不相交、但 ±1..3 位移窗口与之重叠且不匹配时，摘录被无条件放行——伪造内容在位移窗口内"匹配不到任何位置"却通过了本应拒绝它的检查。
FAILURE: diff 的 hunk 覆盖 post-image 第 1-12 行；9 张真实 receipt（真实摘录 [1,10] 覆盖 83%），另加一张伪造摘录 {"start_line": 13, "end_line": 14, "content": "def zzz():\n    return 99\n"}。attempts[0] = (None, None)（13-14 行不在 post-image），shifts -1/-2/-3 的窗口 [12,13]/[11,12]/[10,11] 均重叠且逐一不匹配。实测新代码："all 7 checks passed"；同一输入跑 HEAD 版本："excerpt src/f.py:13-14 not in any diff hunk"（拒绝）。
EVIDENCE:
    424  bad_line = attempts[0][1]
    425  if bad_line is None:
    428      continue
    以及 413-414 行注释 "content that is not in the file still matches at no shift at all, which is what this check is for"——该场景正是"匹配不到任何位置"，却被放行；426 行注释 "Nothing overlaps the post-image here" 在此场景下是事实错误的（位移窗口确实重叠且不匹配）。
修复方向：仅当所有 attempts 都是 (None, None)（无任何位移重叠）时才跳过，否则取第一个非 None 的 bad_line 拒绝。附带影响：没有任何测试覆盖此洞（三个锚点测试的摘录名义窗口都与 post-image 重叠），修复后需补一个"名义窗口紧贴 hunk 边缘 + 伪造内容"的测试。
```

```
SEVERITY: MINOR
FILE:LINE: src/code_forge/verify.py:281-285（配合 252 行的长度门槛）
CLAIM: 完整性检查从精确匹配（旧代码 seen_keys != expected 即拒）退化为"last_three 各周期的 pass 1-3 存在即可"，最后三个周期内超过 3 的 pass（4、5…）被容忍，"9 张 receipt"的契约被打破但错误消息仍写着 %d/9。
FAILURE: 15 张 receipt（周期 [2,3,4] × pass 1-5，时间戳严格递增）→ 实测 "all 7 checks passed"；旧代码对同一集合返回 "missing cycle/pass combinations"。252 行错误消息 "missing receipts: %d/9" 对 10-15 张可通过的集合是谎话。
EVIDENCE:
    252  msg = "missing receipts: %d/9" % len(receipts)
    281  for c in last_three:
    282      for p in range(1, 4):
    283          if (c, p) not in seen_keys:
（旧代码 expected = {(c,p) for c in range(1,4) for p in range(1,4)}; if seen_keys != expected —— 精确匹配。）
注：这是与声明意图（"last 3 consecutive cycles x passes 1-3"）一致子集的放行，真实流程是否产生 pass>3 我无法在 diff 内判定（receipt 写入器在 scope 外）。若 pass>3 在真实流程中不可能出现，此条目降级为 NIT。
```

```
SEVERITY: MINOR
FILE:LINE: src/code_forge/verify.py:270-285 与未改动的 _load_receipts（148 行，glob 按文件名排序）及检查 4（303-305 行）的交互
CLAIM: (a) 的承诺 "whatever their numbers"（250-251 行注释、测试 test_cycles_5_6_7_pass 显式祝福高周期号）在集合跨越 9→10 时失效：文件名按字典序排序（"c10p1.json" < "c8p1.json"），检查 4 要求该顺序下时间戳非降，而真实审查按周期号递增写入——含两位周期号的集合必然误拒。
FAILURE: 周期 [9,10,11]（9 张 receipt，时间戳随周期递增）→ 实测 "timestamps not monotonic"；[8,9,10] 同样失败。真实场景：一次跑了 10+ 轮的对抗审查（receipts c1..c10 齐全）也会命中同一机制（c10 的文件排在 c8、c9 之前）。这是误拒（gate 比修复意图更严），不是安全洞。
EVIDENCE:
    270  cycles = sorted({r["cycle"] for r in receipts})
    304  if ts != sorted(ts):   # 检查 4，未改动，按 _load_receipts 的文件名序比较
（149 行: for f in sorted(rd.glob("receipt-*.json"))）
```

```
SEVERITY: NIT
FILE:LINE: tests/test_verify.py:167-171
CLAIM: test_content_two_lines_below_start_line_is_accepted 在 STEP C 整体被删除时仍然通过——"容忍位移"与"内容检查不存在"无法区分；且覆盖率恰好踩在 60% 线上（6/10 行），diff 多一行就会因无关原因失败。
FAILURE: 我把 verify.py 源码中整个 STEP C 循环删除后重新 exec，用该测试的完全相同凭据（9 张 receipt、摘录 [3,10]、_REAL_BLOCK）运行：passed=True "all 7 checks passed"。该断言只证明"整个 gate 通过"，不证明"STEP C 接受了位移内容"。幸运的是兄弟测试（173、180 行）钉死了拒绝侧（内容缺失/超出窗口），三元组整体是健全的；且按地面真相，shift 集收窄为 (0,) 时本测试确实失败——所以只记 NIT。
EVIDENCE:
    167  def test_content_two_lines_below_start_line_is_accepted(self, tmp_path):
    171      assert r.passed, r.reason
（配平计算：_excerpt_covered 对 [3,10]、6 行内容记 3-8 行 = 6/10 = 0.6，`0.6 < 0.6` 为假，检查 6 通过。）
```

（FINDING-1 的测试缺口已并入该条，不另列。）

## 检查过但未列入的项（避免虚报）

- **STEP C 的"内容尾部未校验"**（内容行数 > 名义窗口时，窗口外行从不比对）：旧代码同形（GM-B1 注释承认），非本 diff 引入。
- **exempt 文件 / 纯删除 hunk** 的摘录路径：行为与旧代码一致。
- **(b) 的"60% 地板使填充无意义"注释**：经核对正确（all_diff ⊆ post_image 键集，越出 hunk 的名义区间与两者都不相交）。
- **新测试的咬合性**：七个 (a) 测试断言的具体消息（"not consecutive"、"fewer than 3 cycles"、"missing cycle 4/pass 3"）都是旧代码不可能产生的文本，逐一咬合；(b) 的四个测试同样咬合（旧代码对同样的 stray 摘录返回 "not in any diff hunk"）。除 FINDING-4 外未发现"错误理由通过"。

## QUESTION（无法构造具体失败输入，仅存疑）

1. **(a) 与"干净"的定义**：gate 从未（现在也不）验证 last_three 各周期 findings 为空——旧代码对含脏周期 3 的 [1,2,3] 同样放行。模块 docstring（verify.py:11-13）明说收敛由 StateMachine 保证、verify 只是篡改检查。若"三连续干净周期"的干净性在流程内别处有保证（scope 外），则 (a) 无新问题；若没有，这是继承自旧设计的问题而非本 diff 引入。无法在 diff 内判定。
2. **±3 窗口 vs 实测"最多差两行"**：代码比测量值放宽了一行。真实内容在任何位移匹配都算真实内容，所以这只是锚点精度问题，不是伪造路径——不构成 finding，但若想收紧可改 ±2。

## SCORECARD

```
SCORECARD: blocker=0 major=1 minor=2 nit=1
```
