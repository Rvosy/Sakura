---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-08-30
---

# ADR-0033：Host 类型化交互时间线与轻量上下文构建

## 背景

Runtime v2 当前把聊天历史保存为只有 `role/content` 语义的 JSONL。定时或手动屏幕观察为了复用 Provider
协议，也会被构造成 `role=user`；一次 assistant generation 的多个 segment 又分别写成多条历史。进入
上下文时，现有路径分别受最近 24 条/40,000 字符、最近 8 条/每条 1,000 字符和动态事实 4,096 token
等固定限制。最终会同时发生两类问题：真实人类输入、Host 观察和主动触发被混成相同角色；大窗口模型又
无法使用已经存在的较长历史。

只扩大消息条数会放大错误语义。另一端的完整 Context Runtime 方案则会同时建立 TimelineStore、
ObservationStore、TurnAssembler、EpisodeBuilder、ContextBudgetPlanner、Retrieval Pipeline 和多套生命周期；
这些正式组件没有相应数量的当前消费者，也会与普通 Memory 插件已经拥有的整理、episodic layer 和检索
职责重复。

本决策需要同时满足：修复当前语义错误、明显放宽上下文、保持 Memory 可替换，并遵守 Sakura 个人维护、
低复杂度的工程边界。

## 候选方案

### 方案 A：只提高现有固定限制

把 8 条改成更多条、把 4,096 token 改成更大的常量。改动最小，但屏幕观察仍被归因为用户发言，assistant
segment 仍不是一个 generation，Memory 仍依赖 best-effort 正文事件；因此不采用。

### 方案 B：建立完整 Context Runtime

把观察、Turn、Episode、预算、检索和 Trace 分别建设为 Store、Manager、Policy 和扩展点。该方案能覆盖
更远的多模态和图记忆需求，但会在尚未建立召回基线前冻结大量抽象；因此不采用。

### 方案 C：Host 单表类型化 Timeline、轻量投影并复用现有 ContextPolicy

Host 用一个简单的 append-only SQLite 表保存四种交互语义；一个纯投影函数按 `turn_id` 重建近期上下文；
现有 `ContextPolicy` 接收根据模型窗口计算出的动态预算。Memory 继续是普通插件，只通过一个窄只读 Timeline
Host Service 增量消费已提交条目，并以普通 Context Contributor 回传少量事实。

## 决策

采用方案 C。

### 1. Timeline 是 Host 数据语义，不是插件或新 Runtime

- Host 是原始交互时间线的唯一写入者。数据库为 `data/chat_history/timeline.sqlite3`，第一版只有
  `timeline_entries` 一张业务表和必要索引。
- 条目只使用 `human / assistant / observation / system` 四种 `kind`；`origin` 单独记录 `chat`、
  `manual_screen`、`scheduled_screen`、`proactive` 等触发来源。Provider 最终使用何种 role 不反向改变
  Timeline 语义。
- 每个条目包含稳定 `entry_id`、`turn_id`、`character_id`、`created_at` 和有界 JSON payload。一次用户可见的
  assistant generation 只写一个条目，所有表现 segment 保存在该条目的 `segments[]`。
- 图片、音频、base64、临时路径和 generation resource token 不进入 Timeline。当前请求的图片和工具
  call/result 继续保持 Provider 原生结构；第一版不把工具循环建设成持久化事件平台。
- 捕获但未提交给对话模型的观察、内部判断和 NOOP 只进入既有 Agent Trace。成功语义分析且不早于两小时的
  定时观察可以作为 untrusted Host fact 参与上下文；它与同 Turn 的可见 assistant 回复原子选择，绝不投影成
  人类发言。过期、捕获占位和分析失败观察不进入候选。

`Interaction Timeline` 是这组数据不变量的名称，不引入拥有独立配置、生命周期或策略的 Timeline Runtime。
读取、投影和写入可以保留为少量模块与纯函数。

### 2. 上下文构建复用既有路径

- `assemble_recent_turns(entries)` 之类的轻量纯函数负责分组、过滤和按时间排序，不建立有状态
  `TurnAssembler` 服务。
- `ContextPolicy` 继续负责优先级、预算、选择和 drop reason，但总预算由模型窗口、输出预留、静态 Prompt、
  工具 schema 和当前输入动态计算；不建立独立 `ContextBudgetPlanner`。
- 最近 8 个真实对话 Turn 是保护尾部而不是历史上限。随后依次考虑最新近期观察、Context Contributor、
  其余两小时内观察和更早真实对话，最终按旧到新发送；观察 Turn 放不下时整体丢弃，不做请求路径摘要。
