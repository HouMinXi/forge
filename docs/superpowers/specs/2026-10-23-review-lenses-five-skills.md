# Forge 评审镜头集成规格（五项外部技能，查漏补缺版）

日期：2026-10-23。版本：v9（v1–v8 分别存于 `…-v1.md` 至 `…-v8.md`；v8→v9 变更见文末"变更记录"）。状态：待复审，未实现。
对象：`~/code/forge`，核对基线 main `609aa51`。下文行号均以该提交为准。

## 结论

五项技能只吸收 forge 尚未覆盖的检查。逐条对照后共 57 条检查（v2 的 49 条，加上第 2 轮补出的 8 条）：21 条已覆盖或不适用，14 条不采纳，22 条缺失或部分覆盖。这 22 条去重后压成 2 条内容规则、1 条选择规则和 5 个镜头，写成一段 1567 字节的静态镜头块（v4 为 L4、L5 各加一处限定，+68 B）。

- 镜头块由新模块 `lenses.py` 统一提供。A（`factories.build_l1_provider`）、C（`cli._make_subagent_spawn`）直接拼接；B 与 C-host（`SKILL.md:1618-1625`）不读文件，只用只读 CLI 输出的文本（`code-forge lenses --pass <p>`，非零退出时告知用户并不带镜头继续）。各处字节一致。
- 开关 `review_lenses` 默认关，只能经单独的 trust 哈希（`lens_hash`）生效。改了开关而没有重新 trust，就视为关闭。关闭时 A 与现有 oracle、C 与在 609aa51 上先采的新 oracle（T1-C）逐字节相同，B 的 pass 文件不变。
- 镜头块不要求输出任何东西。干净的改动没有 finding；不再有"unproven:"和"name the one fact"这类逼出 finding 的句子（N-3）。
- 实现之前必须先准入 Phase 59.1：按 Phase 24.1 的先例作为小数插入，59.1-01 不依赖 Phase 59 关闭（`depends_on: []`），但须 forge 另行批准 REQUIREMENTS/ROADMAP 准入修订（纯文档改动，不属于本 spec 与 PLAN，也未起草），并有独立评审过的 PLAN；PLAN 用 Task -1 闸门机检准入已落地，未落地就全部停止（R5 N-2）。测量（59.1-02）在 EVAL60-01 语料冻结、59.1-02 PLAN 预注册并经独立评审之后启动，不等 EVAL60-02（第 5 轮 forge 批准）；开跑前先做只跑关闭臂的预试验，测出每 run 的 P0–P2 finding 数 λ 后再定重复数 R，找不到合格的 R 就判 b 不可计算（R6 P2-1，只卡 59.1-02，不卡默认关的 59.1-01 合入）；该规则对任何输入都有确定结论：q_lo 不在 (0,1)、λ_lo < 0.5、无合格 R 或脚本非零退出，都判 b 不可计算（R7 P2-1）；预试验另测 q_hi，b 的牙齿在 (λ_lo, q_hi) 下取 k ∈ {2, 0.7} 的最坏值判定，每格 10,000 次，门限各留 2 倍蒙特卡洛标准误（无效应 ≥ 0.906，b10 ≤ 0.192），并固定 numpy 2.5.4 与 scipy 1.18.1，版本不符即不可计算（R8 P2-1、P3-2）。测量不达标就保持默认关。
- 三个 pass、回执、clean-window 判定都不变。门禁侧改动只有 `_rule_type` 与 `legacy.py:262` 先剥掉镜头标签，保证 P3 密度判定和 legacy finding_id 不受影响（N-6、R3-9）。降级人工轮一律 UNVERIFIED，不计入三轮（FU-1）。

## 来源（已核对）

核对方法：读本机插件缓存 `/home/box/agent-data/plugins/cache/cursor-public/<plugin>/<commit>/` 下的 `.cursor-plugin/plugin.json` 与 `LICENSE`，再用 GitHub contents API 或 raw 与上游该提交比对。下表六个文件与四份 LICENSE 都逐字节一致（correctness-reviewer.md 于第 3 版补做 raw `cmp`）。

|技能|插件@版本|上游仓库|插件作者 / 版权方|许可证|上游路径（blob）|提交|
|---|---|---|---|---|---|---|
|ce-code-review|compound-engineering@3.29.0|https://github.com/EveryInc/compound-engineering-plugin|Kieran Klaassen、Trevin Chow / © 2025 Every|MIT|`skills/ce-code-review/SKILL.md`（`551c205`）；`skills/ce-code-review/references/personas/correctness-reviewer.md`（`dc80945`，L1 与 L5 尾句的实际来源）|`dd1bf2b8d905fb9283ca16459bcc4ed172afa8fd`（2026-09-28 11:58 EDT）|
|deslop|cursor-team-kit@1.2.0|https://github.com/cursor/plugins|Eric Zakariasson / © 2026 Cursor|MIT|`cursor-team-kit/skills/deslop/SKILL.md`（`1b3827c`）|`9b93e81fb2921b9263d9463c15bafeabfe3903aa`（2026-10-22 15:11 EDT）|
|thermo-nuclear-code-quality-review|cursor-team-kit@1.2.0；thermos@1.0.0 内有同一副本|https://github.com/cursor/plugins|© 2026 Cursor|MIT|`cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md`；`thermos/skills/…/SKILL.md`（两处同为 `ac76a2b`）|`9b93e81…`|
|thermo-nuclear-review|thermos@1.0.0|https://github.com/cursor/plugins|Cursor（plugins@cursor.com）/ © 2026 Cursor|MIT|`thermos/skills/thermo-nuclear-review/SKILL.md`（`1aa5dfd`）|`9b93e81…`|
|blast-radius|pstack@0.15.30|https://github.com/cursor/plugins（`tree/main/pstack`）|Lauren Tan / © 2026 Lauren Tan|MIT|`pstack/skills/blast-radius/SKILL.md`（`b296cce`）|`9b93e81…`|

- "插件作者"取自 plugin.json，是插件级作者，不一定是单个技能的作者。
- `cursor/plugins` 根目录没有 LICENSE（raw 返回 404，已核对）。许可证在各插件子目录：`cursor-team-kit/LICENSE` 与 `thermos/LICENSE` 全文相同，另有 `pstack/LICENSE`。
- EveryInc 仓库在 `dd1bf2b` 下没有 `plugins/compound-engineering/…` 副本（第 2 轮复审实测 404），v2 的这一未决项关闭。
- `qodo-review` 是 forge 自带技能（`src/code_forge/skills/qodo-review/SKILL.md`），不算外部来源（P3-6）。
- forge 本身是 Apache-2.0（`pyproject.toml:14`）。MIT 内容并入需要保留版权与许可声明，所以新增 `src/code_forge/THIRD_PARTY_NOTICES.md`，并加进 `pyproject.toml:73` 的 package-data，保证进入 wheel 与 sdist（N-9）。选 package-data 是为了和 skills 的打包方式一致；它并不能绕开构建依赖问题：基线 `pyproject.toml:14` 的 PEP 639 `license` 本来就需要 setuptools ≥77，而 `:5` 写的是 `>=68.0`，这是现有缺陷，另列 FU-8（R3-6）。可选再加 `license-files = ["LICENSE", "src/code_forge/THIRD_PARTY_NOTICES.md"]`。草稿见 `draft/THIRD_PARTY_NOTICES.md`。
- `LENS_SOURCES` 为每个键（`rules`、`L1`–`L5`）记录一组 `(skill, repo, path, source_commit)`，取值同上表（N-10）。镜头文本是改写的短句，不逐字复制上游。

## 现状（已核对源码）

|事实|位置|说明|
|---|---|---|
|三个 pass 固定|`state.py:41`|`_PASS_NAMES = ("qodo", "expert", "adversarial")`|
|A：pass 角色|`factories.py:479-483`|每个 pass 只有一句角色描述，没有检查维度|
|A：提示词组装|`factories.py:495-524`|`shared = REVIEW_JSON_CONTRACT`（495），可选段在 496-519，差异在 520，角色句在 522-524 的末尾；485-490 的注释说明这样排是为了缓存前缀|
|C：pass 角色|`cli.py:1077-1081`|`_PASS_ROLES`，与 A 同串|
|C：提示词组装|`cli.py:1088-1106`|角色句在最前（1088），其后接 `REVIEW_JSON_CONTRACT` 常量；没有 Blast Radius、Context Sources、Split 三段|
|B：pass 文本|`skills/code-forge/passes/pass1-qodo.md`（115 行）、`pass2-expert.md`（123 行）、`pass3-adversarial.md`（166 行）|由 `skills/code-forge/SKILL.md:378-396` 加载；子代理路径见 `SKILL.md:1618-1625`。B 输出 markdown（`p2:76` 起），不经过 `_json_to_state_findings`|
|B：参考清单|`skills/code-forge/references/code-quality-checklist.md`（130 行）、`solid-checklist.md`（65 行）、`security-checklist.md`（118 行）、`removal-plan.md`（52 行）|由 `pass2-expert.md:31,43,50,69` 加载，属于 B 的现有覆盖，差距表已计入|
|A/C 与 B 内容不对等|同上|pass 文件和参考清单的维度只在 B，A/C 只有角色句。这是现有缺口，处理见 FU-3|
|finding 必填键|`reviewer_json.py:28`|`file`、`line`、`severity`、`description`，多余键被容忍|
|JSON-only 契约|`reviewer_json.py:40-68`|只输出 JSON，并附 excerpt 证据；finding 是唯一输出通道|
|描述落库格式|`reviewer_json.py:898`|`f"[{pass_name}] {desc}"`，在 `_json_to_state_findings`（856-917）里逐个显式构造，未知键丢弃|
|指纹|`reviewer_json.py:776-790`、调用于 `:889`|只取 (file, 行桶, pass)，不含描述，所以加前缀不影响去重|
|state 扩展方式|`state.py:308`、`_finding_from_dict`（`state.py:651-676`）|"additive only, no schema_version bump"，可选字段用 `.get`|
|P3 门禁|`machine.py:3585`（P2→CYCLE_RESTART）、`_rule_type`（`machine.py:3592-3596`，取 `desc[:50]`）、阈值 `flow_contract.py:26-28`|镜头标签会占掉 50 字符中的 10 个，见 N-6|
|UNCERTAIN→RESET|`machine.py:3490`（docstring），实现在 3496-3580|任何语义 UNCERTAIN 都会让 clean 计数归零|
|证伪器 receipt|`falsify_real.py:228-240`（description 进提示词）、`:313-316`（`check_receipt`）；`falsify_receipt.py:77-89`（库行为模式）、`:117`、`:152`|库行为断言没有执行凭据时，CONFIRMED/DISMISSED 降为 UNCERTAIN|
|赞美过滤|`reviewer_json.py:842-853`|`_is_praise` 是子串匹配，`_DEFECT` 只有四个词|
|gate 读取与 trust|`cli.py:141-182`（`_load_gate_backends`）；`trust.py:1-8`、`is_trusted`（`trust.py:127-157`）|哈希**只覆盖 backends**；backends 已 trust 时返回整份 gate dict，其他键不受保护|
|review_focus 的单独 trust|`trust.py:373-402`（`hash_focus_text`、`is_trusted_focus`）、`record_trust`（`trust.py:159-176`，存 `focus_hash`）、`cli.py:3218-3241`（`_load_trusted_yaml_focus`）|本 spec 的 `lens_hash` 照此实现|
|contract 与 Design Intent|`_merge_contract_spec`（`cli.py:3110`，调用于 3485、4112）、`_load_contract_file`（`cli.py:2956`）；进入 A 提示词于 `factories.py:509-510`|`--contract` 自由文本没有 trust。现有两道约束：`_CONFIRMATION_BIAS_DIRECTIVE`（`cli.py:3012-3016`，"NOT a proof of correctness"）和逐条列出的 `## Do NOT Flag`（`cli.py:3018-3023`）|
|估算器|`cli.py:1180-1209`|按字符数 // 4，不含镜头块|
|打包|`pyproject.toml:72-73` package-data `skills/**/*` 等；`MANIFEST.in` `recursive-include src/code_forge/skills *`|仓库根的 NOTICES 不会进 wheel（N-9）|
|字节 oracle|`tests/test_l1_prompt_byte_identity.py`、`tests/fixtures/l1_prompt_digests_pre_p59.json`、`scripts/capture_l1_digests.py`|fixture 内有 `revision` 记录，先例为原位修订（3451e2e、aa23879、8e18c2f）|
|共享前缀测试|`tests/test_factories.py:1331-1335`（`ROLE_NAMES`）、`:1338-1413`（`TestSharedPromptPrefix`）|角色串不得进入共享前缀，三个尾部必须互不相同|
|canary|`canary_gen.py:342`|提示词独立，镜头块不会到达这里（P3-7）|

## 查漏补缺：逐技能差距分析

判定口径：
- **已覆盖**：forge 已有这条检查。可以在任一出口的提示词里，也可以是确定性机制。舍弃，不进镜头块。
- **部分**：forge 有相邻检查，但没有覆盖这条的具体内容。只收缺的那部分。
- **缺失**：forge 完全没有。收入镜头块。
- **不采纳**：有缺口，但与不变式或收敛冲突，或收益低于成本。写明原因。

**B-only 条目（明确决定）**：标为"已覆盖(B)"的条目只在 `passes/*.md` 或 `references/*.md` 里，A/C 提示词没有。本 spec **有意不处理**这 6 条（DS-1、DS-4、TR-8、CQ-6、CQ-10、CE-13）：收进镜头块会让 B 出现两份，而 A/C 缺维度是比本 spec 大得多的现有缺口，整体交给 FU-3。这是决定，不是未决问题。

**收敛约束**：forge 的门禁对"无法裁决"的 finding 很敏感：语义 UNCERTAIN 会 RESET（`machine.py:3490`），P2 会 CYCLE_RESTART（`machine.py:3585`），库行为断言没有执行凭据时降为 UNCERTAIN（`falsify_receipt.py:77-89`、`falsify_real.py:313-316`）。所以第 3 版额外规定：凡是会**逼出** finding、或产出证伪器无法裁决的 finding 的条目，一律不采纳或加限定（N-3、N-4）。

缩写：`p1`/`p2`/`p3` 指 `skills/code-forge/passes/pass1-qodo.md`、`pass2-expert.md`、`pass3-adversarial.md`；`cq`/`solid`/`sec`/`rm` 指 `skills/code-forge/references/` 下的 `code-quality-checklist.md`、`solid-checklist.md`、`security-checklist.md`、`removal-plan.md`；`fac` 指 `factories.py`；`ce-corr` 指上游 `ce-code-review/references/personas/correctness-reviewer.md`。

### blast-radius

|ID|检查|forge 现有覆盖|判定|去向|
|---|---|---|---|---|
|BR-1|列出调用方和依赖方|`fac:502-503` Blast Radius Context；`p3:126-128` 正反向调用链|已覆盖|舍|
|BR-2|点名改动安全所依赖的那一条事实|无|缺失|**不采纳**（v3 改）：JSON-only 下唯一的输出通道是 finding（`reviewer_json.py:40-68`），"点名"会逼出 finding，与"干净时没有 finding"矛盾（N-3）|
|BR-3|grep 看不到的消费者：其他语言读同一份数据、flag 分支、teardown 与时序、3 跳外|`p3:62` wire 与持久化格式，`p3:69-72` 编解码对称，均只在 B|部分|L4，限定为"只报能指出的具体破坏点"。上游的"锁定版本的依赖行为"**不采纳**：这类 finding 就是库行为断言，会被 receipt 规则降为 UNCERTAIN（N-3）|
|BR-4|证据阶梯，证明不了就标 unproven|`reviewer_json.py:40-68` excerpt 证据；`falsify_real.py:207-228` 证伪器；B 还有 `p3:152` "If you cannot prove an issue exists … do not report it"|已覆盖（v3 改）|舍。v2 的规则 3 删除："unproven:" 类 finding 证伪器无法裁决（N-3），且与 `p3:152` 重复（N-7）|
|BR-5|输出"已排除"清单|无|缺失|不采纳：与 JSON-only 冲突（P1-2），见 FU-4|
|BR-6|跑代码或复现来证明|无，执行能力归 INV-03（`docs/gsd/REQUIREMENTS.md:32`）|缺失|不采纳：A/C 不能执行代码|
|BR-7|多模型竞技|三个 pass 加多后端|已覆盖|舍|
|BR-8|读懂 diff 没写明的部分（上游 `SKILL.md:33`）|`fac:502-503`；`p3:124-129` 跨模块副作用（同 TR-2）|已覆盖（v3 新增）|舍|
|BR-9|每个风险写出概率和代价（上游 `SKILL.md:36`）|severity 分级；TR-7 校准|部分（v3 新增）|不采纳：要新增字段（同 P2-1），概率本身也无法证伪|
|BR-10|给出能抓住真 bug 的最便宜测试或复现（上游 `SKILL.md:46`）|`p1:113` "Every suggestion must include file path and a concrete code fix"|部分（v3 新增）|不采纳：只是描述里的可选内容，不值得占预算|

### deslop

