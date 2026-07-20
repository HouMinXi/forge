# Phase 34: Provider-Aware Parameters + Streaming + CLI Env

**Milestone**: v2.7 Provider Capability
**Implements**: ADR-0004 (cli-backend env field) + ADR-0005 (provider-aware sampling/reasoning parameter passthrough + SSE streaming)
**Depends on**: Phase 33 (MCP server) -- v2.6 complete
**Type**: logic-bearing code -- 3-cycle review required per wave

## Goal

让 forge 的 LLM 后端支持每个 provider 的 reasoning/sampling 参数（thinking、effort、temperature、stream、timeout）和 cli 后端的环境变量声明，使客户配一个 gate.yaml 条目即可驱动任意 reasoning 模型以其验证过的最佳参数进行审查。

## Research Inputs (已完成 -- 不重新调研)

- ADR: docs/adr/0003, 0004, 0005（provider 矩阵 + per-format body mapping + cli env 设计）
- 实现规格: /tmp/draft_20260629_forge_param_passthrough_spec.txt (ADR-0005)
- 实现规格: /tmp/draft_20260629_forge_cli_env_field_spec.txt (ADR-0004)
- 参考实现: ~/code/trinity-router/trinity_router/worker_pool.py（WorkerConfig L40-95, _build_openai_request L180-211, _build_vertex_request L245-280, _read_sse L330+）

## Collision Constraint

两个 ADR 编辑两个共享文件：

1. **backend.py**: BackendConfig dataclass（L59-78）是唯一硬文本冲突 -- Wave 1 一次性加完所有字段。`_parse_backend_entry`（L97）被 Wave 2 原子化处理（api/vertex params + cli env 在同一 wave 的不同分支）。
2. **llm_invoke.py**: Wave 3+4 编辑 API 路径（_invoke_openai/_anthropic/_vertex + 新 _read_sse），Wave 5 编辑 CLI 路径（_invoke_cli Popen）。函数不相交，顺序提交无文本冲突。

一个 worktree 按序提交，不冲突。

---

## Wave 0: Worktree Setup

**文件**: 无代码变更
**操作**:
```bash
git -C ~/code/forge worktree add .worktrees/work-provider-params -b feat/provider-params
```

**Done 条件**:
- `.git` 是文件（非目录）
- `git branch --show-current` == `feat/provider-params`
- `.code-forge/gate.yaml` 可见（symlink from main tree）

---

## Wave 1: BackendConfig 字段（两 ADR 一次性合并）

**文件**: `src/code_forge/backend.py`（dataclass ONLY）, `tests/test_backend.py`
**依赖**: Wave 0

### 变更

在 BackendConfig（L59, 当前末字段 credentials_path L77）后追加：

```python
# ADR-0005: provider-aware reasoning/sampling params
temperature: float = -1.0              # -1 = omit; >=0 = send. _apply_params uses
                                       # default_temperature for per-format fallback (openai=0.0, others=-1.0=omit)
max_completion_tokens: int = 0         # 0 = fallback to existing max_tokens field
thinking_type: str = ""                # "enabled"|"adaptive"|"disabled"; ""=omit
thinking_budget: int = 0              # >0 = add thinking.budget_tokens
reasoning_effort: str = ""             # ""=omit; non-empty = send reasoning_effort
stream: bool = False                   # true = SSE request, reassembled to one response
timeout_s: int = 0                     # 0 = use FORGE_LLM_TIMEOUT_S / default
outcap_key: str = ""                   # "" = format default (openai->"max_completion_tokens",
                                       # anthropic/vertex->"max_tokens"). DeepSeek/GLM openai
                                       # backends set "max_tokens". forge sends EXACTLY ONE cap
                                       # key -- never both. See ADR-0005 section 6.
params: Optional[dict] = field(default=None, compare=False)
    # compare=False keeps BackendConfig hashable despite the dict.
    # IMPORTANT: None->{}  normalization happens in _parse_backend_entry (Wave 2),
    # NOT post-construction (frozen dataclass forbids mutation). Construct with
    # params={} when the YAML key is absent; never pass a mutable default here.

# ADR-0004: cli backend env
env_unset: tuple[str, ...] = ()                     # var NAMES to remove from child env
env_set: tuple[tuple[str, str], ...] = ()           # (NAME, VALUE) pairs to force on child
```

**类型注解说明**: `env_set` 内层是 `tuple[str, str]` 对（name, value）。显式注解而非 bare `tuple`，让类型检查器能捕获误用。

