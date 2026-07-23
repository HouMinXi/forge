六个验证点全部跟踪完毕。核心结论：H1 修复方向正确——`_load_gate_backends` 在 untrusted 时确实返回 `([], {})`（cli.py:153-159），新 raw loader + `is_trusted_focus` 真正解耦了 focus 与 backend 信任（trust.py:125-153 模式镜像一致，store key `str(path.resolve())` 与 trust.py:132 匹配，`record_trust` 侧在 `_run_trust` 拿到的是 raw `gd`（cli.py:1103, 1191），两端 hash 输入一致）。边界行为（absent/empty/non-dict/corrupt）与 `_load_gate_backends` 完全镜像；corrupt gate.yaml 在 MCP 路径本就经由 `load_outlet_from_gate` 抛 ValueError（outlet_resolver.py:150-153，mcp_server.py:989 未捕获），所以 CliError 不引入新的行为分歧。`_load_gate_backends` 本体未被触碰，其真实调用点（cli.py:2185、mcp_server.py:243、:292）契约保持。但 H1 落地文本与真实代码存在 4 处会在实现时咬人的缺口：

SUMMARY: B=0 H=0 M=4 L=2

[M] Task 3a-3: H1 的二次 raw read 与 "Never re-read gate.yaml raw" 不变量注释直接冲突，plan 未给落点也未要求改注释
  Location: plan 41-PLAN.md:301-355 (3a-3 提取伪代码块) vs src/code_forge/cli.py:2182-2185
  Description: cli.py:2182-2184 的注释明文写着 "Never re-read gate.yaml raw after this point -- a second read bypasses the trust check"，紧挨 `_load_gate_backends(gate_yaml_path)`（:2185）。H1 修复要求在同一函数内新增 `_load_gate_yaml_raw(gate_yaml_path)` 二次 raw read，plan 既未锚定插入位置（最自然的位置是 contract 读取处 :2195-2200 旁，距该注释仅十余行），也未要求修订这条安全不变量注释。两种失败模式：(a) 实现者加 read 但留下自相矛盾的注释；(b) 实现者或后续 review pass 遵守注释，改从 `gate_data` 读 `review_focus` —— H1 bug 被原样重新引入，且 3a-3 的 bug-inject 若因下一条 M 失能则无人发现。
  Required fix: 在 3a-3 中明确锚点（"place the extraction next to the contract file read at cli.py:2195-2200"）并要求把注释修订为对该不变量划出 focus 例外，例如 "...never re-read gate.yaml raw for backend consumption; the review_focus read below is gated separately by is_trusted_focus (D5.6)"。

[M] Task 3a-3 bug-inject: "edit it after code-forge trust" 不足——编辑非危险字段不会使 backends 变为 untrusted，guard 会失能
  Location: plan 41-PLAN.md 3a-3 末尾 Bug-inject 段 vs src/code_forge/trust.py:99-122, 23-31
  Description: `hash_backends_block` 只对 `DANGEROUS_FIELDS`（base_url/api_key_env/shell/command/hook/credentials_path/api_key_file，trust.py:23-31）取 hash。bug-inject 指示"edit the backends block after trust"：若实现者顺手编辑 `model:` 或 `temperature:`（最常见字段），`is_trusted` 仍为 True，`_load_gate_backends` 返回真实 `gate_data`，于是"revert focus read 回 gate_data"后测试照样 PASS —— Golden Rule 2 要求的 FAIL 永不出现，H1 解耦的**唯一**证明是空转的。这正是 forge 自己 memory 里 hollow-test 那一类陷阱。
  Required fix: 把 bug-inject 步骤写死为"edit a DANGEROUS_FIELDS field (e.g. change `base_url`) after `code-forge trust`, so `is_trusted` actually flips and gate_data becomes {}"，并在测试里先断言 backends 确实被丢弃（stderr 出现 "Untrusted repo backends ignored"）再断言 focus 仍注入。

[M] Task 3c-2: "Untrusted repo: `_load_gate_backends` returns `{}` -> no focus injected" 测试行是 H1 前的旧机制描述，与 H1 验收标准正面冲突
  Location: plan 41-PLAN.md Task 3c-2 测试矩阵第 5 行 vs plan Acceptance "untrusted BACKENDS with a still-trusted `review_focus` STILL inject focus" + src/code_forge/cli.py:153-159
  Description: H1 之后，阻止 focus 注入的机制是 `is_trusted_focus`，不再是 `_load_gate_backends` 返回 `{}`。按该行字面实现（mock `_load_gate_backends` → `([], {})`，断言无 "## Review Focus"）：若 fixture 的 `review_focus` 是 trusted，测试 FAIL——直接顶撞 Acceptance 的 H1 行；若是 untrusted，测试因错误的原因通过（trust gate 拦的，不是 `{}` 拦的），且对"未来有人重新把 focus 耦合回 gate_data"这一回归毫无捕捉力。该行是 H1 修复引入的跨任务不一致（plan 自己的 Cross-Plan Consistency 规则所针对的类别）。
  Required fix: 重写该行为 "repo with NO trust record（或 review_focus untrusted）→ focus dropped with warning, backends 行为不变"，并明确它与 H1 行（untrusted backends + trusted focus → 注入）是两个独立前提，不得共用 fixture。

