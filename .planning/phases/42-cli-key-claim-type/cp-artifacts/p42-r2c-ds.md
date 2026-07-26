## 验证报告 — REWORK-ORDER-2 确认

### ✅ 1/5 — 测试包含 L0+L1 两个 finding，而非仅 L0

```python
# .worktrees/p42/tests/test_machine_ledger.py:293-298
machine._state.findings.append(
    _make_finding("fp-lint", source="L0", disp=Disposition.FIXED)
)
machine._state.findings.append(
    _make_finding("fp-review", source="L1", disp=Disposition.FIXED)
)
```

断言：L0 → `axis_claim=="lint"`, `version_sensitive==False`；L1 → `axis_claim=="review"`, `version_sensitive==True`。双 source 覆盖，mirror mutation（`derive_claim_type("L0")` 硬编码）会被 L1 断言捕获。

### ✅ 2/5 — Step 4 包含 4 个 injection

| # | Injection type | Lines | Catches |
|---|---------------|-------|---------|
| 1 | 回退硬编码 `"review"` | 400-405 | test 13 + 行为测试 FAIL |
| 2 | 硬编码参数 `derive_claim_type("L1")` | 407-412 | 行为测试 L1 FAIL（test 13 仍绿） |
| 3 | 删除 `version_sensitive` | 415-418 | test 13(c) FAIL |
| 4 | 硬编码参数 `derive_claim_type("L0")` | 420-425 | L0 绿、L1 红（镜像突变） |

### ✅ 3/5 — acceptance_criteria + verification 各有 4 个 injection 条目

- acceptance_criteria（plan 第 440-444 行）：4 条
- verification（plan 第 479-482 行）：4 条

### ✅ 4/5 — 所有文件无非 ASCII

- `42-02-PLAN.md`——通过（`grep` 返回码 1 = 无匹配）
- `.worktrees/p42/tests/test_machine_ledger.py`——通过（返回码 1）

### ✅ 5/5 — main 分支测试文件已被回退

- `4ac9a78` 仅在 `phase-42-cli-key-claim-type` 分支上，不在 `main`
- `main` 分支上的 `tests/test_machine_ledger.py` 不包含 `test_write_ledger_derives_claim_type_from_source` 测试（末尾于 line 274），与工作树版本一致
- `git branch --contains 4ac9a78` → 仅 `phase-42-cli-key-claim-type`

---

### 结论

**CLEAN — 5/5 全部通过，无发现。**