### 测试（TDD RED->GREEN）

1. 每个新字段缺省值 == sentinel
2. `hash(BackendConfig(..., params={"a": 1}))` 不 raise（compare=False）
3. 全量回归绿

### Done 条件

- 所有字段存在且 sentinel 默认正确
- `hash()` 测试绿
- `pytest tests/test_backend.py` 全绿 + 全套 `pytest` 绿

---

## Wave 2: Parse 校验（_parse_backend_entry）

**文件**: `src/code_forge/backend.py`（_parse_backend_entry L97+ 的 api/vertex/cli 分支）, `tests/test_backend.py`
**依赖**: Wave 1

### 变更

#### api + vertex 分支
- 读取/校验 ADR-0005 typed fields + params
- params: reject 9 个 protected key with CliError naming it: `model`, `messages`, `stream`, `anthropic_version`, `temperature`, `thinking`, `reasoning_effort`, `max_completion_tokens`, `max_tokens`。Rationale: 这些 key 要么由 _apply_params 的 typed field 映射（temperature/thinking/reasoning_effort/max_completion_tokens/max_tokens/stream），要么是协议结构 key（model/messages/anthropic_version）。`max_tokens` 回到 protected list（不再是 params 逃生舱）-- DeepSeek/GLM 通过 `outcap_key: max_tokens` 字段选择正确的 cap key，不通过 params。见 ADR-0005 section 6（OpenRouter-validated）。
- outcap_key: validate in `{"max_tokens", "max_completion_tokens", ""}`; "" = 使用 format 默认值（openai -> max_completion_tokens, anthropic/vertex -> max_tokens）。CliError on 非法值。**format 交叉校验**: warn（不 block）when outcap_key 与 format 默认不一致（如 vertex + outcap_key=max_completion_tokens），因 provider 可能 400。
- **cap 值校验**: `max_completion_tokens` 和 `max_tokens` 不能同时为 0（parse 时至少一个 > 0，否则 CliError "output token cap must be positive"）。
- thinking_type: validate in `{"enabled","adaptive","disabled"}` or `""`
- reasoning_effort: accept any non-empty string
- 0005 fields on cli backend -> CliError。显式枚举: `temperature`, `max_completion_tokens`, `thinking_type`, `thinking_budget`, `reasoning_effort`, `stream`, `outcap_key`, `params` 在 type=cli 上均 raise CliError（仅 `env_unset`/`env_set` 被 cli 接受；对称于 `env` 在 api/vertex 上被拒绝）

#### cli 分支
- 读取 `env` dict:
  - `env.unset` -> `tuple(unset_list)`
  - `env.set` -> `tuple(sorted(set_dict.items()))`，value coerce to str
  - unknown key -> CliError
- `env` on api/vertex -> CliError
- `env_unset` 或 `env_set` 作为顶层 entry key on api/vertex -> CliError（对称于 `env` 拒绝；这些是内部字段名，用户应使用 `env: {unset: [...], set: {...}}`）
- `env` not dict -> CliError

### 测试（TDD RED->GREEN + bug-inject）

- typed field absent -> sentinel; present -> stored
- params with each protected key (9 个, 含 max_tokens) -> CliError naming it
- outcap_key="" -> 使用 format 默认; outcap_key="max_tokens" -> stored; outcap_key="invalid" -> CliError
- outcap_key=null (YAML None) -> treated as "" (format default) — YAML 空值边界
- max_completion_tokens=0 + max_tokens=0 -> CliError "output token cap must be positive"
- vertex + outcap_key="max_completion_tokens" -> parse warning（format 默认是 max_tokens，不一致可能导致 provider 400）
- params with benign key (top_p) -> stored; nested (response_format) -> stored verbatim
- thinking_type not in enum -> CliError
- 0005 fields on cli -> CliError
- env absent -> env_unset==(), env_set==()
- env.unset=[A,B] -> env_unset==("A","B")
- env.set={X:1} -> env_set==(("X","1")) value coerced to str
- env with BOTH unset and set populated -> both env_unset and env_set correctly stored
- env on api -> CliError; env on vertex -> CliError
- env_unset/env_set 作为顶层 entry key on api -> CliError; on vertex -> CliError（对称于 env 拒绝）
- env not dict -> CliError; unknown key in env -> CliError

Bug-inject: 移除 protected-key 拒绝 -> 对应测试 FAIL -> revert -> PASS

