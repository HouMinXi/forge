# Phase 44 范围扩展影响分析 — 评审痛点工单并入

**日期:** 2026-08-22
**触发:** 其他架构师痛点工单 (/tmp/forge-review-pain-points.md), 12 轮 39 receipts 实测
**用户裁决:** (1) 全部 5 条并入 Phase 44; (2) 按报告数据排期; (3) 是 forge state machine 收敛循环缺陷, 改管线本身; (4) 先看完整影响分析再定怎么审

---

## 1. 痛点根因 = Phase 44 的 H1 (同一根)

STATE-09 注释自证 (machine.py:288-300):
> "CI mode starts fresh every run... each CI run starts fresh and cannot
> accumulate dispositions, so repeated runs on an unchanged diff report the
> same findings"

这正是痛点报告的"同一 finding 被反复提出"的代码级根因。**forge 作者早就知道并在注释里写了这个缺陷。** 痛点的 12 轮 MCP carve-out 评审全是 CI 模式 (mimo-direct 子进程), 每次从零开始, 无跨轮记忆。

Phase 44 的 H1 (CI 不写 ledger) 和痛点根因是**同一个 STATE-09 缺陷的两个面**: 写侧 (44 已覆盖) + 读侧 (痛点要求, 44 未覆盖)。

## 2. 五条建议的实现锚点 + 与现有决策的关系

| # | 建议 | 实现锚点 (已验证) | 性质 | 与 44 现有决策 |
|---|------|------------------|------|----------------|
| S1 | finding 去重 (同文件+主题→ALREADY_ADDRESSED) | fingerprint 已是去重单位; CI 缺跨 run 抑制。落点: `_run_ci` 读 ledger, 已 FIXED/pin 的 fingerprint 从 CONFIRMED 剔除 | **读侧新增** | 44 只写不读; 这是新维度 |
| S2 | 反驳注册表 (rebuttals.json 自动跳过) | `Falsifier.falsify()` (falsify.py:30) 已产生 DISPROVED。人工反驳 = 同级抑制信号, 作为 ledger 一类行 | **读侧新增** | 复用 D-10 adjudicate 机制 (人工裁决升格) |
| S3 | 收敛条件 (0 new 连续 N 轮→自动 PASS) | `_run_ci` 判 verdict 在 machine.py:528-540 (`_count(CONFIRMED)`)。收敛 = 计数前剔除已知项 | **读侧新增** | 无对应决策; 全新 |
| S4 | pin 清单 (gate.yaml pinned_paths) | `coverage_exempt_patterns` (machine.py:232) 已是"路径排除"先例, 模式可复用 | **读侧新增** | 无对应; 但挂 gate.yaml 同 D-19 kill-switch |
| S5 | 风格意见降级 (test-assertion/naming→advisory) | `AdvisoryFinding` 已独立 (advisory.py:5-8, NEVER 参与收敛; machine.py:253,722-738 产生) | **机制已存在** | 最小改动: 分类规则, 挂现成 advisory |

## 3. 影响面评估

**改动核心**: CI 评审在 `_run_ci` 判 verdict 前, 先读 ledger 抑制已知项。这是一个**读侧注入点**:
```
_run_ci 收集 findings (现状: CONFIRMED 全算)
  -> [新] 读 ledger via resolve_ledger_root: 剔除
       - 已 FIXED 的 fingerprint (S1)
       - 已 DISPROVED/人工 rebuttal 的 (S2)
       - pin 路径下的 (S4)
  -> [新] 风格类 (test-assertion/naming/idiomatic) 降为 advisory (S5)
  -> [新] 剩余 new-confirmed == 0 → 收敛 (S3)
  -> 判 verdict (machine.py:528)
```

**与已收敛 plan 的关系**:
- 44-01 (写侧) **不受影响** — 它写的 UNADJUDICATED 行正是读侧要消费的数据源
- 44-02 (导出) **不受影响** — 导出逻辑不变
- **新增一个 plan 或扩展 44-01**: 读侧收敛逻辑 (S1-S5) 是独立的一块, 落在 machine.py `_run_ci` + ledger 读 + gate.yaml pin

**新增决策需求** (CONTEXT 需补):
- D-23: CI 读 ledger 抑制已知 fingerprint (S1) — 定义"已知"= 存在 FIXED/DUPLICATE/pin 行的 fingerprint
- D-24: rebuttal 作为 ledger 行类型 (S2) — 复用 adjudicate 或新 provenance
- D-25: CI 收敛条件 (S3) — 0 new-confirmed 的判定 + 是否自动 PASS
- D-26: gate.yaml pinned_paths (S4) — 复用 coverage_exempt_patterns 模式
- D-27: 风格意见降级规则 (S5) — 哪些 axis/关键词降 advisory

**风险点**:
- **S3 自动 PASS 危险**: 若抑制逻辑有误, 真 finding 被当"已知"剔除→false green。需保守: 收敛只抑制"有 ledger 终态行佐证"的, 不抑制"没见过的"
- **S1/S2 抑制面**: 抑制错了 = 漏报。fingerprint 是 sha256(file:line:desc), desc 措辞变 → fingerprint 变 → 抑制失效 (痛点报告 R5/R7 就是"换措辞重提"逃过去重)。**需要主题级匹配, 不只 fingerprint 精确匹配** — 这是最难的一条
- **scope 膨胀**: 44 已从 300-450 LOC 涨到 ~750, 再并入 5 条读侧收敛, 估计 44 总量奔 1100+ LOC。**建议拆 44-03 (读侧收敛) 独立 plan**, 不动已收敛的 44-01/44-02

## 4. 推荐排期形态

**方案 A (推荐): 44-03 独立 plan, 串行在 44-01 之后**
- 44-01 (写侧) → 44-03 (读侧收敛) → 44-02 (导出)
- 44-03 依赖 44-01 的 ledger 数据 + resolve_ledger_root; 与 44-02 无耦合
- 已收敛的 44-01/44-02 plan 不动; 44-03 单独走 CP1
- 新增 D-23..D-27 进 CONTEXT

**方案 B: 扩展 44-01**
- 读侧收敛塞进 44-01 (它会从 3 任务涨到 5-6 任务)
- 违反 quality degradation curve (2-3 任务上限), executor context 会爆
- 且需重开 44-01 的 CP1

**方案 C: 独立 Phase 45**
- 最干净, 但用户已拍板"并进 44"

**我的推荐: 方案 A**。理由: 读侧收敛是独立功能块, 与写侧/导出低耦合; 44-03 串行在 44-01 后符合依赖; 不动已收敛的 plan 符合跨计划一致性。

## 5. 审查方式建议

范围变了 (新增 D-23..D-27 + 44-03), 但已收敛的 44-01/44-02 的写侧/导出逻辑没变。建议:
- CONTEXT 补 D-23..D-27
- 新写 44-03-PLAN.md
- **增量审**: CP1 只审 44-03 + 新增决策; 44-01/44-02 已 PASS 的部分不重审 (除非 44-03 引入对它们的耦合)
- CP1b 三模型复审整个 44 (含新 44-03)

---
**下一步**: 你拍板排期形态 (A/B/C) + 审查方式, 我补 CONTEXT 决策 + 写 44-03 plan。