|ID|检查|forge 现有覆盖|判定|去向|
|---|---|---|---|---|
|DS-1|与本地风格不符的多余注释|`p3:112`|已覆盖(B)|舍|
|DS-2|可信路径上不必要的防御性检查和 try|`p3:114` 只管只打日志再抛出、`except: pass`|部分|L5，限定为"类型或同一路径上的前置检查已排除的状态"，与 `p3:138-142` 的 "structurally impossible" 口径对齐（N-7、R3-5）；B 中的残留冲突转 FU-3|
|DS-3|用 `any` 等强转绕过类型检查|无|缺失|L5|
|DS-4|深嵌套，应改用提前返回|`p3:88`|已覆盖(B)|舍|
|DS-5|与所在文件风格不一致|`p3:80-84`；`fac:500-501` Conventions Digest|已覆盖|舍|
|DS-6|直接改代码，保持行为不变|forge 只评不改（`p1:115`；INV-08 `REQUIREMENTS.md:37`）|不适用|舍|

deslop 的 5 个关注点和 3 条守则都已在 DS-1..6 中，无遗漏（第 2 轮复审确认）。

### thermo-nuclear-review

|ID|检查|forge 现有覆盖|判定|去向|
|---|---|---|---|---|
|TR-1|只报新增或修改的代码|`machine.py:2004-2026` 只对 L0 做 delta 过滤；`REVIEW_JSON_CONTRACT` 只要求 excerpt 落在 hunk 内，`diff.py:739-775` 也没有范围限制|部分|规则 1|
|TR-2|跨模块副作用破坏功能|`p3:124-129`；`fac:502-503`|已覆盖|舍|
|TR-3|安全漏洞|`fac:481` 的 expert 角色含 security；`p2:48-58`；`p3:47-52`|已覆盖|舍|
|TR-4|破坏开发与运维约定：secret 来源、env 变量名、端口、新增必需脚本；**不含**新增的替代方式和普通依赖（上游 `SKILL.md:30`）|只有 secret 泄漏：`p2:54`、`sec:30`|部分（v3 改）|L3，只收 env、端口、脚本和 secret 来源变更，并带上上游排除条款（N-13）|
|TR-5|feature gate 泄漏：gate 关闭时行为仍可达|`p2:44` 只说"flag 关掉的代码可删"；`rm:19,34,49` 是死 flag 与 flag 遥测，不是同一问题|缺失|L3|
|TR-6|有意且范围明确的破坏不报，除非低估影响或疑似恶意（上游 `SKILL.md:36`）|`cli.py:3018-3023` 只豁免逐条列出的 `## Do NOT Flag` 模式（v4 不再引 `cli.py:3012-3016` 作依据，那几行只是确认偏差提示，R3-10）|已覆盖（逐条豁免，更保守）|舍。v2 规则 2 的 Design Intent 句删除：contract 是未经 trust 的自由文本（`cli.py:2956`），被审的 agent 一句话就能压掉 finding，违反 INV-08（`REQUIREMENTS.md:37`），而且比上游少了"范围明确"和"恶意例外"两个条件（N-2）。forge 现有的逐条豁免更保守，不再叠加|
|TR-7|不过度报告，不夸大严重度|证伪器只判真伪；`reviewer_json.py:845-853` 只过滤赞美|部分|规则 2|
|TR-8|不交"如果 X 已处理就没事"这类未完成的研究|`p3:138-144` 撤回门槛、`p3:152` 证明不了就不报，均只在 B|已覆盖(B)（v3 改）|舍，FU-3。上游原意是"能查就去查"（`SKILL.md:49`），v2 改写成 "or drop it" 会丢召回，且与 `p3:139-142` 口径不一（N-7）|
|TR-9|审完再查 BugBot 和 PR 评论|无|缺失|不采纳：需要联网和 PR 系统，违反"无网络依赖"与 INV-08|

### thermo-nuclear-code-quality-review

|ID|检查|forge 现有覆盖|判定|去向|
|---|---|---|---|---|
|CQ-1|code judo：能删掉分支或层的更小设计|`p2:38-39` 只给 SOLID 重构建议|部分|L2，限定为"点名要删的分支、层或包装，并给出替代代码"，严重度上限 P3。上游把这类问题列为 "presumptive blockers"（`SKILL.md:183-190`），**不采纳该严重度**，改采 CE-17 的 "not a preferred rewrite"（N-4）|
|CQ-2|文件超过 1000 行|函数级已有：`p3:89`（>50 行）、`solid:45`（Long method）、`solid:6`（低内聚模块）；文件级没有；Step 0b 只做 lint（`skills/code-forge/SKILL.md:107`）|部分（v3 改）|L2，只收文件级（确定性实现见 FU-5）|
|CQ-3|一次性布尔或模式、穿插在无关流程里的 ad-hoc 条件|`p3:90` 控制流清晰度；`p2:34` OCP；`solid:13` "editing many switch/if blocks"|部分|L2|
|CQ-4|薄包装、恒等抽象|`p3:109` over-engineering；`p3:116` Speculative parameters；`solid:52` Speculative generality|部分|L2，只收"薄包装/恒等包装"|
|CQ-5|强转或可选类型模糊了不变量，静默 fallback|`solid:65` "Make illegal states unrepresentable"；`cq:86` optional chaining 掩盖结构问题，均只在 B|部分(B)（v3 改）|L5，与 DS-3 合并；A/C 完全没有，B 只有原则级相邻条目，故保留|
|CQ-6|复用已有 helper|`p3:83`|已覆盖(B)|舍|
|CQ-7|feature 逻辑漏进共享模块（上游 `SKILL.md:62` 前半句）|`p2:37` DIP 相邻|部分|L2|
|CQ-8|非原子更新留下半成品状态|`p3:30`（partial fixes）、`p3:42`（缺少回滚）|部分|L1（与 CE-9 合并）|
|CQ-9|无必要的串行编排|`p3:97` 只管 async 中的阻塞调用|部分|不采纳：属性能类，误报风险高，预算有限|
|CQ-10|复制粘贴的逻辑|`p3:113`|已覆盖(B)|舍|
|CQ-11|少而精，不报 nit（上游 `SKILL.md:166-167`）|同 TR-7|部分|规则 2|
|CQ-12|只搬动代码、没有减少概念数的重构（上游 `SKILL.md:94,104,152`）|无|缺失（v3 新增）|不采纳：主观，证伪器无法裁决，与 N-4 同类风险|
|CQ-13|隐藏简单结构的通用 "magic" 机制（上游 `SKILL.md:53,99`）|`p3:109` 相邻|部分（v3 新增）|不采纳：同上|
|CQ-14|实现细节通过 API 泄漏（上游 `SKILL.md:62` 后半句）|`p2:37` DIP 相邻|部分（v3 新增）|不采纳：与 DIP 重叠大，预算有限|

### ce-code-review

|ID|检查|forge 现有覆盖|判定|去向|
|---|---|---|---|---|
|CE-1|把意图摘要发给每个评审者|`fac:509-510`、`cli.py:1095-1096` Design Intent|已覆盖|舍|
|CE-2|核对计划和需求的完整性|Design Intent；`p3:18` Clarify intent|已覆盖|舍|
|CE-3|按改动面路由 persona|三个固定 pass（`state.py:41`）|不采纳|方案 B 已否决|
|CE-4|off-by-one、null 传播|`fac:480`；`p2:73`；`p3:28-29`|已覆盖|舍|
|CE-5|哨兵值或 flag 现在表示多种状态（`ce-corr:9`）|无|缺失|L1|
|CE-6|默认值或 fallback 掩盖了失败（`ce-corr:14`）|`p3:42-43` 只管吞异常；`cq:28` "Will the caller know something went wrong?" 相邻；`cq:23` 反而要求为可恢复错误定义 fallback|部分|L1，限定为"掩盖**不可恢复**错误"，避免与 `cq:23` 方向相反（N-7）|
|CE-7|工具和环境不变式；守卫或测试替身与生产条件不一致（`ce-corr:10`）|`p3:74-78` 讲优雅降级；`p3:101` "Tests that assert on mocks" 只算相邻|缺失|L1|
|CE-8|竞态、TOCTOU|`p2:57`；`p3:52,56-57`|已覆盖|舍|
|CE-9|出错后状态只更新了一半（`ce-corr:12`）|`p3:30`|部分|L1|
|CE-10|置信锚 0/25/50/75/100，按严重度分级放行（上游 `references/subagent-template.md:59-69`）|证伪器加 excerpt 证据，但没有按严重度分级|部分（v3 改）|不采纳：要新增字段，与 P2-1 同理|
|CE-11|合格的改动不需要 finding（上游 `SKILL.md:9`）|`p1:108` 只说省略空段落，依据偏弱；`p3:152` 只在 B|部分|规则 2|
|CE-12|独立 validator 复核 finding|`falsify_real.py:207` RealFalsifier|已覆盖|舍|
|CE-13|测试缺口|`p3:99-104`|已覆盖(B)|舍|
|CE-14|项目规范|`fac:500-501` Conventions Digest|已覆盖|舍|
|CE-15|读 PR 上已有的评论|无|缺失|不采纳：同 TR-9|
|CE-16|跨模型 peer 评审|多后端；ce 的 Stage 3d 会把代码发出本机|不采纳|代码出机（P2-5）|
|CE-17|"improvements whose consequences justify action … not a preferred rewrite"；"Serious defects remain important even when the diff is small"（上游 `SKILL.md:9`）|无|缺失（v3 新增）|规则 2 第二句；同时约束 L2（N-4）|
|CE-18|不建议给不可能为 null 的值加检查；风格、命名、缺少优化不算 finding（`ce-corr:30-34`）|`p3:139-142` 的撤回门槛相邻|部分（v3 新增）|约束 L5 尾句（"类型或前置检查已排除"），不另加文字。风格与命名一句不采纳：`p3:80-84` 要求风格一致，二者会冲突|

**汇总**：共 57 条（v2 的 49 条加 BR-8..10、CQ-12..14、CE-17、CE-18）。

|判定|条数|ID|
|---|---|---|
|已覆盖或不适用|21（其中 B-only 6）|BR-1、4、7、8；DS-1、4、5、6；TR-2、3、6、8；CQ-6、10；CE-1、2、4、8、12、13、14|
|不采纳|14|BR-2、5、6、9、10；TR-9；CQ-9、12、13、14；CE-3、10、15、16|
|进入镜头块|22|规则：TR-1、TR-7、CQ-11、CE-11、CE-17；L1：CE-5、6、7、9，CQ-8；L2：CQ-1、2、3、4、7；L3：TR-4、5；L4：BR-3；L5：DS-2、3，CQ-5，CE-18（CE-17 同时约束 L2）|

与 v2 相比：BR-2、BR-4、TR-6、TR-8 移出镜头块，CE-17、CE-18 移入。CQ-2、CQ-5、TR-4、CE-10 的判定改了，CQ-3、CQ-4、TR-5、CE-6、CE-7、CE-11 补了引用。

## 选定设计

### 镜头定义（只收缺口）

|编号|名称|收录条目|归属 pass|来源（`LENS_SOURCES` 键）|
|---|---|---|---|---|
|规则 1–2|所有 pass 通用|规则 1：TR-1；规则 2：TR-7、CQ-11、CE-11、CE-17|全部|`rules`：thermo-nuclear-review、thermo-nuclear-code-quality-review、ce-code-review `SKILL.md`|
|规则 3|选择规则（原文 "Apply only the lenses on your last line…"）|只用末行列出的镜头，并加标签|全部|forge 自有，不计来源|
|L1|Hidden failure|CE-5、CE-6、CE-7、CE-9、CQ-8|qodo|ce-code-review `correctness-reviewer.md`、thermo-nuclear-code-quality-review|
|L2|Simpler shape|CQ-1、CQ-2、CQ-3、CQ-4、CQ-7；受 CE-17 约束|expert|thermo-nuclear-code-quality-review、ce-code-review `SKILL.md`|
|L3|Gate and env leaks|TR-4、TR-5|adversarial|thermo-nuclear-review|
|L4|Impact beyond the diff|BR-3|adversarial|blast-radius|
|L5|Type and guard slop|DS-2、DS-3、CQ-5；受 CE-18 约束|expert|deslop、thermo-nuclear-code-quality-review、ce-code-review `correctness-reviewer.md`|

归属沿用 P2-4：qodo=L1，expert=L2+L5，adversarial=L3+L4。
- qodo 的角色是"correctness and logic errors"（`fac:480`），对应 L1。
- expert 的角色含"architecture"（`fac:481`），L2 和 L5 都是结构与类型问题。
- adversarial 的角色是"assume bugs exist"（`fac:482`），L3 和 L4 都要往 diff 之外追。

与 v2 相比（第 2 轮），以及第 3 轮的两处（v4）：

|位置|v2|v3|评审项|
|---|---|---|---|
|规则 2|含 "Breakage the Design Intent declares is not a finding…"|删除该句，改为 "A serious defect matters even in a small diff."（CE-17）|N-2|
|规则 3|"settle it … or drop it"；"write \"unproven:\""|整条删除|N-3、N-7、N-15|
|L1|"a fallback that masks an error"|"hides an unrecoverable error"|N-7|
|L2|"a smaller design that deletes a branch or layer"|"P3 at most: name the exact branch, layer or wrapper to delete and show the replacement, not a preferred rewrite"|N-4|
|L3|无排除条款|加 "not new alternatives or ordinary dependency adds"|N-13|
|L4|"name the one fact …"；含 "pinned dependency behavior"|"report only a concrete break you can point to"；删去依赖行为|N-3|
|L4（v4）|"…you can point to where grep misses"|"…you can point to in this repo's code, not in how a library behaves, where grep misses"（+51 B）|R3-4|
|L5（v4）|"…or an earlier check already rule out"|"…or an earlier check on the same path already rule out"（+17 B）|R3-5|
|L5|"states a trusted path cannot reach"|"states the types or an earlier check already rule out"|N-7|

### 镜头块正文（实测）

对 `lens_block.txt` 用 `len(text.encode())` 实测为 **1567 字节**，纯 ASCII。sha256 为 `bef4b26e48d60ed38afc0beda7b1429b9aa333deea06a0d9335c6fcf1b3fffa1`。正文以一个换行开头，下面的代码块首行即这个空行，其余原样给出（R3-13）：

```text

## Review Lenses
- Flag only defects that added or changed lines introduce or expose, not old issues in untouched code.
- A sound change needs no findings; never pad or inflate severity. A serious defect matters even in a small diff.
- Apply only the lenses on your last line; start such a description with its tag, e.g. "[lens:L2] ".
L1 Hidden failure: a sentinel or flag value that now means two states; a fallback that hides an unrecoverable error; a guard or test stand-in unlike production; a multi-step update left half-applied on failure.
L2 Simpler shape, P3 at most: name the exact branch, layer or wrapper to delete and show the replacement, not a preferred rewrite. Targets: one-off booleans, modes or ad-hoc conditionals in unrelated flows; thin or identity wrappers; feature logic in a shared module; a file pushed past 1000 lines.
L3 Gate and env leaks: behavior reachable with its feature gate off; renamed or newly required env vars, secret sources, ports or setup scripts that break dev or deploy; not new alternatives or ordinary dependency adds.
L4 Impact beyond the diff: report only a concrete break you can point to in this repo's code, not in how a library behaves, where grep misses: other-language readers of a stored or wire format, flag-gated paths, teardown or ordering timing, consumers 3 hops away.
L5 Type and guard slop: casts, any, or ignore pragmas that dodge type checks; optional types or fallbacks that blur an invariant; defensive checks or try blocks for states the types or an earlier check on the same path already rule out.
```

关于严重度上限：L2 的 "P3 at most" 只是提示词约束，代码不按标签改严重度（标签只作统计，见下）。P3 不走 `machine.py:3585` 的 P2 分支，只进去重与密度检查（`machine.py:3588-3622`）；超标的风险由验收 c、e、f 衡量。

每个 pass 的选择行是提示词最后一行：qodo 为 `\nLenses: L1.\n`（13 B），expert 为 `\nLenses: L2, L5.\n`（17 B），adversarial 为 `\nLenses: L3, L4.\n`（17 B）。每次调用因此增加 1580–1584 B，按估算器口径 `(1567+17)//4 = 396` token。T9 上限仍为 1600 B，余量 33 B，预算不调整；再加字须先删字。

### 模块 `lenses.py`

可运行草稿见 `/workspace/forge-review/draft/lenses_draft.py`，已自测：

```python
LENS_BLOCK: str                                       # 上面的正文，常量
PASS_LENSES: dict[str, tuple[str, ...]] = {"qodo": ("L1",), "expert": ("L2", "L5"), "adversarial": ("L3", "L4")}
Source = tuple[str, str, str, str]                    # (skill, repo, upstream path, source_commit)
LENS_SOURCES: dict[str, tuple[Source, ...]]           # 键为 "rules"、"L1".."L5"，每个键可有多个来源（N-10）
def selection_line(pass_name: str) -> str
LENS_RE = re.compile(r"^\[(qodo|expert|adversarial)\] \[lens:(L[1-5])\] ")
def lens_of(description: str) -> tuple[str, str] | None   # 只做语法解析
def lens_mismatch(description: str) -> bool               # 标签的镜头不属于该 pass（N-12）
def strip_lens_tag(description: str) -> str               # 去掉 "[pass] " 之后连续的 "[lens:Lx] "，保留 "[pass] "（N-6、R3-9）
_LENS_TAG_RE = re.compile(r"^(\[[a-z]+\] )(?:\[lens:L[1-5]\] )+")   # 模型重复加标签时一次剥净
```

`lens_of("[qodo] [lens:L2] x")` 仍返回 `("qodo","L2")`，这是语法上合法的解析；"错配"由 `lens_mismatch` 按 `PASS_LENSES` 判定，验收 4 的错配比例用它统计。