### Done 条件

- parse 测试全绿（含 protected key, 枚举校验, cross-branch 拒绝）
- bug-inject 证明 teeth
- 全套 `pytest` 绿

---

## Wave 3: API Body Mapping + Per-Backend Timeout

**文件**: `src/code_forge/llm_invoke.py`（_invoke_openai L673, _invoke_anthropic L730, _invoke_vertex L803, invoke() timeout）, llm_invoke body 测试
**依赖**: Wave 1

### 变更

#### _apply_params 公共 helper（ONE definition, no copy-paste）
```python
def _apply_params(
    body: dict,
    backend: BackendConfig,
    *,
    outcap_key: str,
    allow_thinking: bool,
    allow_effort: Union[bool, str],  # False=skip, True="top_level", "output_config"=nested
    default_temperature: float = -1.0,
) -> None:
    """Apply typed config fields + generic params to a request body.

    default_temperature: format-specific fallback when backend.temperature
    is -1 (sentinel = "not configured"). openai passes 0.0 here for
    backward compat (forge historically hardcoded temperature:0 on openai);
    anthropic/vertex pass -1.0 (omit, matching today's behavior).
    Intentional: openai's 0-default is a backward-compat choice, not a
    universal best practice -- reasoning models mostly ignore temperature.
    """
    # Resolve outcap key: backend-level override > format default
    resolved_key = backend.outcap_key or outcap_key
    # cap fallback: max_completion_tokens > max_tokens. 0 = "not configured"
    # (0 output tokens is nonsensical; the `or` treats 0 as falsy intentionally)
    cap = backend.max_completion_tokens or backend.max_tokens
    body[resolved_key] = cap        # EXACTLY ONE cap key, never both
    if allow_thinking and backend.thinking_type:
        th = {"type": backend.thinking_type}
        if backend.thinking_budget > 0:
            th["budget_tokens"] = backend.thinking_budget
        body["thinking"] = th
    if allow_effort and backend.reasoning_effort:
        if allow_effort == "output_config":
            # Anthropic/Vertex: effort nests in output_config, field name is "effort"
            body.setdefault("output_config", {})["effort"] = backend.reasoning_effort
        else:
            # openai: reasoning_effort is top-level (DeepSeek/GLM/GPT confirmed)
            body["reasoning_effort"] = backend.reasoning_effort
    # Temperature resolution: configured value wins; else format default; -1 = omit
    effective_temp = backend.temperature if backend.temperature >= 0 else default_temperature
    if effective_temp >= 0:
        body["temperature"] = effective_temp
    if backend.stream:
        body["stream"] = True
    for k, v in (backend.params or {}).items():
        body[k] = v
```

**openai temperature 零回归说明**: BackendConfig.temperature 默认 -1.0（omit）。当前 _invoke_openai 硬编码 `temperature: 0`。移除硬编码后，`_apply_params` 通过 `default_temperature=0.0` 参数注入 openai 专属默认值。unconfigured openai 后端仍发 `temperature: 0`（行为不变）。客户设 `temperature: 0.7` -> 发 0.7；设 `temperature: -1` -> 不发。anthropic/vertex 不传 `default_temperature`（默认 -1.0 = omit），与当前行为字节相同。

#### Per-format 调用
- `_invoke_openai`（L673）: `_apply_params(body, backend, outcap_key="max_completion_tokens", allow_thinking=True, allow_effort=True, default_temperature=0.0)`。**移除** hardcoded `temperature: 0` 和 `max_tokens` key。KEY 变更 max_tokens -> max_completion_tokens 是预期行为变更 -- commit 标注。DeepSeek/GLM 通过 `outcap_key: max_tokens` 字段选择正确 key。
- `_invoke_anthropic`（L730）: **移除** body 初始化中的 hardcoded `"max_tokens": backend.max_tokens`（L745）。`_apply_params(body, backend, outcap_key="max_tokens", allow_thinking=True, allow_effort=False)`。无 default_temperature（默认 -1.0 = omit，与当前相同）。effort=False 因 MiniMax anthropic endpoint 不暴露 reasoning_effort 字段（thinking 通过 thinking_type 控制）；如果未来需要 Claude-via-Anthropic 的 effort，改为 `allow_effort="output_config"`。
- `_invoke_vertex`（L803）: **移除** body 初始化中的 hardcoded `"max_tokens": backend.max_tokens`（L878）。`_apply_params(body, backend, outcap_key="max_tokens", allow_thinking=True, allow_effort="output_config")`。无 default_temperature。**effort 放置**: Anthropic Messages API（Vertex Claude 兼容）要求 `output_config: {effort: value}`（不是 top-level `reasoning_effort`）。Research 确认：Anthropic 官方 docs (platform.claude.com/docs/build-with-claude/effort) 展示 output_config.effort 的 curl/SDK 示例；Anthropic Vertex 文档 (platform.claude.com/docs/build-with-claude/claude-on-vertex-ai) 确认 Vertex API "nearly identical to the Messages API"；LiteLLM #18241 确认 Vertex 接受 output_config（4.6+ 无需 beta header）。字段名是 `effort` 不是 `reasoning_effort`，forge 内部统一用 `reasoning_effort` 存储值，_apply_params 负责映射到正确的 wire format。

