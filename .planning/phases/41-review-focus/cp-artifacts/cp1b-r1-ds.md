发现报告如下：

---

[B] Task 3b (a)+(c)+(d) ∩ Task 3a-4: MCP 预合并 + 子进程二次合并导致 review_focus 内容重复
  **Location:** Task 3b (c) "merge it with the gate.yaml review_focus field via Task 3a's _merge_focus_spec FIRST"; 3b (a) "Do for focus EXACTLY what the function already does for contract"; 3b (d) "merge via _merge_focus_spec"; 3a-4 "focus_spec = _merge_focus_spec(yaml_focus, file_content, warn) at the two existing merge sites"
  **Issue:** 计划要求 forge_review MCP handler 预合并 yaml_focus + MCP focus 入参（3b (c)），然后把合并后的结果传给 `_dispatch_cli` 和 `_dispatch_sampling`。但 `_dispatch_cli` 把这个已合并结果写入 tmpfile 后，CLI 子进程内的 `_run` 会在 merge site 再次调用 `_merge_focus_spec(yaml_focus, file_content, warn)`——这将 yaml_focus 与已包含 yaml_focus 的已合并内容再次合并。同样，`_dispatch_sampling` 也调用 `_merge_focus_spec` 合并入参（已合并）+ yaml_focus。结果都是 `yaml_focus + "\n" + yaml_focus + "\n" + focus_raw`，内容重复。而 contract 路径的 MCP handler 是传原始值（不做预合并），所以 "mirror contract"（3b (c) 括号里写的）与预合并指令直接矛盾。parity test（3b (e)）也会在两个 outlet 上得到一致的错误输出。
  **Impact:** 凡是 gate.yaml 里有 `review_focus` 的调用都会在 prompt 里得到重复的 focus 区域描述，不影响但不限于 prompt 膨胀、引导语出错。
  **Suggestion:** 选择一条路径：(a) MCP handler 不做预合并，传原始 focus 值给 `_dispatch_cli`，让子进程的 `_run` 做唯一一次合并（与 contract 一致）；或 (b) MCP handler 做预合并，但给 `_dispatch_cli` 加一个 `focus_premerged=True` 标志，使子进程跳过 gate.yaml 的 `review_focus` 提取。如果选方案 (a)，`_dispatch_sampling` 的 `focus_spec` 入参也要改为原始值，由 `_dispatch_sampling` 内部做唯一一次合并。

---

[H] Task 3b 的 replan (a)-(f) 与 superseded 的 3b-3/3b-4/3b-5 文本交界不清
  **Location:** Task 3b replan blockquote 结尾到 "### Task 3c" header 之间
  **Issue:** blockquote 以 "IMPLEMENT THIS BLOCK. The 3b-3 / 3b-4 / 3b-5 text below it (down to the '### Task 3c' header) is SUPERSEDED" 结尾。紧接着是 3b-3. MCP param、3b-4. Tempfile dual-file ownership、3b-5. Pre-existing bug 三节看似正常的 action 文本（不在 blockquote 内），包含可执行的行号引用（mcp_server.py:936-943、:950-952 等）。实现者很可能误以为这些也是新计划。
  **Impact:** 实现者同时实现 (a)-(f) 和旧的 3b-3/3b-4/3b-5，导致冲突、代码重复或线号已迁的废弃逻辑。
  **Suggestion:** 在 blockquote 之后、第一个老 task 之前显式加分隔行，如 `--- SUPERSEDED TEXT BELOW (keep for history, do NOT implement) ---`，或者干脆删除老文本并从 3b 的描述中移除对 3b-3/3b-4/3b-5 的引用。

---

