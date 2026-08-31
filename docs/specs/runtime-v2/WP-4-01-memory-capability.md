---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-26
---

# WP-4-01：Runtime v2 Memory 能力等价

> 计划中的 [WP-4-07R](WP-4-07R-typed-timeline-adaptive-context.md) accepted 后，Memory 将通过只读
> `sakura.host.timeline` 游标消费已提交交互，完成事件不再携带聊天正文。在此之前，本规范下述
> `sakura.host.chat.completed` 与 ChatHistory 行为仍是当前 accepted 契约。

## 1. 目标与范围

本规范定义 CAP-008 在 Runtime v2 的产品行为。长期记忆由 bundled `sakura.memory.mem0` 作为普通 Plugin
API v4 插件提供；Core 不再拥有统一 Memory Service、Memory Router、专用 Bridge 或固定 Memory
Prompt 分支。Mem0 与其他存储模型可以同时贡献上下文，任一插件故障、停用或移除都不能阻断普通聊天或
改变另一 Contributor 的行为。

本能力必须保持：

- 按当前角色 scope 检索长期记忆，并经普通 `sakura.host.context` Contributor 注入聊天；检索、embedding、
  Qdrant、SQLite 或整理失败时聊天仍能完成，且不伪造命中。
- 通过常驻“记忆”页面管理记忆 CRUD，通过通用插件设置管理整理间隔和固定 embedding 模型，通过动态
  模型槽位管理整理 Provider/模型。
- 在已完成聊天事实落盘后异步整理兼容历史；取消、失败或未完成回复不推进整理状态。
- ADR-0032 生效后，只有 Memory 自身启停/reload 才局部 dispose Memory 及传递消费者；任何无关设置保存
  不得关闭 MemoryStore、FastEmbed、Qdrant 或 SQLite，也不得重新 preload embedding。
- v2 正式发布后的 Memory schema migration 可以按 v2 合同演进；正常启动不扫描或导入旧 main 数据。

本规范不维护 Work Package 当前状态；唯一状态源是
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)。Plugin Runtime 的通用行为由
[`sakura-plugin-runtime-v4.md`](./sakura-plugin-runtime-v4.md) 约束。

## 2. 所有权与运行边界

- `plugins/builtin/sakura_mem0` 是 Runtime v2 Mem0 的唯一运行 owner。插件拥有 `MemoryBoundary`、`MemoryStore`、
  `MemoryRecallService`、整理状态、本地模型任务及相关资源；Core 不构造第二个 Memory owner。
- 插件只使用普通 `sakura.host.context`、`sakura.host.tools`、`sakura.host.settings`、
  `sakura.host.model_slots`、`sakura.host.storage`、`sakura.host.character`、`sakura.host.timeline` 和
  `sakura.host.chat.completed`。不得增加 Memory 专用 Host Service、Generic Runtime 分支或公开
  `application_root`。
- 共享 Memory 数据、cache、当前角色和模型凭据只通过这些 Host Service 的受限 descriptor/resolve 合同取得。
  插件不得从 `data_path()` 或自身源码位置反推 Sakura 根目录。
- Mem0、FastEmbed/ONNX Runtime、Qdrant 与 SQLite 位于 Mem0 自己的 generation 私有插件进程和 dependency
  root 中。调用或 cleanup 卡死时只终止 Mem0 及其受控后代，不重启无关插件。
- 自动提炼只由插件拥有的 `MemoryCurator` 和插件配置引用的 Provider/模型完成。Mem0 raw 写入保持
  `infer=False`；整理凭据只通过 `sakura.host.model_slots.resolve()` 取得，本地存储初始化不得联网。
- 当前角色在插件 setup 时冻结。Context request、Collection 投影和 completed-chat 事实的角色不一致时
  fail-closed；不得查询、修改或整理其他角色 scope。
- Memory 只服务 Runtime v2 Plugin Kernel，不导入 Legacy Qt owner、协议或启动链。

## 3. 数据与配置契约

官方插件只使用当前 `user_root` 中的 v2 数据：