#### Per-backend timeout
- `invoke()`（L377）: 当前签名接收 `timeout_s` 参数。优先级变更为：`backend.timeout_s > 0` 时覆盖 caller 传入值（reasoning 模型需要 1800s，caller 可能传 120s 默认值）。具体：在现有 L410-411 的 `if timeout_s is None or timeout_s <= 0` 之前插入 `if backend.timeout_s > 0: timeout_s = backend.timeout_s`。完整优先级链：backend.timeout_s > caller timeout_s > FORGE_LLM_TIMEOUT_S > DEFAULT_TIMEOUT_S(120)。

### 测试（TDD RED->GREEN + bug-inject）

- unconfigured openai -> body has `temperature==0`（来自 default_temperature=0.0）+ `max_completion_tokens` KEY == max_tokens 值; 无 thinking/effort/stream key
- unconfigured anthropic -> body 与当前字节相同; assert `"temperature" not in body`
- unconfigured vertex -> body 与当前字节相同; assert `"temperature" not in body`
- thinking_type=enabled + budget=16000 -> body `thinking=={type:enabled, budget_tokens:16000}`
- reasoning_effort=high on openai -> body has top-level `reasoning_effort=="high"`
- reasoning_effort=high on vertex -> body has `output_config=={"effort": "high"}`; `"reasoning_effort" not in body`（不是 top-level）
- reasoning_effort=high on anthropic -> absent（allow_effort=False）
- temperature=-1 -> key ABSENT（所有 format）; temperature=0.2 -> ==0.2
- params={top_p:0.9} -> body top_p==0.9
- DeepSeek-style openai backend (outcap_key="max_tokens", max_tokens field=32768) -> body has `max_tokens==32768` AND `"max_completion_tokens" not in body`（单 cap key）
- default openai backend (outcap_key="") -> body has `max_completion_tokens` AND `"max_tokens" not in body`
- params={max_tokens: N} -> CliError（protected key, 不再是逃生舱）
- stream=True -> body stream==True
- backend.timeout_s=1800 -> urlopen timeout=1800
- backend.timeout_s=0 -> urlopen timeout == _default_timeout_s() 值（零回归: 不用 0 作为 timeout）
- default_temperature 约定: assert openai caller passes 0.0, anthropic/vertex callers pass -1.0（convention enforceable test, 非仅文档）

Bug-inject:
- drop `effective_temp >= 0` guard -> temperature=-1-absent 测试 FAIL
- make _apply_params write BOTH cap keys (body[outcap_key] + body["max_completion_tokens"]) -> "not in body" 断言 FAIL（双写检测）
- remove `backend.timeout_s > 0` override -> timeout=1800 测试 FAIL

### Done 条件

- body 测试全绿
- 零回归: unconfigured anthropic/vertex 字节相同; unconfigured openai 仍发 temperature 0
- openai 唯一变更: KEY max_tokens -> max_completion_tokens（commit 标注）
- bug-inject teeth（3 个注入全证明）
- 全套 `pytest` 绿

---

## Wave 4: SSE Streaming（高风险新子系统，单独隔离）

**文件**: `src/code_forge/llm_invoke.py`（新 `_read_sse` + _invoke_openai stream 分支）, stream 测试
**依赖**: Wave 3（body 发出 stream:true）

### 变更