### 三个出口

开关打开时：

|出口|镜头块位置|选择行位置|改动点|
|---|---|---|---|
|A|紧接 `REVIEW_JSON_CONTRACT` 常量（`fac:495`）之后、`manifest_spec` 之前，属于静态共享前缀|角色句之后（`fac:522-524`），属于各 pass 的尾部|`build_l1_provider` 增加参数 `review_lenses: bool = False`|
|C|紧接 `REVIEW_JSON_CONTRACT` 常量（`cli.py:1088` 的角色句 + 该常量）之后、manifest 之前（R3-11：此处 contract 一律指该 JSON 契约常量，不是 `--contract` 的 Design Intent）|差异块（`cli.py:1106`）之后，作为最后一行|`_make_subagent_spawn`（`cli.py:1052`）加同一参数。真实路径是 `_run`（`cli.py:3720`）→ `_dispatch_subagent`（`cli.py:3449-3462`，新增 `review_lenses: bool = False`）→ `_make_subagent_spawn`（`cli.py:3496`）：`_run` 在 `cli.py:3855-3857` 读 `yaml_focus` 处一并读开关，在 `cli.py:4068`（`yaml_focus=yaml_focus` 旁）传入 `review_lenses=review_lenses`（R4 P1）。关闭时对照 T1-C 新 oracle（R3-2）|
|B|不新增 `passes/lenses.md`（N-5）。B 运行 `code-forge lenses --pass <p>`，把输出原样追加在该 pass 指令的**末尾**|输出本身以 `selection_line(p)` 结尾，所以每个 pass 读到的末行就是自己的选择行|新增只读子命令 `lenses`：开关有效（见下）时打印 `LENS_BLOCK + selection_line(p)`，否则什么都不打印。`SKILL.md:378-396` 三个 pass 各加一句"追加 `code-forge lenses --pass <p>` 的输出；输出为空就不加；非零退出见下方契约"|
|C-host（R3-12）|`SKILL.md:1609` 起 "If outlet is \"subagent\" (Outlet C)"，`:1618-1625` 是宿主自己派生子代理的 C 路径，不是 B。和 B 一样取 `lenses` 子命令输出，追加在子代理提示词末尾|同 B|`SKILL.md:1618-1625` 加同一句；落库走宿主提交路径，前缀不保证，不计入 `lens_of` 统计|

- B 的 pass 文件本身不改；`SKILL.md` 多出的那句在开关关闭时得到空输出，B 的实际指令不变。
- B 只认 CLI 给出的文本，skill 不自己读 gate.yaml（N-1）。
- B 的 pass 文件输出 markdown（`p2:76` 起），不经过 `_json_to_state_findings`。C-host（`SKILL.md:1618-1625`）的子代理虽然返回 JSON，但落库走宿主自己的提交路径，前缀是否保留没有保证。所以 `lens_of` 统计和验收 4 不含 B 与 C-host（N-5、R3-12）。
- B 与 C-host 每次 run 只调用一次 `lenses`（每个 pass 一次，run 开始时取齐后缓存），中途改 gate.yaml 不影响本 run；run 元信息逐 pass 记录镜头开/关（R3-8）。
- MCP：本版**不提供** `forge_lenses` 工具，`tests/test_mcp_server.py:75-85` 的工具集合不变；MCP 宿主走 CLI（R3-14，列入 FU-7）。
- C-host 子代理 schema 没有 code_excerpts，这是现有问题，本 spec 不修，改这一段时保留原样。

### `code-forge lenses` 契约（R3-3）

|项|约定|依据|
|---|---|---|
|调用|`code-forge lenses --pass {qodo,expert,adversarial}`；`--pass` 必填，`choices` 固定为三个 pass|新 subparser 加在 `cli.py:747-756`（resolve-outlet）旁，并加入 `known_subcommands`（`cli.py:2614-2632`）|
|stdout|开关有效：恰为 `LENS_BLOCK + selection_line(p)`，不多一个字节；其他情况为空|T15 逐字节比对 `lenses.py` 常量|
|stderr|只放告警（未 trust、值非布尔、gate.yaml 不可读），格式同 `cli.py:3228-3241`：`gate.yaml review_lenses ignored: ...`|—|
|退出码|开、关、未 trust、非布尔、gate.yaml 缺失：一律 `EXIT_PASS`(0)；`--pass` 缺失或非法：argparse 的 2（`exit_codes.py:16`）|"关"不是错误，skill 不应因此停|
|gate.yaml 路径|`Path.cwd() / ".code-forge" / "gate.yaml"`，与 `review`（`cli.py:3800`）、`resolve-outlet`（`cli.py:5294`）一致；**不**用 `trust` 的 `resolve_workspace`（`cli.py:1966-1969`，会上溯到祖先目录）|保证 B 取到的开关就是同一目录下 `review` 会用的开关；子目录运行时两者同为"无 gate.yaml → 关"|
|只读|只调用 `_load_trusted_review_lenses` → `is_trusted_lenses`（同 `is_trusted_focus`，`trust.py:389-402`，不写 store）；不调用 `_load_gate_backends`、`is_trusted`（`trust.py:144-148` 会写）、`trust_status`（`trust.py:207-208` 会写）|T15 用只读 `XDG_CONFIG_HOME` 验证 store 的 mtime 与内容不变|

SKILL.md 侧（三个 pass 段与 C-host 段同一句）：退出码非 0 时，把 stderr 原样告诉用户，然后**不带镜头继续**该 pass；不 STOP（区别于 `SKILL.md:1585` 对 resolve-outlet 的 STOP）。

版本错位（已在 609aa51 实测）：
- 旧 CLI + 新 skill：`code-forge lenses --pass qodo` 被 `cli.py:2637-2639` 的兼容逻辑改写成 `review lenses --pass qodo`，argparse 报 `unrecognized arguments: --pass qodo`，退出 2，不会真跑 review。正因如此 `--pass` 必须是必填的具名参数。skill 按上面的回退处理。
- 新 CLI + 旧 skill：镜头永远不追加，等同关闭；`install-skill` 重装后生效。仓库没有 CHANGELOG（已核对），这条写进 59.1-01 SUMMARY、提交说明正文，并在 SKILL.md 回退句旁加一行（Q9）。

绕过：B/C-host 里模型可以不追加输出。块是常量、不含用户文本，绕过只等于"关"，接受并记录。

关于缓存前缀：A 里的镜头块对三个 pass 完全相同，放在 contract 之后不会打断共享前缀，差异仍在共享前缀内（`test_diff_body_is_inside_the_shared_prefix`）。各 pass 不同的只有角色句和选择行，二者都在尾部（P2-3）。

### 开关与 trust（N-1）

- gate.yaml 新增布尔键 `review_lenses`，默认 `false`。`gate.schema.json` 补描述，该 schema 本来就 `additionalProperties: true`。
- 现有 trust 只哈希 backends（`trust.py:1-8`、`:127-157`），`_load_gate_backends`（`cli.py:141-182`）在 backends 已 trust 时返回整份 dict。所以**不能**直接读这个 dict 里的 `review_lenses`。
- 照 `review_focus` 的做法单独授权：
  - `trust.py` 新增 `hash_lenses_flag(gate_data) -> str`：值不是 `True` 时返回 `""`，保证现有 trust 记录全部有效（同 `hash_focus_text`，`trust.py:373-386`）；
  - `record_trust`（`trust.py:159-176`）多存一个 `lens_hash`；
  - 新增 `is_trusted_lenses(gate_yaml_path, gate_data) -> bool`，口径同 `is_trusted_focus`（`trust.py:389-402`）；
  - `cli.py` 新增 `_load_trusted_review_lenses(gate_yaml_path, warn_fn) -> bool`，照 `_load_trusted_yaml_focus`（`cli.py:3218-3241`）实现：未 trust 时告警并返回 `False`；
  - A、C 和 `lenses` 子命令都只用这个函数的返回值：`return flag is True and is_trusted_lenses(...)`（R3-7）。
  - 值只认 `is True`：`"true"`、`1`、`yes` 等非布尔值按关闭处理，并告警 `gate.yaml review_lenses ignored: not a bool (got %s)`，同 `cli.py:3236-3240`（R3-7）；
  - `cli.py:2065-2071` 的 trust 守卫加 `has_lenses = gd.get("review_lenses") is True`，与 `has_backends`、`has_focus` 并列，报错文案改为 "No backends, review_focus or review_lenses configured…"。否则只写 `review_lenses: true` 的 gate.yaml 无法 trust（R3-1）；
  - `trust` 成功且 `review_lenses: true` 时多打一行：`review_lenses: true changes the baseline review prompts for this repo.`（R3-1）；
  - `trust --status`（`cli.py:2004-2030`）加两行：`Lens hash: <stored|(none)>` 与 `Review lenses: <状态>`，状态串互不相同（R4 P3-6）：`on`；`untrusted (run code-forge trust)`；`off (not set)`；`off (not a bool)`；`off (true was trusted before; enabling needs no re-trust)`；`off (re-trust after enabling)`。这两行由只读的 `lens_trust_state` 计算，不经 `trust_status` 的迁移写入（R3-1）。
- 后果：trust 之后把 `review_lenses` 改成 `true`，没有重新 `code-forge trust` 就仍然关闭（T16）。
- `hash_lenses_flag` 的固定串是字面量 `code-forge:review_lenses:true` 的摘要，**不**绑定 `LENS_BLOCK` 的 sha：块文本随版本变化不要求用户重新 trust，块本身由 sha 测试（T7）与评审约束（Q5）。
- 已知取舍（R3-7，Q5 接受）：`hash_lenses_flag` 只有 `""` 与固定串两个值。在 `true` 状态 trust 过一次以后，`true→false→true` 不需要再 trust，因为 store 里的 `lens_hash` 一直匹配。开关只在两个常量之间选，这个取舍可接受。
- 其他写 trust 的调用方：`setup_mcp.py:223-232`（写自己的 gate.yaml，不含镜头）、`eval/runner.py:1001` 都是自动 trust，会顺带存 `lens_hash`。eval 自动 trust 的是 harness 自己生成的 gate.yaml，可能含 `review_lenses: true`，威胁模型记为 Elevation/accept（R4 P3-5）；eval 的 LENS-03 臂由临时 gate.yaml 的 `review_lenses` 切换，并纳入 `_missing_arm_settings`（`eval/runner.py:138`）的完整性检查，缺失时按 `:1004-1011` 拒跑（R3-16，LENS-03 范围）。
- 开关只在两个常量之间选择，不引入用户文本。
- 开关状态写进 run 的元信息，供测量区分对照组（INV-07 口径）。回执 schema 不变，因为回执只存原始响应的 sha 和 shingles（`receipt.py:227-246`、`:449-453`）。
- 与 `review_focus` 互相独立：Review Focus 仍在原位置，不合并，也不互为回退。

### 归属（仅供参考）

- 镜头 finding 的描述以 `[lens:Lx] ` 开头。落库后是 `[pass] [lens:Lx] ...`（`reviewer_json.py:898`），由 `lens_of()` 解析。
- 前缀只用于统计：没有前缀、编号越界、镜头不属于该 pass，都不会让解析失败，不影响指纹（`reviewer_json.py:889`），不改变严重度。
- **门禁侧唯一改动（N-6）**：`_rule_type`（`machine.py:3592-3596`）取 `desc[:50]`，标签会占掉其中 10 个字符，使 `distinct_per_file`、`distinct_per_diff` 失真。实现时 `_rule_type` 先调用 `strip_lens_tag`。只剥镜头标签，`[pass] ` 前缀保留，因此关闭路径的结果逐字节不变（T14）。
- 第二个前缀消费者（R3-9）：`legacy.py:262` `rule_hint = sf.description[:16].replace(" ", "_")` 进 `legacy:%s:%d:%s` 的 finding_id。同样先 `strip_lens_tag`，否则开启时 16 个字符里有 10 个是标签。不带标签时结果不变（T14）。
- 不新增 JSON 字段，理由见修订说明 P2-1。

### JSON-only 与"不逼出 finding"

- 镜头块里没有任何要求输出的句子：没有覆盖说明，没有 "unproven:"，没有"点名安全依据"（N-3）。干净的镜头什么都不输出（规则 2）。
- L4 只报"在本仓库代码里能指出的具体破坏点，不是某个库怎么表现"（R3-4）。上游的依赖行为类条目已删，因为它们正是 `falsify_receipt.py:77-89` 会降为 UNCERTAIN 的库行为断言。
- 实测（R3-4，box 上对 609aa51 的真 falsifier）：评审给的三条 L4/L5 样例描述全部命中 `asserts_library_behaviour`，经 `RealFalsifier.falsify`（`falsify_real.py:228`，llm_invoke 打桩返回无回执的 CONFIRMED/DISMISSED）全部变成 UNCERTAIN（`:313-316`），进而 RESET（`machine.py:3490`）。同样三个缺陷按 v4 措辞改写成指向仓库代码 file:line 后 0/3 命中，判决原样保留。样例是人工构造，真实命中率由验收 4 在测量中统计。脚本：`draft/measure_falsifier.py`。
- 可选根字段 `lens_coverage` 本版不加（FU-4）。
- `_is_praise`（`reviewer_json.py:842-853`）是子串匹配。v3 删掉了 "unproven:" 句式，这条风险不再由镜头块引入；测量时仍统计被它丢弃的带标签 finding 数（验收 4）。

## GSD 准入（N-8）

本 spec 改的是基线评审提示词，必须先准入，不能直接实现（P1-1）。时序约束（已核对）：
- `docs/gsd/REQUIREMENTS.md:27`："The existing phase 59/60 sequence owns this scope"；
- `REQUIREMENTS.md:39`（EVAL60-02）："Freeze scripts, denominators and acceptance before results"；
- `ROADMAP.md:284`：Phase 59 ACTIVE，"this entry does not admit new implementation"；
- `PROJECT.md:31`："independently reviewed GSD plans precede new implementation"；
- 先例 Phase 24.1（`ROADMAP.md:665-669`）："Depends on: Phase 24 (decimal insert; does not renumber 25-27)"，"Requirements: None mapped (decimal insert)"。

准入步骤：
1. 在 `REQUIREMENTS.md` v3.0 段（:25-41）新增需求：
   - **LENS-01**：`lenses.py` 单一来源，A/C 字节一致，B 经 `lenses` 子命令取同一文本。开关关闭时 A/C 字节与现有 oracle 相同。
   - **LENS-02**：开关经 `lens_hash` 单独 trust，默认关；`_rule_type` 剥标签（N-1、N-6）。
   - **LENS-03**：开/关对照测量（59.1-02），在 **EVAL60-01 语料冻结**、59.1-02 PLAN 预注册并经独立评审之后启动，不等 EVAL60-02（第 5 轮 forge 批准改动启动条件）。自己冻结脚本、分母和验收（下文验收 3），不改动 EVAL60-02 已冻结的任何内容；若 EVAL60-02 先冻结了 matcher 或 runner，经显式修订复用。启动前与测量不达标时，开关都不得默认打开。
   - **LENS-04**：`src/code_forge/THIRD_PARTY_NOTICES.md` 进 package-data，`LENS_SOURCES` 多来源（N-9、N-10）。
2. ROADMAP 插入 **Phase 59.1: Review Lenses (INSERTED)**，写明 "Depends on: none for 59.1-01 (decimal insert; does not renumber 60); base 609aa51"。理由：开关默认关，关闭路径由 A、C 两个 oracle 锁定，不影响 Phase 59 验收。仍须 forge 明确批准这次 REQUIREMENTS/ROADMAP 修订（R4 P2-5）。
   冲突规则（与 59-06、59-08 都碰 `build_l1_provider` / `_make_subagent_spawn` 一带）：谁后合入，谁 rebase 后重采 A、C 两个 oracle（A 用 `capture_l1_digests.py` 加一条 revision，C 用 T1-C 脚本对新基线写新 revision），并重核本 spec 与 PLAN 的全部行号。这条规则由准入修订写进 ROADMAP 的 59.1 条目；59.1-01 PLAN 只在 Task -1 检查它已存在，不写 ROADMAP（R5 N-2）。
3. PLAN 正本在 `.planning/phases/59.1-review-lenses/59.1-01-PLAN.md`（`.planning/` 被 `.gitignore:28` 忽略，`docs/gsd/MANIFEST.json` 的 `source_relative_root` 为 `.planning/`）；发布副本在 `docs/gsd/phases/59.1-review-lenses/59.1-01-PLAN.md`，同时在 `MANIFEST.json` 加条目（sha、大小、transformations）、`coverage` 加 `phase59_1_plans: 1` 与 `public_files` +1，在 `docs/gsd/README.md` 的 Phase59 support（:89）与 Phase60（:103）之间加 "### Phase59.1 plans" 表（R4 P2-2、Q1）。PLAN 里的 context 链接按正本位置写（`../../PROJECT.md` 等，发布后在 `docs/gsd/` 下同样成立），spec 用仓库路径引用。内容为本 spec 的 LENS-01/02/04 与 T1–T18，并按 `PROJECT.md:31` 经独立评审后才实现。LENS-03 另起 59.1-02-PLAN，在 EVAL60-01 语料冻结后写并预注册。

