# Phase 31: CN Backend Robustness - Discussion Log

**Date:** 2026-06-27
**Participants:** User + Claude (Opus 4.6)

## Areas Discussed

### 1. Retry Strategy

**Q1: Retry 应该在哪一层实现？**
Options: llm_invoke 层 / factories.py pass 循环层 / 两层都做
**Selected:** 两层都做

**Q2: HTTP 级 retry 参数怎么定？**
Options: 3次/1s起步 / 5次/2s起步 / 可配置
**Selected:** 可配置，默认选项2（5次/2s起步）

**Q3: Pass 级别 retry 策略？**
Options: 重试整个 pass 1 次 / skip 失败的 pass / 你决定
**Selected:** 重试整个 pass 1 次

### 2. Provider Error Classification

**Q1: Provider 错误码映射表放哪里？**
Options: 硬编码在 llm_invoke.py / 外部 YAML 配置 / 你决定
**Selected:** 硬编码在 llm_invoke.py

**Q2: Zhipu 的 21 个子码覆盖范围？**
Options: 只映射常见 5-6 个 / 全部 21 个 / 你决定
**Selected:** 只映射常见的（用户先选全部后改为常见的）

### 3. L1 Concurrency Control

**Q1: 当前 3 passes 串行。要不要改并发？**
Options: 保持串行 / 可配置并发度 / 你决定
**Selected:** 保持串行

### 4. Degradation and Feedback

**Q1: retry 耗尽后的行为？**
Options: fail-closed / warn-through / 可配置
**Selected:** fail-closed

**Q2: 错误消息格式要求？**
Options: 命名 provider + 问题 + 操作建议 / 简洁模式 / 你决定
**Selected:** 命名 provider + 问题 + 操作建议

### 5. Additional Gray Areas (sequential-thinking + Exa scan)

**Q1: Body-based error detection (Zhipu/MiniMax HTTP 200 + error in body)?**
Options: 响应解析前检查 / 包装层统一拦截 / 你决定
**Selected:** 响应解析前检查

**Q2: 5xx/网络错误也要 retry?**
Options: 是，和 429 同等 retry / 只 retry 429 / 你决定
**Selected:** 是，和 429 同等 retry

**Q3: retry 过程中打印进度?**
Options: 打印 / 静默
**Selected:** 打印

## Deferred Ideas

- Provider fallback chain -- own phase
- Circuit breaker -- overkill for current pattern
- Per-backend retry config -- premature
- Zhipu full 21 sub-codes -- expand on demand