#### _read_sse（port from trinity worker_pool.py:330+）
```python
def _read_sse(response) -> dict:
```
- 逐行读 response, 解析 `data: {...}` chunk
- 跳过 `data: [DONE]` 和空行
- delta 中缺少 `content` key（如 role-only delta）-> 视为空字符串, 不 crash
- 连接中断（partial final line）-> 丢弃不完整行
- **error-only chunk**（如 `{"error": {...}}` 无 `choices` key）-> 不 crash; 返回含 error 的 dict，让 `_check_body_error` 走正常错误处理路径（外部 review gm+mm 同时发现此边界）
- 拼接 `choices[0].delta.content` 为完整 content
- 组装为 `{"choices": [{"message": {"content": assembled}}]}` -- 注意是 `message.content` 不是 `delta.content`，与 json.loads 路径的已有 extractor（L720 附近）shape 一致
- **Intentional scope limit**: `_read_sse` 仅组装 `delta.content`; `delta.reasoning_content`（DeepSeek/MiMo thinking 阶段的思考内容）被丢弃。forge 审查场景只需最终审查结果，不需要 reasoning 过程。外部 review（mimo + ds）确认此设计可行但要求显式声明

#### _invoke_openai stream 分支
```python
resp_data = _read_sse(response) if backend.stream else json.loads(response.read().decode())
```
然后 `_check_body_error(resp_data, backend)` 照常。

#### stream=True on non-openai format
`stream=True` on anthropic/vertex format -> raise `CliError("streaming not supported for {format} format; use format: openai")`。Intentional: openai SSE 是 Phase 34 scope（DeepSeek/GLM/MiMo 全走 openai path）；anthropic/vertex SSE 需要不同的 event shape 解析器，scope 外。明确拒绝优于静默忽略。

#### Scope
- openai SSE FIRST（DeepSeek/GLM/MiMo 走 openai path -- 最需要 streaming 的 providers）
- anthropic/vertex SSE: 不在此 phase 构建; stream=True on 这两个 format 是 CliError

### 测试（TDD RED->GREEN + bug-inject）

- stream=True + fake SSE bytes -> assembled content == 拼接的 deltas
- 组装后 shape 断言: `resp_data["choices"][0]["message"]["content"]`（不是 `delta`）
- _check_body_error 在 SSE 后运行
- stream=False -> json.loads 路径不变
- SSE 含 body error -> 被 _check_body_error 捕获
- delta 中无 content key -> assembled 包含空串部分, 不 crash
- **MiMo/DeepSeek-style**: delta has `reasoning_content` but no `content` -> assembled content unaffected, reasoning_content discarded（显式测试，非隐式覆盖）
- **error-only SSE**: stream 返回 `data: {"error": {...}}` 无 choices -> _read_sse 不 KeyError crash，返回含 error 的 dict，_check_body_error 正常捕获
- stream=True + format=anthropic -> CliError（消息含 `backend.name` 方便多后端 triage）
- stream=True + format=vertex -> CliError（消息含 `backend.name`）

Bug-inject: 跳过 SSE 后 _check_body_error -> body-error-in-stream test FAIL -> revert -> PASS

### Done 条件

- stream 测试全绿; stream=False 零回归
- 非 openai format + stream=True -> CliError
- bug-inject teeth
- 全套 `pytest` 绿

---

## Wave 5: CLI Backend Env（ADR-0004）

**文件**: `src/code_forge/llm_invoke.py`（_invoke_cli L426, Popen L462）, cli invoke 测试（mock Popen）
**依赖**: Wave 1, Wave 2。与 Wave 3-4 不相交但同文件 -> 顺序提交。

### 变更

```python
if backend.env_unset or backend.env_set:
    child_env = dict(os.environ)
    for k in backend.env_unset:
        child_env.pop(k, None)
    child_env.update(dict(backend.env_set))
else:
    child_env = None                 # byte-identical to today

proc = subprocess.Popen(cmd, ..., env=child_env)
```

### 测试（TDD RED->GREEN + bug-inject）

- no env field -> Popen called with `env=None`（不是空 dict, 不是任何 dict）
- env_unset=("ANTHROPIC_BASE_URL",) + monkeypatch os.environ 含该 key + PATH -> Popen env= 是 dict, 是 os.environ 的 COPY（非同一对象）, 该 key absent, PATH present
- env_set=(("X","1"),) -> Popen env= has X=="1"
- env_unset + env_set both present -> 两者都正确应用; unset 在 set 之前执行（pop then update）
- absent var in env_unset -> 静默跳过（pop default None），不 raise