4. 准入闸门（R5 N-2）：上面 1–2 步是 forge 另行批准的纯文档改动（REQUIREMENTS、ROADMAP、冲突规则、`.planning` 源文件、重新发布与 MANIFEST），不属于 59.1-01 PLAN，本轮也未起草。PLAN frontmatter 为 `autonomous: false`；Task -1 为 `checkpoint:human-verify`，在执行基线上只读已提交内容：先要求 `git diff --quiet HEAD -- docs/gsd/REQUIREMENTS.md docs/gsd/ROADMAP.md`，再用 `git show HEAD:` 读两份文件；REQUIREMENTS 中以 "- `[ ]` **LENS-0N**: " 开头的行，LENS-01..04 各须恰好一行（awk `index == 1` 计数），且该行不得含 "rejected"、"not admitted"、"declined"（不分大小写）；LENS-03 那一行本身须写 "after … EVAL60-01"，且同一分句内不得出现 "independent of / before / without / not after … EVAL60-01"。ROADMAP 中 "Phase 59.1" 标题须恰好一个，且整行等于 "### Phase 59.1: Review Lenses (INSERTED)"；59-06 与 59-08 须出现在该标题到下一个以 "##" 开头的行（任何 2 级及以上标题）或文件末尾之间（`grep -F`；"##" 指任何 2 级及以上标题，含 `####`，章节内的 `####` 子标题会提前截断章节，按失败处理，R8 附注 N4）。任何一项不满足就输出 STOP 并退出 1（R6 P3-2、R7 附注）。在 609aa51 的 /tmp 克隆上实测 22 种情形，全部符合预期（R7 附注；第 6 轮表中的"7 种"指下面作者给的 1–6 加 5b）。退出 1：(1) 基线；(2) 提交后的拒绝文字（"Rejected proposals: LENS-01 LENS-02 LENS-04; LENS-03 (blocked by EVAL60-01, not admitted)" 与 "Phase 59.1 declined; 59-06 and 59-08 unrelated"）；(3) LENS-03 写成 EVAL60-02；(4) 章节内缺冲突规则；(5) 正样例未提交；(5b) 正样例只暂存；A1 四行格式正确但写 REJECTED，章节写 "Declined. 59-06 and 59-08 unrelated."；A2 "Independent of EVAL60-01; runs before EVAL60-02"；A2b "before the EVAL60-01 freeze"；A3 缩进；A4 `[x]`；A5 标题带尾随空格；A6 59.1 是最后一个 phase，59-06/59-08 只在 `## Progress` 里；A6b 只在文件末尾附近；A7 另有伪行 "- note - `[ ]` **LENS-03**: after EVAL60-01"；A7b LENS-03 锚定行出现两次；A10 staged delete；A11 59.1 标题出现两次；A11b 另有 "## Phase 59.1" 变体。退出 0：(6) 正样例已提交；A6+ 59.1 是最后一个 phase 且规则在章节内；A8 提交后为 CRLF。第 8 轮在同一克隆上、闸门不变，再测 17 种：退出 1 的有 "NOT ADMITTED"、"DeClInEd"、"NOT AFTER EVAL60-01"、冲突规则前有 `####` 子标题、59-08 只在下一个 `###` 节、ROADMAP 以 CRLF 提交（按失败处理）、"after Phase 60.1 and EVAL60-01"（60.1 里的句点截断了分句，按失败处理）；退出 0 的有 "starts AFTER the frozen, independently reviewed EVAL60-01 corpus snapshot"、59.1 为最后一节且文件末尾无换行。剩余风险（已知能通过、退出 0，交给 forge 在 checkpoint 人工确认）：LENS 行写 "withdrawn"、"deferred indefinitely"、"not yet admitted"、"未准入（已拒绝）" 或 "superseded"；拒绝写在格式正确的 LENS 行下面的续行；LENS-03 行写 "after LENS-01 merges, regardless of EVAL60-01"；另有一个小写的重复标题 "### phase 59.1"（R8 附注 N3）。更一般地，措辞能过这些检查但意思不对，由 forge 在 checkpoint 人工确认，脚本不负责。闸门未过，包括 Task 1a/1b 在内一律不执行；Task 0 与 Task 10 在改 MANIFEST/README 之前各自重跑该检查。must_haves 里的 LENS-* 标为"拟议 ID，准入后才存在"。

时序：准入修订（forge 另行批准，不在本 PLAN 内）→ PLAN Task -1 闸门 → 独立评审 PLAN 并发布 → LENS-01/02/04（开关关闭，59.1-01）→ EVAL60-01 语料冻结 + 59.1-02 PLAN 预注册与独立评审 → LENS-03（59.1-02）。EVAL60-02 与 LENS-03 互不等待；LENS-03 不改 EVAL60-02 的冻结内容。

## 不变式

- `_PASS_NAMES` 仍为三个（`state.py:41`），每轮固定 3 个 pass（`verify.py:47` `PASSES_PER_CYCLE = 3`），earned_clean_window 逻辑不变（`machine.py:1639-1649`、`:1690-1700`）。
- finding 的必填键、指纹、回执 schema、state.json 结构都不变。（state 本可加法扩展，见 P2-1；本版刻意不扩展。）
- 开关关闭时，A/C 提示词与 `609aa51` 逐字节相同；B 的 pass 文件不变，`lenses` 子命令输出为空；`_rule_type` 对不带标签的描述结果不变。
- 镜头块是本地常量：不联网，不在运行时加载外部技能。
- 镜头块不含 `ROLE_NAMES` 中的任何子串，也不含要求输出的句式。
- Design Intent（`--contract`）不能压掉镜头 finding；豁免只走现有的 `## Do NOT Flag`（`cli.py:3018-3023`）。
- 开关关闭时：A 对照现有 `tests/fixtures/l1_prompt_digests_pre_p59.json`；C 对照 T1-C 在 609aa51 上新采的 `tests/fixtures/outlet_c_prompt_digests_pre_lens.json`（R3-2）。两者分开，C 是独立 fixture（Q4）。
- `cross_repo.py:430` 的 `build_l1_provider` 不接镜头：多仓联合 diff 没有单一 gate.yaml 的镜头 trust 上下文，且不走分组估算；有 T19 反例测试，理由写进 SUMMARY，后续见 FU-9（Q6）。
- 已知残留（R3-5）：L5 限定为 "an earlier check on the same path" 后，B 里与 pass3 撤回纪律（`p3:138-142`）的冲突缩小但未消除，转 FU-3；验收 f 统计。
- 人工或降级执行的轮次不进 state，不计入 clean cycles（见 FU-1）。

## 测试与缺陷注入

期望值一律在测试里硬编码，不从 `lenses.py` 推导，否则测试会变成同义反复（P3-5）。

|ID|断言|注入，须 FAIL|
|---|---|---|
|**T1-C** C 基线（R3-2，先于任何代码改动；R4 P2-1 拆为采集与检查）|采集（一次性）：在 `609aa51` 上用 `scripts/capture_outlet_c_digests.py --write`（新，仿 `capture_l1_digests.py`，`ROOT` 取 `Path(__file__).resolve().parent.parent` 同 `capture_l1_digests.py:21`，打桩 `code_forge.llm_invoke.llm_invoke`，调用 `_make_subagent_spawn`）写入 `tests/fixtures/outlet_c_prompt_digests_pre_lens.json`（`source_tree` 记 609aa51）；脚本自己检查并拒绝写（退出 1）：HEAD 不是 609aa51；`git diff --quiet 609aa51 -- src/ scripts/capture_l1_digests.py` 非零（含已暂存改动）；`git status --porcelain -- src scripts/capture_l1_digests.py` 非空（src 下有未跟踪文件）；目标已存在，包括悬空 symlink——写入用 `open(dest, "x")`（O_CREAT|O_EXCL），没有先查后写的窗口（R5 N-5）。执行期间不按任务提交，HEAD 一直是 609aa51，所以只查 HEAD 不够。写入工作区并记录 sha256，不提交。box 在 /tmp 克隆上实测 r3 草稿：未暂存改动、已暂存改动、未跟踪文件、悬空 symlink 四种情况都拒绝且不产生文件；干净时写出的 sha 为 `99f3f74d…aa43e`，`--check` 对只读副本通过且 mtime 不变。检查（常驻）：`tests/test_outlet_c_prompt_identity.py` 与 `--check` 只读比对，断言 `source_tree == 609aa51` 且摘要等于硬编码值，从不改写 fixture 或其 `source_tree`；后续任务与回滚演练只跑检查。box 草稿实测：C_full qodo `b9475fff…`、expert `1776b05b…`、adversarial `7a2b8ece…`（`draft/outlet_c_digests_609aa51.json`）|把 C 的默认值改成 `True`；把镜头块插进 C；手改 fixture 的 `source_tree`；对已存在的 fixture 再跑 `--write`（须拒绝且字节不变）；src 有一行改动（未暂存与已暂存各一次）、src 下放一个未跟踪文件、目标为悬空 symlink 时跑 `--write`（都须拒绝）|
|T1 关闭路径不变|A：`test_l1_prompt_byte_identity.py` 不改，现有 3 个 case 的摘要仍然相同；C：T1-C 的 oracle 仍然相同|把默认值改成 `True`|
|T2 剥离等价|开启时的提示词去掉 `LENS_BLOCK` 和选择行之后，sha256 等于关闭时的值。A 逐 case（A_two_rows、B_empty、C_exception_as_empty，这是 oracle case 名，与出口 C 无关），C 逐 T1-C 的 case，均逐 pass 检查|把镜头块插到 Post-Image 之后|
|T3 开启 oracle|fixture 新增 `*_lenses` 三个 case，并在 `revision` 中写明原因，旧 revision 原样存进 `previous`（保留 `previous` 链），`source_tree` 不变。`capture_l1_digests.py` 的 `main()`（`:89-96`，现为整文件覆盖写 `{source_tree, cases}`）改为合并模式：默认只检查；重算的关闭摘要与已存不同就退出 1，不覆盖；`--lenses` 只追加 `*_lenses` case。新测试 `tests/test_capture_l1_digests.py` 在临时副本上验证（R4 P2-3）。`*_lenses` 有消费方（R5 N-3）：`test_l1_prompt_byte_identity.py` 为每个 `*_lenses` case 新增 `test_case_<name>_lenses`，用 `review_lenses=True` 重建 A 提示词并与 fixture 比对；`main()` 的检查模式在已有 `*_lenses` 时也重算并比对，缺项、多项或不符都退出 1（box 草稿 `draft/capture_l1_merge_draft.py` 自测：改坏、缺失、多出一个 lens case 均失败，真实 fixture 未动）|改镜头块的一个字节；`main()` 丢掉 `previous`；`main()` 覆盖已有 case；把 fixture 里 `A_two_rows_lenses` 的一个摘要改一位（`test_case_a_two_rows_lenses` 与检查模式都须 FAIL）|
|T4 出口字节一致|A、C 的提示词与 `code-forge lenses --pass <p>` 的输出都包含同一个 `LENS_BLOCK`，三处选择行相同|让 `lenses` 子命令用另一份文本|
|T5 位置|A、C：`LENS_BLOCK` 紧接 `REVIEW_JSON_CONTRACT` 常量（R3-11）；两个出口的选择行都是最后一行|把选择行挪到差异块之前|
|T6 共享前缀|`TestSharedPromptPrefix`（`test_factories.py:1338-1413`）参数化为开、关两种：开启时三个 pass 的公共前缀包含 `LENS_BLOCK` 和差异，三个尾部互不相同|把选择行放进共享段|
|T7 角色串与强制句式禁入|`ROLE_NAMES` 中每个串都不是 `LENS_BLOCK` 的子串；`LENS_BLOCK` 不含 `unproven`、`name the one fact`、`Design Intent`、`intent`|把 "adversarial QE: assume bugs exist" 写进 L4；把 v2 规则 3 加回去|
|T8 归属集合|`PASS_LENSES == {"qodo": ("L1",), "expert": ("L2","L5"), "adversarial": ("L3","L4")}`，块中恰好出现 L1–L5 五个编号行。两处期望都硬编码|删掉 L4 行|
|T9 预算|`len(LENS_BLOCK.encode()) <= 1600`，并且 `max(len(selection_line(p))) <= 32`|镜头块加 30 个词|
|T10 估算器|`_estimate_l1_prompt_tokens` 增加 `lens_text` 项，开启时比关闭时多 `(1567+17)//4 = 396`（容差 ±1）。分组预算 `cli.py:4243-4254` 使用新的估算|估算器漏掉镜头块；`lens_text` 按位置传。`cli.py:4244-4252` 的调用全是位置传参，第 7 个是 focus `""`、第 8 个是 `context_sources_text`（签名 `cli.py:1180-1189`），所以 `lens_text` 定义为仅关键字参数（`*, lens_text: str = ""`），调用处按关键字传（R4 P3-7）|
|T11 解析器|`lens_of("[expert] [lens:L5] x") == ("expert","L5")`；无前缀、`L9`、双空格、缺空格、`Lens` 大写都返回 `None`，任何输入不抛异常；`lens_mismatch("[qodo] [lens:L2] x") is True`，`lens_mismatch("[expert] [lens:L5] x") is False`（N-12）|把正则改成匹配任意 `L\d`；让 `lens_mismatch` 恒返回 False|
|T12 降级负例|state 中只有 2 个 Forge cycle，外加一份人工报告文件：`earned_clean_window` 不满足，verify 失败，状态不变|让 verify 读取人工报告|
|T13 来源|`LENS_SOURCES` 的键集合为 `{"rules","L1","L2","L3","L4","L5"}`，每个键的 (skill, path) 集合按硬编码期望比对（L1 必含 `correctness-reviewer.md`），commit 与来源表一致；`src/code_forge/THIRD_PARTY_NOTICES.md` 存在、含三条版权声明，且 wheel 与 sdist 都含该文件。构建（R4 P2-4、Q2）：对 `git archive` 出的副本在 /tmp 下跑 `UV_CACHE_DIR=/tmp/… uv build`，不在检出目录构建，不向 `.venv` 装包；记录 uv 与解析到的 setuptools 版本（box 在 609aa51 预演：uv 0.13.5、setuptools 84.2.1，wheel 与 sdist 均成功，egg-info 只出现在 /tmp 副本）。检出目录里已有可编辑安装留下的 `src/code_review_forge.egg-info`（`.gitignore:6` 忽略），所以断言为构建前后其清单与 mtime 不变、其中每个文件的 sha256 不变；构建前 `touch` 一个标记文件，构建后 `find . -path ./.git -prune -o -path ./.venv -prune -o -newer 标记 -print` 必须为空（uv 先建 sdist 再在临时目录建 wheel，单靠 `test ! -e build` 太弱）；没有 `build/`、git status 只含本次文件。临时索引 `git add -A` 会往 `.git/objects` 写松散对象，无害，记入 evidence（N-9、N-10、R3-6、R5 P2-d、N-7）|删除 L1 的 correctness-reviewer 来源；从 package-data 去掉 notices|
|**T14** `_rule_type` 不受标签影响|同一组 P3 finding，描述分别为 `"[expert] [lens:L2] X"` 与 `"[expert] X"`，`_rule_type` 结果集合相同，`distinct_per_file`、`distinct_per_diff` 与 CYCLE_RESTART 判定相同；`"[expert] [lens:L2] [lens:L5] X"` 也剥成 `"[expert] X"`；`legacy.py:262` 的 `rule_hint` 对带标签与不带标签的描述相同；无标签描述的结果与 `609aa51` 相同（N-6、R3-9）|去掉 `_rule_type` 或 legacy 里的 `strip_lens_tag`；把正则改回只剥一个标签|
|**T15** B 每个 pass 的末行|`code-forge lenses --pass p` 的输出以 `selection_line(p)` 结尾，三个 pass 各自正确；开关无效时输出为空且退出 0，告警只在 stderr；`--pass` 缺失或非法退出 2。只读：`XDG_CONFIG_HOME` 指向只读目录，运行前后 trust store 的 mtime 与内容不变。路径：在含 `.code-forge/gate.yaml` 的仓库子目录运行时输出为空（与 `review` 同口径，`cli.py:3800`）。`SKILL.md:378-396` 三处与 `:1618-1625` 的追加说明各自带对应的 pass 名，并都含非零退出回退句（N-5、R3-3）|三个 pass 都输出 adversarial 的选择行；`lenses` 改调 `_load_gate_backends` 或 `trust_status`；删掉一处回退句；删掉回退句旁的版本错位行（Q9）|
|**T16** trust 之后改开关|对一份 `review_lenses: false` 的 gate.yaml 执行 trust，再改成 `true`：`_load_trusted_review_lenses` 返回 False 并告警，A/C 提示词等于关闭时，`lenses` 输出为空；重新 trust 后返回 True。旧 trust 记录（无 `lens_hash` 键）对 `false` 或缺省视为有效（N-1）。只含 `review_lenses: true`、没有 backends 的 gate.yaml：`code-forge trust` 退出 0 并打印基线提示词告警，之后开关有效（R3-1）。`trust --status` 打印 `Lens hash:` 与 `Review lenses: on/off/untrusted`，且不写 store。`review_lenses: "true"` 视为关闭并告警（R3-7）|`_load_trusted_review_lenses` 改成直接读 dict；`hash_lenses_flag` 对 `false` 返回非空；守卫不加 `has_lenses`；把 `is True` 改成真值判断；`hash_lenses_flag` 改成由 `LENS_BLOCK` 推导；`lens_trust_state` 改调 `trust_status`；status 把两种 off 合并成一个串|
|**T16b** 只读硬断言与 Q5（R4 P3-3、Q5）|构造一条会被 `trust_status` 迁移的旧 store 条目，只调 `lens_trust_state` 与 `is_trusted_lenses`，断言 trusted.json 字节与 mtime 不变；在 `true` 下 trust 过一次后 `true→false→true` 免重新 trust，status 显示 `off (true was trusted before; enabling needs no re-trust)`；monkeypatch `LENS_BLOCK` 后 `hash_lenses_flag` 不变；六个状态串逐一出现且互不相同|同上
|**T19** 接线（R4 P1、P3-4；R5 N-4 改为值级并加行为测试）|AST 值级，仿 `test_l1_prompt_byte_identity.py:150-177`（`inspect.getsource` 加 `ast.walk`）：`_dispatch_subagent` 有参数 `review_lenses`；它唯一的 `_make_subagent_spawn` 调用（`cli.py:3496`）传关键字 `review_lenses`，值必须是 `ast.Name(id="review_lenses")`；`_run` 里对 `review_lenses` 的每次赋值都是 `_load_trusted_review_lenses(...)` 调用；`_run` 里唯一的 `_dispatch_subagent` 调用（`cli.py:4057-4071`，关键字放在 `:4068` 的 `yaml_focus=yaml_focus` 旁）以及两个 `build_l1_provider`、一个 `build_grouped_l1_provider` 调用都传 `review_lenses=<Name review_lenses>`，字面量 True/False 不算；估算器调用带关键字 `lens_text`；反例：`cross_repo.py:430` **不**带 `review_lenses`（Q6）。box 草稿 `draft/t19_value_level_draft.py` 对 609aa51 源码做内存内合成接线，6 个变体（`_run` 传字面量 False、spawn 传字面量 True、两处各漏传、删参数、从非 trust 来源赋值）都被抓到。行为 B1：照 `tests/test_outlet_c_cli.py:320-451` 驱动 `_run`，patch `code_forge.cli._dispatch_subagent`，gate.yaml 写 `review_lenses: true`，`code_forge.trust.is_trusted_lenses` 打桩为 True 时断言 mock 被调用且 `call_args.kwargs["review_lenses"] is True`，为 False 时断言 `is False`（box 在 609aa51 的 /tmp 克隆上探测：该 harness 能走到 `:4057`）。行为 B2：照 `:264-318`，patch `code_forge.outlet_c.run_outlet_c`，调用 `_dispatch_subagent(..., review_lenses=True)`，取 `call_args.kwargs["spawn_fn"]`，在 `llm_invoke` 打桩下跑 `spawn("qodo", diff)`，断言提示词含 `LENS_BLOCK` 与 `selection_line("qodo")`；`review_lenses=False` 时两者都不含（box 探测：spawn_fn 可截获，提示词 1748 B）|`cli.py:4068` 不转发（T19、B1 FAIL）；`:4068` 传字面量 `False`（T19、B1 FAIL）；删掉 `_dispatch_subagent` 的参数（T19、B1、B2 FAIL）；`cli.py:3496` 不转发（T19、B2 FAIL）；`:3496` 传字面量 `True`（T19 FAIL，B2 关闭用例 FAIL）；漏一个 A 调用点；给 `cross_repo.py:430` 加上参数|
|**T17** contract 不压 P1|contract 文件写"本次有意删除 X 校验"（不含 `## Do NOT Flag`）。确定性部分：开启时的提示词中，镜头块里没有任何依据 intent 豁免的文字（同 T7）；用录制的响应回放，一条 P1 finding 经 `_json_to_state_findings` 落库不丢失、严重度不变。实时部分进验收 3 的固定注入用例（N-2）|在规则 2 加回 Design Intent 句|
|**T18** 库行为 finding 的管线效果（R3-4 重写）|(1) 录制响应回放：两条带 `[adversarial] [lens:L4] ` 前缀、引用库行为的描述（`json.loads returns …`、`requests.get raises …`）和一条 `[expert] [lens:L5] … the library returns None …`，经 `RealFalsifier.falsify`（`falsify_real.py:228`，打桩 `code_forge.falsify_real.llm_invoke` 返回无回执的 CONFIRMED）均得 UNCERTAIN；同一缺陷改写为指向仓库 file:line 的描述保持 CONFIRMED。这固定了"L4 措辞偏向库行为 → UNCERTAIN → RESET"的管线后果（`:313-316`、`machine.py:3490`）。(2) 干净 fixture：录制的三份空 findings 回放后 state 无新增，本轮计为 clean。(3) 仅限块自身措辞：`LENS_BLOCK` 每一行 `asserts_library_behaviour(line) is None`；这一项只防块文本本身像库断言，不代表模型输出（v3 把它当成主要证据，过弱）。实时部分进验收 3 的 e、f 与验收 4（N-3）|把打桩改成带回执；让 `check_receipt` 恒返回不降级；在 L4 加回 "pinned dependency behavior returns …"|

