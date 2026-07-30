8/8 测试全绿。四个轴的实验全部完成，工作树完整。下面是报告。

---

## Round 3 review: receipt-load crash guard

### Axis 1 — Is the catch too broad? **CLEAN**

try 块里**零 forge 代码**——只有 `receipts.append(json.loads(f.read_text(encoding="utf-8")))`，两个 stdlib 调用加一个 `list.append`。所以"forge 自己的 bug 产生 `ValueError` 被误标为 corrupt receipt"在结构上不可能：try 里没有 forge 代码可以出错。

实验枚举 `json.loads` 的 `ValueError` 来源（`/tmp` 内运行）：

| 输入 | 异常 | ValueError? | 内容驱动? |
|------|------|-------------|-----------|
| `{ not json` | JSONDecodeError | Yes | 是 |
| `{"x": 9*5000}` | ValueError | Yes | 是（oversized int） |
| `﻿{"x":1}` (BOM) | JSONDecodeError | Yes | 是 |
| `{"x": NaN}` | 无异常 | — | json 默认接受 |
| `{"a":1,"a":2}` | 无异常 | — | 默认接受 |

`read_text` 的环境类 `OSError`（如 `PermissionError`）确实会被标为 "corrupt receipt: X: Permission denied"，但这**符合 docstring 自述的 scope**（"cannot be read or parsed"——读不了就算），且消息里带 errno，operator 能看到真实原因，不算误诊。`RecursionError` 仅来自深嵌套内容（try 内无其它递归路径）。**结论：catch 不算过宽。** [HIGH]

### Axis 2 — Is `test_unreadable_entry_reports_the_file` portable? **CLEAN**

POSIX 实测：`read_text` on 目录抛 `IsADirectoryError`（OSError 子类）——已在 axis 1 实验中确认。Windows 上 `read_text` on 目录抛 `PermissionError`（同为 OSError 子类，Windows 无 EISDIR，给 EACCES）。两者都被 `except (ValueError, OSError, RecursionError)` 捕获。测试只断言 `not r.passed` 和 `"receipt-c2p1.json" in r.reason`，**不依赖具体子类**，所以两个平台都过。

项目确实声明 Windows 支持（memory `project_forge_windows_support.md`：Wave 1+2 已合并，gpu-win 已验证）。`mkdir()` 一个 `.json` 扩展名的目录、`glob` 匹配目录——Windows 上都成立。

**Caveat**：本机是 Linux，我无法在 Windows 上实跑验证；结论基于 CPython 异常层级（`IsADirectoryError`/`PermissionError` 均 < `OSError`）。另：CI 仅有 `codeql.yml`，无 Windows 测试矩阵，所以这个测试在 CI 里不会被 Windows 实跑——但异常层级保证了可移植性。 [HIGH for POSIX, MED-HIGH for Windows by reasoning]

### Axis 3 — Does the early return break the caller contract? **CLEAN**

早返回 `VerifyResult(False, "corrupt receipt: ...", 1, 0)`（checks_run=1, checks_passed=0）与已有的 "missing receipts" 早返回（`verify.py:152`，同为 `VerifyResult(False, msg, 1, cp)` 且 cp=0）**形状完全一致**。

全仓 grep `.checks_run` / `.checks_passed` 属性访问：**零命中**。消费者只有：
- `cli.py:1513`：只读 `vr.passed` 和 `vr.reason`。
- `write_attestation`（死代码）：写自己的 attestation dict（`verified_at`/`diff_sha256`/`receipt_sha256`/`result`），不读 checks_run。

没有任何比例/百分比/日志/attestation 数字地读这两个字段。**早返回不破坏任何契约。** [HIGH]

### Axis 4 — Is `CorruptedReceiptError` caught in the right places, and only there? **CLEAN**

`CorruptedReceiptError` 仅由 `_load_receipts` 抛出（`verify.py:81`）。`_load_receipts` 全仓两个调用者：
- `run_verify:136`——**在 137 被 `except CorruptedReceiptError` 捕获**，转为 `VerifyResult(False, ...)`。
- `write_attestation:403`——未捕获，但 `write_attestation` 全仓**零调用者**（grep 含 .py/.sh/.md/.txt 仅命中定义行；`__init__.py` 未导出）。死代码，不可达。

