# Phase 44 EVAL-ON-DUTY: R2 DevOps & Operational-Viability Review

**Reviewer Profile:** DevOps & Infrastructure Specialist  
**Artifact Under Review:** `/home/houminxi/code/forge/.planning/phases/44-LEDGER-eval-on-duty/44-CONTEXT.md`  
**Focus:** Production operability, CI reliability, failure modes, operator ergonomics, worktree topology resilience, and rollback gates.

---

## 1. Executive Summary & Verification

Phase 44 上一轮（R1）解决了关于 Dead-SHA、Adjudication 元数据继承、PIPE_BUF 截断等核心机制的歧义。本轮 R2 DevOps 运维可行性审查从真实 CI 流水线生产运行、多 Worktree 环境、人类运维排障 UX 及紧急止血（Rollback/Kill-switch）等 Day-2 Ops 维度进行审查。

审查结论：**未发现架构级阻塞性死锁（B=0），但存在 4 个高风险（H=4）、3 个中风险（M=3）及 1 个低风险（L=1）的运维可靠性隐患**。核心隐患集中在：
1. CI telemetry 异常未做隔离，磁盘满或 IO 异常会导致 review verdict 崩溃；
2. 缺乏紧急停用开关（Kill-switch / Config Gate）；
3. 缺少 `ledger list --pending` 使得人工研判（Adjudication）在数百条历史记录下无从下手；
4. `git rev-parse --git-common-dir` 在 Bare 仓库、Submodule 和相对路径下的解析歧义。

---

## 2. Detailed Findings by Operational Axis

### Axis 1: Failure Modes in Production (CI-Write Path)

#### [H] DO-01: CI 写 Ledger 缺乏异常防御隔离，遥测故障可导致 CI 构建崩溃
- **Scenario**: D-01 将在 CI 运行末期（PASS/FAIL/ESCALATED）调用写入逻辑。在生产 CI 环境（如 GitHub Actions、GitLab CI、Jenkins 挂载 Docker runner）中，磁盘写满（`ENOSPC`）、只读挂载、文件权限错误或临时 IO 故障是常见环境事件。若 `_write_ledger_rows()` 或 `append_row()` 抛出未捕获的 `OSError`，会导致整个 `code-forge review` 抛出 traceback 异常退出（exit code 1），把原本正常通过的代码评审变成构建失败。
- **Evidence**:
  - `src/code_forge/ledger.py:68-74` (`append_row`)：直接执行 `path.parent.mkdir()` 和 `path.open("a")`，无任何异常捕获与降级。
  - `src/code_forge/machine.py:539-542` (`_run_ci`)：主流程计算出 `verdict` 后持久化 state，若在此处加入未受保护的 ledger 写入，IO 异常会阻断 `return verdict`。
- **Remediation**:
  - CI 写 ledger 属于遥测/样本收集（Telemetry），绝对不能作为构建主干的阻塞点。
  - `_write_ledger_rows()` / `append_row()` 在 CI 路径下必须用 `try...except OSError as exc` 包裹，记录警告到 `_state.infra_errors` 或 `stderr`，保证 review verdict 正常返回。

#### [M] DO-02: 进程异常终止导致的截断行污染后续追加
- **Scenario**: 若 CI runner 被超时强杀（SIGKILL）、OOM Killer 终止或容器销毁，正在进行的 `fh.write(line)` 可能只写入了半行 JSON 且末尾缺少换行符 `\n`。当下一次 CI 任务或本地命令调用 `append_row()` 时，`open("a")` 会直接将新行拼接在残缺行的末尾，形成如 `{"fingerprint":"abc"{"fingerprint":"def"...}` 的复合畸形行。
- **Evidence**:
  - `src/code_forge/ledger.py:73-74` (`append_row`)：直接追加字符串加换行，不检查当前文件末尾是否以换行符结尾。
  - `src/code_forge/ledger.py:92-100` (`iter_rows`)：解析异常时跳过整行，导致被强杀的行以及随后被粘连的一整行有效数据同时丢失（两行报废）。
- **Remediation**:
  - `append_row` 或 `_write_ledger_rows` 在打开非空文件时，可做极轻量的尾部字节检测（若末尾不是 `\n` 则先补写 `\n`），防止单次进程强杀污染后续所有追加记录。

#### [L] DO-03: 并发 CI 竞争的最佳努力去重在 POSIX 本地盘可行，网络共享盘需记录边界
- **Scenario**: D-08 接受并发 CI 下 check-then-act 的 TOCTOU 竞争，依赖 Extractor 在读取时做最终去重。在 Linux 本地文件系统（ext4/xfs）上，单个 JSONL 行尺寸受 D-07 约束（<2048 字节，远低于 `PIPE_BUF` 4096 字节），`O_APPEND` 保证单行写入的原子性，不会出现字符交叉混乱。
- **Evidence**:
  - `src/code_forge/ledger.py:11-14`，`machine.py:1309-1315`。
