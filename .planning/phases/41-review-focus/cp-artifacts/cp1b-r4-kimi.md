验证完成。所有关键声明已对照真实源码核实，攻击面 A/B/C/D 均已端到端追踪。

SUMMARY: B=0 H=0 M=4 L=5

---

## 核实通过的攻击面（无缺陷，简述证据）

**攻击面 A（`_load_trusted_yaml_focus` 双路径服务）— 通过。** 3b(d) 的 sampling 调用块正确：`workspace`/`staged` 是 `_dispatch_sampling` 形参（mcp_server.py:803/805），块内自算 `gate_yaml_path`（模式与 :837 contracts.yaml 一致），inline warn lambda 与真实 :847 完全一致，`sys` 已在 mcp_server.py:23 导入、`cli` 在 :829 导入。CLI `_run` 侧 `gate_yaml_path`(:2180) 与 `warn`(:2112-2114) 均在作用域内，放置点 :2195-2200 在全部三个下游分发（subagent :2379、outlet-A 合并 :2427、cross-repo :2510）之前。未发现 disposition #7 之外的新输入类（bool/int/null/falsy 非 str 均被既有分支正确覆盖）。

**攻击面 C（不变量注释豁免）— 通过。** 二次裸读只取 `review_focus` 键，后端仍走 `_load_gate_backends`(cli.py:2185) 与 SEC-02 注释点（:2249-2251)；`is_trusted_focus` 哈希的正是它刚读出的同一个 dict（TOCTOU 自防御：文件被改→哈希变→丢弃+告警）。且二次裸读已有先例（siblings 读取，:2507-2509 注释），豁免不削弱原不变量。

**Bug-inject 前提（3a-3）— 通过。** `base_url` 在 DANGEROUS_FIELDS(trust.py:24)，`hash_backends_block` 只哈希危险字段（:115-118)，"Untrusted repo backends ignored" 与 cli.py:154-158 真实字符串匹配。

---

## Medium

**M1 — REPLAN(e)/(f)：三个 `_unlink(focus_tmp)` 站点中 inline 路径无对应失败测试，按站点注入无法证明。**
位置：REPLAN(a)/(e)/(f) ；真实代码 mcp_server.py:682-685。
REPLAN(a) 要求在 dispatch try 的三处既有 `_unlink(contract_tmp)` 旁各加 `_unlink(focus_tmp)`，其中一处是 inline-result 路径（:684)。REPLAN(e) 说"镜像既有 5 个 contract 测试"，但逐一核实（test_mcp_server.py:2421/2451/2481/2515/2559):5 个全部走 job 分支（mock 返回 task 元组）或 raise 分支，**没有任何一个以 str 元组驱动 inline 路径并断言 tmpfile 被删**。唯一经过 inline 路径的测试 `test_forge_review_with_contract_writes_tempfile`(:595-619）注释写着"Tempfile was deleted after inline completion"却**只断言 `written_path is not None`，从未断言删除**——既有的 contract inline unlink 本身就是空心覆盖。REPLAN(f) 要求"删一条 `_unlink(focus_tmp)` → 对应泄漏测试必须 FAIL"，inline 站点没有可失败的测试，该站点的删除会静默通过。
要求修复：REPLAN(e) 增加第 6 个镜像测试——`_run_cli_budgeted` 返回 `("out",0,1.0,"")` 驱动 inline 路径，捕获 `--focus` tmpfile 路径并 `assert not os.path.exists(...)`。

**M2 — REPLAN(e)(i)：`_evict_stale` 测试配方用错旋钮，未隔离目标消费者，注入将无测试可失败。**
位置：REPLAN(e)(i) ；真实代码 mcp_jobs.py:74/:227-231/:307-314/:334-359。
计划写"drive `_evict_stale` via TTL expiry (small `max_lifetime_s`)"。但 `_evict_stale` 只看模块全局 `_JOB_TTL_SECONDS`(:74/:345)`max_lifetime_s` 是 `_wait_for_job` 的 `asyncio.wait_for` 上限（:227-231)——超时后由 `_wait_for_job` 的 finally(:307-314）执行 unlink，那是**另一个消费者**。按此配方写出的测试：小 `max_lifetime_s` 触发超时 → finally 先删掉 focus tmpfile → 断言通过，即使 `"focus_tempfile_path"` 根本没加进 `_evict_stale` 的元组——正是 REPLAN(f) 要抓的静默泄漏，且使"删 `_evict_stale` 元组项 → 该消费者测试 FAIL"的注入失去靶子。
要求修复：配方改为直接构造隔离场景——向 `mcp_jobs._jobs` 手工写入 `status="failed"`、回溯 `created_at`（或 monkeypatch `_JOB_TTL_SECONDS`)、携带真实 focus tmpfile 的条目，调用 `_evict_stale()` 或 `get_job()`，断言文件被删；不得经由真实 job 生命周期（否则 finally 抢先 unlink)。