缺陷注入必须改真实定义（`lenses.py`、`factories.py`、`cli.py`、`trust.py`、`machine.py`），不另写假数据。T17、T18 的录制响应只验证管线，不验证模型；模型行为由验收 3 衡量。

## 验收（可证伪）

1. T1-C、T1–T18 全部通过，每条都有"注入后 FAIL、恢复后 PASS"的记录。
2. 开关关闭时，`git diff 609aa51 -- tests/fixtures/l1_prompt_digests_pre_p59.json` 只新增 `*_lenses` case 和 revision 记录，原有摘要不变；`tests/fixtures/outlet_c_prompt_digests_pre_lens.json` 的 `source_tree` 为 609aa51，且在 59.1-01 的首个任务中、任何 `src/` 改动之前生成（R3-2）。
3. 测量（LENS-03，59.1-02）：在 EVAL60-01 语料冻结、59.1-02 PLAN 预注册并经独立评审之后启动，不等 EVAL60-02，也不改 EVAL60-02 的冻结内容；若 EVAL60-02 先冻结了 matcher 或 runner，经显式修订复用。下列规则预注册在 59.1-01 PLAN 的 `<verification>`（Task 0 发布即冻结）；59.1-02 只能用一次写明日期、在看任何结果之前的显式修订来改动（Q7）；但"不可计算 ⇒ 不打开默认值"与"任一门槛不过 ⇒ 保持默认关"两条不在可修订范围内（R6 P3-3）。按 REVIEW-5 §3(b) 改为配对非劣效加裕量（R5 N-1）。满足全部 6 条才允许默认打开：
   - 语料与两臂：EVAL60-01 冻结语料，每个条目有 bug 侧（有缺陷）与 fix 侧（无缺陷）；两臂只差 `review_lenses`，同一后端、模型与 gate.yaml；只统计 A/C。
   - 重复：每个 (条目, 侧, 臂) 跑 R 次，R ∈ {3, 5, 7} 由下面的预试验确定并在任何开启臂 run 之前写进 59.1-02 PLAN，标号 1..R。后端不接受 seed，标号只用来配对，不是随机种子。一次 run = (条目, 侧, 标号, 臂)；同一 (条目, 侧, 标号) 的开/关两次 run 成对，交错执行，顺序用种子 591 打乱。
   - 前提与预试验（R6 P2-1、R7 P2-1、R8 P2-1 与 P3-2）：b 在两臂相同时的通过率取决于 λ（每个 bug 侧 run 在 falsify 之后上报的 P0–P2 finding 数均值，去掉 UNCERTAIN）和 q（fix 侧非空 run 的比例）。按下面的校准，q ≤ 0.2 时 R = 3 要 λ ≥ 2 联合通过率才 ≥ 0.90；q = 0.5 时 λ = 0.5 与 λ = 1 已达到（k = 2/0.7 分别为 0.946/0.947 与 0.946/0.949），但那里 b 没有牙齿（R = 3 时 b10 下 b 仍通过 0.766 与 0.468），所以预试验不会选它（R7 P3-1）。开启臂开跑之前，59.1-02 先做只跑关闭臂的预试验：用种子 591 抽 50 个语料条目（不足 50 则全取），两侧各 1 次，后端、模型 id、forge commit、gate.yaml 与正式两臂相同，沿用重跑规则。按条目聚类重采样 5,000 次（种子 591）：λ_lo 取 bug 侧每 run P0–P2 数的第 20 百分位；q_lo、q_hi 取 fix 侧非空比例的第 20 与第 80 百分位（单侧 80% 下界与上界；用 numpy.percentile 的默认线性插值）。然后用固定版本的 `sim_null_gates.py --pilot λ_lo q_lo q_hi N`（N = 语料条目数，2 ≤ N ≤ 1000）跑一次，环境为 numpy 2.5.4、scipy 1.18.1（下面所有数字都出自这两个版本）。规则对任何输入都有结论：脚本总是输出一条 JSON 判定（含输入、截断后的 q、N、每格次数、两个门限、numpy/scipy 版本与每一格结果），只有退出码为 0 且 `"computable": true` 时 b 才可计算，此时 R 取其 `chosen_R`，即同时满足以下两条的最小 R ∈ {3, 5, 7}：(1) 在 (λ_lo, q_lo) 下，k = 2 与 k = 0.7 两种离散度的无效应联合通过率都 ≥ 0.906；(2) 在 (λ_lo, q_hi) 下，b10（每条 finding 精确率 −0.10）时 b 的通过率对 k ∈ {2, 0.7} 取最大值后 ≤ 0.192。预试验每格 10,000 次，蒙特卡洛标准误在 0.90 附近为 0.0030、在 0.20 附近为 0.0040；两个门限都比目标 0.90、0.20 收紧 2 倍标准误，真实通过率恰在目标上的格子被判合格的概率约 0.023（种子敏感性：R = 3、λ = 2、q = 0.5、k = 2 的 b10 换 10 个种子为 0.195–0.204，标准差 0.0027）。无效应一侧用 q_lo，是因为 q 越小无效应通过率越低；牙齿一侧用 q_hi，是因为 q 越大 b 的牙齿越弱（R = 3、λ = 2 的 b10：q = 0.2 为 0.019，0.5 为 0.207，0.6 为 0.374），两项各取保守的一端。以下任一成立即判 b 不可计算、保持默认关：(i) 截断前 q_lo 不在 (0, 1)（比例为 0 或 1 说明不了下限在哪，模型的 Beta(2q, 2(1−q)) 在那里也没有定义）；q_lo 或 q_hi 落在 (0, 0.01) 或 (0.99, 1) 时截断到 [0.01, 0.99]，这只是防护（q = 0.01 一列与 q = 0.05 至多相差 0.061：R = 3、λ = 0.5 为 0.501 对 0.562；λ = 2 为 0.938 对 0.937）。设计取舍：q_lo = 0 也会挡住 fix 侧从不出 finding 的干净评审者；之所以接受，是因为 50 个条目得到的下界 0 分不清真实比例是 0 还是 0.03 左右，模型在 0 处也没有定义，代价只是在更大的预试验之前保持默认关（R8 附注 N5）；(ii) λ_lo < 0.5，即网格最小的 λ：再往下通过率不再单调，b 形同虚设（λ = 0.02、R = 3、q = 0.2 时无效应联合通过率 0.917，b10 下 b 仍通过 0.953）；(iii) 没有合格的 R（例如 λ = 0.5、q = 0.05 过不了无效应；下面的预试验结果里 q_hi = 0.6 时任何 R、λ 都过不了 b 的牙齿）；(iv) numpy 或 scipy 版本与固定值不同（退出 0，`"computable": false`）；(v) 脚本非零退出（2 为输入格式错误：参数个数不对、非数值或非有限值、q_hi < q_lo 或 q_hi > 1、N 不在 [2, 1000]；3 为模拟出错；其他非零同样处理，例如缺 scipy）。预试验的无效应部分只校准无效应通过率；各门槛的牙齿来自有害情形模拟（λ = 2、q = 0.2 的 harm 与 harm_R，以及 `--checks` 的 teeth_lowlam 与 tooth_qhi），这些都在 N = 150 下算出，预试验按自己的 N 重算 b 的牙齿，因为 b 的检出力随 λ 与 q 变化（R8 附注 N6）；a15 与 f 在所测低 λ 格子里与 λ = 2 的值至多相差 0.071（最大的是 R = 3、λ = 1、q = 0.5 的 f：0.095 对 0.024）。预试验的 run 不并入正式两臂。预试验只卡 59.1-02，不卡 59.1-01 合入（默认关）。
   - 重跑（预先声明）：只有基础设施失败（后端报错、超时、没有可解析的评审输出，由 runner 记录）可以重试，最多 2 次，沿用原标号；已产出评审结果的 run 不论结果如何都不重跑。全部尝试都记录并报告。重试后仍失败的 run 记为缺失，缺一边的配对从配对指标里剔除并计数；任一侧缺失配对超过 5%，结论为"不可计算"，保持默认关。
   - 匹配：`src/code_forge/eval/scorer.py:59` 的 `finding_hit`，版本取 59.1-02 PLAN 记录的 commit；不依赖尚不存在的 EVAL60-02 matcher。"已确认"指匹配且 falsifier 不是 DISMISSED。
   - 统计：单侧精确二项符号检验，α = 0.025；bootstrap 为按条目聚类的配对重采样 10,000 次、随机种子 591，每次重采样重算汇总比值。各门槛取合取（intersection-union），不做多重校正。
   - a. 某臂"检出"某条目 = 在 R 个 bug 侧重复中严格过半（3 中 2、5 中 3、7 中 4）有已确认的 P0/P1 匹配。lost = 关闭检出而开启未检出，gained 相反。门槛：lost 对 gained 的单侧符号检验（H1：lost > gained）p ≥ 0.025，且 T17 的 contract 注入条目在开启的 R 个重复中至少 1 个有已确认的 P0/P1 匹配。分母为全部 bug 侧条目；报告 lost、gained 与 p。不再要求 gained ≥ 1（非劣效；收益只报告）；
   - b. 精确率 = 匹配的 finding / 全部上报的 P0–P2 finding（falsify 之后、去掉 UNCERTAIN），两侧全部 run 汇总（micro）。门槛：开启 − 关闭 的配对 bootstrap 单侧 95% 下界（第 5 百分位）≥ −0.08；另报点估计与 2.5/97.5 百分位；
   - c. 每个 run 的 P3 = P3 总数 / run 数（两侧）。门槛：开启 ≤ max(1.20 × 关闭, 关闭 + 0.10)；带 `[lens:L2]` 的 P3 单独列出；
   - d. 输入 token = INV-07 记录的第 1 轮每次逻辑 L1 调用的提供方 input usage，按 (条目, 侧, 标号, pass) 配对。上限 = 1.25 × T_lens，T_lens 是后端报告的 `LENS_BLOCK` + 选择行的输入 token 数，由一次校准配对测得（同一第 1 轮提示词带块与不带块，三个 pass 取均值），在两臂开跑前记录；有 usage 但校准失败时上限取 600。门槛：配对差中位数 ≤ 上限；另报均值与最大值。缺 usage：任一臂超过 10% 的第 1 轮调用缺 usage，d 记为"不可计算"，按失败处理（保持默认关）。INV-07 的 usage 采集在 609aa51 上尚不存在（INV-07 未勾选，eval 不记录 usage），在它落地之前 d 一律"不可计算"：59.1-02 仍可运行并报告 a–c、e、f，`cli.py:1180` 的 chars//4 估算只作参考，但不能打开默认值；INV-07 落地后可单独补测 d（只跑第 1 轮，R = 1），不必重跑其他门槛，但后端、模型 id、forge commit 与语料 sha 必须与 a–c、e、f 的 run 相同并逐项记录，任一项不同 d 即不可计算（R6 P3-3）；
   - e. UNCERTAIN 每轮 = falsifier UNCERTAIN 判定总数 / 评审轮数（汇总）；RESET 每 run = RESET 转移数 / run 数。门槛：两项各自 开启 ≤ max(1.25 × 关闭, 关闭 + 下限)，下限分别为每轮 0.02、每 run 0.05（N-3）；
   - f. "非空 run" = falsify 之后至少 1 个上报的 P0–P2 finding（去掉 UNCERTAIN，与 b 的分母同一集合），只有 P3 的 run 算空（R6 P3-5）。每个 run 到达 3 个 clean cycle 的轮数，达不到的记为配置的最大轮数。门槛：开启中位数 ≤ 关闭中位数 + 1；fix 侧按条目聚类：每个条目的净值 = "关闭无 finding、开启有 finding"的重复数 − "开启无、关闭有"的重复数，净值 > 0 与 < 0 的条目数做单侧符号检验，p ≥ 0.025；fix 侧同一位置 L5 与 pass3 相反的 finding 对数为 0（硬门槛）（N-3、N-4、R3-5）。
   - 校准（box 模拟 `draft/sim_null_gates.py`，sha256 `28ad17a4…5ded`，结果 `draft/sim_null_gates_result.json`，补充检查 `draft/sim_null_gates_checks.json`；两臂参数完全相同；150 个条目；严格过半判检出；检出概率混合 40% Beta(1,49)、30% Beta(18,2)、30% U(0.2,0.8)；每条目 λ 服从形状 k 的 gamma 分布；每格 1,000 次；bootstrap 10,000 次、种子 591）。R = 3、λ = 2：P3 均值 {0.3, 1, 3} × q {0.05, 0.2, 0.5} 九格联合通过率 0.918–0.965，目标 ≥ 0.90。λ 网格（P3 均值 1，k = 2，依次为 q = 0.05 / 0.2 / 0.5；k = 0.7 至多相差 0.056）：R = 3 时 λ = 0.5 为 0.562/0.716/0.946，λ = 1 为 0.765/0.828/0.946，λ = 2 为 0.937/0.938/0.961，λ = 3 为 0.954/0.967/0.966；R = 5 时依次 0.701/0.886/0.962、0.910/0.947/0.967、0.963/0.959/0.965、0.963/0.961/0.965；R = 7 时依次 0.842/0.950/0.952、0.957/0.954/0.956、0.972/0.979/0.962、0.969/0.969/0.969。λ 低时不过的主要是 b。有害情形（R = 3、λ = 2、q = 0.2）：每条 finding 精确率 −0.10（汇总约 −0.086，对照 0.08 的裕量）时 b 通过 0.019（R = 5 为 0.018，R = 7 为 0.017）；−0.15（汇总 −0.128）时 0.000；P3 ×1.5 时 c 通过 0.000；UNCERTAIN ×1.5 时 e 通过 0.000；fix 侧非空率 +0.10 时 f 通过 0.024（R = 5、7 为 0.000）。a 的最小可检出效应：丢 10 / 15 / 30 个高检出条目时，R = 3 下 a 通过 0.774 / 0.528 / 0.035（REVIEW-6 独立复算 0.77 / 0.549 / 0.027），R = 5 下丢 15 / 30 为 0.397 / 0.005，R = 7 下为 0.298 / 0.000。即 R = 3 时 a 能稳定抓住 150 个里丢约 30 个，丢 15 个只有一半把握；更小的损失只报告，不设门槛（R6 P3-5）。补充检查（`--checks`，每格 1,000 次，R7）：q = 0.01、k = 2、λ = 0.5/1/2/3 时，R = 3 为 0.501/0.764/0.938/0.976，R = 5 为 0.722/0.914/0.975/0.978，R = 7 为 0.853/0.962/0.983/0.982（与 q = 0.05 至多相差 0.061，出现在 R = 3、λ = 0.5）。低 λ 的牙齿（受攻击门槛的通过率，依次 a15 / b10 / f）：R = 7、λ = 0.5、q = 0.2 为 0.307 / 0.193 / 0.000；R = 5、λ = 1、q = 0.05 为 0.394 / 0.018 / 0.000；R = 3、λ = 1、q = 0.5 为 0.535 / 0.468 / 0.095；R = 3、λ = 0.5、q = 0.5 为 0.547 / 0.766 / 0.082。虚设：λ = 0.02、R = 3、q = 0.2 时无效应 0.917，b10 下 b 仍通过 0.953。b 在 q_lo 以上的牙齿（`--checks` 的 tooth_qhi，每格 10,000 次，b10 下 b 的通过率，依次 k = 2 / 0.7）：R = 3、λ = 2 时 q = 0.5 为 0.207/0.213，0.6 为 0.374/0.389，0.7 为 0.659/0.662；R = 7、λ = 0.5 时 q = 0.2 为 0.209/0.213，0.3 为 0.474/0.484，0.4 为 0.801/0.790；R = 5、λ = 1 时 q = 0.05 为 0.018/0.016，0.1 为 0.028/0.030，0.2 为 0.074/0.073；R = 3、λ = 1、q = 0.35 为 0.194/0.195；R = 3、λ = 3 时 q = 0.3 为 0.030/0.028，0.6 为 0.216/0.220。12 格里有 8 格 k = 0.7 更差。种子敏感性（tooth_seed_spread，R = 3、λ = 2、q = 0.5、k = 2，种子 1100–1109）：0.195–0.204，均值 0.201，标准差 0.0027；若门限是 0.20，10 个种子里有 2 个会通过，门限 0.192 下一个都不通过。N = 150、每格 10,000 次的预试验结果（λ_lo, q_lo, q_hi → R）：(3, 0.2, 0.3) 3；(2, 0.2, 0.3) 3（b10 最大 0.057）；(2, 0.2, 0.2) 3；(1, 0.05, 0.1) 5（无效应 0.911/0.916）；(0.5, 0.2, 0.3) 无（只有 R = 7 过无效应，0.935/0.940，但 b10 为 0.485）；(0.5, 0.05, 0.1) 无（无效应最高 0.845）；(3, 0.5, 0.6) 无（b10 0.21–0.36）；(2, 0.5, 0.6) 无（b10 0.38–0.64）；(1, 0.5, 0.6) 无（b10 0.72–0.96）；(0.5, 0.5, 0.6) 无（b10 0.95–1.00）。十次都退出 0，并记录 numpy 2.5.4 / scipy 1.18.1。
   任一条不满足就保持默认关，并把结果写回本 spec。这本身也是合法的结论。