[H] Acceptance 中 "MCP sampling outlet passes contract_spec (pre-existing gap, separate commit)" 与 replan 确认 D5.7 已合并矛盾
  **Location:** Acceptance 第 6 项
  **Issue:** Replan blockquote 明确声明 "D5.7 / Task 3b-5 (sampling contract_spec wiring) ... merged (2edb9d4)"，不是 still-pending。但 Acceptance 仍用现在时写作 "(pre-existing gap, separate commit)"，暗示需要实现。
  **Impact:** 实现者可能重复实现已存在的功能，或混淆 scope。
  **Suggestion:** 更新为 "MCP sampling outlet passes contract_spec（已合并 2edb9d4）；确认该路径的合并 helper 调用方式与 CLI-subprocess 一致。"

---

[M] Task 3a-2: `record_trust` 全量替换 dict 会丢弃其他已有或未来添加的 key
  **Location:** Task 3a-2 "the new version must write BOTH keys in one replacement: `store[key] = {"hash": current_hash, "focus_hash": focus_hash}`"
  **Issue:** 当前实现若只有 `hash` key，替换没问题。但一旦有其他代码或未来 phase 向 store entry 添加额外 key（如 `hash_gates`），此全量替换会静默丢弃它们。计划意识到了 `store[key]["focus_hash"] = ...` 会 KeyError，但选择了过度纠正：全量替换而非合并。正确的防御模式是 `store[key] = {**store.get(key, {}), "hash": current_hash, "focus_hash": focus_hash}`。
  **Impact:** 与其他 future phase 的 key 共存时，无冲突静默丢失其他 key。
  **Suggestion:** 用先读后合并模式：`store[key] = {**store.get(key, {}), "hash": current_hash, "focus_hash": focus_hash}`。保留 `hash` key 的覆盖（确保 re-trust 更新），但共存其他 key。

---

[M] Task 3a-2: `hash_focus_text(gate_data: Optional[dict])` 未处理 `gate_data=None`
  **Location:** Task 3a-2 `hash_focus_text(gate_data: Optional[dict]) -> str`
  **Issue:** 函数签名接受 `Optional[dict]`（可以是 None），但说明仅描述 "field is absent or empty" 返回 ""。若 `gate_data` 是 None，`gate_data.get("review_focus")` 会 AttributeError。`gate_data` 在 `is_trusted_focus` 的调用链上可能总为 dict，但 Optional 注解承诺了 None-safe 接口。
  **Impact:** 若未来某个调用路径传 None，crash。即使当前无路径传 None，类型注解误导。
  **Suggestion:** 函数体前加 `if gate_data is None: return ""`，或改签名为 `gate_data: dict`。

---

[M] Task 3a-2: trust store key 的规范形式未指定，可能和现有 `record_trust`/`is_trusted` 不匹配
  **Location:** Task 3a-2 `is_trusted_focus` 的 `store.get(str(gate_yaml_path))`
  **Issue:** 计划用 `str(gate_yaml_path)` 作为 store key。但同一个 Path 对象的 `str()` 在不同调用上下文中可能不同（相对 vs 绝对，解析 vs 未解析）。现有的 `record_trust` 和 `is_trusted` 写/读 entry 所用的 key 格式可能不一样（例如 `str(path.resolve())` 或 `str(path.absolute())`）。如果 `is_trusted_focus` 使用了不同的格式，会在已 trust 的门控上误判 focus 为 untrusted，或者反之。
  **Impact:** focus 信任检查不可靠——假阴性（silent no-op）或假阳性（注入未授权内容）。
  **Suggestion:** 明确使用与 `is_trusted` 相同的 key 构造方式。添加注释："Key format MUST match `is_trusted` and `record_trust` exactly——resolve symlinks and use absolute path."

---

[M] Task 4: 缺失 date 的 blame 降级测试需要合成 blame_map，但计划未说明测试实现方式
  **Location:** Task 4 step 2
  **Issue:** `test_legacy.py` 中对 `LegacyRunner` 的测试通常通过真实 git diff 进行。但 Phase 41 修改后 `git_blame()` 始终返回 `"date": ""`（而非缺失 key），所以真实 git 输出不可能产生缺失 date 的 `blame_entry`。要测试 `.get("date", "")` 的防御性，需要合成修改后的 blame 数据，但计划未说明如何构造（mock git_blame？patch blame_map？直接调用 format 函数？）。
  **Impact:** 测试无法按计划编写，或实现者写了一个始终通过的假测试。
  **Suggestion:** 明确指定 synthetic 测试策略。例如："Patch `code_forge.git.git_blame` to return a blame_map with entries lacking the 'date' key, then assert attribution format doesn't crash and produces output without a date segment."