**M3 — REPLAN(a) 伪代码：`*_tmp = tmp.name` 在 write/close 之后才赋值，写失败时正在创建的文件自身泄漏——未达成 LEAK TRAP 段自封的不变量。**
位置：REPLAN(a) 伪代码块；真实代码 mcp_server.py:664-672。
LEAK TRAP 段明确以"OSError while creating/**writing** focus_tmp"为动机，并承诺"wrap BOTH creations in one guard that unlinks **whichever exists** on failure"。但伪代码中 `contract_tmp = tmp.name` / `focus_tmp = ftmp.name` 都在 `tmp.write(...); tmp.close()` **之后**才赋值；`NamedTemporaryFile(delete=False)` 在构造时即落盘。若 write/close 抛 OSError(ENOSPC/EIO),except 里两个变量仍为 None,`_unlink` 双 no-op，正在写的那个文件泄漏。既有代码对 contract 有同样窗口，但 REPLAN(a) 是作为泄漏加固重写出售的，其自封不变量未兑现。
要求修复：伪代码改为构造后立即赋值（`tmp = NamedTemporaryFile(...); contract_tmp = tmp.name; tmp.write(...); tmp.close()`)，使 except 能捕获到真实路径。

**M4 — 3a-2 改动 trust 拒绝信息文案，两个既有测试逐字断言旧文案，测试矩阵未列出更新。**
位置：3a-2 sampling-only trust 段；真实代码 cli.py:1175-1180；测试 tests/test_trust_empty_backends.py:51 与 :64。
计划将拒绝信息改为 "No backends or review_focus configured in this gate.yaml. Configure at least one."。两个既有测试的 fixture 写的 gate.yaml 只有空/None backends、无 `review_focus`——新守卫仍拒绝（行为正确），但旧断言 `"No backends configured in this gate.yaml" in result.stderr`(:51）与 `"No backends configured"`(:64）对新文案**均不成立**（新串中 "backends" 与 "configured" 之间插入了 "or review_focus")。3c-2 只新增两行 trust-command 测试，未列出这两个既有断言的更新；Wave 3/5 将意外变红。
要求修复：3c-2 增加一行——更新 test_trust_empty_backends.py 两处断言以匹配新文案（或 3a-2 保留无 `review_focus` 时的旧文案并说明）。

---

## Low

**L1 — 3b-1 表格 sampling 行 call site 陈旧。** 表内写 `mcp_server.py:765`；真实调用已迁至 mcp_server.py:853-858(2edb9d4 后）。REPLAN(d) 给了正确锚点，但表格本身未按 RECONCILE 重锚，按号施工会落空。要求：表格该行改为符号锚点（`_dispatch_sampling` 内 `build_sampling_l1_provider(...)` 调用）。

**L2 — 3a-3 "For the sampling path, also load the contracts.yaml digest" 是陈旧指令。** 该加载已存在于 mcp_server.py:837-842(2edb9d4 合入）;REPLAN(d) 的显式块正确地不再包含它，但 3a-3 这句独立读来像在指派新增工作，有重复加载风险。要求：删除或标注"已存在，勿重复添加"。

**L3 — Task 3b 头部 "Fixes the pre-existing sampling contract_spec gap" 与 RECONCILE 矛盾。** D5.7 已由 2edb9d4 修复（RECONCILE 与 REPLAN 均明确），此句残留可能让施工者误判仍有 gap 要补。要求：删除该句。

**L4 — Task 2c 未列出更新 test_legacy.py:262 的格式 docstring。** 该 docstring 逐字描述旧格式 `'git-blame: {author} {sha[:8]} {subject}'`，与更新后的断言（:279 含 date）自相矛盾；Task 2 Step 8 对 git.py docstring 有更新要求，此处标准不一。要求：2c 增加该 docstring 的更新。

**L5 — REPLAN(a) 未列出更新 `_dispatch_cli` docstring。** mcp_server.py:654-663 的 docstring 逐条描述 contract-only 的清理路径（"Cleanup is automatic on every exit path" 后只列 contract_tmpfile);Task 2 已树立"改行为必改 docstring"的标准（Step 8)，此处遗漏会留下误导性文档。要求：REPLAN(a) 显式列出 docstring 更新为双文件生命周期。

---

## 结论

核心 H1 架构（focus trust 独立于 backend trust、单一共享 helper、`_dispatch_cli` 集中化tmpfile 生命周期）经全路径追踪**无方向性缺陷**；4 个 Medium 全部集中在 REPLAN 块的**测试可证伪性**(M1/M2）与**伪代码精确性**(M3/M4)——均为本轮新改文本引入，符合"攻击改动本身"的复审定位。修复 M1-M4 后本计划可达 0/0/0/0。
