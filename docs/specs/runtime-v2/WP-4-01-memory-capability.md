---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-20
---

# WP-4-01：Runtime v2 Memory 能力等价

## 1. 目标与范围

本规范定义 CAP-008 在 Runtime v2 的产品行为。长期记忆由 bundled `sakura.memory.mem0` 作为普通 Plugin
Kernel v3 插件提供；Core 不再拥有统一 Memory Service、Memory Router、专用 Bridge 或固定 Memory
Prompt 分支。Mem0 与其他存储模型可以同时贡献上下文，任一插件故障、停用或移除都不能阻断普通聊天或
改变另一 Contributor 的行为。

本能力必须保持：

- 按当前角色 scope 检索长期记忆，并经普通 `sakura.host.context` Contributor 注入聊天；检索、embedding、
  Qdrant、SQLite 或整理失败时聊天仍能完成，且不伪造命中。
- 通过通用插件设置、Action 与 Collection 管理整理配置、固定 embedding 模型和记忆 CRUD。
- 在已完成聊天事实落盘后异步整理兼容历史；取消、失败或未完成回复不推进整理状态。
- 动态停用、启用、reload、Worker 重建与应用退出时有界回收 callback、Effect、线程、子进程和存储句柄。
- 既有 Memory 数据、旧配置与模型缓存保持兼容，不因 cutover 自动迁移、重建、删除或清空。

本规范不维护 Work Package 当前状态；唯一状态源是
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)。Plugin Kernel 的通用行为由
[`sakura-plugin-kernel-v3.md`](./sakura-plugin-kernel-v3.md) 约束。

## 2. 所有权与运行边界

- `plugins/sakura_mem0` 是 Runtime v2 Mem0 的唯一运行 owner。插件拥有 `MemoryBoundary`、`MemoryStore`、
  `MemoryRecallService`、整理状态、本地模型任务及相关资源；Core 不构造第二个 Memory owner。
- 插件只使用普通 `sakura.host.context`、`sakura.host.tools`、`sakura.host.settings` 和
  `sakura.host.chat.completed`。不得增加 Memory 专用 Host Service、Generic Bridge 分支或公开
  `application_root`。
- bundled 插件只在受信任的打包布局中用 `Path(__file__).resolve().parents[2]` 定位 Sakura 根目录。布局
  不满足时稳定加载失败，不猜测其他目录、不创建空库、不迁移现有数据。
- Mem0、FastEmbed/ONNX Runtime、Qdrant 与 SQLite 继续位于 generation 私有 Plugin Worker 进程树内。
  lifecycle callback 卡死时由 Worker deadline 终止并重建整个 Worker；不为 Mem0 建立独立 Worker 或第二套
  Supervisor。
- 自动提炼只由 Sakura `MemoryCurator` 和插件配置引用的 Provider/模型完成。Mem0 raw 写入保持
  `infer=False`，不得启用 Mem0 内置 LLM、读取聊天 Provider API Key 或因本地存储初始化执行网络请求。
- 当前角色在插件 setup 时冻结。Context request、Collection 投影和 completed-chat 事实的角色不一致时
  fail-closed；不得查询、修改或整理其他角色 scope。
- Legacy Qt 如需召回，只能把同一既有 Memory 实现包装成普通 Context provider；不得恢复 Runtime v2 的
  `assistant.memory` owner 或专用协议。

## 3. 数据与配置契约

官方插件继续原位使用既有数据：

| 数据 | 契约 |
|---|---|
| `data/memory/qdrant/**` | 既有本地 Qdrant collection；不得为 cutover 删除、重建或批量重算 |
| `data/memory/mem0_history.db` | 既有 Mem0 SQLite history；由库事务管理，Rust/WebView 不解析 |
| `data/memory/core_profiles.json` | 保留其他角色 scope；仅通过既有原子写语义修改 |
| `data/memory_curation_state.json` | 保留整理游标和 pending 状态；只在 completed-chat 整理语义下更新 |
| `data/memory.json` | 未确认的历史文件；保留原始字节，不导入、不写、不删除 |
| 现有 FastEmbed/ONNX cache | 原位复用固定 snapshot；不得因迁移覆盖或移走 |
| 旧 PyTorch 模型 cache | 只作兼容回退材料；不删除、不覆盖，也不视作 ONNX 已安装 |

插件可写配置仅为：

```text
data/plugins/sakura.memory.mem0/config.json
```

字段为 `triggerTurns`、`backfillLimit`、`curationProfileId` 和 `curationModel`。首次缺失字段时，插件可以从
`data/config/system_config.yaml` 的旧整理配置和 `data/config/api.yaml` 的旧模型槽执行 copy-only 合并；旧
YAML 始终只读，已有插件字段优先，部分合并失败下次启动可重试。不得把 Memory 数据或模型 cache 复制到
plugin-data。