Bug-inject:
- else-branch 改为 `env={}` -> env=None 测试 FAIL + PATH-present 测试 FAIL -> revert -> PASS
- skip env_unset pop -> key-absent 测试 FAIL -> revert -> PASS

### Done 条件

- cli env 测试全绿; 无 env 时 Popen env=None
- bug-inject teeth（2 个注入）
- 全套 `pytest` 绿

---

## Wave 6: 示例配置 + 文档

**文件**: `src/code_forge/init_template.py`, `docs/configuration.md`
**依赖**: 所有前序 wave
**Commit marker**: `# docs`

### 变更

#### init_template.py 示例后端

| 示例 | 关键参数 |
|------|---------|
| deepseek | thinking_type: enabled, reasoning_effort: high, max_completion_tokens: 32768, **outcap_key: max_tokens** |
| claude-46 | format: vertex/anthropic, thinking_type: adaptive, reasoning_effort: high, max_completion_tokens: 32768 |
| mimo | stream: true, max_completion_tokens: 65536, timeout_s: 1800, thinking_type: enabled |
| kimi | max_completion_tokens: 32768（不设 thinking/temperature） |
| minimax | format: anthropic, timeout_s: 1800, **thinking_type: adaptive**（omit 有 empty response 风险，显式 adaptive 更安全） |
| glm | stream: true, **thinking_type: enabled**, reasoning_effort: max, max_completion_tokens: 32768, timeout_s: 1800, **outcap_key: max_tokens** |
| cli env 示例 | type: cli, env: {unset: [...]} |

**不 ship GPT-5.6**（limited preview）; GPT-5.5 是 GA flagship。

**init_template 字段关系说明**（防读者困惑）: `max_completion_tokens` 是统一的 cap 值字段（新增），`max_tokens` 是既有 legacy 字段（回退），`outcap_key` 控制发送到 provider 的 wire key 名。三层：值 → key → wire。在示例注释中说明此关系。

**MiMo thinking_budget 注释**: mimo 示例不设 `thinking_budget`（omit = server default）。如 API 要求显式值，real-API smoke 会 400 — 在示例注释中标注 "set explicitly if API requires"。

#### configuration.md
- account-auth env 段落更新为声明式 `env:` 形式
- 新增 per-provider 参数表
- 新增 `params` passthrough 说明 + protected key 列表
- **迁移说明**: "v2.7 起 openai format 的 output-cap key 默认变更为 `max_completion_tokens`。如果你的 DeepSeek/GLM 后端之前使用默认的 `max_tokens` key，请在 gate.yaml 中添加 `outcap_key: max_tokens` 以保持兼容。Kimi/MiMo 已使用 `max_completion_tokens`，不受影响。"
- **SSE reasoning_content 文档**: "SSE streaming 仅组装 `delta.content`。thinking-mode provider（DeepSeek/MiMo）的 `delta.reasoning_content` 被丢弃 — forge review 只需最终审查结果。"

### Done 条件（provable by command）

- `grep -cE 'thinking_type|reasoning_effort|max_completion_tokens|stream|timeout_s' src/code_forge/init_template.py` 返回 >= 6（每个示例至少一个）
- `grep 'GPT-5.6' src/code_forge/init_template.py` 返回空（不 ship limited preview model）
- `grep -c 'env_unset\|env:' src/code_forge/init_template.py` 返回 >= 1（cli env 示例）
- `# docs` commit

---

## Post-Wave Gate

1. **独立 reviewer sub-session**（cold context, impl != reviewer）: forge 三周期 9-pass on FULL diff
2. **Step 0**: ruff, py_compile, non-ASCII grep, 无 plan/ADR ref 在代码注释中
3. **真实 API smoke**（非 mock）: ds/mimo via CN proxy 端到端, 确认 thinking+effort 塑造 response, SSE 组装干净
4. **Wrap-up**: ROADMAP/STATE, snapshot-planning, 报告 branch+SHA+diff stat

## Non-Negotiables

- 不自动合并; sub-session 报告 branch+SHA+diff stat; host ff-merge
- Commit markers: 代码波 `post-review-c3`, Wave 6 `# docs`
- 两行 commit, WHY in body, 无 review 词汇, Signed-off-by: Minxi Hou <houminxi@gmail.com>
- 每波 main session 验证后再发下一波
- 所有代码注释和 commit message 必须英文（plan 可用中文，代码产物不可）
- 不在代码注释中引用 ADR 编号/plan 编号/wave 编号 -- 把设计理由翻译为自包含的技术注释

