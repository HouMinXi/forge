工作区已恢复作者原状（`git diff` 对 index 为空，我未留下任何改动）。以下是 Round 2 审查报告。

---

## Round 2 审查结果

**1 个真实缺陷（逃逸异常），1 个测试覆盖推论，其余攻击面验证为守住。**

### Q1：异常元组仍不完整 —— 大整数字面量的 plain `ValueError` 逃逸

**`src/code_forge/verify.py:73-82`**

实验证明（非推断）：Python 3.11+ 的 int-max-str-digits 限制（CVE-2020-10735 缓解，默认 4300 位）使 `json.loads` 对超长整数字面量抛出 **plain `ValueError`**，它不是 `JSONDecodeError` 的子类，不在元组内：

```
json.loads('9'*5000)  ->  ValueError: Exceeds the limit (4300 digits) ...
MRO: (ValueError, Exception, BaseException)   # 无 JSONDecodeError
```

端到端复现：一张字段完好、只含一个 5000 位整数 receipt → `_load_receipts` 抛出未捕获 `ValueError` → `cli.py:1513` 的 `run_verify(...)` 调用处无任何 try/except → `code-forge verify` 以 traceback 中止。**这正是本修复要消灭的事故形态**（坏 receipt 导致 verify 崩溃而非报告文件名），round-1 修掉的只是它的一个子集。

修复已实验验证：改为 `except (ValueError, OSError, RecursionError)`——`JSONDecodeError` 和 `UnicodeDecodeError` 都是 `ValueError` 子类（已验证 MRO），`RecursionError` 是 `RuntimeError` 子类需保留。大整数 receipt 变为 `CorruptedReceiptError: receipt-c1p1.json: Exceeds the limit...`，且 17 个测试全绿。元组反而更短。

其余候选逐一实验排除：lone surrogate、float 溢出（`1e99999`→inf）、超长 float 字符串均不抛异常；`TypeError` 不可能（`read_text` 恒返回 str）；目录列举的 `OSError` 被 pathlib glob 自身吞掉（返回空 → 走 "missing receipts"，非崩溃，且是既有行为）。除大整数外无其它逃逸路径。

### Q2：排除 MemoryError —— 判定为正确

先例已核实：`cli.py:1833-1835` 确有 `except MemoryError: raise`，注释 "Let memory exhaustion abort the review rather than degrade it"，语义与本次排除一致。理由成立：MemoryError 是环境依赖的资源条件而非文件内容属性——同一文件在内存更大的机器上能解析，把它报成 "corrupt receipt: <file>" 会把 OOM 误诊为篡改/损坏，误导操作员。receipt 是 writer 生成的小 JSON，实际不可达。不重构此问题。

### Q3：6 个测试名实相符，但覆盖矩阵缺一格

逐个核对：6 个测试断言均匹配其名（`not passed` + 文件名在 reason 中）。注入证明我独立重跑：从元组删除 `RecursionError` → 恰 `test_deeply_nested_json_reports_the_file` 一测失败（1 failed / 16 passed）→ 还原 → 17 passed。作者的 injection-proven 声明属实。`_nine_with_one_broken` 先 `_write_all` 再覆写 c2p1，时序正确。

两个弱点：
- **缺大整数用例**——Q1 的洞正是因此活过 round-1。加 `test_oversized_int_reports_the_file`（5000 位整数字面量）即可，与现有 5 个坏文件用例同构。
- 次要：无测试锁定 `"corrupt receipt"` 前缀本身（只断言文件名 + "missing receipts" 不出现）。F4 的措辞若被回退，测试保持绿色。nit 级，一行断言可补。

### Q4：diff 无越界内容

三文件全部围绕本缺陷：errors.py 新异常类、verify.py 守卫 + 调用点、tests 6 个测试。`cp = 0` 前移行为中性（check 1 前 cp 恒为 0）；早退 `VerifyResult(False, ..., 1, cp)` 的 `checks_run=1` 与既有 "missing receipts" 路径（`verify.py:153`）约定一致，不属缺陷。

---

**结论**：修复方向与 MemoryError 判断成立，注入证明真实；但异常元组需从具名子类改为 `ValueError` 父类捕获并补一个大整数测试，否则同一事故形态在 Python 3.11+ 上仍可从单张 receipt 复现。