- **Remediation**:
  - 该行为在运维上可接受；只需在文档中明确：跨网络挂载（NFS/SMB）若存在极端并发，可能存在行间交错风险，但在标准独立 Runner / Worktree 本地存储下完全安全。

---

### Axis 2: Operator UX of Adjudication (人工研判流)

#### [H] DO-04: 缺乏未研判项检索与过滤能力，Operator 无法发现待研判 Fingerprint
- **Scenario**: D-10 设计了 `code-forge ledger adjudicate <fingerprint> <state>` 命令。但在实际操作中，CI 会源源不断产生 `UNADJUDICATED` 状态的行。当 Ledger 积累数百条记录时，运维人员要执行 Adjudication，必须先知道“有哪些 fingerprint 等待处理”。目前 `ledger list` 只能列出全部记录或按已知 fingerprint 过滤，没有状态过滤机制。
- **Evidence**:
  - `src/code_forge/cli.py:832-840, 1661-1688`：`list_parser` 仅有 `--json` 和 `--fingerprint` 参数。
  - 运维人员若要在终端中找出未研判项，只能手动 `code-forge ledger list | grep UNADJUDICATED` 或编写 jq 脚本解析 `--json`，工作流存在严重断层（Friction）。
- **Remediation**:
  - `ledger list` 必须增加 `--unadjudicated`（或 `--pending` / `--state UNADJUDICATED`）过滤选项。
  - 建议提供紧凑视图：`code-forge ledger pending`，输出包含 `fingerprint`、`file:line`、`axis_claim`、`evidence` 摘要及生成时间。

#### [M] DO-05: `ledger adjudicate` 缺乏上下文回显与交互模式
- **Scenario**: 运维人员执行 `code-forge ledger adjudicate <fingerprint> FIXED` 时，若命令行不回显该 fingerprint 所对应的上下文（文件名、代码行、原 Finding 描述、Review pass 来源），Operator 必须在另一个窗口反复查看 `ledger list --json`，容易盲敲错判。
- **Evidence**:
  - `src/code_forge/cli.py:1655-1658`：仅打印 `ledger: marked <fingerprint> as <state>`，没有任何关于该 Finding 的语义信息回显。
- **Remediation**:
  - 执行 `adjudicate` 成功后，CLI 输出应回显所继承的元数据摘要（如 `Adjudicated finding 3a7f9c2d (src/foo.py:42 [TRUST]) -> FIXED`）。
  - （可选增强）后续可支持交互式 `adjudicate --interactive`，逐条展示并由人类键入 `[F]ixed / [D]isproved / [E]scaped / [S]kip`。

---

### Axis 3: Worktree Lifecycle & Persistence (持久化与拓扑兼容)

#### [H] DO-06: `git rev-parse --git-common-dir` 在 Bare 仓库、Submodule 及相对路径下的解析歧义
- **Scenario**: D-05/D-11 提出使用 `git rev-parse --git-common-dir` 寻找主仓库持久化路径，以避免 Worktree 删除时 `.code-forge/ledger.jsonl` 被销毁。然而 Git 在不同拓扑下返回的 common dir 形式不同：
  1. 链接 Worktree 中：可能返回绝对路径 `/path/to/main/.git` 或相对路径 `../../.git`（取决于 Git 版本和工作目录层级）；
  2. Bare 仓库中：返回 `.` 或绝对路径 `/path/to/bare.git`，其 `.parent` 根本不是工作区；
  3. Git Submodule 中：返回 `/path/to/superproject/.git/modules/<submodule_name>`，其父目录是 `.git/modules`；
  4. 非 Git 目录：命令直接报错。