| 数据 | 契约 |
|---|---|
| `data/memory/qdrant/**` | v2 本地 Qdrant collection；不得由 Rust/WebView 直接解析 |
| `data/memory/mem0_history.db` | v2 Mem0 SQLite history；由库事务管理 |
| `data/memory/core_profiles.json` | 按角色 scope 保存；仅通过原子写语义修改 |
| `data/memory/curation_state/**` | 保存 v2 Timeline 整理游标和 pending 状态 |
| `data/cache/memory/**` | 用户主动下载的固定 FastEmbed/ONNX snapshot；不进入发行包 |

插件可写配置仅为：

```text
data/plugins/sakura.memory.mem0/config.json
```

字段为 `triggerTurns`、`backfillLimit`、`curationProfileId` 和 `curationModel`。缺失时使用 v2 插件默认值，
不得从旧 Core 整理字段或旧 Memory 模型槽补齐。Provider 目录与解析后的选择通过
`sakura.host.model_slots` 取得；不得直接读取 `user_root/config/api.yaml`，也不得把 Memory 数据或模型 cache
复制到 plugin-data。

`triggerTurns` 只允许整数 `1..50`；`backfillLimit` 读取并保留，不在当前声明式设置页编辑。整理模型引用
必须是已有 Provider profile 与 model 的成对选择；空选择动态继承当前对话模型，只有继承源也不可用时才跳过
自动整理，不影响本地管理、召回或聊天。

embedding 公开模型固定为 `sentence-transformers/all-MiniLM-L6-v2`，维度 384；实际工件固定为
`qdrant/all-MiniLM-L6-v2-onnx@5f1b8cd78bc4fb444dd171e59b18f3a3af89a079`，使用 FastEmbed 0.8.0 与
ONNX Runtime 1.28.0 的 `CPUExecutionProvider`。不得开放任意模型名、URL、revision 或缓存路径输入。
旧 PyTorch cache 不满足已安装状态。

Memory 内容、query、完整历史、Prompt、API key、cache 绝对路径和第三方异常原文不得进入插件状态、通用
Snapshot、日志或证据工件。公开错误只使用稳定 code、简短脱敏 message 和 retryable 标记。

## 4. 协商、贡献与通用接口

Memory 不再协商 `assistant.memory`。只有客户端协商 `assistant.plugins-v1` 后，Core 才创建 PluginApplication
并加载 enabled 的 `sakura.memory.mem0` 进程；未协商时不打开 Memory 存储、不创建 plugin-data，也不暴露插件
设置请求。插件 Tool 是 `assistant.plugins-v1` 的普通 contribution；`assistant.tools-v1` 独立控制 Core-owned
工具与其设置面。当前产品拓扑同时协商两者；仅未协商 `assistant.tools-v1` 时不得无条件注入
`get_current_time` 或因此改变模型请求形态。

桌面和测试只使用通用接口：

- `plugins.settings.get/save/action`
- `plugins.collection.query/create/update/delete`
- `settings.provider_model.get/save` 中的动态 `model_slots`
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
| `memory_remember` | 仅在用户明确要求或信息明显长期有用时，保存通过既有敏感内容校验的记忆 |
| `memory_update` | 先取得准确 `memory_id`，仅用于用户纠正、补充、合并或明显过时的当前角色记忆 |
| `memory_forget` | 仅在用户明确要求时幂等删除当前角色记忆 |

工具随插件 root Effect 注册和撤销。停用、reload 或插件进程退出后旧 callback handle 必须失效；恢复
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

两段 content 与事件总大小服从 Plugin Runtime 的通用上限。事件不提供 History Store、Memory cursor 或整理
方法。插件核对角色后读取既有 `ChatHistoryStore`，按 `triggerTurns` 串行整理；未选整理模型时跳过。事件
Handler 失败、超时或插件进程退出不能改变已经确定的聊天 terminal。

整理失败保留既有 Memory 和可重试状态，不提前提交游标。正常退出先关闭整理与模型任务；卡死由插件
cleanup deadline 终止其受控后代进程。新 generation 只从已原子提交的状态恢复，迟到结果不得写入新
generation 或其他角色。

单个自动整理任务最多发出两次真实 Provider HTTP 请求，包含正常整理和 JSON 格式修复在内；计数必须在
网络调用前消耗，超时和传输失败也不得绕过。任务用尽两次请求仍未成功，或收到确定无效的响应结构后，当前
插件 generation 必须打开自动整理请求保险丝，后续 Timeline 事件不得继续重放该区间；重新加载插件产生的
新 generation 可以从未提交游标重新尝试。该保险丝不得改变用户配置的 `triggerTurns`、既有 Memory 或
Timeline 内容。

