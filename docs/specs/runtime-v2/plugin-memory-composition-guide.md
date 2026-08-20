---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
updated: 2026-08-21
---

# Memory Plugin Composition Guide

Memory 不获得统一 Store/Search/Recall/Curation Kernel 协议。实现自行拥有向量库、图谱、SQLite、时间线或
总结模型，通过 Session/聊天事实 Event 观察输入，通过 `sakura.host.context` 提供有界 Context，通过普通
Tools 暴露用户操作，并可用 experimental Collection 管理数据。

Application 资源跨 Assistant Session 保持；依赖当前角色、Agent Runtime 或聊天事实的 Handler 必须注册在
`ctx.on_session()` child scope 中，unbind 时归零。只有已完成且已持久化的聊天事实可以推进整理状态；取消、
失败和未完成 terminal 不得写入。具体 Mem0 行为与数据兼容继续由
[WP-4-01](WP-4-01-memory-capability.md) 约束。