- 当前输入、当前图片和当前 tool call/result 不得静默裁剪。必需内容本身超过已配置窗口时明确失败；旧候选
  可以按类型使用已有摘要/引用或整 Turn 丢弃。
- Prompt 具体措辞、候选权重和位置微调属于可观测 A/B 参数，不升格为本 ADR 的固定算法。

### 3. Memory 继续是普通可组合插件

- 本决策不建立统一 Memory Record、Memory Layer 或 Episode 公共协议。`sakura.memory.mem0` 继续自行拥有
  core profile、semantic、episodic、procedural、session、向量库和整理策略。
- Host 新增窄的只读 `sakura.host.timeline` Service，只提供当前角色的 recent、read-since 和 latest-cursor。
  `sakura.host.chat.completed` 只通知 `character_id/turn_id/cursor`，不再复制聊天正文。
- Memory 保存自己的 opaque cursor；插件启动和下一次完成事件时补读遗漏区间。该机制不是 outbox、ack、
  自动重试或第二份 Timeline。
- Memory 产生的记录只需保存 `source_entry_ids` 等最小来源 metadata；不建设 Provenance Graph。
- FTS + vector + RRF、图数据库、实时主题检测和新的情绪状态机都不属于第一阶段。只有基线证明纯向量召回
  在精确词或排名上存在实际缺口时，才增加简单混合召回。

既有 legacy `VisualObservationStore` 不因本 ADR 自动删除，但 WP-4-07R 不依赖、不扩张也不把它提升为
新的架构边界；其存废由现有消费者单独决定。

### 4. 数据切换保持可恢复

现有每角色 JSONL 在一次事务中只读导入 Timeline；旧文件、archive 和 corrupt backup 原样保留，导入不
重写、不删除。只有完整导入和校验成功后 Runtime v2 才切换单写 SQLite；失败时继续使用当前 JSONL 行为并
明确报告。切换后不长期双写。代码回退不得删除 SQLite，即使旧版本暂时看不到切换后的新记录，数据仍需
保留供重新升级或显式迁移。

## 研究证据边界

- [LongMemEval](https://arxiv.org/abs/2410.10813) 支持更完整的 Turn、时间感知和更新/拒答评测，也显示超长
  上下文本身不会自动解决长期记忆；它不规定 Sakura 的 32K fallback、75% 输入目标或最近 8 Turn。
- [RMM](https://arxiv.org/abs/2503.08026) 与 [ES-Mem](https://arxiv.org/abs/2601.07582) 支持语义主题和事件
  分段的后续价值，但不要求首版建立在线 Episode Runtime。
- [Lost in the Middle](https://arxiv.org/abs/2307.03172) 只说明特定检索任务存在位置敏感，不能替代 Sakura
  自己的 Prompt A/B；因此具体排序不在本 ADR 冻结。
- [LD-Agent](https://aclanthology.org/2025.naacl-long.272/) 支持短期会话、长期事件和 persona 分层，但这些
  layer 继续属于 Memory 实现，不进入 Host Core。

## 与既有决策的关系

- 本 ADR 延续 [ADR-0027](0027-thin-composable-plugin-kernel.md)：Host 组装最终 Prompt，Memory 是普通插件。
- 本 ADR 不改变 [ADR-0014](0014-sakura-memory-manager-raw-vector-backend.md) 的 Mem0 raw backend 所有权。
- WP-4-07R accepted 后，它替代 ADR-0003 中“Runtime v2 继续把新聊天兼容写入旧 JSONL”的窄部分；旧数据
  原字节保留和不可破坏原则继续有效。
- Agent Trace 继续使用 WP-4L-02 的现有文件与 operation 生命周期；不创建 ContextTrace 数据库。

## 后果

收益是观察不再伪装成人类发言、近期屏幕活动能够延续到普通聊天、assistant generation 恢复原子性、
大窗口模型可以使用数万 token 的真实历史、Memory 能从可补读的来源提炼而不拥有第二份原始数据。插件
仍能像积木一样组合，因为 Host 只冻结通用时间线读取和 Context Contributor 边界。

代价是 Runtime v2 聊天数据会发生一次单向存储切换，旧版本不能自动看到切换后的新记录；SQLite schema、
导入和角色过滤需要聚焦测试。第一版有意不解决事件图、多跳图检索、完整双时间事实、自动 Episode 生命周期
或最优 Prompt 排序，这些能力必须由真实失败样本重新立项。
