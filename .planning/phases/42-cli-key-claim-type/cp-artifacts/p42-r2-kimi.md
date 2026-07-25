## External Model (Claude inline) Review — Phase 42 Plans (Round 2)

核验方式：两份 PLAN 全文 + 所有引用源码行逐一比对（cli.py:2390-2410、backend.py:80-113/310-320/600-668、llm_invoke.py:838-862、machine.py:1185-1219、ledger.py:30-119、state.py:55-86、cli.py:1305-1326），并对关键声明做了实证验证。

### Findings

| # | Severity | Plan | Finding |
|---|----------|------|---------|
| 1 | **BLOCKER** | 42-02 | Task 2 Step 2 指示把 `version_sensitive: bool = False` 插在 `axis_claim: str` 与 `pass_provenance: str` 之间。LedgerRow 是 `@dataclass(frozen=True)` 且后续字段全无默认值，此插入在类定义期即抛 `TypeError: non-default argument 'pass_provenance' follows default argument 'version_sensitive'`（已用 Python 实证复现）。ledger.py 将无法 import，整个测试套件在 collection 阶段全灭。修复：字段追加到 LedgerRow 末尾（`ts` 之后），所有构造点（machine.py:1204、cli.py:1314、tests）均为关键字传参，不受影响。 |
| 2 | **HIGH** | 42-02 | Task 2 Step 4 注入 #2 不成立：从 machine.py 的 LedgerRow 构造中删掉 `version_sensitive=...` 后，没有任何测试会 FAIL——Test 9-10 自行构造 LedgerRow（绕过 machine.py，正是 Round 1 已确认的事实），Test 13 的源码断言只覆盖 (a) derive_claim_type import 存在、(b) `axis_claim="review"` 消失，**未断言 version_sensitive 存在于 machine.py**。该 mutation 存活，与计划自身 success_criteria「Bug-injection proof at version_sensitive write site」直接矛盾。修复：Test 13 增加第三条源码断言（machine.py 的 ledger-row 构造中含 `version_sensitive`）。 |
| 3 | MEDIUM | 42-02 | Task 2 Step 4 注入 #1 引用错误的测试：重新硬编码 "review" 到 machine.py 后，计划称 `test_ledger_row_has_derived_claim_type`（L0）会 FAIL——该测试绕过 machine.py，实际保持绿色；真正捕获此 mutation 的是 Test 13。执行者按指令跑具名测试会看到 PASS，在强制 inject→FAIL→revert→PASS 流程中误判为 false green 或无所适从。mutation 覆盖本身存在（Test 13），仅引用错误。 |
| 4 | MEDIUM | 42-02 | frontmatter `files_modified` 遗漏 `src/code_forge/ledger.py`，而 Task 2 明确修改它（LedgerRow 加字段 + iter_rows 反序列化），acceptance_criteria 亦引用之。计划级元数据与任务范围不一致，wave 规划/核验据此漏文件。 |
| 5 | LOW | 42-02 | Test 9-10 模式代码使用 `TerminalState.FIXED`，但所示 import 行（`from code_forge.ledger import LedgerRow, append_row, iter_rows`）未含 TerminalState，照抄即 NameError。 |

### 已核验无误的声明（节选）

- cli.py:2396-2400 现有守卫与 Plan 01 引用逐字一致；backend.py 凭证字段（api_key_env:88 / api_key_file:89 / credentials_path:96）、XOR 校验（backend.py:310-320）、_probe_api（600-668）均属实。
- machine.py:1211 硬编码 `axis_claim="review"`、ledger.py:108 显式反序列化、cli.py:1321 `axis_claim="manual"`（当前恰 1 处）、state.py:66-85 StateFinding source Literal 全部与源码吻合。
- Plan 01 grep 验收当前计数为 0（api_key_file / credentials_path 在 cli.py 中均不存在），`>= 1` 标准非空洞；Plan 01 的 elif 链逻辑在 XOR 配置校验 + inline 构造路径下安全；bug-injection 避开既有 api_key_env 守卫的 false-green 陷阱，推理正确。
- `from .claim import derive_claim_type` 与 machine.py 现有相对 import 风格一致，无模块名冲突。
- Round 1 修复（Test 13 源码断言）方向正确，但如 Finding #2 所示，断言覆盖面尚缺 version_sensitive 一维。

### Verdict: NOT CLEAN — 1B/1H/2M/1L

Finding #1（BLOCKER）使 Plan 02 按字面执行必然失败，必须先修；Finding #2 使 version_sensitive 写入点的 bug-injection 证明空洞，与计划自身成功标准冲突。建议：将 version_sensitive 字段移至 LedgerRow 末尾、Test 13 增加 version_sensitive 源码断言、修正注入 #1 的测试引用、补全 frontmatter 后重审。