`triggerTurns` 只允许整数 `1..50`；`backfillLimit` 读取并保留，不在当前声明式设置页编辑。整理模型引用
必须是已有 Provider profile 与 model 的成对选择；未选择时跳过自动整理，不影响本地管理、召回或聊天。

embedding 公开模型固定为 `sentence-transformers/all-MiniLM-L6-v2`，维度 384；实际工件固定为
`qdrant/all-MiniLM-L6-v2-onnx@5f1b8cd78bc4fb444dd171e59b18f3a3af89a079`，使用 FastEmbed 0.8.0 与
ONNX Runtime 1.28.0 的 `CPUExecutionProvider`。不得开放任意模型名、URL、revision 或缓存路径输入。
旧 PyTorch cache 不满足已安装状态。

Memory 内容、query、完整历史、Prompt、API key、cache 绝对路径和第三方异常原文不得进入插件状态、通用
Snapshot、日志或证据工件。公开错误只使用稳定 code、简短脱敏 message 和 retryable 标记。

## 4. 协商、贡献与通用接口

Memory 不再协商 `assistant.memory`。只有客户端协商 `assistant.plugins-v1` 后，Core 才创建 Plugin Worker
并加载 enabled 的 `sakura.memory.mem0`；未协商时不打开 Memory 存储、不创建 plugin-data，也不暴露插件
设置请求。插件 Tool 是 `assistant.plugins-v1` 的普通 contribution；`assistant.tools-v1` 独立控制 Core-owned
工具与其设置面。当前产品拓扑同时协商两者；仅未协商 `assistant.tools-v1` 时不得无条件注入
`get_current_time` 或因此改变模型请求形态。

桌面和测试只使用通用接口：

- `plugins.settings.get/save/action`
- `plugins.collection.query/create/update/delete`
- 普通 ToolRegistry 调用
- 普通 Context Contributor 调度
- `sakura.host.chat.completed` 事实事件

Rust 不注册 Memory commands，不解析 Memory record，不持有 Memory task handle，也不观察 Memory 专用事件。
Core 协议、Rust command、WebView runtime 和诊断 allowlist 中不得恢复 `memory.*`、`assistant.memory` 或
`memory_gateway` 运行链。

插件注册四个普通工具：

| Tool | 行为 |
|---|---|
| `memory_search` | 搜索当前角色的长期记忆 |
| `memory_remember` | 保存显式、长期有用且通过既有敏感内容校验的记忆 |
| `memory_update` | 按准确 `memory_id` 更新当前角色记忆 |
| `memory_forget` | 仅在用户明确要求时幂等删除当前角色记忆 |

工具随插件 root Effect 注册和撤销。停用、reload 或 Worker invalidation 后旧 callback handle 必须失效；恢复
后只出现一组新 Tool、Context、Settings 与 Collection contribution，不得重复注册。

插件的 Context callback 返回普通受限 fragment。Host 将来源标记为 `plugin:<plugin_id>`、trust 标记为
`untrusted`，并统一执行数量、字符、token、敏感度和总动态上下文上限。调度不得识别 `memory` 来源或预留
Memory/Plugin 固定配额；一个 Contributor 失败时继续选择其他 Contributor 与 Host required facts。

## 5. 聊天召回与整理语义

每个 Mem0 Contributor 每轮最多执行一次相关检索。query 由当前输入和受界近期消息构造；去重、过期过滤、
相关性阈值和最多五条命中沿用 `MemoryRecallService`。命中作为 private context，不回显内部 ID，不进入
日志。初始化中、模型缺失、锁冲突、超时、损坏或任意存储错误均返回空 fragment，Provider 请求与唯一聊天
terminal 继续。

Host 仅在同轮 user 与 assistant 历史都成功落盘且 terminal 为 `chat.completed` 后发送一次
`sakura.host.chat.completed`：

