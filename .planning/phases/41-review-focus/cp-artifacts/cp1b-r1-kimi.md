[B] Task 3b replan (c)+(d): merge-in-forge_review double-merges gate.yaml `review_focus` on both MCP outlets
  Location: Replan block (c) "forge_review (:1025 call): ... merge it with the gate.yaml `review_focus` field via Task 3a's `_merge_focus_spec` FIRST, then pass `focus=<merged> or None`"; replan block (d) "The forge_review sampling call (mcp_server.py:997-1004 ...) also passes the merged `focus_spec`"; 3a-3 "For the CLI-subprocess outlet, `gate_data` is available at the `forge_review` call site"
  Issue: Ground truth on main @ ca0d860: `forge_review` passes the RAW MCP `contract` param to `_dispatch_cli` (mcp_server.py:1026 `contract=contract or None`) and to `_dispatch_sampling` (:1003 `contract_spec=contract`); the yaml+param merge happens at the LEAF — inside the subprocess at cli.py:2068/2427 for the CLI outlet, and inside `_dispatch_sampling` at :845 for the sampling outlet. The plan's "(mirror `contract=contract or None`)" is therefore false as written: contract is never merged in forge_review. Implemented literally, (c) writes yaml_focus+MCP-focus into the tmpfile and the subprocess re-merges gate.yaml `review_focus` a second time (3a-4 merge sites), duplicating yaml focus in the CLI-outlet prompt; (d)'s "passes the merged focus_spec" does the same in-process, because `_dispatch_sampling` independently loads yaml_focus (3a-3: "must call `cli._load_gate_backends` itself") and merges again — and `raw_focus` then captures the already-merged value, so the fallback tmpfile triple-counts yaml focus after the subprocess merge. Additionally, 3a-3's premise is factually wrong: `forge_review` (mcp_server.py:971-1027) never loads gate_data — it reads only the outlet via `load_outlet_from_gate`.
  Impact: gate.yaml `review_focus` appears 2x (CLI outlet) or 2-3x (sampling + fallback) in prompts; CLI vs sampling parity breaks whenever `review_focus` is set; the plan's own MERGE PARITY rationale ("writing merged values would cause double-merge ... a third divergent prompt") is violated by its live replan text.
  Suggestion: Rewrite (c)/(d) to pass RAW end-to-end from forge_review: `focus=focus or None` to `_dispatch_cli` (merge+trust gate only inside the subprocess) and `focus_spec=focus` (raw MCP param) to `_dispatch_sampling` (merge only inside `_dispatch_sampling`). Delete "merge it ... FIRST" and "passes the merged focus_spec"; fix 3a-3's false gate_data claim. Add a test asserting tmpfile content == raw MCP param on the primary CLI outlet (not just the fallback).

[H] Task 3a-3 / replan (d): no `staged` guard for yaml_focus on the sampling path — gate-check prompt leaks review_focus
  Location: 3a-3 "`_dispatch_sampling` ... must call `cli._load_gate_backends` itself to extract `review_focus` behind `is_trusted_focus`"; replan (d) "merge via `_merge_focus_spec`" (unconditional)
  Issue: `_dispatch_sampling` is shared by forge_review (staged=False, mcp_server.py:997) and forge_gate_check (staged=True, mcp_server.py:1059). The merged contract code guards digest loading with `if not staged` (mcp_server.py:839, comment: "review path only (not gate-check) ... outlet divergence (D2)"). Neither 3a-3 nor (d) specifies any staged guard for loading/merging yaml_focus, and (c) only covers the `_dispatch_cli` call from forge_gate_check — not the `_dispatch_sampling` call at :1059.
  Impact: `code-forge trust`-authorized `review_focus` is injected into gate-check sampling prompts, violating the plan's own Acceptance criterion "forge_gate_check's sampling dispatch passes no contract/focus, asserted" — review-only prompt content crosses into the gate-check boundary.
  Suggestion: Specify the guard explicitly in (d): load and merge yaml_focus only `if not staged`, mirroring mcp_server.py:839. Extend the gate-check assertion test to run with a trusted gate.yaml `review_focus` present and assert the prompt contains no "## Review Focus".

[M] Task 3a-3: backend-untrusted repo drops focus silently — warning branches are unreachable
  Location: 3a-3 extraction pseudocode (`raw = gate_data.get("review_focus", "")` + warn branches); Must-Have "untrusted or post-trust-edited focus is dropped with a warning"
  Issue: `gate_data` comes from `_load_gate_backends` (cli.py:118), which returns `([], {})` when backend trust fails (cli.py:153-159). With `gate_data == {}`, `raw` is `""` and neither warn branch in 3a-3 can ever fire — focus is dropped with only the generic "Untrusted repo backends ignored" message, never a focus-specific warning. On the sampling path this is strictly worse: backends are irrelevant to sampling, yet a backend-trust failure silently strips a VALID, trusted `review_focus` because the plan reuses the backend-trust-gated loader as the focus source.
  Impact: Acceptance "untrusted ... focus is dropped with a warning" holds only for the post-trust-edit case with healthy backend trust; the backend-untrusted case is a silent drop. Sampling-outlet users lose authorized focus with no diagnosable message.
  Suggestion: Either (a) document the coupling in Acceptance ("backend-untrusted suppresses focus without a focus-specific warning"), or (b) on the sampling path load gate.yaml for `review_focus` independently of backend trust and gate solely behind `is_trusted_focus` (the trust hash already makes this safe), emitting the 3a-3 warning there.