---

[M] Task 1 step 2: `replace_all` 替换所有测试文件中的 "Contract Reference" 可能过度替换
  **Location:** Task 1 step 2
  **Issue:** 计划指示 `grep -rn "Contract Reference" tests/` 后 replace_all，包括 assert messages 如 "Blast Radius < Contract Reference < Diff"。但有些 "Contract Reference" 可能出现在注释、文档字符串或架构说明中，这些内容不是要替换的 header 名，而是对旧机制的描述。替换后会失去语义准确性（例如注释里写着 "`contract_reference` is the old term for design intent" 这种过渡期描述）。
  **Impact:** 测试代码的文档性内容被不必要地修改，可能反而降低可读性。
  **Suggestion:** 指定 replace 范围：仅替换 prompt 输出断言中的 header 文本和功能测试的 expected strings。架构注释/历史说明中的 "Contract Reference" 应单独审阅。

---

[L] Task 3a-1: 8192 字节长度检查用 Python `len()` 计算字符而非字节
  **Location:** Task 3a-1 "exceeds 8192 bytes" / "Size guard"
  **Issue:** 计划写 "8192 bytes" 但未给出实现代码。Python 的 `len(s)` 返回字符数（Unicode code point 数），不是字节数。对纯 ASCII 的 focus 文本无区别，但 focus 区域描述包含非 ASCII（如中文"安全检查"）时，字符数 < 字节数，阈值实际偏大。
  **Impact:** 极少实际影响（8KB 阈值的 ± 偏差在长文本中可忽略），但 spec 和技术实现不一致。
  **Suggestion:** 明确写 `if len(focus_spec.encode("utf-8")) > 8192:` 或改为 "8192 characters" 并统一字符/字节口径。

---

[L] Task 3c verify: "plus CLI and MCP test modules" 未指定具体模块名
  **Location:** Task 3c verify
  **Issue:** 显式列出了 4 个测试模块，然后补充 "plus CLI and MCP test modules"。实现者不知道确切要运行哪些——`test_cli.py`？`test_mcp_server.py`？`test_contract_wiring.py`？当前上下文可推断是后两者，但这个 "plus" 依赖推断而非规格。
  **Impact:** 实现者可能漏跑某些测试或跑错文件。
  **Suggestion:** 显式列出完整的模块列表，或写成 "plus tests/test_cli.py tests/test_mcp_server.py"。

---

[L] Task 3a-3: forge_review MCP handler 的 gate_data 来源未显式说明
  **Location:** Task 3a-3 "Same extraction on the MCP side"
  **Issue:** MCP handler `forge_review` 如何获得 gate_data 以提取 `yaml_focus`？3a-3 在 CLI path 中说 "the review path already holds gate_data; do not add a new read"，但对 MCP path 只写了 "Same extraction on the MCP side"。`forge_review` 函数体在当前代码中是否已有 `_load_gate_backends` 调用？如果由其他 handler（如 `_dispatch_sampling`）负责但 forge_review 也需提取 focus，就得自己调。计划未说明。
  **Impact:** 实现者可能假设 forge_review 已有 gate_data（实际没有），需额外增加 `_load_gate_backends` 调用才能完成预合并——增加一个未计划的 workspace I/O。
  **Suggestion:** 显式写一句："In forge_review MCP handler, call `cli._load_gate_backends(workspace)` to obtain gate_data if not already loaded. Extract `review_focus` per 3a-3 extraction code."

---

SUMMARY: B=1 H=2 M=5 L=4
