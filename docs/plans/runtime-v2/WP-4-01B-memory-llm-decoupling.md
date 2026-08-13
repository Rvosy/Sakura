---
kind: plan
status: accepted
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-13
---

# WP-4-01B Memory LLM 解耦计划

## 范围

本纠正包把正式记忆架构收口为 `MemoryCurator → raw vector backend`：Mem0 不再初始化、预加载、热重载
或接收任何模型 API 配置；原有 Qdrant、SQLite history、embedding、scope 和公开 Memory DTO 保持兼容。
不修改 vendored Mem0，不迁移、修复或重建真实用户数据，也不改变整理 prompt 和 ADD/UPDATE/DELETE 语义。

WP-4L-02 的已验证日志候选原样保留并暂挂回 `planned`；本包完成验收后再恢复其人工验收，不合并尚未
整合的 WP-4-04 远端提交。

## 实施顺序

1. 在 Memory 子进程建立 Sakura-owned raw backend，只创建 FastEmbed、Qdrant 和 SQLite history。
2. 用受限 facade 强制 `add(..., infer=False)`，删除 LLM RPC、依赖预加载、配置和热重载代码。
3. 删除未使用的 `add_history_entries()` 及 Mem0 提炼专用 prompt/转换辅助函数。
4. 从 Core、legacy bootstrap 和设置保存链路移除传给 `MemoryStore` 的 API 配置；保留独立
   `memory_curation` client。
5. 补充单元与集成测试，证明 LLM factory/OpenAI import 零调用、旧数据 CRUD/检索兼容、误用
   `infer=True` 明确失败、Provider 设置变更不重启 Memory。
6. 更新架构、规范、用户配置说明和 CHANGELOG，运行 task 自动门后等待负责人实机验收。

## 退出条件

- 无 API 配置、空 API Key、无 OpenAI client 的环境仍可初始化本地 Memory 并完成 CRUD/搜索。
- Memory 子进程启动事件不再出现 LLM/OpenAI 依赖与 LLM create/reload；失败仍有 embedding、Qdrant、
  SQLite 的固定诊断。
- 全仓生产路径不存在 `mem.add(..., infer=True)`，受限 facade 对任何 inference 请求稳定拒绝。
- 现有 `sakura_memories` collection、384 维向量、metadata、ID 和 `mem0_history.db` 原样复用；测试和实施
  均不读写真实 `data/**`。
- 聊天/Provider 模型切换不关闭、不重建 Memory owner；专用整理模型仍按模型槽创建 client 并产生 Trace。
- required profiles 全绿后进入 `stabilizing`，只等待负责人验收，不由 Agent 标记 accepted。

## 回退

正常退出应用后整体 revert 本 WP。回退只恢复代码和文档，不删除、迁移、重建或修复 Qdrant、SQLite、
embedding cache、配置、聊天历史或任何真实用户数据。