```json
{
  "characterId": "sakura",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

两段 content 与事件总大小服从 Plugin Kernel 的通用上限。事件不提供 History Store、Memory cursor 或整理
方法。插件核对角色后读取既有 `ChatHistoryStore`，按 `triggerTurns` 串行整理；未选整理模型时跳过。事件
Handler 失败、超时或 Worker 重建不能改变已经确定的聊天 terminal。

整理失败保留既有 Memory 和可重试状态，不提前提交游标。正常退出先关闭整理与模型任务；卡死由 Worker
lifecycle timeout 终止整棵后代进程。新 generation 只从已原子提交的状态恢复，迟到结果不得写入新
generation 或其他角色。

## 6. 通用插件设置与 Collection

旧桌面的“记忆”页面及 `memory.manage`、`memory.curation`、`memory.embedding_model`、
`model.memory_curation_slot` 产品 feature 保持 `unavailable`，理由为“长期记忆已迁至通用插件页”。旧页面
不得再动态加载 Memory runtime 或调用专用命令。

通用“插件”页面从 `plugins.settings.get` 展示 `sakura.memory.mem0` 的 `memory` section：

- readonly 运行状态、固定 embedding 模型与安装状态；
- `triggerTurns` 与整理 Provider/模型组合选择；
- `downloadEmbedding`、`cancelEmbedding`、`refreshStatus` 普通 Action；
- `memories` 普通受限 Collection，支持分页、搜索、layer 枚举筛选和 CRUD。

Collection 只公开 `content/layer/category/source/importance/confidence/updatedAt`，item identity 使用通用
`itemId`。layer 只允许 `core_profile/semantic/episodic/procedural/session`；内容上限 16384 字符；查询每页
最多 100 条，并同时受 256 KiB 通用 Collection payload 上限。未知字段、非法 cursor、跨角色记录和超界
响应稳定拒绝或不投影。

模型下载是插件 Settings Action，由插件内部线程执行固定 snapshot 下载。Action 立即返回，状态通过后续
设置读取或刷新 Action 观察；取消只影响当前 plugin generation 启动的任务。失败或取消保留旧完整 cache，
不得晋升 staging 或隐式更换模型。当前不提供 ZIP 导入；未来若恢复，必须由通用 artifact/插件 Action 组合
驱动，不能恢复 Memory 专用 Rust 文件选择 token 或 Bridge。

## 7. 生命周期与故障边界

- Plugin setup 的 Memory runtime、completed-chat Handler、Context、四个 Tool、Settings 和 Collection 全部
  绑定同一 root EffectScope。setup 任一步失败必须整体回收，插件不能半激活。
- `active → disabled` 必须撤销所有 Host contribution、令 `effectCount` 归零并关闭 Memory runtime；
  `disabled → active` 与 reload 使用新实例和新 callback handle 恢复，不能重放旧 Handler。
- callback、Event、Service 或 cleanup 超时不重试原调用；Worker 终止后按 persisted desired state 重建。
  generation 正在 quiesce/close 时不得再生成替代 Worker。
- 模型下载 cleanup 先发送取消并等待插件线程；无法协作结束时交由 Worker lifecycle timeout 终止，不允许
  daemon thread 越过 generation 继续写 cache。
- 插件 unavailable、degraded、disabled、failed 或 Worker 重建期间，普通聊天仍能在没有该 Contributor 与
  tools 的情况下完成。另一 Memory Contributor 的 Context 不受影响。
- 用户未明确执行 CRUD、配置保存或模型下载时，不得产生相应写入或网络访问。completed-chat 仅允许按既有
  整理语义更新 chat history/curation state 和最终记忆写入。

## 8. 验收门

自动验证至少覆盖：

- 官方 manifest 默认 enabled，并只依赖三个通用 Host Service；当前产品拓扑真实加载该插件。
- 未协商 `assistant.plugins-v1` 时不创建 Memory owner、不打开 Qdrant、不创建插件配置目录。
- 真实 `PluginWorkerClient → Host Service → callback → SakuraMem0Runtime.context(dict)` 重建完整
  `ContextRequest`，角色不一致 fail-closed。
- 两个不同 Memory Contributor 同时存在；一个抛错不影响另一个入选，Core/Prompt 不按 Memory 来源分支。
- Tool、Context、Settings、Collection 在 disable/re-enable/reload 后完整撤销与恢复；停用时
  `effectCount == 0`，旧 Collection/callback 不可调用。
- 模型缺失、依赖导入、Qdrant/SQLite/锁冲突、损坏配置、回调超时和下载取消时聊天继续、旧数据保持、
  无隐式网络访问。
- 在隔离根记录切换前后的 SHA-256/size：`memory.json`、旧 YAML、Qdrant、SQLite、core profiles、现有
  FastEmbed/ONNX cache 和旧 PyTorch cache 在只读设置/搜索路径保持不变；completed chat 只允许既有
  curation-state 语义变化。
- 正常退出、插件停用、reload、Worker timeout、Core crash 后线程、callback、Effect、pipe、文件锁与后代
  进程有界归零。
- Frontend、Rust、Python focused tests，以及 `runtime-v2-memory-tests` 与当前产品 smoke journey 通过；
  无法本地执行的平台/真实模型门明确记录风险。

所有测试只能写隔离临时根，不得以真实用户 Memory 数据、配置、日志或 cache 作为 fixture。

## 9. 非目标与回退

本 WP 不建设统一 Memory Service/Record DTO、Memory 专用 Bridge、权限系统、Slot、通用事务框架、逐插件
进程、在线模型市场或任意下载器。它不自动扫描或迁移外部旧程序数据，也不修改 vendored Mem0 源码。

回退时先把官方插件 desired state 设为 disabled 并停止接收新调用，再 dispose 当前 Worker；超时终止当前
generation Worker/后代。可以恢复代码入口，但不得删除、回滚、重建、迁移或手工修复用户 Qdrant、SQLite、
Memory JSON、curation state、模型 cache、旧 YAML、插件配置或已完成聊天历史。