4. 测量报告附 `lens_of` 统计（只含 A/C，B 与 C-host 不在范围内）：带前缀的比例、`lens_mismatch` 比例、被 `_is_praise` 丢弃的带标签 finding 数、有缺陷语料上 L5 与 pass3 相反的 finding 对数（N-7）、`[lens:L4]`/`[lens:L5]` finding 中 `asserts_library_behaviour` 命中的比例及其中降为 UNCERTAIN 的条数（R3-4）、严重度 ≥P2 的 `[lens:L2]` finding 数（违反 "P3 at most"，R3-15）。只作参考，不设门槛。

## 后续项（本次不做）

- **FU-1 降级契约**：Forge 不可用时，人工评审只能产出 UNVERIFIED 报告：
  - 不写入 state，不计入 clean cycles；
  - Forge 恢复后从零重跑完整三轮；
  - 报告固定以 `UNVERIFIED — not counted toward clean cycles; full Forge 3-cycle rerun required before commit` 结尾；
  - 理由：这正是 `evidence/fabrication-specimens.md:9,17,41` 记录过的伪造路径。
- **FU-2 打包**：不复制五个 SKILL.md，也不把它们加入 `install-skill`（`cli.py:5122`）。原因：复制会丢掉 `disable-model-invocation`；`--force` 会覆盖用户已装的同名技能；ce-code-review 的 Stage 3d 会把代码发出本机。如有需要，在 README 里链接上游即可。
- **FU-3 A/C 与 B 维度对齐**：`p2`/`p3` 和 `references/*.md` 的检查维度没有进入 A/C 提示词。这是比本 spec 大得多的现有缺口，需要单独立项和测量。差距表里的 6 条 B-only 条目在本 spec 中**有意不处理**，由 FU-3 承接。另承接 R3-5 的残留：B 里 L5 与 `p3:138-142` 撤回纪律的口径差异。
- **FU-4 `lens_coverage`**：只有测量表明需要覆盖证据时才加，并且要定义消费方。
- **FU-5 文件长度确定性检查**：把 CQ-2 移进 Step 0，之后可从 L2 删掉对应短语。
- **FU-7 MCP 对等**（R3-14）：本版不提供 `forge_lenses`；若加，照 `forge_resolve_outlet`（`mcp_server.py:1449-1455`，`readOnlyHint=True`）并更新 `tests/test_mcp_server.py:75-85`。
- **FU-8 构建依赖下限**（R3-6，现有缺陷，单独一个小 MR，Q8；建议在 59.1-01 之前或同时合入，59.1-01 不改 `:5`）：`pyproject.toml:14` 的 `license = "Apache-2.0"`（PEP 639）需要 setuptools ≥77，而 `:5` 写的是 `setuptools>=68.0`；68.0.0、76.1.0 构建失败，77.0.3 通过（评审在 /tmp/r3pkg 实测）。修正为 `>=77` 与本 spec 无关，单独提交。
- **FU-9 跨仓镜头**（v5 新增，Q6）：`cross_repo.py:430` 若要接镜头，须先设计多仓下的 trust 口径（哪个 gate.yaml 说了算）和估算，再单独立项。
- **FU-6 contract 的 trust**（第 3 版新增，现有问题）：`--contract` 与 `## Do NOT Flag` 都是未经 trust 的自由文本（`cli.py:2956`、`:3018-3023`）。本 spec 不再叠加新的豁免；现有豁免是否要 trust，另行评估。

## 风险与回滚

|风险|影响|应对|
|---|---|---|
|提示词变长|每次调用约多 396 token（1580–1584 B）；在 oracle case A 上（A 出口三个 pass 实测 2677–2698 B）占比约 +59%，真实 diff 下占比小得多|T9 设上限；测量 d 项设门槛；第二轮起镜头块可被前缀缓存命中|
|P3 噪声上升，L2 主观意见|评审成本上升，密度门禁触发 restart|L2 限定为可指名的删除并封顶 P3；规则 2；验收 c、f|
|UNCERTAIN/RESET 放大|clean cycle 凑不满|删掉逼出 finding 的句子与库行为条目；L4 限定为"本仓库代码，不是库行为"；验收 e、f 与验收 4 的命中比例；T18|
|L5 与 pass3 撤回纪律振荡|来回 finding，阻碍收敛|L5 限定为 "an earlier check on the same path"；验收 f 要求无缺陷语料上相反对为 0；B 的残留转 FU-3|
|Design Intent 注入压 finding|漏报 P1|镜头块不承认 intent 豁免；T17；验收 a|
|模型套用镜头标签|归属失真|归属只作参考；`_rule_type` 剥标签（T14）|
|开关绕过 trust|未授权改变基线提示词|`lens_hash`；T16；`lenses` 子命令只读（T15）|
|CLI 与 skill 版本错位|B 拿不到镜头或误停|非零退出回退句；`--pass` 必填，旧 CLI 退出 2 而不是跑 review（实测）；T15|
|镜头文本漂移|三个出口不一致|单一来源加 T4|
|上游技能更新|`LENS_SOURCES` 过期|commit 固定；更新时重做差距表|

回滚：开关保持关闭即可回到 `609aa51` 的提示词：A 由现有 oracle、C 由 T1-C 新 oracle 保证（T1）；`_rule_type` 对无标签描述的行为不变（T14）。如需彻底移除，删除 `lenses.py`、`lenses` 子命令、`trust.py` 的 `lens_hash` 相关函数、新增的 fixture case（revision 退回 `previous`）和参数（含 `_dispatch_subagent` 的）。C oracle、其测试和 `capture_l1_digests.py` 的合并模式保留，它们独立守护基线。

## 文献

外部事实只保留已联网核对的部分：上游两个仓库在来源表所列提交存在；六个上游文件与四份 LICENSE 逐字节一致（GitHub API 或 raw，2026-10-23）；许可证为 MIT；`cursor/plugins` 根目录没有 LICENSE。v1 表中的 stars 数、arXiv 与 DOI 摘要都没有核对，已删除，不作依据（P3-3、N-14）。

## 已决问题（第 4 轮，REVIEW-4 §3 Q1–Q9）

1. 正本与发布：PLAN、SUMMARY、evidence 在 `.planning/phases/59.1-review-lenses/`；PLAN 与 SUMMARY 另行发布到 `docs/gsd/phases/59.1-review-lenses/`，加 MANIFEST 条目、coverage 与 README 索引（见 GSD 准入第 3 步）。
2. T13 构建：接受隔离 uv 构建，限于 `git archive` 的 /tmp 副本、`UV_CACHE_DIR` 在 /tmp，记录 uv 与 setuptools 版本；可选 `--offline` 复跑。
3. 准入：单独准入，小数插入（先例 Phase 24.1），`depends_on: []`，base 609aa51，登记与 59-06、59-08 的冲突规则；仍须 forge 批准。第 5 轮：准入修订是另行批准的纯文档改动，未起草；PLAN 以 Task -1 闸门机检。第 6 轮：闸门只读已提交内容，按行格式与标题整行匹配（R6 P3-2）。第 7 轮：LENS 行各恰好一行且不含拒绝字样，LENS-03 在锚定行上查 "after … EVAL60-01"，59.1 标题恰好一个，章节止于下一个 `##`/`###`；22 种情形实测。第 8 轮：写明 "##" 指任何 2 级及以上标题（含 `####`，按失败处理），已知能通过的语义绕过列为剩余风险，另测 17 种情形；闸门本身未改。准入修订仍未起草。
4. C 基线：单独 fixture `outlet_c_prompt_digests_pre_lens.json`，不并入 pre_p59。
5. `true→false→true` 免重新 trust：接受；status 让该状态可见；`hash_lenses_flag` 不绑定 `LENS_BLOCK` 的 sha。
6. `cross_repo.py:430`：保持不接；T19 反例 AST 测试，SUMMARY 写理由，FU-9。
7. 门槛 a–f：连同分母、单位与统计方法冻结在 59.1-01 PLAN；只能经 59.1-02 的显式修订改动。第 5 轮按 REVIEW-5 §3(b) 重写为非劣效加裕量，见验收 3。第 6 轮：R 由预注册的关闭臂预试验确定；"不可计算 ⇒ 不打开"与"任一不过 ⇒ 保持关"不可修订。第 7 轮：预试验规则对任何输入都有结论，并在预试验内复测 b 的牙齿。第 8 轮：牙齿改在 (λ_lo, q_hi) 下按 k 取最坏值判定，门限留 2 倍蒙特卡洛标准误，numpy/scipy 版本固定。
8. FU-8：单独的小 MR，不进本 slice；建议先于或同时于 59.1-01 合入。
9. 无 CHANGELOG：版本错位说明写进 SUMMARY、提交说明正文，并在 SKILL.md 回退句旁加一行。第 5 轮：PLAN hygiene 给出提交说明正文的逐字模板（以 "Version skew:" 开头），Task 9 在提 MR 前 grep 该行（P3-1）。第 6 轮：Task 9 把提交说明写到 `.planning/phases/59.1-review-lenses/execution/59.1-01/COMMIT_MSG.txt`，verify 用 `grep -qF` 匹配整句。第 7 轮：提交后、提 MR 前对 `git log -1 --format=%B` 跑同一 grep，并要求 `test -z "$(git status --porcelain)"`。第 8 轮：这一步成为 PLAN 中编号的 Task 11（签名提交之后、提 MR 之前，由 forge 提交），有自动 verify，并检查该提交未改准入文档。

## 修订说明

