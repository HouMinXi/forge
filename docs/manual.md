# code-forge Manual (English / 中文)

A task-oriented walkthrough: install, configure a backend, trust it, run a
review, and read the result. For the exhaustive field-by-field reference, see
[configuration.md](configuration.md); this manual shows the path end to end.

面向操作的手册:安装、配置 backend、信任它、跑一次 review、读懂结果。逐字段的
详尽参考见 [configuration.md](configuration.md);本手册讲的是从头到尾的完整路径。

---

## 1. What forge does / forge 是做什么的

forge runs a code change through repeated review cycles until zero defects
remain, then gates the commit. A green verdict is either earned (three
consecutive clean cycles) or it honestly declares what it did not verify.

forge 把一段代码改动反复过 review 直到无缺陷,再卡住 commit。绿灯要么是挣来的
(连续三轮干净),要么诚实声明哪些没验证。

**The single most important fact:** where the review actually runs depends on
the *outlet* (see section 6). An armed external backend (subprocess outlet)
produces real, un-fakeable receipts. The inline outlet runs inside your AI
session and returns `DELEGATED` -- it is honest narration, not an enforced gate.

**最重要的一点:** review 究竟在哪跑,取决于 *outlet*(见第 6 节)。武装了外部
backend(subprocess outlet)才会产出真实、不可伪造的 receipt。inline outlet 在你
的 AI 会话里跑、返回 `DELEGATED`,那是诚实自述,不是被强制的门。

---

## 2. Quickstart / 快速上手

```bash
pip install code-review-forge          # PyPI package name (CLI stays code-forge)
cd /path/to/your/repo
code-forge init                        # writes .code-forge/gate.yaml + gate.schema.json
# edit .code-forge/gate.yaml: add a backend (section 3)
export ANTHROPIC_API_KEY=sk-ant-...    # or the key your backend names
code-forge trust                       # trust this repo's gate.yaml (section 4)
git worktree add .worktrees/work -b my-feature   # forge refuses to run in the main tree (section 5)
cd .worktrees/work
# ... make your change ...
code-forge review                      # review uncommitted changes (section 6-7)
```

The PyPI package is `code-review-forge`; the command is `code-forge`.

PyPI 包名是 `code-review-forge`;命令行是 `code-forge`。

---

## 3. Configure a backend / 配置 backend

`code-forge init` creates `.code-forge/gate.yaml`. Add a `backends:` block (a
dict keyed by name). `type: api` calls an HTTP endpoint; `type: cli` shells out
to the `claude` binary. Mark one entry `default: true`.

`code-forge init` 会建 `.code-forge/gate.yaml`。加一个 `backends:` 块(按名字索引
的字典)。`type: api` 走 HTTP;`type: cli` 调本机 `claude`。给一个条目标
`default: true`。

**Never put a key in gate.yaml.** Use `api_key_env` to name an env var and
export the key in your shell. forge rejects any entry containing `api_key`.

**绝不要把 key 写进 gate.yaml。** 用 `api_key_env` 指定环境变量名,在 shell 里
export。带 `api_key` 字段的条目会被 forge 拒绝。

### Anthropic API

```yaml
backends:
  claude-api:
    type: api
    format: anthropic
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    model: claude-opus-4-5
    default: true
```

### CN models (cross-Pacific; chosen for price) / 国产模型(跨太平洋,按价格选)

```yaml
backends:
  mimo-pro:
    type: api
    format: anthropic
    base_url: https://token-plan-cn.xiaomimimo.com/anthropic
    api_key_env: MIMO_PRO_API_KEY
    model: mimo-v2.5-pro
    max_tokens: 16384      # mimo-pro truncates its text block at lower caps; keep 16384
    default: true

  deepseek:
    type: api
    format: openai
    base_url: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
    model: deepseek-v4-flash
```

Export the key the backend names before running forge:

跑 forge 前先 export 对应的 key:

```bash
export MIMO_PRO_API_KEY=$(pass show api/mimo-pro)
export DEEPSEEK_API_KEY=$(pass show api/deepseek)
```

A practical note on CN backends: deepseek is fast and finds real bugs in the
first one or two rounds, but on a small, already-clean diff it tends not to
converge -- it keeps producing false positives. Run a round or two, then stop;
do not chase a clean verdict from it on a tiny change. mimo-pro is higher
precision. Both are cross-Pacific, so keep timeouts generous (section 8).

国产 backend 的实战经验:deepseek 快、前一两轮能找到真 bug,但在小而干净的 diff
上往往不收敛——会一直刷假阳。跑一两轮就停,别在小改动上追它的干净绿灯。mimo-pro
精度更高。两者都跨太平洋,超时要给宽(见第 8 节)。