整理 Prompt 必须以当前角色人格卡作为身份、关系边界和称呼方式的唯一依据；没有明确称呼时使用中性“用户”，
不得由通用任务说明引入主从、亲属、恋爱、朋友或搭档等关系。自动整理同时区分普通用户事实与有明确双向证据的
共同记忆：共同制定并得到后续反馈、共同解决问题、双方确认的约定、反复形成的互动习惯或用户主动分享事件后续，
可以整理为 `episodic/shared_experience`；角色单方面表达陪伴、一次屏幕观察或只发生在用户一方的事实不得改写成
共同经历。已有记忆使用无依据称呼时，后续整理应保留事实并改回符合当前人格的中性称呼。

## 6. 记忆 Surface、插件设置与 Collection

左侧“记忆”入口常驻。插件通过 `sakura.host.settings` 注册 `surface=memory` 的
`memory_management` section；该 section 只包含 `memories` Collection，宿主在“记忆”页面统一呈现搜索、
筛选、新增、编辑和删除。插件详情页不得重复渲染该 Collection，只提供“前往记忆页管理”入口。没有 active
Memory surface 时，页面显示状态说明和“前往插件页”入口，不恢复 `memory.*` 专用协议。

设置窗口早于 Mem0 插件进程完成初始化时，“记忆”页必须在可见期间用普通有界 timer 重新读取通用插件
Snapshot，并在 Memory surface 可用后原地更新，不要求关闭并重开设置。内容相同的 Snapshot 不得清空
Collection 状态或重绘页面；离开“记忆”页或得到稳定的 `disabled/active/failed` 结果后停止读取。

通用“插件”页面从 `plugins.settings.get` 展示 `sakura.memory.mem0` 的普通 `memory` section：

- section 标题旁的轻量 `status` 运行状态；正常时只显示“运行正常”，异常时展开影响和恢复说明；
- `triggerTurns` 整理间隔。

本地向量模型拆为只读 `memory_embedding_component` section，并投影到 `surface=about`，不在插件详情或
模型页重复显示。该资源卡合并固定 embedding 模型、安装状态、真实下载进度和当前可用 Action；
- 未安装显示 `downloadEmbedding`，下载中只显示 `cancelEmbedding`，失败或取消只显示
  `retryEmbedding`，已安装且空闲不显示操作。独立 `refreshStatus` 不再公开。

整理 Provider/模型不在插件详情中重复显示。Mem0 通过 `sakura.host.model_slots` 注册可选
`plugin:sakura.memory.mem0:curation` 槽位，统一显示在“模型 → 模型槽位”；保存仍写入插件私有
`curationProfileId/curationModel`。插件停用只隐藏槽位，不删除选择；重新启用后若引用已删除 Provider/模型，
页面显示“原选择不可用”并要求重新选择。该可选槽位显示“继承”控件；空选择表示动态继承当前对话模型，
对话模型不可用时才跳过自动整理，且始终不影响召回、聊天或手工 CRUD。

Collection 只公开 `content/layer/category/source/importance/confidence/updatedAt`，item identity 使用通用
`itemId`。layer 只允许 `core_profile/semantic/episodic/procedural/session`；内容上限 16384 字符；查询每页
最多 100 条，并同时受 256 KiB 通用 Collection payload 上限。未知字段、非法 cursor、跨角色记录和超界
响应稳定拒绝或不投影。

模型下载是插件 Settings Action，由插件内部线程执行固定 snapshot 下载。它属于带独立 Runtime 的本地资源，
不是远程 Chat Completion 模型槽位。Action 立即返回，插件页在任务运行期间自动读取 Snapshot，并把
`connecting/downloading/installing/completed` 映射为用户可读阶段；取消只影响当前 plugin generation
启动的任务。失败或取消保留旧完整 cache，
不得晋升 staging 或隐式更换模型。当前不提供 ZIP 导入；未来若恢复，必须由通用 artifact/插件 Action 组合
驱动，不能恢复 Memory 专用 Rust 文件选择 token 或 Bridge。记忆导出本次不实现；未来必须作为 Mem0
插件设置 Action，经通用 artifact/file-save 流程交付。