- **Evidence**:
  - `src/code_forge/cli.py:1644` (`str(cwd.resolve())`)，`src/code_forge/ledger.py:58-59` (`cwd / ".code-forge" / "ledger.jsonl"`）。
  - 若直接使用 `Path(git_common_dir).parent / ".code-forge"`，在 Submodule 和 Bare 仓库下会直接将 `.code-forge` 写入非预期的 Git 元数据目录深处甚至仓库外部。
- **Remediation**:
  - 必须使用 `git rev-parse --path-format=absolute --git-common-dir` 避免相对路径陷阱。
  - 明确持久化文件的规范落盘位置：推荐直接存放在 `<git-common-dir>/code-forge-ledger.jsonl`（或 `<git-common-dir>/code-forge/` 目录下），这样无论当前是主工作树、子工作树还是无工作树状态，均安全存储在 Git 通用元数据区，完全不受 `git worktree remove` 影响，也不会污染工作区。

---

### Axis 4: Export Hygiene (导出与语料库管理)

#### [M] DO-07: 导出语料库目录默认路径、Git 追踪规范与重导出清理语义未定义
- **Scenario**: D-04/D-09 引入 `code-forge ledger export-eval`。
  1. 若未指定 `--out`，默认导出目录位于何处？若默认写入 `.planning/eval-bank/extracted/`，在 Forge 项目内部 `.planning/` 是 gitignored（local-only），但在外部接入 Forge 的普通用户仓库中可能没有 `.planning/`。
  2. 重复导出（Re-export）语义：若某条记录在重新研判后被修正/跳过，第二次执行 `export-eval` 时：若是增量覆盖，磁盘上会遗留上一次导出的孤儿 `.diff` 和 answers 文件；若是整目录清空（`rmtree`），又可能误删用户在该目录下手动维护的其他说明文件。
- **Evidence**:
  - `src/code_forge/eval/corpus.py:81-100` (`load_corpus` 依赖 manifest.yaml，但 runner 会扫描目录下的 diff 文件）。
- **Remediation**:
  - D-04 必须规范默认 `--out` 路径（如 `./eval-corpus` 或 `.code-forge/eval-export`）。
  - 明确重导出覆盖语义：在写入前检查目标目录，输出前生成原子临时目录或清理由 manifest 管理的文件，并在存在已有文件时提示或要求 `--clean` / `--force` 标志。

---

### Axis 5: Rollback & Emergency Control (紧急止血开关)

#### [H] DO-08: 缺少生产环境 CI 写 Ledger 的紧急关闭开关（Kill-switch / Config Gate）
- **Scenario**: 在生产 CI/CD 流水线中，如果 Phase 44 上线后因某种未知环境缺陷（如高并发写冲突、特定平台上的文件锁死、磁盘配额告警等）引发异常，DevOps 工程师必须能够以“零代码修改、零重新发版”的方式立即停用 CI 写入 Ledger 的行为。
- **Evidence**:
  - 检索 `src/code_forge/` 全局代码，目前没有任何针对 Ledger 功能的配置开关或环境变量（如 `CODE_FORGE_DISABLE_LEDGER`）。
  - `src/code_forge/gate_check.py:39-100` (`load_gate_config`) 也不支持 `ledger:` 配置段。
- **Remediation**:
  - 必须提供双层停用机制：
    1. **环境变量紧急停用**：`CODE_FORGE_DISABLE_LEDGER=1`（或 `CODE_FORGE_LEDGER_ENABLED=0`），可在 CI 平台（GitHub Secrets / Jenkins env）全局注入一键止血；
    2. **配置文件开关**：`gate.yaml` 中支持 `ledger: { enabled: false }`，允许仓库级显式禁用。

---

## 3. Review Scorecard

| Severity | Count | Finding Identifiers | Core Risk Area |
|:---|:---:|:---|:---|
| **Blocker [B]** | **0** | *(None)* | 无架构级死锁或不可实现缺陷 |
| **High [H]** | **4** | DO-01, DO-04, DO-06, DO-08 | CI 异常隔离、待办发现 UX、Git Common Dir 歧义、紧急止血开关 |
| **Medium [M]** | **3** | DO-02, DO-05, DO-07 | 截断行污染、研判上下文回显、导出目录重写与清理 |
| **Low [L]** | **1** | DO-03 | POSIX vs 网络共享盘原子追加说明 |

**SCORECARD: B=0 H=4 M=3 L=1**

---

## 4. Next Actions for Phase 44 Planning (44-01 & 44-02)

1. **44-01 (Write Path & Persistence & Adjudication)**:
   - 将 `CODE_FORGE_DISABLE_LEDGER` / `gate.yaml` 配置开关纳入 write-path 统一门禁；
   - 在 `_write_ledger_rows` CI 路径上增加 `try...except OSError` 异常隔离保护；
   - 确定持久化路径规范（建议 `<git-common-dir>/code-forge-ledger.jsonl`，使用 `--path-format=absolute`）；
   - 在 `cli.py` 中为 `ledger list` 增加 `--unadjudicated` / `--pending` 过滤标志及 `adjudicate` 完成后的元数据回显。
2. **44-02 (Export Extractor)**:
   - 规范 `export-eval` 默认路径与 `--force` / 孤儿 diff 清理机制。