Full field reference (vertex, cli, max_tokens, all formats): see
[configuration.md](configuration.md#gateyaml-backends-block).

完整字段参考(vertex、cli、max_tokens、所有 format):见
[configuration.md](configuration.md#gateyaml-backends-block)。

---

## 4. Trust the config / 信任配置

A `gate.yaml` that ships inside a repo is **untrusted by default** -- a hostile
repo could point `base_url` at an attacker endpoint and name a real env var in
`api_key_env` to exfiltrate your key. So forge will not use repo-supplied
backends until you trust the repo.

仓库里自带的 `gate.yaml` **默认不被信任**——恶意仓库可能把 `base_url` 指向攻击者
端点、用 `api_key_env` 点名一个真实环境变量来窃取你的 key。所以在你信任该仓库
之前,forge 不会用仓库提供的 backend。

```bash
code-forge trust            # list the dangerous fields, then trust THIS repo
code-forge trust --status   # read-only: is this repo trusted?
code-forge trust --revoke   # undo
```

Trust is recorded per worktree path. Review the listed dangerous fields
(`api_key_env`, `base_url`) before you confirm.

信任按 worktree 路径记录。确认前先看清列出的危险字段(`api_key_env`、`base_url`)。

---

## 5. The worktree requirement / worktree 要求

`code-forge review` refuses to run in a repository's main working tree. Create a
linked worktree and run from there. This keeps review (and any fix commits) off
your primary checkout.

`code-forge review` 拒绝在仓库主工作树里运行。建一个 linked worktree、在里面跑。
这样 review(以及任何修复 commit)都不会落在你的主 checkout 上。

```bash
git worktree add .worktrees/work -b my-feature
cd .worktrees/work
```

If you see `must run inside a linked git worktree`, this is why.

看到 `must run inside a linked git worktree` 就是这个原因。

---

## 6. How forge picks the outlet / forge 怎么选 outlet

The *outlet* decides where review runs. Resolution order (first match wins):

*outlet* 决定 review 在哪跑。解析顺序(先命中先用):

1. `--outlet` CLI flag
2. `FORGE_OUTLET` env var
3. `outlet:` field in gate.yaml
4. **zero-config guard:** if no backend is configured at all, forge errors out
   (it will not silently fall back to a fake gate)
5. **reachability probe:** backend reachable -> `subprocess`; unreachable ->
   error (FAIL CLOSED, never a silent fallback to inline)

| Outlet | Where review runs | Receipts? |
|---|---|---|
| `subprocess` | a fresh `claude` subprocess per pass (real StateMachine) | yes, real |
| `subagent` | a fresh Agent per pass, inside the session | yes |
| `inline` | the merged skill, inside your current AI session | no -- returns `DELEGATED` |

`inline` and `subagent` are reached **only** by explicit override (flag / env /
gate.yaml). The default path, when a backend is armed and reachable, is
`subprocess` -- the one that produces un-fakeable receipts.

`inline` 和 `subagent` **只能**通过显式 override(flag / env / gate.yaml)选到。
默认路径——当 backend 已武装且可达——是 `subprocess`,也就是产出不可伪造 receipt
的那个。

**Why this matters:** if you only configure an LLM in your editor and let the
skill narrate the review (inline), forge returns `DELEGATED`, not an enforced
PASS. To get a real gate, arm an external backend so review resolves to
`subprocess`.

**为什么重要:** 如果你只在编辑器里配了个 LLM、让 skill 自述 review(inline),
forge 返回的是 `DELEGATED`,不是被强制的 PASS。要拿到真门,就武装一个外部
backend,让 review 解析到 `subprocess`。

---

## 7. Run a review / 跑一次 review

```bash
code-forge review                 # review uncommitted changes (staged + unstaged)
code-forge review --committed     # review the current branch vs its merge-base
code-forge review --whole-file PATH   # review an entire file (no diff needed)
```

forge runs the three-cycle static review (qodo / expert / adversarial passes),
then the dynamic gates (R1 tests, R2 mutation, R3 e2e) where applicable, and
prints a verdict.

forge 跑三轮静态 review(qodo / expert / adversarial 三道 pass),再跑动态门
(R1 测试、R2 变异、R3 e2e,适用时),然后打印 verdict。

---

## 8. Read the result / 读懂结果

**Verdict and exit code / verdict 与退出码:**

| Verdict | Exit | Meaning |
|---|---|---|
| `PASS` | 0 | earned a clean gate |
| `FAIL` | non-zero | findings remain; see the grouped output |
| `DELEGATED` | 5 | inline outlet -- review was narrated in-session, not enforced |

**Receipts / 凭据:** a `subprocess` run writes machine-readable artifacts under
`.code-forge/` (and `.forge/findings.json`): per-pass findings, per-round state,
and a SARIF report. These are the proof that the passes actually ran. An inline
run emits no real receipt and no real verdict -- that is by design (it is
honest about being un-enforced).

**Receipts:** `subprocess` 跑完会在 `.code-forge/`(以及 `.forge/findings.json`)
写机器可读的产物:逐 pass 的 findings、逐轮 state、一份 SARIF 报告。这些是 pass
真跑过的证据。inline 跑不产出真 receipt、不产出真 verdict——这是有意的(它对"未被
强制"这件事诚实)。

A `subprocess` FAIL with confirmed findings (rather than a self-narrated inline
PASS) is forge working as intended: an independent process caught what the
in-session narration would have rubber-stamped.

`subprocess` 报 FAIL + 确认的 findings(而不是会话内自述的 PASS),恰恰是 forge
在正常工作:一个独立进程抓到了会话内自述会橡皮图章放过的东西。

---

## 9. Key environment knobs / 关键环境变量

Quick reference; full descriptions in [configuration.md](configuration.md).

快查;完整说明见 [configuration.md](configuration.md)。

| Variable | Default | What it does |
|---|---|---|
| `FORGE_BACKEND` | gate.yaml `default:` | pick a named backend |
| `FORGE_OUTLET` | reachability probe | force `subprocess` / `inline` / `subagent` |
| `FORGE_LLM_MODEL` | `claude-sonnet-4-6` | model for `cli` backends only |
| `FORGE_AUTH_TIMEOUT` | `20` (max 120) | reachability-probe timeout, seconds |
| `FORGE_LLM_TIMEOUT_S` | `120` | per-call LLM-invocation timeout, seconds |

**`FORGE_LLM_TIMEOUT_S`** is the one to raise for slow, cross-region backends
(CN models, reasoning models). The default 120s aborts a healthy-but-slow call
mid-flight; an unset, malformed, or non-positive value falls back to 120s.

```bash
export FORGE_LLM_TIMEOUT_S=300   # give a cross-Pacific reasoning backend room
```

**`FORGE_LLM_TIMEOUT_S`** 是给慢的跨区 backend(国产模型、推理模型)调高的那个。
默认 120s 会把"健康但慢"的调用拦腰打断;未设、非法、或非正值都回退到 120s。

---

## 10. Troubleshooting / 排错

| Symptom | Cause / fix |
|---|---|
| `must run inside a linked git worktree` | you are in the main tree -- `git worktree add` and run from there (section 5) |
| error about no backend / zero-config | no backend armed -- add one to gate.yaml + trust (sections 3-4) |
| `... backend timed out after 120s` | raise `FORGE_LLM_TIMEOUT_S` (section 9) |
| repo-supplied backend not used | not trusted -- run `code-forge trust` (section 4) |
| review returns `DELEGATED`, no receipts | you are on the inline outlet -- arm a backend for `subprocess` (section 6) |
| CN backend never converges on a tiny diff | deepseek spins false positives on small clean diffs -- run 1-2 rounds, then stop (section 3) |

---

## 11. Canary on the inline outlet / inline outlet 的 canary 检查

The canary is an opt-in objective laziness check for the inline review
outlet. It answers: did the reviewer actually read the diff, or did it
rubber-stamp an empty findings list?

canary 是 inline review outlet 的可选懒检查。它回答:reviewer 到底有没有读
diff,还是橡皮图章式地交了个空 findings?

**How to opt in / 如何启用:**

- CLI flag: `code-forge review --canary`
- gate.yaml: add a `canary:` block (see
  [configuration.md](configuration.md#canary-inline-outlet) for full field
  reference)

With no opt-in, the inline outlet is unchanged -- it returns `DELEGATED`
(exit 5) exactly as before.

**What happens / 工作流程:**

1. forge generates N (3..5) semantic defects and injects them into an
   isolated copy of the diff. The real working tree is never mutated and
   git history is never touched.
2. A fresh-context review (no author narrative, anti-anchoring) evaluates
   the modified diff.
3. forge gates on the catch rate: the reviewer must flag at least
   `ceil(0.6 * N)` of the planted defects.

**Exit codes / 退出码:**

| Exit | Verdict | Meaning |
|---|---|---|
| 5 | `DELEGATED` | default (no canary opt-in), unchanged |
| 7 | `UNRELIABLE` | canary miss -- reviewer did not catch enough planted defects |

**Key guarantees / 关键保证:**

- Canary findings never appear in user-facing output. The planted defects
  are stripped before findings are reported.
- The working tree is never mutated. Planted defects exist only in the
  isolated review copy.
- The canary result never alters outlet or model selection.

**Graceful degradation / 优雅降级:**

- If fewer than 2 verified canaries can be generated, the check is skipped
  with a notice (not a hard failure).
- If the canary dispatch fails (LLM timeout, network error), the check
  degrades to `DELEGATED`.
- Non-Python diffs skip the canary with a notice (Python only for now).

For the gate.yaml `canary:` field reference (types, ranges, defaults), see
[configuration.md](configuration.md#canary-inline-outlet).

gate.yaml `canary:` 块的字段参考(类型、范围、默认值)见
[configuration.md](configuration.md#canary-inline-outlet)。

---

## Related / 相关文档

- [configuration.md](configuration.md) -- exhaustive env var + gate.yaml field reference
- [outlet-alignment.md](outlet-alignment.md) -- how the A / B / C outlets share one flow contract
- [setup-vscode.md](setup-vscode.md) / [setup-cursor.md](setup-cursor.md) / [setup-pycharm.md](setup-pycharm.md) -- editor env setup
- [../README.md](../README.md) -- project overview
