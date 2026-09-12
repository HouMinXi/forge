# code-forge

[English](README.md) | 简体中文

[![PyPI version](https://img.shields.io/pypi/v/code-review-forge.svg?cacheSeconds=300)](https://pypi.org/project/code-review-forge/)
[![Python](https://img.shields.io/pypi/pyversions/code-review-forge.svg?cacheSeconds=300)](https://pypi.org/project/code-review-forge/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/HouMinXi/forge/blob/main/LICENSE)

给 AI 编程助手用的五步代码评审流水线。把评审当状态机来跑：每轮三个独立
pass，要求连续三轮干净，任何一条发现都把计数器归零。到提交之前的最短路径
是 9 次静态评审 pass 加一次运行时冒烟测试。

## 为什么

AI 编程助手交出来的代码能编译、能跑、看着也对。单次评审（Copilot、Cursor、
CodeRabbit 之类）能抓到明显缺陷，但漏掉两种失败方式：

- **作者和评审者是同一个人。** 同一个模型既写又审，就会继承自己的盲区。
  code-forge 跑三个独立的评审视角（qodo、expert、adversarial），并把它们的
  发现当作不可信的主张，修之前必须先复现。
- **自称完成。** 靠「我做完了」这类标记来放行的钩子，任何会写字符串的 agent
  都能绕过。code-forge 卡的是真实状态：一个真正的 `pre-commit` 钩子跑测试
  套件，一个变异测试运行器证明测试能抓到回归，一个覆盖启发式检测跨组件的
  漂移。

## 快速开始

```bash
pip install code-review-forge
code-forge install-skill
```

需要 MCP 服务器（IDE 集成）的话：

```bash
pip install code-review-forge[mcp]
```

第一条命令装 CLI（Python >=3.12）。第二条把 6 个评审 skill 复制到
`~/.claude/skills/`。然后在 Claude Code 里跑完整流水线：

```
/code-forge
```

或者单独调用某个 pass：

```
/qodo-review          # change-aware pre-review (Pass 1)
/code-review-expert   # SOLID, architecture, security (Pass 2)
/adversarial-qe       # red-team QE, 12 attack dimensions (Pass 3)
/kernel-fp-verify     # false-positive verification (Step 3.5)
/smoke-test           # runtime verification (Step 4)
```

其他 agent 目标：

```bash
code-forge install-skill --target vscode      # <cwd>/.claude/skills/
code-forge install-skill --target universal   # <cwd>/.agents/skills/
code-forge install-skill --dest /path/to/dir  # explicit location
code-forge install-skill --skill code-forge   # one skill only
code-forge install-skill --force              # overwrite existing
```

## 上手指南

从安装到第一次带门禁的提交，完整走一遍。

### 1. 安装并初始化

```bash
pip install code-review-forge
cd your-repo
code-forge init
```

`init` 会创建 `.code-forge/gate.yaml`，里面带注释掉的示例。它**不会**替你
配置后端或测试运行器，这两样都要你自己来。

### 2. 配置后端

打开 `.code-forge/gate.yaml`，把某个后端块的注释去掉。以 Anthropic API 为例：

```yaml
backends:
  claude-api:
    type: api
    format: anthropic
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    default: true
```

`api_key_env` 是环境变量的**名字**，不是 key 本身。在 shell 里设置这个变量：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

然后信任这份配置（每次改动 gate.yaml 后都要做一次）：

```bash
code-forge trust
```

至少有一个后端配置好并且被信任之前，评审**拒绝运行**。

### 3. 跑第一次评审

code-forge 要求在 git worktree 里跑评审，做隔离。建一个 worktree，改点东西：

```bash
git worktree add .worktrees/work -b my-feature
cd .worktrees/work
# edit some code, then stage it
git add -A
code-forge review
```

评审读的是**暂存区（index）的 diff**，不需要先提交。

**单分支的简单仓库：** 如果 worktree 对你的工作流来说太重，可以绕过这个检查：

```bash
code-forge review --allow-main
# or permanently:
FORGE_ALLOW_MAIN=1 code-forge review
```

### 4. 安装提交门禁（可选，但推荐）

提交门禁在每次代码提交时跑 `code-forge verify`（回执防篡改检查）和
`code-forge gate-check`（测试套件）。它要求 gate.yaml 里有 `test` 一节：

```yaml
test:
  command: [pytest, -q]
  timeout_seconds: 900
```

然后安装：

```bash
code-forge install-hooks
```

**这对你的工作流意味着什么：**

- **代码提交**（`.py`、`.go`、`.rs` 等）必须先有一次通过的评审。钩子跑
  `code-forge verify`，检查暂存的 diff 是否已经记录了一次完整评审。没有
  评审在先，提交会被拦住。
- **文档/配置提交**（`.md`、`.yaml`、`.toml`、`LICENSE` 等）自动跳过门禁，
  不需要评审，也不需要 `--no-verify`。

**新仓库的引导：** 如果你在第一次评审之前就装了钩子，代码提交会被拦。要么
先跑一次成功的 `code-forge review`，要么临时设 `FORGE_ALLOW_NO_BACKEND=1`
绕过回执门禁，等后端调通再说。

### 5. 在门禁生效的情况下提交

```bash
# 1. Stage your changes
git add -A
# 2. Run a review (must pass before committing code)
code-forge review
# 3. Commit -- the pre-commit hook verifies the review receipt
git commit -m "your message"
```

评审发现了问题就修，然后重新跑 `code-forge review` 直到通过。提交门禁检查
的是暂存的 diff 有没有一份干净的评审回执。

### 6. 诊断

```bash
code-forge doctor    # check backend reachability, config health
code-forge verify    # check review receipt status
```

## 后端配置

后端也可以在 `~/.config/code-forge/config.yaml` 里一次配好，对所有项目
生效（用 `FORGE_CONFIG_DIR` 改位置）；项目 `gate.yaml` 里同名的后端优先。
`code-forge doctor` 每次运行都会打印解析出来的用户级配置路径。

默认情况下，code-forge 用 PATH 里的 `claude` CLI 和会话模型（不固定模型）。
三个环境变量控制后端：

| 变量 | 用途 | 默认值 |
|---|---|---|
| `FORGE_BACKEND` | 从 `gate.yaml` 里选一个命名后端 | 会话默认 |
| `FORGE_OUTLET` | 强制 outlet：`subprocess` \| `inline` \| `subagent` | 自动探测 |
| `FORGE_LLM_MODEL` | 覆盖 CLI 后端的模型 | `claude-sonnet-4-6` |

**几个例子：**

```bash
# Use the default (claude CLI, session model)
code-forge review

# Pin a specific model for this run
FORGE_LLM_MODEL=claude-opus-4-5 code-forge review

# Use a named API backend from gate.yaml
FORGE_BACKEND=claude-api code-forge review

# Force inline outlet (no subprocess)
FORGE_OUTLET=inline code-forge review
```

**命名后端**（可选）定义在 `.code-forge/gate.yaml`（由 `code-forge init`
创建）的 `backends:` 键下：

```yaml
backends:
  claude-api:
    type: api
    format: anthropic
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    default: true
  openai-compatible:
    type: api
    format: openai
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
  local-claude:
    type: cli
    model: claude-opus-4-5
    command: claude
```

完整参考：[docs/configuration.md](docs/configuration.md)

编辑器配置指南：
- Claude Code：[docs/setup-claude-code.md](docs/setup-claude-code.md)
- VS Code：[docs/setup-vscode.md](docs/setup-vscode.md)
- Cursor：[docs/setup-cursor.md](docs/setup-cursor.md)
- PyCharm：[docs/setup-pycharm.md](docs/setup-pycharm.md)

## MCP 服务器（IDE 集成）

`code-forge-mcp` 是一个本地 stdio MCP 服务器，把 forge 暴露成任何支持 MCP
的编辑器（Claude Code、VS Code Copilot、Cursor、PyCharm AI Assistant）都能
调用的工具。评审路由到配置好的国内后端，调用方模型永远不评审自己的代码。

| 工具 | 用途 |
|------|---------|
| `forge_review` | 评审当前 git diff（快就内联返回，慢就返回 job_id） |
| `forge_gate_check` | 对暂存改动跑提交前门禁 |
| `forge_resolve_outlet` | 显示 forge 会用哪个后端（只读） |
| `forge_job_status` | 按 job_id 轮询一个长时间评审 |
| `forge_init` | 在工作区创建 `.code-forge/` |
| `forge_trust` | 信任 gate.yaml 里的后端 |

**前提：** 一个配置好的后端，API key 在服务器进程的环境里。没有的话
`forge_review` 关门失败（跟 CLI 一样）。一个服务器实例只服务一个项目，多
项目的话按项目配 `FORGE_PROJECT_DIR`（见 [docs/setup-mcp.md](docs/setup-mcp.md)）。

**Claude Code：**

```bash
claude mcp add forge -- code-forge-mcp
```

从仓库根目录启动 `claude`，服务器才能找到 `.code-forge/gate.yaml`。

**VS Code**（1.102+，`.vscode/mcp.json`）：

```json
{
  "servers": {
    "forge": {
      "type": "stdio",
      "command": "code-forge-mcp",
      "cwd": "${workspaceFolder}"
    }
  }
}
```

**坑：** 图形界面的编辑器不继承你的 shell 环境。要么用一个导出 API key 的
脚本包一层 `code-forge-mcp`，要么在服务器配置里设 `env`。基于 `pass` 的
包装脚本示例和各编辑器的 MCP 配置见 [docs/setup-mcp.md](docs/setup-mcp.md)。

**验证：** 调用 `forge_resolve_outlet`，它应该报出一个后端名，而不是
"key not set"。然后对一个真实 diff 调用 `forge_review`。

### 排障：残留的服务器进程

如果旧的 `code-forge-mcp` 进程越积越多（表现为内存偏高或多个 PID），手动
清理：

```bash
pgrep -af code-forge-mcp          # list survivors
pkill -TERM -f code-forge-mcp     # graceful shutdown
sleep 3
pkill -KILL -f code-forge-mcp     # force-kill any that remain
```

服务器重启之后，上一个实例的 job ID 就失效了。已完成的评审无论如何都会在
`.code-forge/` 下留下回执。

## 流水线

```
Code Change
     |
     v
[Step 0]  Syntax (0a) + Lint (0b) + Non-ASCII (0c)
     |
     v
[Cycle 1] Pass 1: qodo-review
          Pass 2: code-review-expert
          Pass 3: adversarial-qe
     |
     |  zero findings -> counter += 1
     |  any finding   -> fix, counter = 0, restart Cycle 1
     v
[Cycle 2] (same 3 passes)
     |
     v
[Cycle 3] (same 3 passes)
     |  counter = 3
     v
[Step 3.5] kernel-fp-verify (if fixes were applied during cycles)
     |
     v
[Step 4]   smoke-test (runtime verification)
     |
     v
[COMMIT GATE]  # post-review-c3
```

## 包含什么

| Skill              | 步骤      | 用途                                                  |
|--------------------|-----------|----------------------------------------------------------|
| code-forge         | 编排器 | 跑完整的五步流水线                         |
| qodo-review        | Pass 1    | 感知改动的预评审，按功能分组走查 |
| code-review-expert | Pass 2    | SOLID、架构、安全分析                   |
| adversarial-qe     | Pass 3    | 红队 QE，12 个攻击维度                    |
| kernel-fp-verify   | Step 3.5  | 10 步误报核验协议             |
| smoke-test         | Step 4    | 运行时验证，带 bash 断言原语      |

## code-forge 做了别人没做的什么

- **多 pass 收敛。** 三个独立视角，连续三轮干净。任何一条发现都把计数器
  归零。Copilot、CodeRabbit、Cursor、Devin 都是单次评审。
- **反幻觉门禁。** code-forge 把 LLM 的评审输出当作不可信的主张。解析器
  确定的发现自动确认；LLM 的发现必须先过证伪才能处置；Step 4 真的把代码
  跑起来。只靠 prompt 的缓解手段最多减少 15% 的幻觉；工具落地能到 65-80%
  （CodeAnt 和 Suprmind 的数据，2026）。
- **真正的提交门禁（R1）。** 一个真实的 `.git/hooks/pre-commit`，跑测试
  套件，相对基线有新失败就拦。卡的是 diff 内容和测试结果，不是自称的
  标记。堵住了 PreToolUse 钩子够不着的终端和 IDE 绕过路径。
- **变异测试把关的评审（R2）。** 静态评审之后、裁决之前，按 diff 范围跑
  变异测试。每个注入改动代码的变异体都对着测试套件跑；存活的变异体标出
  抓不到这次改动的测试。没牙的测试在发现缺陷的同一轮就被拦下。
- **跨组件覆盖启发式（R3）。** 检测横跨多个源码区域、且函数签名有变的
  diff。可选的组件映射配置下，当一个枢纽和一个依赖方在同一个 diff 里都
  变了、而依赖方路径下没有匹配配置测试模式的集成测试时，抛出一条不确定
  的发现。
- **裁决之前先执行。** 被评审的 diff 是跑过的，不只是读过的。声明的环境
  用 sha256 对照 lockfile 校验；执行在一次性目录里进行，被评审的树保持
  只读；超时会杀掉整个子进程组。证据刻意不对称：修复前测试失败是裁决
  输入，修复后测试通过只做记录、不能用来确认一条发现。环境无法落地时，
  报告会直说，而不是对着构建从未用过的版本推理。
- **大 diff 不会被静默评审成干净。** 超过 token 预算的 diff 会沿 def-use
  关系拆开，每组单独评审，共享的 prompt 前缀放在各 pass 的角色句之前，
  后端可以缓存它。在一个 14 文件的 diff 上实测，此前三个 pass 全部因截断
  丢失、零发现：现在 15/15 个 pass 完成，每轮 16-22 条发现，每个 pass 的
  计费输入从 65,748 降到 21,987 token。预算内的 diff 保持逐字节相同的
  单 pass 路径。在推理过程中被截断的回复，只在裁决已经完整时才会被
  抢救，永远不会被算成一轮干净。

## 实测质量

在 SWE-bench Verified 构造的 150 条语料上实测（11 个仓库的 75 条真实缺陷
diff 和 75 条干净对照），每条跑一次，后端 `mimo-v2.5-pro`。默认的三轮干净
设置下，条目级：recall 0.800，precision 0.588，F1 0.678。同一深度的发现级：
precision 40.1%，recall 49.3%，F1 0.442。从一轮干净提到三轮，召回上升
（75 条缺陷从抓到 52 条到 60 条），精度没动。把证伪门关掉，150 条全部以
HOLD 退出，75 条干净对照一条不剩。同一份语料后来又用 `agnes-cn` 跑过深度 1，表格在 [docs/EVALUATION.md](docs/EVALUATION.md)。

这些数字是没有误差棒的点估计，而且因为基准事实不同，不能跟其他工具公开
的数字比。完整的表格、语料构造和注意事项在
[docs/EVALUATION.md](docs/EVALUATION.md)。

## 诚实的局限

- **没有跨仓库影响分析。** code-forge 评审的是单个仓库。多仓库依赖分析
  要靠 CodeRabbit 那类工具或者 Chromium 的 `Cq-Depend`。
- **不会从反馈里学习。** code-forge 不会根据被驳回的发现或开发者偏好做
  调整。每次评审都是独立的。
- **没有长期可维护性评分。** code-forge 不评估技术债的累积。SonarQube 的
  技术债跟踪是最接近的自动化近似。
- **没有性能回归套件。** 没有等价于 Rust `perf.rust-lang.org` 的基准
  harness。
- **R3 检查的是产物存在，不是覆盖证明。** 跨组件检查确认预期路径下存在
  一个集成测试文件；它不验证这个测试是否真的练到了改动的那段代码。一个
  存在但过时的测试也能过门。

静态评审（三轮收敛）只是一层。code-forge 从自己的 Phase 2 经历里学到的
教训是：9 次静态 pass 和 639 个 mock 测试漏掉了 3 个 bug，动态验证抓到了。
验证落地（测试套件 + 变异测试 + e2e 覆盖检查）才是论点，不是 pass 的数量。

## 依赖

- Python 3.12 或更新
- `jq`，bash 冒烟原语要用
- Claude Code 或兼容的 AI 编程助手，用来调用 skill
- `mcp` Python 包（可选，`code-forge-mcp` 要用）：`pip install code-review-forge[mcp]`

改 code-forge 本身要装 dev extras——测试运行器、linter、变异测试运行器都在里面。semgrep 单独一个 extra：声明、PATH、forge venv 必须钉同一条版本线：

```bash
pip install -e '.[dev,mcp,semgrep]'
code-forge doctor
```

`doctor` 会为每个声明的依赖打印一行 `python-deps`，装漏或版本不对当场就能看见。
没有这一步，症状要等到很晚才冒出来而且指错方向：`>=3.4` 的要求下装了 mutmut 2.x，
每次变异测试都以一个模块路径错误中止，看起来像评审自己有 bug。

## 其他安装方式

### git clone

```bash
git clone https://github.com/HouMinXi/forge.git
cd forge
./install.sh
```

把 6 个 skill 从 `~/.claude/skills/<name>` 逐个软链到本仓库的
`skills/<name>`。钩子要手动装，见 `hooks/README.md` 和
`hooks/settings-snippet.json`。

## 启用提交门禁（R1）

`install-skill` 和 `./install.sh` 只安装评审 **skill**，不设置强制执行。
R1 提交前门禁，也就是每次提交都跑测试套件、有新失败就拦、不管编辑器里的
评审怎么说的那一层，是单独的手动步骤：

要在 CI 里跑这道门而不是作为本地钩子，见 [docs/setup-ci.md](docs/setup-ci.md)。

1. 在 `.code-forge/gate.yaml` 里加一节 `test:`。没有的话 `gate-check` 会以
   `gate.yaml must have a 'test' section` 退出：

```yaml
   test:
     command: [pytest, -q]
     timeout_seconds: 900
   ```

   `command[0]` 必须是已知的运行器（`python3`、`python`、`pytest`、`cargo`、
   `go`、`make`、`npm`、`npx`、`node`）；不允许 shell 元字符。

2. 安装钩子：

```bash
   code-forge install-hooks
   ```

   这会写入 `.git/hooks/pre-commit`，先跑 `code-forge verify`（回执防篡改
   检查），再跑 `code-forge gate-check`（测试门禁）。

3. 如果设置了 `git config core.hooksPath`，`install-hooks` 会拒绝写入自定义
   钩子路径，并打印手动方案。把下面两行手动加进你现有的 pre-commit 钩子：

```sh
   code-forge verify --quiet 2>/dev/null || exit 1
   exec code-forge gate-check
   ```

skill 给你评审 pass；这道门才让绿灯真的意味着测试通过。没有它，一次从没
跑过的编辑器内评审也能到达提交。只暂存了非代码文件（文档、配置、元数据，
比如 `.md`、`.yaml`、`.toml`、`LICENSE`、`README`）的提交会被钩子识别并
自动跳过门禁，不需要回执也不需要 `--no-verify`。暂存了那个集合之外的任何
文件，包括未知扩展名，整个提交都会重新触发门禁。

## 钩子（参考实现）

| 钩子                          | 触发点               | 用途                       |
|-------------------------------|-----------------------|-------------------------------|
| `check_worktree.sh`           | PreToolUse Edit/Write | 拦住在主 worktree 里的编辑  |
| `check_non_ascii.sh`          | PreToolUse Write/Edit | 非 ASCII 字符检测 |
| `check_read_before_edit.sh`   | PreToolUse Edit       | 1:1 先读后改比例    |
| `check_review_tracker.sh`     | PostToolUse Bash      | 评审轮次状态机    |
| `check_git_commit_review.sh`  | PreToolUse Bash       | 拦住未评审的提交      |
| `check_git_push_review.sh`    | PreToolUse Bash       | 拦住未评审的推送       |

有些钩子含有环境相关的逻辑（Kerberos 认证、模式匹配），需要你自己改。
见 `hooks/README.md`。

## Bash 冒烟原语

`skills/smoke-test/test-library/shell/` 带 19 个可复用的 bash 断言函数，
除了 `jq` 没有别的依赖：

- `run_and_capture`、`run_concurrent`、`concurrent_wait`
- `assert_success`、`assert_failure`、`assert_exit_code`
- `assert_output_contains`、`assert_output_not_contains`
- `assert_stderr_contains`、`assert_stderr_empty`
- `assert_file_exists`、`assert_file_not_exists`、`assert_file_contains`
- `assert_json_valid`
- `assert_no_zombie`、`assert_temp_clean`
- `assert_no_command_exec`、`assert_no_command_exec_json`、`assert_no_path_traversal`

`test-library/` 处有一个向后兼容的软链指向
`skills/smoke-test/test-library/`，给从
[bash-smoke-primitives](https://github.com/HouMinXi/bash-smoke-primitives)
迁移过来的用户。

## 文档

- [docs/ROADMAP.zh-CN.md](docs/ROADMAP.zh-CN.md) -- 已发布的里程碑、当前的和接下来的
- [docs/EVALUATION.zh-CN.md](docs/EVALUATION.zh-CN.md) -- 在 SWE-bench Verified 语料上的评审质量实测
- [docs/REFERENCES.zh-CN.md](docs/REFERENCES.zh-CN.md) -- 设计依赖的论文，以及每篇拿来做什么
- [docs/manual.md](docs/manual.md) -- 端到端走查（中英对照）
- [docs/configuration.md](docs/configuration.md) -- 逐字段的配置参考
- `evidence/cross-model-complementarity.md` -- 为什么是 3 个不同的评审 pass
- `evidence/design-iterations.md` -- 流水线是怎么演进的
- `evidence/ground-truth-verification.md` -- 为什么冒烟测试必须注入 bug
- `evidence/shell-assertion-footguns.md` -- 5 个 bash 特有的坑
- `evidence/v9-model-coverage-matrix.md` -- 4 个模型的覆盖数据
- `hooks/README.md` -- 钩子安装与改造指南

## 参与

问题和讨论：<https://github.com/HouMinXi/forge/issues>。

## 许可证

Apache-2.0
