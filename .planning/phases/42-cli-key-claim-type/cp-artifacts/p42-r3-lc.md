## LongCat Review — Phase 42 Plans (Round 3)

### Findings

| # | Severity | Plan | Finding |
|---|----------|------|---------|
| 1 | LOW | 42-01 | Note 中写 "the `elif` chains off the existing `if backend.api_key_env`"，但实际外层条件是 `if backend.format != "vertex" and backend.api_key_env`（cli.py:2396），`elif` 链的是整个复合条件而非仅 `api_key_env`。XOR 推理本身对非 vertex 后端成立（backend.py:310-20 保证二者互斥），代码行为正确，但注释措辞省略了 `format != "vertex"` 子句，可能让执行者误解 elif 的触发条件。建议改为 "chains off the existing `if backend.format != "vertex" and backend.api_key_env`"。 |

### Verdict: CLEAN — 0B/0H/0M/1L

---

**验证摘要（逐项核对结果）：**

1. **file:line 引用** — 全部准确：
   - cli.py:2396-2400（guard）、2402（state_dir）✓
   - backend.py:80-113（credential fields）、310-320（XOR 校验）✓
   - llm_invoke.py:838-862（运行时 key 解析）✓
   - machine.py:1204-1216（hardcoded axis_claim="review" @ 1211）✓
   - ledger.py:40-54（LedgerRow frozen dataclass，无默认值）、92+（iter_rows）、108（axis_claim 反序列化）✓
   - state.py:66-86（StateFinding.source 7 个 Literal 值）✓
   - cli.py:1314-1326（manual mark @ 1321，axis_claim="manual"）✓
   - test_cli_integration.py:691-706（BackendConfig 构造模式）✓

2. **逻辑正确性** — `elif` 链正确（XOR 保证非 vertex 后端只走一条分支）；vertex 独立 `if` 正确（ADC 路径无 credentials_path）；claim_type 映射覆盖全部 7 个 source 值，无遗漏。

3. **测试可验证性** — 6 + 13 个测试全部有具体断言，bug-injection 指向正确测试（Test 13 为 wiring 回归检查，Round 2 修复有效）。

4. **向后兼容** — `version_sensitive: bool = False` 置于 dataclass 末尾（避免 non-default follows default 的 TypeError）；`iter_rows` 用 `data.get("version_sensitive", False)` 兼容旧行。

5. **导入路径** — `from .claim import derive_claim_type`（machine.py）与 `from code_forge.claim import ...`（tests）均符合包结构。

6. **范围** — 两 plan 均严格限定在 F8 fast-fail + claim_type，未触碰 cli.py:1321 的 manual 路径。