## Zero-Regression Acceptance

- Unconfigured anthropic/vertex: body 字节相同; assert `"temperature" not in body`
- Unconfigured openai: 仍发 temperature==0（来自 _apply_params default_temperature=0.0）; cap key 由 outcap_key 决定（default="" -> max_completion_tokens; DeepSeek/GLM 设 outcap_key=max_tokens）; body 中只有一个 cap key，绝不双写
- BackendConfig hashable: `hash(config)` 不 raise（params compare=False; env fields are typed tuples）
- `env` field on api/vertex backend -> CliError at parse（不静默忽略）
- `stream=True` on anthropic/vertex format -> CliError（不静默忽略）
- timeout_s=0 -> urlopen timeout == _default_timeout_s()（不用 0 作为 timeout）
- 无 env field 的 cli backend -> Popen env=None（字节相同, 不传空 dict）
- 全套测试绿

## External Review Dispositions (Round 1, 2026-06-29)

4 模型 per-provider routing review。去重后的 findings 和处理：

### FIXED in this plan revision

| ID | 来源 | 问题 | 修复 |
|----|------|------|------|
| gm-B1 / ds-H2 | gm+ds 同时发现 | `max_tokens` 同时是 protected key 和 params 逃生舱 = 双写缺陷 | **Round 2 修正（OpenRouter-validated, ADR-0005 sec 6）**: 加 `outcap_key` 字段，forge 只发一个 cap key。`max_tokens` 回到 protected list（9 个）。DeepSeek/GLM 通过 `outcap_key: max_tokens` 选择正确 key，不通过 params。Round 1 的 "移除 max_tokens" 修法被推翻 |
| gm-M1 | gm | Claude 4.6 effort -> adaptive 映射 | **FIXED in Round 2**: Vertex 实际要求 `output_config.effort`（非 top-level reasoning_effort）。_invoke_vertex 改为 `allow_effort="output_config"`。见 Research #4 |

### ACKNOWLEDGED -- scope-limited, documented (不改代码, 在文档/示例中标注)

| ID | 来源 | 问题 | 处理 |
|----|------|------|------|
| ds-H1 | ds | reasoning_effort 是 top-level 还是 nested in thinking（DeepSeek 官方文档自相矛盾） | forge 发 top-level reasoning_effort（与 thinking_mode guide 的 Python 示例一致）。如果 API reference 的 nested 版本是权威的，客户可用 `params: {thinking: {reasoning_effort: "max"}}` 覆盖。real-API smoke 会验证 |
| gm-H1 | gm | GPT-5.5 Responses API 用 max_output_tokens | forge 走 Chat Completions（/chat/completions），不走 Responses API。Responses API 是 GPT-5.5 的新路径但不是唯一路径。Chat Completions 使用 max_completion_tokens。超出 Phase 34 scope |
| gm-H2 | gm | GPT-5.5 reasoning + temperature 0 可能被拒 | GPT-5.5 Chat Completions 接受 temperature 0-2。reasoning 偏好 Responses API（不同端点）。如果 GPT-5.5 确实拒绝，客户设 `temperature: -1`（omit）。forge 的 0 是 backward-compat 默认，不是强制 |
| mm-H1 | mm | anthropic format 下 temperature 范围 0-1 非 0-2 | 正确。anthropic format 的 temperature 范围确实是 0-1。forge 不校验 temperature 范围（provider 校验），但 init_template 的 minimax 示例不设 temperature（omit），避免范围冲突 |
| mm-H2 | mm | stream=True 阻塞 MiniMax 首选 anthropic format | Acknowledged. Phase 34 scope: streaming = openai only。MiniMax 的 anthropic format 不支持 stream。客户需要 stream 时用 openai format；不需要 stream 时用 anthropic format。init_template minimax 示例用 anthropic + 无 stream（当前行为不变） |
| mimo-M1 | mimo | 用户 proxy 走 anthropic 但示例假设 openai | Wave 6 docs: init_template 的 mimo 示例标注 "openai format 直连 api.xiaomimimo.com/v1; Anthropic proxy 不支持 stream"。两种格式都提供示例 |
| mimo-M2 / ds-L3 | mimo+ds | reasoning_content 被 _read_sse 丢弃 | Intentional scope limit: forge review 只需最终答案（content），不需要 reasoning 过程。Wave 4 plan 中显式标注: "_read_sse 仅组装 delta.content; delta.reasoning_content 被丢弃（forge 审查场景不需要思考过程，仅需最终审查结果）" |
| mimo-M3 | mimo | thinking 模式下 temperature 是噪音（发 0 被忽略为 1.0） | Benign: MiMo 忽略而非报错。init_template mimo 示例不设 temperature。功能正确 |
| ds-M1 | ds | anthropic-format DeepSeek 映射未说明 | forge 的 deepseek 示例配置使用 openai format（与 thinking_mode guide 一致）。anthropic format 的 DeepSeek effort 映射（output_config.effort）超出 Phase 34 scope |
| ds-M2 | ds | Tool-calling + thinking mode 限制 | forge review 不使用 tool_choice。超出 scope |
| gm-M2 | gm | Opus 4.7+ temperature 400 | init_template 的 claude 示例不设 temperature（omit, sentinel -1）。如果客户手动设了 temperature on Opus 4.7+，provider 会 400 并 forge fail-closed。docs 中标注 |
| mm-M1/M2/M3 | mm | thinking 语义/out-cap 路由/omit=on 未定义 | init_template minimax 示例: anthropic format, timeout_s=1800, **thinking_type: adaptive**（Round 2 research #7 发现 omit 有 empty response 风险，已改为显式 adaptive）。out-cap: anthropic format -> max_tokens。MiniMax thinking 客户端可控（adaptive=ON, disabled=OFF），非纯 server-side |

