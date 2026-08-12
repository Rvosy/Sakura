---
kind: adr
status: proposed
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# ADR-0014：Sakura Memory Manager 与无 LLM 的向量后端

本 ADR 替代 ADR-0011 中“Memory 子进程创建/热重载 LLM，并随聊天 API 设置变化”的窄决策；
ADR-0011 的 generation 私有子进程、FastEmbed/ONNX、Qdrant/SQLite 所有权和资源回收决策继续有效。

## 背景

Sakura 的正式自动记忆链路已经由 `MemoryCurator` 使用角色人格、既有记忆和专用
`memory_curation` 模型槽生成 `ADD / UPDATE / DELETE` 操作。写入阶段一直使用
`mem0.add(..., infer=False)`，Mem0 实际只提供本地 embedding、Qdrant CRUD/检索和兼容 SQLite history。

但现有适配层仍向 Mem0 传入聊天模型配置。Mem0 构造函数无条件创建 LLM，Memory 子进程还预加载
OpenAI 依赖、支持 LLM 热重载，并保留一个可触发 `infer=True` 的旧入口。这造成未使用的 Provider
耦合，也留下未来双重提炼的风险。

## 候选方案

1. 继续提供聊天模型或伪 API Key：改动小，但保留错误依赖和误用风险。
2. 修改 vendored Mem0 为惰性 LLM：能解决上游构造问题，但触碰仓库全局保护的第三方源码，并让
   Sakura 的正式语义依赖私有 fork。
3. 完全重写向量存储：边界最纯，但会重复 Qdrant、过滤、返回值和 SQLite history 兼容实现，迁移风险大。
4. 在 Sakura 自有适配层组装 raw backend：复用已验证的 Mem0 数据方法与格式，但只创建 embedder、
   vector store 和 SQLite history；受限 facade 明确禁止 inference。

## 决策

采用方案 4：

- `MemoryCurator` 是唯一自动记忆提炼器，只有 `memory_curation` 模型槽可以为它提供 Provider client。
- generation 私有 Memory 子进程不读取聊天或整理模型 API 配置，不导入或创建 Mem0 LLM，也不支持
  LLM reload。
- Sakura 自有 raw backend 继续复用既有 Mem0 的 CRUD、向量检索和 history 实现，保持 collection、
  384 维向量、record ID、metadata 与 SQLite 文件兼容；不修改 `third_party/**`，不迁移或重建用户数据。
- 子进程 facade 只开放既有受限 CRUD/搜索方法；`add` 必须显式收到 `infer=False`，否则稳定失败。
- 删除生产未使用的 `add_history_entries()` 与 Mem0 整理 prompt，避免恢复 Mem0 自带提炼。
- Provider/聊天模型保存不再重启或热重载 Memory。embedding 模型生命周期仍由 Memory owner 独立管理。
- 现有 Mem0 history 继续记录 raw CRUD 审计；旧 messages cache 可清理但不再新增，不作为提炼输入。

## 后果

Memory 初始化不再需要 API Key、Provider endpoint、OpenAI client 或 SOCKS LLM 预检。聊天模型切换不会
扰动 Qdrant owner，设置中只有一个含义明确的“记忆整理模型”。代价是 Sakura 需要维护一个很窄的
Mem0 raw 初始化适配面；测试必须锁定其属性、方法和持久化兼容性，Mem0 升级时显式复核。

若未来确实需要 Mem0 inference，必须建立新的架构决策和独立配置，不得放宽当前 facade 或复用聊天模型。
Work Package 状态和验收结论只以
[`work-packages.md`](../plans/runtime-v2/work-packages.md) 为准。