|评审项|处理（v2；第 2 轮条目为 v3）|是否偏离评审建议|
|---|---|---|
|P0-1 降级轮计入三轮|删除。降级轮一律 UNVERIFIED，不写 state，不计入 clean cycles，Forge 从零重跑。契约移到 FU-1；不变式和 T12 负例留在本版|否|
|P0-2 只改 Outlet A|单一 `lenses.py` 供 A/B/C 三个出口使用，T4 保证字节一致，T5 检查位置|否|
|P1-1 无 GSD、无测量|新增 GSD 准入（LENS-01..04，Phase 59.1 INSERTED）、默认关的开关、验收 3 中的开/关测量 a–d|否|
|P1-2 覆盖说明与 JSON-only 冲突|删除覆盖说明，干净的镜头不输出；`lens_coverage` 暂不加（FU-4）|否；可选字段决定暂缓|
|P1-3 镜头与来源语义不符|按差距表重写：L3 去掉安全，L4 去掉"谁调用"，L5 换成 deslop 的真实内容|否|
|P1-4 oracle 与测试|T1 关闭路径不改 fixture；T2 剥离等价；T3 新增 case 并原位记录 revision；T6 参数化 TestSharedPromptPrefix；T7 角色串禁入；T10 估算器|否。补充一点：默认关意味着原有摘要无需重算|
|P2-1 `[lens:Lx]` 约定|采用评审方案 (b)：正则加 `lens_of()` 解析落库后的描述，只作参考|**部分偏离**：评审给的方案 (a) 指向 `reviewer_json.py:562`，那是 `_normalize_root_finding`（533-563），每次验证都会调用（`validate_reviewer_json` 650，在 668 调用）但很少命中，不是主路径；主路径是 `_json_to_state_findings`（856-917）。**理由已按第 2 轮更正**：state 可以加法扩展（`state.py:308` "additive only, no schema_version bump per D2"，`_finding_from_dict` `state.py:651` 起用 `.get`），本版刻意不扩展，以免 receipt/state 改动面扩大。v2 中"会冲突"的说法删除|
|P2-2 1200 B 预算|v2 实测 1593 B；v3 实测 1499 B，v4 实测 1567 B，上限仍为 ≤1600 B（T9），并计入估算器|否|
|P2-3 布局矛盾|镜头块紧接 contract，作为静态前缀；每个 pass 的选择行放在尾部|否；选择行是补充，因为块是静态的，必须在尾部指明本 pass 用哪些镜头|
|P2-4 归属错位|qodo=L1，expert=L2+L5，adversarial=L3+L4；L4 改名 "Impact beyond the diff"|否|
|P2-5 许可与打包|不复制 SKILL.md；新增 `LENS_SOURCES`（含 source_commit）和 `THIRD_PARTY_NOTICES.md`；打包移到 FU-2|否|
|P3-1 行号|改为 3177-3193 和 3218-3237|否|
|P3-2 PR #1 无法核实|删除该段背景|否|
|P3-3 文献未核实|删除未核实的条目|否|
|P3-4 "可读"/"收敛"不可证伪|改为验收 1–4|否|
|P3-5 删除 L4 的注入是同义反复|T8 硬编码期望集合|否|
|P3-6 qodo-review 是内部技能|来源表注明，外部只有五项|否|
|P3-7 canary 独立|现状表注明；召回只用 EVAL60 语料衡量|否|
|P3-8 改动面低估|三出口表、T10 估算器、`SKILL.md:378-396,1618-1625`、schema、fixture 全部列出；v3 再加 `trust.py`、`machine.py:3592-3596`、`lenses` 子命令、package-data（`passes/lenses.md` 已取消）|否|
|P3-9 范围蔓延|降级契约和打包分别拆到 FU-1、FU-2|否|
|Q1 预算|≤1600 B 断言，v3 实测 1499 B，v4 实测 1567 B|否|
|Q2 是否复制|不复制；用 `LENS_SOURCES` 加 notices|否|
|Q3 降级模板|FU-1 采用评审给的固定结尾句|否|
|用户补充 1：查漏补缺|新增差距分析；v2 为 49 条中 24 条进入镜头块，v3 为 57 条中 22 条|—|
|用户补充 2：来源|新增来源表；blob 与上游逐字节核对|—|
|**第 2 轮**|||
|N-1 开关不在 trust 内，B 无 trust|`trust.py` 加 `hash_lenses_flag`/`is_trusted_lenses`，`record_trust` 存 `lens_hash`，`cli.py` 加 `_load_trusted_review_lenses`，全部照 `review_focus` 的做法（`trust.py:373-402`、`cli.py:3218-3241`）。B 不读 gate.yaml，只取 `code-forge lenses --pass <p>` 的输出；未 trust 时输出为空。T16|否|
|N-2 Design Intent 压掉 breakage|删掉规则 2 的 Design Intent 句。豁免只走现有 `## Do NOT Flag`（`cli.py:3018-3023`），确认偏差指令（`cli.py:3012-3016`）保持不变。T7 禁入 `intent`；T17 加验收 a 的注入用例。contract 本身未经 trust，记为 FU-6|否|
|N-3 逼出 finding，放大 UNCERTAIN/RESET|删掉规则 3（"settle it … or drop it"、"unproven:"）、L4 的 "name the one fact" 和 "pinned dependency behavior"；L4 改为 "report only a concrete break you can point to"。T18（录制响应加逐行 `asserts_library_behaviour` 检查）；验收 e、f|否|
|N-4 L2 无法证伪|L2 改为 "P3 at most: name the exact branch, layer or wrapper to delete and show the replacement, not a preferred rewrite"（CE-17，`ce SKILL.md:9`）。上游的 "presumptive blocker" 严重度不采纳（差距表 CQ-1 行）。验收 c 单列 L2 的 P3，f 看收敛轮数|否|
|N-5 B 出口的末行|取消 `passes/lenses.md`。`lenses` 子命令的输出以本 pass 的选择行结尾，追加在该 pass 指令末尾。T15；验收 4 不含 B|**部分偏离**：评审建议在 lenses.md 里加逐 pass 的末行说明，本稿改为 CLI 输出，同时解决 N-1 的 trust 问题和打包问题|
|N-6 前缀影响 P3 门禁|`_rule_type` 先调用 `strip_lens_tag`，只剥 `[lens:Lx] `。这是门禁侧唯一改动，关闭路径结果不变。T14|否|
|N-7 与 pass3 撤回纪律冲突|规则 3 删除；L5 限定为 "states the types or an earlier check already rule out"；L1 限定为 "unrecoverable error"|**部分偏离**：L5 与 pass3 的相反对放在验收 4 统计，没有写成确定性测试，因为这是模型行为，录制响应证明不了|
|N-8 GSD 时序|Phase 59.1 写明 "Depends on: Phase 59"，实现等 59 关闭或单独准入；新增 `59.1-01-PLAN.md` 并经独立评审（`PROJECT.md:31`）；LENS-03 作为 EVAL60-02 冻结后的附加臂，自己冻结脚本和分母|否|
|N-9 NOTICES 不进 wheel|NOTICES 移到 `src/code_forge/THIRD_PARTY_NOTICES.md`，并加入 `pyproject.toml:73` 的 package-data；T13 检查 wheel 内容|**部分偏离**：评审提到 license-files；本稿用 package-data，理由是和 skills 的打包方式一致。（v4 更正：v3 写的"现有 setuptools>=68 就能用"不成立，基线本身就需要 ≥77，见 FU-8、R3-6）|
|N-10 来源归属不完整|`LENS_SOURCES` 改为多来源，新增 `rules` 键；L1 补 `correctness-reviewer.md`（blob dc80945，已和上游 raw 逐字节比对）；T13 硬编码来源集合|否|
|N-11 漏看 B 的 references|差距表逐条补上 `references/*.md` 的引用；新增 BR-8..10、CQ-12..14、CE-17..18；6 条 B-only 明确写为有意不处理，交给 FU-3|否；评审数为 5 条，TR-8 改判后为 6 条（DS-1、DS-4、TR-8、CQ-6、CQ-10、CE-13）|
|N-12 `lens_of` 错配与类型|新增 `lens_mismatch()`；`LENS_SOURCES`、`lens_of` 补类型注解；T11|否|
|N-13 L3 漏排除条款|L3 末尾加 "not new alternatives or ordinary dependency adds"|否|
|N-14 文献自相矛盾|文献一节统一为"已联网核对"，列出核对内容；`plugins/` 副本问题已关闭|否|
|N-15 `_is_praise` 子串匹配|规则 3 已删，风险不再由镜头块引入；验收 4 统计被丢弃的带标签 finding|否|
|N-16 风险数字|改为约 379 token、约 +56%（case A）；v4 为约 396 token、约 +59%|否|
|P2-1 理由更正|见上面 P2-1 行|否|
|FU-4 推迟 `lens_coverage`|保留推迟；造成冲突的两句（L4、规则 3）已删，见 N-3|否|
|第 2 轮"七"：B-only 写明|差距表加 B-only 决定段落，FU-3 写明有意不处理；未决问题 3 关闭|否|

### 第 3 轮（REVIEW-3，GO with conditions；无 P0/P1）

|评审项|处理|是否偏离|
|---|---|---|
|R3-1 只开镜头无法 trust|`cli.py:2065-2071` 守卫加 `has_lenses`（`is True`），文案同步；trust 时打印基线提示词告警；`trust --status` 加 `Lens hash`、`Review lenses` 两行，只读计算；T16 加只含 `review_lenses: true` 的用例|否|
|R3-2 C 没有 oracle|新增 T1-C：先于任何代码改动，在 609aa51 上采集 C 摘要到 `tests/fixtures/outlet_c_prompt_digests_pre_lens.json`；不变式与回滚改为"A 对现有 oracle、C 对新 oracle"；box 草稿已实测出摘要|否|
|R3-3 `lenses` 契约缺失|新增契约表（stdout/stderr/退出码/路径/只读）；SKILL.md 非零退出回退句（告知用户，不带镜头继续）；路径与 `review` 一致（`cli.py:3800`）；版本错位实测（旧 CLI 退出 2）；T15 加只读、子目录、回退句三项|否|
|R3-4 T18 空转、L4 偏库行为|L4 加 "in this repo's code, not in how a library behaves"；T18 改为经 `RealFalsifier.falsify` 的管线测试，逐行检查降为附属；验收 4 加命中比例；真 falsifier 实测：评审三例 3/3 命中并降为 UNCERTAIN，按 v4 改写后 0/3|否|
|R3-5 L5 振荡|L5 改为 "an earlier check on the same path"；B 残留转 FU-3；验收 f 加"无缺陷语料相反对为 0"|否|
|R3-6 package-data 理由错|理由改为"与 skills 打包一致"；setuptools ≥77 列为现有缺陷 FU-8；T13 用 `python -m build --no-isolation` 并检查 wheel 与 sdist|否|
|R3-7 非布尔值|只认 `is True`，非布尔告警；`return flag is True and is_trusted_lenses(...)`；写明 true↔false 免重新 trust 的取舍（未决问题 3）|否|
|R3-8 B 中途改开关|每 run 开始时每 pass 调用一次并缓存；run 元信息逐 pass 记录|否|
|R3-9 重复标签、legacy 前缀|正则改为 `(?:\[lens:L[1-5]\] )+`；`legacy.py:262` 也先剥；T14 加两例|否|
|R3-10 TR-6 依据|改为"已覆盖（逐条豁免，更保守）"，不再引 `cli.py:3012-3016`|否|
|R3-11 contract 歧义|出口表与 T5 明写 `REVIEW_JSON_CONTRACT` 常量|否|
|R3-12 C-host 误归 B|出口表新增 C-host 行（`SKILL.md:1609`、`:1618-1625`）|否|
|R3-13 代码块首行|镜头块代码块以空行开头，与文件逐字节一致|否|
|R3-14 MCP|写明本版不提供 `forge_lenses`，转 FU-7|否|
|R3-15 L2 超 P3|验收 4 统计 ≥P2 的 `[lens:L2]`|否|
|R3-16 eval 臂|LENS-03 臂由临时 gate.yaml 的 `review_lenses` 切换，纳入 `_missing_arm_settings`（`eval/runner.py:138`）|否；属 59.1-02 范围|
|四(d) 措辞|N-11 行改为"TR-8 改判后为 6 条"|否|

### 第 4 轮（REVIEW-4，GO with conditions）

|评审项|处理|是否偏离|
|---|---|---|
|P1-1 C 开关漏了 `_dispatch_subagent`|出口表写明 `_run`（`cli.py:3720`）→ `_dispatch_subagent`（`cli.py:3449-3462`，加参数）→ `_make_subagent_spawn`（`cli.py:3496`），在 `cli.py:4068` 转发；PLAN 的 "run_review" 改为 `_run`；新增 T19 AST 测试与"`:4068` 不转发""`:3496` 不转发"两个注入|否|
|P2-1 Task 1 会重写 oracle|T1-C 拆成一次性采集（`--write`，已存在或 HEAD≠609aa51 时拒绝）与常驻只读检查（`--check` 与测试，断言 `source_tree`）；"commit-to-worktree" 改为"写入工作区并记录 sha256，不提交"；box 草稿已实测拒绝重写|否|
|P2-2 GSD 落点|正本 `.planning/phases/59.1-review-lenses/`，发布到 `docs/gsd/phases/59.1-review-lenses/`，MANIFEST 条目与 coverage、README 索引；PLAN 加 Task 0 与 Task 10；链接按正本位置修正|否|
|P2-3 capture 脚本抹掉 revision 链|`capture_l1_digests.py` 的 `main()`（`:89-96`）改合并模式，保留 `source_tree` 与 `previous` 链；新测试 `tests/test_capture_l1_digests.py`；box 草稿 `draft/capture_l1_merge_draft.py` 对真实 fixture 自测通过，fixture 未改|否|
|P2-4 构建联网、产生 egg-info|`git archive` 副本在 /tmp 构建，`UV_CACHE_DIR` 在 /tmp，记录版本（box 预演 uv 0.13.5、setuptools 84.2.1）。偏离：检出目录本来就有可编辑安装留下的 `src/code_review_forge.egg-info`（2026-10-23 07:58 EDT），"src 下不出现 egg-info"无法成立，改为构建前后其清单与 mtime 不变|部分：断言口径改为"不变"|
|P2-5 依赖 Phase 59 等于无限期阻塞|`depends_on: []`，小数插入（`ROADMAP.md:665-669`），base 609aa51，冲突规则写进 ROADMAP；仍须 forge 批准|否|
|P3-1 无 CHANGELOG|改为 SUMMARY、提交说明与 SKILL.md 一行|否|
|P3-2 `ROOT` 硬编码|改为 `Path(__file__).resolve().parent.parent`，`_git_head()` 在 ROOT 下运行|否|
|P3-3 只读证明用了 "or"|T16b 硬断言：需迁移的旧条目，只调新函数，比字节与 mtime|否|
|P3-4 cross_repo 不接要可测|T19 反例；SUMMARY 写理由；FU-9|否|
|P3-5 eval 自动 trust|威胁模型加一行（Elevation，accept，harness 自有 gate.yaml，`eval/runner.py:1001`）|否|
|P3-6 status 串|六个互不相同的状态串|否|
|P3-7 估算器位置参数|`lens_text` 一律按关键字传；T10 注入"按位置传"；T19 断言关键字|否|

### 第 5 轮（REVIEW-5，GO with conditions C-1..C-4）

|评审项|处理|是否偏离|
|---|---|---|
|N-1 门槛 a–f 在两臂相同时几乎必挂（C-1）|验收 3 与 PLAN `<verification>` 重写：a 改符号检验（α=0.025）加 T17 至少 1/3 命中；b 按 bootstrap 单侧 95% 下界 ≥ −0.08；c 加下限 +0.10；d 上限 = 1.25 × 实测 T_lens（无校准时 600），缺 usage >10% 即不可计算，INV-07 落地前 d 一律不可计算、不能打开默认值、之后可单独补测；e 用 1.25 倍加下限；f 中位数 +1、fix 侧按条目符号检验；R=3 标号、重跑规则、缺失 >5% 不可计算；matcher 绑定 `src/code_forge/eval/scorer.py:59 finding_hit`。box 模拟两臂相同时联合通过率 0.918–0.965（目标 ≥0.90），v5 规则 0.000|部分：b 的裕量取 0.08、按单侧 95% 下界判。首轮试算（2.5 百分位、δ=0.05、α=0.05）两臂相同时联合通过率仅 0.53–0.69，b 的区间半宽约 0.045，150 个条目分辨不了更小的精确率下降；a 在丢 10% 高检出条目时仍有 0.482 通过，如实报告|
|LENS-03 启动条件（§3(c)，forge 批准）|改为 EVAL60-01 语料冻结 + 59.1-02 PLAN 预注册并独立评审，不等 EVAL60-02；不改 EVAL60-02 冻结内容，先冻结的 matcher/runner 经修订复用。REQUIREMENTS 里 LENS-03 的措辞属于准入修订，仍待另行批准|否|
|N-2 准入只是一句文字（C-2）|PLAN `autonomous: false`；新增 Task -1 `checkpoint:human-verify`，grep REQUIREMENTS/ROADMAP，缺失即 STOP（609aa51 上实测退出 1）；Task 0、Task 10 重跑；`:96` 的 ROADMAP 编辑改为存在性检查；LENS-* 标为拟议 ID。准入修订本身未起草、未修改|否|
|N-3 `*_lenses` 无消费方（C-3）|T3 加 `test_case_<name>_lenses` 与检查模式重算比对；合并草稿自测：改坏、缺失、多出均失败|否|
|N-4 T19 只查关键字（C-4）|T19 改值级；加 B1（`_run`）、B2（spawn 提示词）；点名注入须让 B1/B2 也 FAIL；box 上 6 个变体全被抓到，B1/B2 的 harness 在 609aa51 克隆上可走通|否|
|N-5 `--write` 守卫偏弱|脚本内查 `git diff --quiet` 与 `git status --porcelain`，写入改 `open(dest, "x")`；/tmp 克隆上四种拒绝情形实测|否|
|N-6 发布的 PLAN 引用 box 路径|read_first 改为 `.planning/phases/59.1-review-lenses/drafts/`（gitignored），Task 0 把草稿放进去并列入 MANIFEST `planning_references_not_in_bundle`|否|
|N-7 小项|setuptools 正则改为 `setuptools-[0-9][0-9.]*[0-9]`（box 实测输出 `setuptools-84.2.1`）；Task 4 断言 `revision.previous` 等于 609aa51 版；松散对象写入 evidence；锚点改为 `cli.py:4057-4071`；提交说明模板（P3-1）|否|
|P2-d 构建守卫|标记文件 + `find -newer`（排除 .git/.venv）为空；egg-info 每个文件 sha256 前后一致；在克隆上实测改动 egg-info 时两项都报出|否|

### 第 6 轮（REVIEW-6，GO with conditions；P2 1 项、P3 5 项）

|评审项|处理|是否偏离|
|---|---|---|
|P2-1 b 的通过率依赖未声明的 λ≈2|PLAN 与验收 3 写明 λ 前提；模拟加 R {3,5,7} × λ {0.5,1,2,3} × k {2,0.7} × q 网格（结果见验收 3）；预注册只跑关闭臂的预试验，测 λ_lo、q_lo 后用 `sim_null_gates.py --pilot` 定 R，无合格 R 即 b 不可计算；只卡 59.1-02|部分：评审给了两条路（预试验定 R，或把样本量前提写成前置条件）。两条一起用：预试验定 R，前提不满足时 b 不可计算。另加 R = 7，因为 λ = 1 时 R = 5 在 q = 0.05 只有 0.910，λ = 0.5 时 R = 7 在 q = 0.05 仍只有 0.842|
|P3-1 Task 9 的 grep 不在 verify 里|Task 9 action 写 `.planning/phases/59.1-review-lenses/execution/59.1-01/COMMIT_MSG.txt`；verify 用 `grep -qF` 匹配整句 "Version skew: …"；box 上整句在时退出 0，改一个字符时退出 1|否|
|P3-2 Task -1 闸门可被拒绝文字或未提交改动骗过|三处（Task -1、0、10）统一改为：`git diff --quiet HEAD`、`git show HEAD:`、行首前缀 `grep -xF`、LENS-03 同行含 EVAL60-01、标题整行匹配、只在 59.1 章节内找 59-06/59-08；/tmp 克隆上 7 种作者情形实测（1–6 加 5b；第 7 轮扩为 22 种，见 GSD 准入第 4 步）|否|
|P3-3 修订条款可改"不可计算 ⇒ 不打开"|这两条移出可修订范围；单独补测 d 须同一后端、模型 id、forge commit、语料 sha 并记录，不同即不可计算|否|
|P3-4 Task 0 草稿未固定|Task 0 点名 9 个文件并写入 sha256，verify 用 `sha256sum -c --strict` 且要求 drafts/ 恰好 9 个文件；`_vN` 备份、spec 片段、oracle 样例不复制；box 上多放一个 `_v2` 时退出 1|否|
|P3-5 校准措辞|删去"v5/r2 规则 0.000"对照；b 写成"每条 −0.10（汇总约 −0.086）"；f 的非空定义为 P0–P2（去 UNCERTAIN）；a 的最小可检出效应写明（丢 30 个 ≥ 96%、丢 15 个约一半）|否|