## 7. 生命周期与故障边界

- Plugin setup 的 Memory runtime、completed-chat Handler、Context、四个 Tool、两个 Settings section、
  Collection 和 model slot 全部绑定同一 LIFO cleanup 栈。setup 任一步失败必须整体反向回收，插件不能
  半激活。
- 启停、reload、安装、卸载以及返回 `restart_required` 的配置只在当前用户操作内局部处理目标插件及其硬依赖
  consumer。先使涉及的旧 Host contribution 和 callback handle 失效并反向清理，再按持久化 enabled 状态
  加载；无关插件、Memory owner 和重资源进程保持不动，不能重放旧 Handler。
- callback、Event、Service 或 cleanup 超时不重试原调用，也不自动重启或恢复。generation 正在
  quiesce/close 时不得再生成替代插件进程。
- 模型下载 cleanup 先发送取消并等待插件线程；无法协作结束时交由插件 cleanup deadline 终止，不允许
  daemon thread 越过 generation 继续写 cache。
- 插件 `disabled/failed`、进程不可用或显式 lifecycle 操作期间，普通聊天仍能在没有该 Contributor 与
  tools 的情况下完成。另一 Memory Contributor 的 Context 不受影响。
- 用户未明确执行 CRUD、配置保存或模型下载时，不得产生相应写入或网络访问。completed-chat 仅允许按既有
  整理语义更新 chat history/curation state 和最终记忆写入。

## 8. 验收门

自动验证至少覆盖：

- 官方 manifest 默认 enabled，并只依赖四个通用 Host Service；当前产品拓扑真实加载该插件。
- 未协商 `assistant.plugins-v1` 时不创建 Memory owner、不打开 Qdrant、不创建插件配置目录。
- 真实 `PluginRuntimeManager → Mem0 process → Host Service → callback → SakuraMem0Runtime.context(dict)` 重建完整
  `ContextRequest`，角色不一致 fail-closed。
- 两个不同 Memory Contributor 同时存在；一个抛错不影响另一个入选，Core/Prompt 不按 Memory 来源分支。
- Tool、Context、Settings、Collection、model slot 在 disable/re-enable/reload 的显式操作后完整
  撤销与恢复；停用时公开状态为 `disabled`，旧 Collection/callback 不可调用，无关插件 scope 不变。
- 设置早于插件初始化完成时，Memory surface 原地恢复；重复的相同插件 Snapshot 不触发页面重绘。
- 模型缺失、依赖导入、Qdrant/SQLite/锁冲突、损坏配置、回调超时和下载取消时聊天继续、v2 数据保持、
  无隐式网络访问。
- 在隔离 v2 根记录切换前后的 SHA-256/size：Qdrant、SQLite、core profiles 和已安装的固定
  FastEmbed/ONNX snapshot 在只读设置/搜索路径保持不变；completed chat 只允许当前 curation-state
  语义变化。
- 正常退出、插件停用、reload、插件调用/cleanup timeout、Core crash 后线程、callback、Effect、pipe、文件锁与后代
  进程有界归零。
- Frontend、Rust、Python focused tests，以及 `runtime-v2-memory-tests` 与当前产品 smoke journey 通过；
  无法本地执行的平台/真实模型门明确记录风险。

所有测试只能写隔离临时根，不得以真实用户 Memory 数据、配置、日志或 cache 作为 fixture。

## 9. 非目标与回退

本 WP 不建设统一 Memory Service/Record DTO、Memory 专用 Bridge、权限系统、通用推理代理、跨 owner
事务框架、逐插件
进程、在线模型市场或任意下载器。它不自动扫描或迁移外部旧程序数据，也不修改 vendored Mem0 源码。

回退时先把官方插件 desired state 设为 disabled 并停止接收新调用，再 dispose 当前 Worker；超时终止当前
generation Worker/后代。可以恢复代码入口，但不得删除、回滚、重建、迁移或手工修复用户 Qdrant、SQLite、
Memory JSON、curation state、模型 cache、旧 YAML、插件配置或已完成聊天历史。