### NOT APPLICABLE (informational only)

| ID | 来源 | 内容 |
|----|------|------|
| ds-L1 | ds | frequency/presence_penalty 也忽略 -- 已知, forge 不发这些 |
| ds-L2 | ds | thinking absent = default enabled -- 正确, forge 对 DeepSeek 不显式发 thinking |
| mimo-L1 | mimo | Responses API effort -- 超出 Chat Completions scope |
| mimo-L2 | mimo | 阿里云 enable_thinking 差异 -- 不影响直连 |
| mimo-L3 | mimo | 测试缺 reasoning_content delta -- 已被 mimo-M2 覆盖（丢弃行为） |
| mm-L1/L2/L3 | mm | penalties vocabulary / name hyphenation / example opacity -- cosmetic |
| gm-L1 | gm | anthropic streaming 禁用影响体验 -- scope 限制, 已有 CliError |

## Independent Research Results (Round 2 pre-review, 2026-06-29)

7 个灰区（含 4 个 provider outcap 子项 = 10 个调研问题），4 个并行 agent 使用 exa/firecrawl 调研官方 API 文档。

| # | 灰区 | 结论 | 来源 | Plan 修订 |
|---|------|------|------|----------|
| 1 | DeepSeek reasoning_effort 放置 | **top-level** (7 源一致) | api-docs.deepseek.com, Alibaba DashScope, LiteLLM #28138 | 无需改 |
| 2a | DeepSeek outcap key | **max_tokens only** | api-docs.deepseek.com schema | 无需改 (outcap_key: max_tokens) |
| 2b | GLM outcap key | **max_tokens only** | docs.bigmodel.cn schema | 无需改 (outcap_key: max_tokens) |
| 2c | Kimi outcap key | **max_completion_tokens** canonical, max_tokens deprecated | platform.kimi.com/docs/api/chat | 无需改 (default) |
| 2d | MiMo outcap key | **max_completion_tokens only** | mimo.mi.com/docs/en-US/api | 无需改 (default) |
| 3 | GLM reasoning_effort values | 确认 7 值, top-level, 需 thinking.type=enabled | docs.bigmodel.cn/cn/guide/capabilities/thinking | 无需改 |
| **4** | **Claude Vertex/Anthropic effort** | **`output_config.effort`, 不是 top-level** | platform.claude.com/docs, LiteLLM #18241 | **已修: _invoke_vertex allow_effort="output_config"** |
| 5 | Kimi thinking field | enabled OK, disabled 400, omit OK (default on) | platform.kimi.ai/docs, CherryHQ #16017 | 无需改 |
| 6 | SSE 格式一致性 | 4 provider 全部标准 OpenAI SSE | 各 provider 官方 docs + codex-convert-proxy | 无需改, 一个 _read_sse 够 |
| 7 | MiniMax thinking.type | adaptive=ON, disabled=OFF, omit=OFF; **omit 有 empty response 风险** | platform.minimax.io, OpenClaw #90138 | **已修: minimax 示例显式 thinking_type: adaptive** |