### 第 7 轮（REVIEW-7，GO with conditions；P2 1 项、P3 1 项，另有非阻塞附注）

|评审项|处理|是否偏离|
|---|---|---|
|P2-1 预试验规则不完备（q_lo = 0/1 时 ValueError，λ→0 时 b 形同虚设、结果不单调）|`--pilot` 总是输出一条 JSON 判定：截断前 q_lo ∉ (0,1)、λ_lo < 0.5 或无合格 R 时退出 0 并判不可计算；输入格式错误退出 2，模拟出错退出 3，任何非零退出都判不可计算；q 截断到 [0.01, 0.99]。λ 下限 0.5 依据网格与新增 `--checks`（λ = 0.02 时 b10 下 b 仍通过 0.953）。box 上边界输入全部按预期，无 traceback（见验收 3）。只改了预试验分支、参数解析，新增 `--checks` 分支；网格分支与 entry_params..run 逐字节相同，`sim_null_gates.py 20` 改前改后输出 cmp 一致，因此未重跑完整网格，结果 json 不变；脚本重新固定 sha，Task 0 改为 10 个文件|部分：另外要求合格的 R 在预试验自己的 (λ_lo, q_lo) 下 b10 通过率 ≤ 0.20，因为 `--checks` 显示 q = 0.5、λ ≤ 1 时 b 没有牙齿，只写"牙齿来自现有有害模拟"会与证据不符|
|P3-1 "R = 3 只在 λ ≥ 2" 与网格不符|PLAN r4 修订记录、`<verification>` 与本 spec 验收 3 改为：q ≤ 0.2 时要 λ ≥ 2；q = 0.5 时 λ 0.5/1 已达到 0.946/0.947、0.946/0.949，但 b 的牙齿不过|否|
|附注：闸门语义绕过（A1/A2/A6/A7/A11）|LENS 行各恰好一行、不含 rejected/not admitted/declined；LENS-03 在锚定行上查 "after … EVAL60-01" 并排除 independent of/before/without/not after；59.1 标题恰好一个；章节止于下一个 `##`/`###` 或文件末尾；22 种情形实测|否|
|附注：情形数不一致|统一为 22 种（作者 7 种：1–6 加 5b；对抗 15 种）；第 6 轮表注明"7 种"即作者情形|否|
|附注：Task 9 只 grep COMMIT_MSG.txt，`git status --porcelain` 恒为 0|提交后、提 MR 前对 `git log -1 --format=%B` 跑同一 grep 并 `test -z "$(git status --porcelain)"`；Task 9 在提交之前执行，工作区按设计是脏的，所以它的 verify 改为只对准入文档 `test -z`；Task 7 末尾同样的空检查改为 `test -z "$(git status --porcelain --ignored -- build dist)"`|部分：整棵树的 `test -z` 放在提交后的 MR 前检查，没放进 Task 9|
|附注：Task 0 用相对路径|源目录改为 `/workspace/forge-review/draft/`，目标与 sha 校验目录改为 `/workspace/forge/.planning/...`|否|
|附注：写明预试验只校准无效应|验收 3 与 PLAN 写明：无效应部分只校准通过率，牙齿来自有害情形模拟，b 的牙齿在预试验内复测|否|

### 第 8 轮（REVIEW-8，GO with conditions；P2 1 项、P3 3 项，另有 6 条非阻塞附注）

|评审项|处理|是否偏离|
|---|---|---|
|P2-1 b 的牙齿只在 (λ_lo, q_lo)、k = 2 下复测，q 偏高或 k = 0.7 时会漏|预试验另收 q_hi（fix 侧非空比例的第 80 百分位），牙齿在 (λ_lo, q_hi) 下对 k ∈ {2, 0.7} 取最坏值；每格 10,000 次，无效应 ≥ 0.906、b10 ≤ 0.192，各留 2 倍标准误。新增 `--checks` 的 tooth_qhi（12 格 × 2 个 k）与 tooth_seed_spread；旧键逐键不变。预试验结果表按新规则重跑（见验收 3）|否|
|P3-1 MR 前检查不是编号任务；行内代码嵌套反引号|PLAN 新增 Task 11（签名提交后、提 MR 前），自动 verify 为 `git log -1 --format=%B` 的 grep、整棵树 `test -z`，并检查该提交未改准入文档；/tmp 克隆上 7 种情形符合预期。Task 9 不再内联这条命令|部分：多查一项"提交未改 REQUIREMENTS/ROADMAP"|
|P3-2 版本未固定；门限边缘对种子敏感|固定 numpy 2.5.4、scipy 1.18.1（box 环境实测正是这两个版本，CPython 3.14.9），版本不符退出 0 且不可计算，JSON 记录版本；门限留 2 倍标准误，并实测 10 个种子的离散（0.195–0.204，标准差 0.0027）|否|
|P3-3 "≤ 0.06""≤ 0.07" 是上界不是实测值|改为 0.061（q = 0.01 对 0.05）与 0.071（f）；k = 0.7 的差写为至多 0.056|否|
|附注 N1：N 没有上限（N = 1e20 时 traceback）|N ≤ 1000，否则退出 2；N = 1e20 退出 2，不再出现 traceback|否|
|附注 N2："1/50 的整数倍"不成立|写明 q_lo、q_hi 是 bootstrap 百分位（numpy 默认线性插值），删掉该说法|否|
|附注 N3：闸门仍有语义绕过|列为剩余风险并逐条写出，另测 17 种情形；闸门不改|否|
|附注 N4：`/^##/` 也匹配 `####`|写明"任何 2 级及以上标题"，`####` 提前截断按失败处理|否|
|附注 N5：q_lo = 0 会挡住干净的评审者|写明这是设计取舍及理由|否|
|附注 N6：teeth_lowlam 与 harm 是 N = 150 的值|写明这些值在 N = 150 下算出，预试验按自己的 N 重算|否|

### 未采纳或改换做法的第 2 轮建议

- **lenses.md 加逐 pass 末行**（N-5）：改用 `lenses` 子命令。原因是一份静态文件不能只对某个 pass 显示对应的末行，而 CLI 可以同时完成 trust 判断。
- **license-files**（N-9）：改用 package-data，原因见上表。
- **L5/pass3 冲突写成确定性测试**（N-7）：改为验收 4 的统计。
- **上游条目未纳入**：BR-9、BR-10、CQ-12（只搬动代码的重构）、CQ-13、CQ-14、CE-10 不进镜头块，理由见差距表各行，主要是与 forge 现有机制重复、会逼出 finding，或无法证伪。

## 自审记录

- 第 2 轮新增的所有行号都对 `609aa51` 重新读过，包括：
  - `trust.py:1-8,127-176,373-402`
  - `cli.py:141-182,2956,3012-3023,3110,3218-3241`
  - `machine.py:3490-3622`
  - `flow_contract.py:26-28`
  - `falsify_real.py:228-240,313-316`
  - `falsify_receipt.py:77-89,117,152`
  - `reviewer_json.py:650,842-853,889,898`
  - `state.py:41,308,651`
  - `pyproject.toml:4-6,72-73`
  - `test_factories.py:1331-1413`
  - `docs/gsd/REQUIREMENTS.md:27,39`、`ROADMAP.md:284,665`、`PROJECT.md:31`
  - B 的四份 references 文件及 `SKILL.md:378-396,1618-1625`
- `/workspace/forge` 全程只读：运行时设置 `PYTHONDONTWRITEBYTECODE=1`，`git status` 干净。
- 第 3 轮新读的行号（609aa51）：`cli.py:747-756,1052-1115,2004-2030,2065-2075,2614-2639,3228-3241,3800,5284-5320`、`trust.py:53-80,127-215,389-402`、`legacy.py:262`、`setup_mcp.py:232`、`eval/runner.py:138,995-1011`、`mcp_server.py:1449-1455`、`tests/test_mcp_server.py:75-85`、`exit_codes.py:14-16`、`SKILL.md:1585,1609,1618-1625`。
- 镜头块（v4）：1567 B，sha256 `bef4b26e48d60ed38afc0beda7b1429b9aa333deea06a0d9335c6fcf1b3fffa1`（v3 为 1499 B，`1cfc1814…`）。脚本已核对四项：不含角色串；不含 unproven 或 Design Intent；逐行 `asserts_library_behaviour` 都是 None；正文与 `draft/lens_block.txt` 一致。
- 上游的 `correctness-reviewer.md` 已和 raw 逐字节比对，结果一致；`cursor/plugins` 根目录没有 LICENSE（404），许可证文本取自 `cursor-team-kit/LICENSE`。
- 仍未核实，留待验收 3：
  - 镜头块对模型输出的实际影响；
  - UNCERTAIN/RESET 的实际变化幅度。v4 已对真 falsifier 跑过人工样例（打桩 llm_invoke，`draft/measure_falsifier.py`），但样例不是模型输出，真实比例仍待验收 4。

## 变更记录（v8 → v9）

- 验收 3 的预试验：另收 q_hi，b 的牙齿在 (λ_lo, q_hi) 下按 k ∈ {2, 0.7} 取最坏值判定；每格 10,000 次，门限 0.906 / 0.192 留 2 倍标准误；补 tooth_qhi 与种子离散结果，预试验结果表按新规则重跑（R8 P2-1）。
- 固定 numpy 2.5.4、scipy 1.18.1，版本不符即不可计算；N 上限 1000；q_hi 越界退出 2（R8 P3-2、N1）。
- `draft/sim_null_gates.py` 与 `draft/sim_null_gates_checks.json` 重新固定 sha；网格分支与 entry_params..run 逐字节相同，`sim_null_gates.py 20` 输出 cmp 一致，结果 json 不变。
- "≤ 0.06""≤ 0.07" 改为实测值 0.061、0.071；k = 0.7 的差写为至多 0.056（R8 P3-3）。
- 删掉"1/50 的整数倍"，写明百分位算法；写明 q_lo = 0 的设计取舍；写明牙齿数值在 N = 150 下算出（R8 N2、N5、N6）。
- GSD 准入第 4 步：写明 "##" 指任何 2 级及以上标题；已知语义绕过列为剩余风险，另测 17 种情形；闸门未改（R8 N3、N4）。
- PLAN：新增 Task 11（签名提交后、提 MR 前的检查），Task 9 不再内联该命令（R8 P3-1）。
- 新增"第 8 轮"处理表；Q3、Q7、Q9 补第 8 轮说明。准入文档未动、未起草。镜头块未改：1567 B，sha256 `bef4b26e…fffa1`。

## 变更记录（v7 → v8）

- 验收 3 的预试验规则对任何输入都有结论：q_lo ∉ (0,1)、λ_lo < 0.5、无合格 R、非零退出都判 b 不可计算；q 截断到 [0.01, 0.99]；合格的 R 还须在 (λ_lo, q_lo) 下 b10 通过率 ≤ 0.20；写明预试验只校准无效应通过率，牙齿来自有害情形模拟（R7 P2-1）。
- `draft/sim_null_gates.py` 改预试验分支并新增 `--checks`（结果 `draft/sim_null_gates_checks.json`）；网格结果不变；sha 重新固定（R7 P2-1）。
- "R = 3 只在 λ ≥ 2" 改为与网格一致的措辞（R7 P3-1）。
- GSD 准入第 4 步的闸门：LENS 行各恰好一行且不含拒绝字样，LENS-03 锚定行查 "after … EVAL60-01"，59.1 标题恰好一个，章节止于 `##`/`###`；情形数统一为 22（R7 附注）。
- PLAN：Task 0 用绝对路径、固定 10 个文件；Task 9 与 Task 7 的空 `git status` 检查改为 `test -z`；MR 前对 `git log -1 --format=%B` 跑 grep（R7 附注）。
- 新增"第 7 轮"处理表；Q3、Q7、Q9 补第 7 轮说明。准入文档未动、未起草。镜头块未改：1567 B，sha256 `bef4b26e…fffa1`。

## 变更记录（v6 → v7）

- 验收 3 写明 λ 前提，新增只跑关闭臂的预试验来定 R ∈ {3,5,7}，无合格 R 即 b 不可计算；a 的"检出"改为 R 个里严格过半；校准改为 R × λ × k × q 网格并补 a 的最小可检出效应，删去 v5 规则对照，b 的有害情形注明汇总约 −0.086（R6 P2-1、P3-5）。
- "不可计算 ⇒ 不打开"与"任一不过 ⇒ 保持关"不可修订；单独补测 d 须同一后端、模型 id、forge commit 与语料 sha（R6 P3-3）。
- f 的"非空"定义为 falsify 后 ≥1 个 P0–P2（去 UNCERTAIN）（R6 P3-5）。
- GSD 准入第 4 步的闸门改为只读已提交内容并按行格式匹配（R6 P3-2）；PLAN Task 0 固定 9 个草稿的 sha256（P3-4），Task 9 的 verify 内 grep 提交说明文件（P3-1）。
- 新增"第 6 轮"处理表；Q3、Q7、Q9 补第 6 轮说明。准入文档未动、未起草。镜头块未改：1567 B，sha256 `bef4b26e…fffa1`。

## 变更记录（v5 → v6）

- 验收 3 按 REVIEW-5 §3(b) 重写为配对非劣效加裕量：R=3 重复标号、重跑规则、缺失规则、`finding_hit` matcher、α=0.025 符号检验、bootstrap 单侧下界；d 在 INV-07 落地前不可计算并有单独补测路径；附两臂相同模拟（联合 0.918–0.965）与有害情形检验力（N-1、C-1）。
- LENS-03 启动条件改为 EVAL60-01 语料冻结 + 59.1-02 PLAN 预注册与独立评审，不等 EVAL60-02（forge 第 5 轮批准）；结论、GSD 准入、时序同步。
- GSD 准入加第 4 步"准入闸门"：PLAN `autonomous: false`、Task -1 机检、Task 0/10 重检；ROADMAP 冲突规则由准入修订写入，PLAN 只检查（N-2、C-2）。准入修订本身未起草。
- T3 给 `*_lenses` 加消费方（N-3、C-3）；T19 改值级并加 B1/B2 行为测试（N-4、C-4）；T1-C 脚本自查 git 并用 `open(dest, "x")`（N-5）；T13 加标记文件与 egg-info 内容哈希（P2-d、N-7）。
- 新增"第 5 轮"处理表；Q3、Q7、Q9 补第 5 轮说明。镜头块未改：1567 B，sha256 `bef4b26e…fffa1`。

## 变更记录（v4 → v5）

- 出口 C：开关经 `_run` → `_dispatch_subagent` → `_make_subagent_spawn` 传递，行号写明；新增 T19 接线 AST 测试（含 `cross_repo.py` 反例）（P1-1、P3-4）。
- T1-C 拆成一次性采集与常驻只读检查（P2-1）；T3 写明 `capture_l1_digests.py` 合并模式与 `previous` 链（P2-3）。
- GSD：正本 `.planning/`，发布副本加 MANIFEST/README；`depends_on: []` 小数插入与冲突规则；时序不再等 Phase 59 关闭（P2-2、P2-5、Q1、Q3）。
- T13 构建改为 /tmp 下的 `git archive` 副本隔离 uv 构建，egg-info 断言改为"不变"（P2-4、Q2）。
- trust：status 六个状态串；`hash_lenses_flag` 不绑定块 sha；T16b 只读硬断言；eval 自动 trust 记入威胁（P3-3、P3-5、P3-6、Q5）。
- 验收 3：a–f 连同分母、单位与统计方法冻结（Q7）。
- 后续项：FU-8 单独小 MR（Q8）；新增 FU-9 跨仓镜头（Q6）。版本错位说明改写位置（P3-1、Q9）。
- 未决问题改为"已决问题"；新增"第 4 轮"处理表。镜头块未改：1567 B，sha256 `bef4b26e…fffa1`。

## 变更记录（v3 → v4）

- 镜头块：L4 加 "in this repo's code, not in how a library behaves"（R3-4），L5 加 "on the same path"（R3-5）；1499 → 1567 B，sha256 `bef4b26e…`；估算器增量 379 → 396 token。
- trust：守卫接受只开镜头的 gate.yaml；`trust --status` 显示镜头状态；只认 `is True`（R3-1、R3-7）。
- 出口：新增 C-host 行；C 有了 T1-C 基线 oracle；"contract" 一律写成 `REVIEW_JSON_CONTRACT`（R3-2、R3-11、R3-12）。
- `lenses` 子命令：补齐 stdout/stderr/退出码/路径/只读契约与 SKILL.md 回退（R3-3、R3-8）。
- 归属：正则剥多重标签；`legacy.py:262` 同样剥（R3-9）。
- 测试与验收：T1-C 新增；T13、T14、T15、T16、T18 扩充或重写；验收 4 加库行为命中比例与 ≥P2 L2 计数，验收 f 加相反对为 0（R3-4、R3-5、R3-15）。
- 后续项：新增 FU-7（MCP）、FU-8（setuptools ≥77，现有缺陷）；FU-3 承接 L5 残留。
- 文字：TR-6 判定、N-9 理由、N-11 计数更正（R3-6、R3-10、四(d)）。
