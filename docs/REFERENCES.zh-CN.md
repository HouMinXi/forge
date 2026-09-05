# 参考文献

[English](REFERENCES.md)

code-forge 的设计所依赖的论文和数据集，以及每一篇在这里拿来做什么。这不是综述。只有代码树里某个决定能追溯到它，一篇论文才会列在这里，段落里会说明是哪个决定。

下面每个 arXiv id 都在 2026-09-05 用 `curl -sI https://arxiv.org/abs/<id>` 核对过，全部返回 HTTP 200。

## Autorubric

Delip Rao，"Autorubric: A Unifying Framework for Rubric-Based LLM Evaluation on Non-Verifiable Tasks"，arXiv [2603.00077](https://arxiv.org/abs/2603.00077)，2026。

Autorubric 把 LLM 裁判的失败方式列了一遍：位置偏差、随机不一致、判据混淆、不确定时被迫下判断、依赖模型的校准。它对判据混淆的解法是每条判据单独一次调用、只问一个二元问题，而不是一个把几件事揉在一起的整体性问题。它还坚持裁判的可靠性必须在标定集上测出来，不能假设。

code-forge 的证伪步骤正是这篇论文警告的整体性形态：一个 prompt 装着十步协议、三态裁决和可选的回执，模型要在同一口气里理解、裁决、举证。v3.1 的判断线从 Autorubric 拿了两样东西。一是从评测账本里抽出证伪器标定集，改 prompt 之前先把这道门自己的准确率测成一个数。二是取决于这个数，把证伪 prompt 拆成几个独立的是/否问题（路径可达吗，符号存在吗，失败会发生吗），每个问题单独一次调用。论文自己的保留意见同样适用：「有些构造抗拒二元分类」。可达性可以二元，「这个设计好不好」不行，拆分只用在问题允许的地方。

Autorubric 的集成裁判（k 个裁判投票）是刻意不采用的。三个里取多数会惩罚这个工具存在的目的，那些难抓的缺陷；而且正常使用中一次评审只跑一遍。

## AACR-Bench

Lei Zhang 等，"AACR-Bench: Evaluating Automatic Code Review with Holistic Repository-Level Context"，arXiv [2601.19494](https://arxiv.org/abs/2601.19494)，2026。

一个多语言代码评审基准，带完整跨文件上下文和专家核验的答案，作者用它比较几种上下文策略：无上下文、BM25 检索、embedding 检索、agent 自主检索。主要结论是上下文是精度杠杆（论文里某个前沿模型的数字从无上下文的个位数升到 agent 检索上下文的约 40%），而朴素检索把 F1 压到无上下文基线以下，高精度的 agent 模式付出的代价是召回掉到 10% 附近。

code-forge 拿的是框架而不是技术：到达评审者的应该是少量带来源的结构化事实，不是更多文本。这是 v3.1 证据线里 `context_sources.py` 背后的设计规则，每条事实带来源和快照 commit，provider 索引的 commit 不对就拒绝。论文 agent 模式里的召回崩塌正是证据线要避开的失败方式，所以评审者是从配置好的来源接收事实，而不是自己去找。要说明的是，写 v3.1 计划时 AACR-Bench 只读了摘要和一份概述；分表数字是转引的，单独拿来做决策依据之前应该回到 PDF 重读。

## RARe

Qianru Meng 等，"When More Retrieval Hurts: Retrieval-Augmented Code Review Generation"，arXiv [2511.05302](https://arxiv.org/abs/2511.05302)，2025。

RARe 把检索到的历史评审评论当作上下文示例喂给评审生成模型。code-forge 用到的结论就在标题里：只检索一条示例效果最好，加到第三条、第五条反而变差。

这是继 AACR-Bench 之后第二份证据，说明上下文密度比上下文数量重要。它是 context source 设计里每个来源带硬性 token 预算的原因，也是 forge 完全不检索历史评审评论的部分原因。RARe 自己的目标是评论文风，用 BLEU 衡量，跟缺陷召回不是一回事。

## CR-Bench

Kristen Pereira、Neelabh Sinha、Rajat Ghosh、Debojyoti Dutta，"CR-Bench: Evaluating the Real-World Utility of AI Code Review Agents"，arXiv [2603.11078](https://arxiv.org/abs/2603.11078)，2026。

CR-Bench 从 SWE-bench 派生出一个代码评审语料：对每个实例，用 `git blame` 把修复补丁删掉的行追溯到引入它们的那个 PR，语料就是一组真实 PR，每个都含有一个后来被修掉的缺陷。论文评测了一个单次 agent 和一个 Reflexion 式的迭代 agent，报告说逼 agent 找出所有隐藏问题会拉低它的信噪比。

这篇论文是 v3.0 里程碑存在的原因。code-forge 的三轮收敛循环是 Reflexion 形态的架构，论文预测了它的一种失败方式，证伪门就是这个工具对这个预测的防御。v3.0 之前，预测和防御都没有在这个工具上测过。评测语料借用了 CR-Bench 的构造思路（SWE-bench 缺陷加修复作答案）和指标集（precision、recall、F1、信噪比）；没有用 CR-Bench 的数据集，当时还没发布，而且直接用 SWE-bench Verified 的筛选代替重跑 blame 追溯。CR-Bench-Verified 的 174 条是 150 条语料的规模参照。

## SWE-bench 与 SWE-bench Verified

Carlos E. Jimenez 等，"SWE-bench: Can Language Models Resolve Real-World GitHub Issues?"，arXiv [2310.06770](https://arxiv.org/abs/2310.06770)，2023。Verified 子集：Hugging Face Hub 上的 `princeton-nlp/SWE-bench_Verified`，500 个实例，由人工标注者筛过，保证问题可解、描述清楚。

code-forge 评测语料的每一条都来自 SWE-bench Verified。每个实例带仓库、基准 commit、修复补丁和人写的问题描述。`code_forge.eval.build_corpus` 把修复反过来得到缺陷 diff，正向打上得到干净对照；答案是修复的文件和行范围加问题描述第一行。选取规则和这份语料能说明什么、不能说明什么，见 [EVALUATION.md](EVALUATION.md)。

## 读过但没采用的

规划 v3.1 时还读了三篇，它们决定了 forge 不做什么。列在这里是为了让这些不做读起来像决定。

- Yuxin Zhang 等，"LAURA: Enhancing Code Review Generation with Context-Enriched Retrieval-Augmented LLM"，arXiv [2512.01356](https://arxiv.org/abs/2512.01356)，2025。为评论生成检索评审范例。没采用：它提升的是评论文风，不是缺陷召回。
- John Naulty 等，"Bugdar: AI-Augmented Secure Code Review for GitHub Pull Requests"，arXiv [2503.17302](https://arxiv.org/abs/2503.17302)，2025。报告检索让安全类发现的精度和召回各涨几个点，同时让解释类发现的精度下降。读作检索效果分类别的又一份证据。
- Imen Jaoua 等，"Combining Large Language Models with Static Analyzers for Code Review Generation"，arXiv [2502.06633](https://arxiv.org/abs/2502.06633)，2025。静态分析结果喂进 prompt 比事后拼接效果好。与 forge 已有的做法一致，lint 层排在模型 pass 前面。
