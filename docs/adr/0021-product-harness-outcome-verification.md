---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-08-15
---

# ADR-0021：Harness 只验证产品结果

> 决策日期：2026-08-15
> 替代：[ADR-0009](../archive/adr/0009-lean-agent-development-harness.md)

## Context

ADR-0008/0009 逐步建立并简化了 task contract、固定 Git base、allowed paths、Work Package 依赖、全局
保护目录和人工验收状态。实际使用表明，稳定且有价值的是 profile Runner、产品 journeys 和 JSON 报告；
治理层则需要预先猜测修改范围、持续维护任务文件，并把 Work Package 路线图变成代码修改许可。

这些约束限制跨模块根因调查，却不能替代清晰的产品不变量和真实 regression coverage。Sakura 已有 Spec、
ADR、Tests 和 Git，分别能够保存当前行为、设计理由、可执行证据和修改历史，不需要 Harness 再维护一份
开发审批账本。

## Alternatives

1. 保留 task v2，仅放宽部分范围失败。实现改动最小，但旧概念和维护成本继续存在。
2. 创建 task contract v3 或新的 capability/scope manifest。可以重新表达流程，却会用新框架替换旧框架。
3. 删除 Agent governance，只保留 Product Harness，并把真正的长期约束写入 Spec/ADR/Tests。

## Decision

采用方案 3：

- Harness 只负责列出和运行产品能力 profile、捕获结果并生成报告。
- 删除 task contract、base_ref、allowed_paths、Git scope gate、Work Package gate、activation 和
  manual acceptance 状态；不创建替代状态机。
- Work Package 可以继续作为 Runtime v2 路线图，但不授权或禁止代码修改，也不影响 Harness PASS/FAIL。
- AGENTS 只提供仓库地图、相关知识入口、验证建议和真实安全边界；允许跨模块调查和修改根因。
- 产品长期行为进入 Spec，重要架构理由进入 ADR，回归由 tests/journeys 固化，普通实现过程留给 Git。

## Consequences

Harness 代码、配置和开发指令显著减少，强模型可以围绕真实调用链自由调查；CI 和开发者仍可通过稳定
profiles 获得可重复产品证据。现有 Tools、MCP、Plugins、Shell、Core 和可观测性 journeys 不因治理删除
而丢失。

代价是 Harness 不再机械阻止无关文件修改或测试删除。正确性更多依赖明确的 Spec invariants、regression
tests、产品 journeys、diff review 和 CI；凭据、破坏性 Git、生产部署与不可逆用户数据操作仍由真实安全
边界保护。