因为 `run_verify` 把异常转成 `VerifyResult`，**`CorruptedReceiptError` 从不作为异常逃出 `run_verify`**，所以 `cli.py:1513` 上游任何宽 `except Exception`（如 `review` 子命令 1453 行的那个）都不可能吞掉它——`verify` 子命令本身（1489-1516）在 `run_verify` 调用周围甚至没有局部 try。`main()` 也无外层 try 包住分派（1429 的 try 只接 `SystemExit`）。

`--quiet` 模式下不打印、exit 1——这是 `--quiet` 对**所有**失败的设计行为，非本 fix 引入，且仍以非零退出。**无任何路径把响亮失败变安静。** [HIGH]

---

### Additional finding (outside the four axes — on the escape axis, but structurally distinct from rounds 1-2)

**F5: 一个"合法 JSON 但非 dict"的 receipt 仍会让 verify 崩溃在原始 `AttributeError` traceback。**

`_load_receipts` 对 `[1,2,3]`、`42`、`"a string"`、`null`、`true` 这类输入**不抛异常**（它们能被 parse），receipt 进入 check 1 后在 `verify.py:155` `key = (r.get("cycle"), r.get("pass"))` 抛 `AttributeError: 'list' object has no attribute 'get'`。

真实路径实跑（`/tmp/forge_typemismatch`，8 个好 receipt + `receipt-c2p1.json = [1,2,3]`，`code-forge verify`）：

```
Traceback (most recent call last):
  File ".../cli.py", line 1513, in main
    vr = run_verify(cwd, diff_sha, diff_f, diff_text=diff_text)
  File ".../verify.py", line 155, in run_verify
    key = (r.get("cycle"), r.get("pass"))
           ^^^^^
AttributeError: 'list' object has no attribute 'get'
```
exit 1，stdout 空——正是本 fix 要消除的崩溃形态（raw traceback 指向 forge 内部，而非点名文件）。

复现：`python3 /tmp/type_mismatch_realpath.py`

**Scope 张力**：fix 的 docstring 把 scope 限定为 "cannot be read or parsed"——类型错配**能被 parse**，严格说在 docstring scope 之外，是 check 1 的结构假设问题。但本 review 的目标陈述更宽（"make a bad receipt a reported failure naming the file, never a crash"），而 verify 本身就是 tamper check，把某 receipt 替换成 `[1,2,3]` 是合理的篡改场景。是否纳入本 fix 的 scope，由你定。前两轮找的 escape 全是 `_load_receipts` **内部**抛的 parse/load 错（ValueError/JSONDecodeError/RecursionError/OSError）；F5 是 **`_load_receipts` 之后**、check 1 抛的 `AttributeError`，是不同的代码路径和异常类型，不是重提已结案的点。

---

### Worktree integrity

**我触碰的工作树文件：零。** 所有实验都在 `/tmp`（`/tmp/type_mismatch_realpath.py`、`/tmp/forge_typemismatch/`），或对工作树只读（Read/grep/`python3 -c` 经 sys.path 读入但未写）。未做任何 `git add/commit/stash/reset`，未做任何注入式编辑（无需注入——四个轴都干净；F5 用独立 /tmp 仓实跑）。

`git status --porcelain`（与对话开始时一致）：
```
M  src/code_forge/errors.py
MM src/code_forge/verify.py
MM tests/test_verify.py
```

`git diff`（worktree vs index）**非空**，但这是对话开始时就存在的 `MM` 状态——staged 是 round-1 的 tuple（`json.JSONDecodeError, OSError, UnicodeDecodeError, RecursionError`），worktree 是 round-2 的 tuple（`ValueError, OSError, RecursionError`）+ 新测试。这是 round-1→round-2 的增量，**非我的改动**。我未触碰任何工作树文件，故未改变该状态。若你期望 `git diff` 为空，说明你期望 worktree 与 index 一致（即 round-2 已完全 staged），但当前 round-2 的更新是未暂存的工作树修改——这是评审开始前的既有状态，不是我造成的。

`TestCorruptReceipt` 8/8 通过（0.06s）。