[M] Task 3a-3 / 3b replan(d): extract+gate 块（约 10 行 + 2 条 warning 文案）没有命名共享 helper，却必须同时存在于 cli._run 和 mcp_server._dispatch_sampling
  Location: plan 41-PLAN.md:301-355 (3a-3 伪代码) + 3b replan (d) "IMPORTANT: gate-check isolation" 段 vs src/code_forge/mcp_server.py:837-848, cli.py:2185 区域
  Description: plan 为 advisory 文案提取了 `_format_focus_section` 并明言 "to satisfy GR4"，但对**本特性的安全边界**——raw 读取 + `isinstance/strip` 判断 + `is_trusted_focus` gate + 两条 warning——只给内联伪代码。sampling 侧的提取逻辑还分散在 3a-3 与 replan (d) 两处描述中（后者规定 `not staged` 条件），Wave 1（CLI）与 Wave 2（sampling）由不同执行者手写两份拷贝，warning 文案与 gate 逻辑漂移只是时间问题。mcp_server 已有 reach-in cli 私有的既定模式（`cli._load_gate_backends` at :243/:292），提取共享 helper 零障碍。
  Required fix: 命名一个共享 helper，如 `cli._load_trusted_yaml_focus(gate_yaml_path, warn_fn) -> str`（内部调 `_load_gate_yaml_raw` + 提取 + gate），CLI `_run` 与 `_dispatch_sampling` 共用；`_dispatch_sampling` 侧只保留 `if not staged:` 条件包裹。

[L] Task 3a-3 提取伪代码: whitespace-only 字符串会命中错误分支报 "not a string (got str)"；falsy 非字符串值（`[]`/`{}`/`0`）被静默忽略，与 3c-2 测试行矛盾
  Location: plan 41-PLAN.md:301-355 中 `elif raw:` 分支 vs plan 3c-2 "Non-string `review_focus` (list, dict, int) -> ignored with warning"
  Description: `raw = "   "`（whitespace 字符串）：第一分支因 `raw.strip()` 为假跳过，`elif raw:` 中 `"   "` 为 truthy → 告警 "not a string (got str)"——它**就是**字符串，诊断文案是假的（M2 disposition 只认可了"drop"行为，未覆盖错误文案）。反向问题：`review_focus: []` / `{}` / `0` 是 falsy 非字符串，`elif raw:` 不命中 → 静默忽略无告警，而 3c-2 测试行承诺 "list, dict, int -> ignored with warning"——用 `[]` 写该用例会失败，用 `[x]` 才过。
  Required fix: 重组分支：`if isinstance(raw, str):`（`raw.strip()` 则 trust-gate，否则静默丢弃）`elif raw is not None:` warn 非字符串类型。

[L] Task 3a-4: "No new error handling needed beyond what argparse FILE type provides (missing file → argparse error, binary content → UTF-8 decode error)" 错误归因校验位置
  Location: plan 41-PLAN.md 3a-4 括号句 vs src/code_forge/cli.py:351, 1666-1709
  Description: `--contract`（cli.py:351）是 `default=None, metavar="FILE"`，**没有** `type=argparse.FileType`，argparse 不做任何校验；missing file/binary/oversize/stdin 全部守卫都在 `_load_contract_file`（cli.py:1682-1709）内以 CliError 抛出。若实现者按括号字面理解去给 `--focus` 加 `type=argparse.FileType`，`_load_focus_file` 收到的将是文件对象而非路径，`-` stdin 约定被破坏。
  Required fix: 删除该括号或改为 "all guards live in `_load_focus_file`, mirroring `_load_contract_file`'s CliError behavior (cli.py:1682-1709)"。

补充说明（已核验、不构成 finding）：`_dispatch_cli` 的 contract_tmp 生命周期（mcp_server.py:664-698）、`start_job` 的 key 结构与三个消费者（mcp_jobs.py:103-104, :124, :308, :353）、`forge_gate_check` 两路均不传 focus（:1078、:1059-1066 + `staged=True` 跳过提取）、fallback 传 raw（:917-919）、factories 三处注入锚点（factories.py:279-283, :575-576, cli.py:778-782）、cross_repo 契约现状披露（cross_repo.py:250-257）——均与 plan 一致。