[M] Task 3b replan (e): cross-outlet parity test underspecified — would not catch the double-merge
  Location: Replan block (e) "the cross-outlet parity test: one MCP `focus` input yields identical `## Review Focus` text on the CLI outlet and the sampling outlet"
  Issue: As specified the test uses MCP `focus` input only. With gate.yaml `review_focus` absent, the double-merge defect (finding above) produces identical output on both outlets and the test passes green. The boundary that actually diverges — yaml source merged at two different layers per outlet — is never exercised.
  Impact: The one test designed to guard the MCP→subprocess/sampling boundary cannot detect the plan's own (c)/(d) defect; a wrong implementation ships with a passing parity test.
  Suggestion: Pin the parity test matrix: (i) MCP focus only, (ii) trusted gate.yaml `review_focus` only, (iii) BOTH yaml + MCP focus (the case that exposes double-merge), asserting byte-identical "## Review Focus" sections across CLI outlet and sampling outlet.

[L] Task 3a-3: `elif raw:` skips falsy non-string values — promised warning never fires
  Location: 3a-3 pseudocode: `elif raw:  # non-string, non-None: list/dict/int from hand-edited YAML`
  Issue: Falsy non-strings (`0`, `[]`, `{}`, `False`) fail the `elif raw:` check, so `review_focus: []` or `review_focus: 0` is ignored with NO warning, contradicting Must-Have "Non-string `review_focus` is ignored with a warning, never coerced" and the 3c-2 test row "Non-string `review_focus` (list, dict, int) -> ignored with warning".
  Impact: Hand-edited YAML with empty-list/zero focus silently no-ops; user gets no remediation signal.
  Suggestion: Change the branch to `elif raw is not None:` (after the `isinstance(raw, str) and raw.strip()` check), and add `[]` and `0` cases to the 3c-2 non-string test row.

[L] Task 3a-2: `hash_focus_text` "empty" semantics unpinned — consistency with 3a-2/3a-3 checks unspecified
  Location: 3a-2 "`hash_focus_text(gate_data) -> str`: sha256 of the canonical JSON of the `review_focus` value; returns "" when the field is absent or empty"
  Issue: Three behaviors are unspecified: (a) non-string values (list/dict/int, which 3a-3 ignores) — hash the canonical JSON of the value or return ""? (b) whitespace-only strings — 3a-2's `has_focus` and 3a-3's gating both use `.strip()`, but "empty" for the hash is undefined; if the hash is over the raw value while trust-eligibility uses the stripped check, a record can exist for content that is never injected, or an injectable value can fail trust comparison. (c) Whether the hashed input is the raw string or the stripped string.
  Impact: Implementer coin-flip produces a trust record and a runtime check that disagree; post-trust `review_focus` edits could be mis-judged (silent trust bypass or false untrusted) depending on which side normalizes.
  Suggestion: Pin in 3a-2: `hash_focus_text` returns "" unless `isinstance(v, str) and v.strip()`; otherwise hash the RAW string. State explicitly that this mirrors the extraction gate in 3a-3 so trust-record and runtime check can never diverge.

SUMMARY: B=1 H=1 M=2 L=2
ssue: 本 plan 无 Task 3g(采样修复已拆分为 41-sampling-fix 并合并,RECONCILE 头明确 :997-1004 已传 contract_spec)。caveat 与 RECONCILE 头直接矛盾,会误导实现者放弃本可编写的 e2e 断言。
  Impact: 第三个改名站点的测试被错误地降级为纯单元测试,覆盖率说明失真。
  Suggestion: 删除 "Task 3g" 引用与 UNREACHABLE 论断,改为:2edb9d4 后该路径 production 可达,优先写经 `_dispatch_sampling` 的 e2e 断言。

[L] Task 3b REPLAN (e): 测试清单丢失了被取代文本中 "tmpfile 内容等于 RAW 输入" 的关键断言
  Location: REPLAN (e) 五个镜像测试 + parity 测试;被取代的旧 3b-5 曾要求 "assert the tmpfile **content** equals the raw params, not just flag presence";3c-2 MCP 行仅保留 "--contract and --focus tmpfiles in CLI fallback args"(flag 存在性)
  Issue: 内容相等断言是专门捕获"merged 值越过边界"(即上述两个 B 类缺陷)的测试。REPLAN 宣布旧文本 SUPERSEDED 而 (e) 未继承该断言,3c-2 也未保留,防线出现空洞:即使错误地传了 merged 值,flag-presence 测试仍绿。
  Impact: Finding 1/2 类回归可无告警通过测试。
  Suggestion: 在 (e) 或 3c-2 显式恢复:fallback 场景下断言 `_dispatch_cli` 收到的(或其 tmpfile 写入的)内容 == RAW MCP 输入,而非 merged spec。

SUMMARY: B=2 H=2 M=2 L=2
```