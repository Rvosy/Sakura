---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-03
---

# WP-4-01：Runtime v2 Memory 能力等价

## 1. 目标与范围

本规范定义 CAP-008 在 Runtime v2 的首个完整产品纵向切片：由 bundled Python Core 在当前 generation
内拥有真实 `MemoryStore`、检索注入和自动整理；Rust 只负责协商、窗口授权、请求 identity、deadline
和公开 DTO；现有 Tauri 设置窗口同步开放 Memory 设置与记忆管理。普通聊天是第一个真实消费者，设置
窗口是第二个真实消费者。

本 WP 必须交付：

- 按当前角色 scope 检索长期记忆并以私有 context fragment 注入聊天；检索、embedding、Qdrant、SQLite
  或整理模型失败时，聊天仍可完成且不伪造记忆命中。
- 手工搜索、新增、编辑和幂等删除记忆；常驻档案与四类向量记忆保持既有层语义。
- 完成回复后按冻结阈值异步整理兼容聊天历史；取消、失败或未完成回复不推进整理游标。
- Memory 配置、整理模型槽、本地 embedding 模型状态、ZIP 导入与显式下载的真实设置闭环；所有模型
  相关控件统一位于“模型”页面，“记忆”页面不建立第二套模型配置入口。
- 正常退出、Core 强杀、generation 重建、设置关窗和任务失败后的资源回收与数据兼容。

本规范不维护 Work Package 当前状态；唯一状态源是
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)。

## 2. 所有权与依赖边界

- `app/core_host/assistant_adapter.py` 在 initializer worker 内懒加载并构造当前 generation 唯一的无 Qt
  Memory owner。Memory、整理器和相关线程按创建反序幂等关闭，不建立第二 Python 进程或生命周期根。
- `AgentRuntime` 与 `MemoryRecallService` 保留 Memory 业务语义；`RealChatBoundary` 只协调已完成聊天与
  整理调度，不成为 Memory 或历史真相源。
- 禁止复用 `app/core/bootstrap.py`、`AppContext`、`app/ui/tauri_settings.py`、
  `MemoryCurationWorker` 或任何 PySide6 对象。Core hello、health、shutdown 和未协商 Memory 的启动路径
  必须保持无 Qt。
- Rust/WebView 不解析 Qdrant、SQLite、mem0 或 Memory 文本文件；不缓存可写 Memory 副本。旧
  generation 的 response/event/task handle 一律失效。
- vendored mem0 和现有依赖锁不在本 WP 修改。若现有库不能满足三平台门，应停止并重新审查依赖，不能
  在本 WP 静默升级或替换。

## 3. 数据与配置契约

Python `MemoryStore` 是以下数据的唯一运行时所有者：

| 数据 | 行为 |
|---|---|
| `data/memory/qdrant/**` | 既有本地 Qdrant collection；只允许持有生产共享应用锁的当前 Core 打开 |
| `data/memory/mem0_history.db` | 既有 mem0 SQLite history；由库事务管理，Rust 不解析或修复 |
| `data/memory/core_profiles.json` | UTF-8 JSON mapping；同目录原子写，保留其他角色 scope |
| `data/memory_curation_state.json` | 保留 `processed_history_count`、`pending_turns`、`backfill_completed`；原子写 |
| `data/memory.json` | 未确认的历史文件；本 WP 保留字节，不导入、不写、不删除 |
| embedding cache | 继续使用 `MemoryStore` 解析的既有缓存根；不把绝对路径发布到 WebView/Snapshot |

所有自动测试只写隔离临时根。真实应用只允许用户明确触发的 Memory CRUD、模型导入/下载、配置保存和
完成回复后的整理写入；验收必须生成允许集合及 SHA-256/size/mtime manifest。未来配置 schema、损坏
JSON/YAML、路径逃逸、外部库不兼容或锁冲突在首次写入前进入稳定只读/失败状态，禁止自动删除
Qdrant `.lock`、重建 collection、清库或从 `memory.json` 猜测迁移。

设置字段冻结为：

- `memory_curation.trigger_turns`：整数 `1..50`；`backfill_limit` 读取并原样保留，不在 UI 编辑；
  `enabled` 保持既有产品语义，不新增伪开关。
- `api.yaml.model_slots.memory_curation`：可选 `{profile_id, model}`，引用规则、密钥保留和原子保存复用
  Provider/模型领域契约；未配置时跳过自动整理，不影响检索和聊天。
- embedding 模型固定为当前 `sentence-transformers/all-MiniLM-L6-v2` 与 384 dimensions。本 WP 不新增
  任意模型名、任意下载 URL 或任意缓存路径输入。

Memory 内容、query、完整聊天历史、Prompt、API key、embedding cache 绝对路径和外部存储内部错误不得
进入 capability manifest、通用 Snapshot、普通日志或证据工件。公开错误只含稳定 code、简短 message、
retryable 和无敏感 details。

## 4. 协商能力与协议字段

Memory 是可选协商能力 `assistant.memory`。未协商时 Core 不打开 Memory 外部存储，Rust 不注册 Memory
命令，设置 feature 保持 `unavailable`。不为本能力新增 transport、stdout writer 或公共协议 major。

Core 请求名和 payload 精确冻结如下；所有请求继续使用现有 generation credential 和 envelope：

| 请求 | payload | 成功结果要点 |
|---|---|---|
| `memory.search` | `query: string`、`limit: 1..120`、可选 `layer` | `status`、`message`、`memories[]` |
| `memory.upsert` | 可选 `id`，以及 `content/layer/category/source/importance/confidence` | `status`、`memory` |
| `memory.delete` | `id` | `status`、`deletedId`、`alreadyMissing` |
| `memory.settings.get` | 空 object | curation 字段、模型槽公开 DTO、embedding 状态 |
| `memory.settings.save` | `triggerTurns`、可选 `curationModelSlot` | 保存结果与 `changePlan` |
| `memory.model.import` | Rust 选择器签发的 opaque `selectionToken` | Rust 签发 `taskId`，不返回裸路径 |
| `memory.model.download` | 空 object | Rust 签发 `taskId`；只有用户点击才允许网络访问 |
| `memory.model.cancel` | Rust 签发的 opaque `taskHandle` | `accepted`、`taskId` |

Memory record 公开字段仅为 `id/content/layer/category/importance/confidence/source/scope/createdAt/updatedAt/
lastAccessedAt/score`；未知字段不透传。`scope` 必须等于当前角色 ID，WebView 不能指定其他 scope。
`layer` 只允许 `core_profile/semantic/episodic/procedural/session`。文本和数组均设有界大小；Rust 在送入
Core 前再次验证。Memory 管理命令只授权设置窗口，聊天 WebView 不能调用；selection token 与 task
handle 由 Rust 签发、绑定设置 window generation 和 Core generation，不能伪造路径或跨代重放。

模型任务事件只允许 `memory.model.started/progress/completed/failed/cancelled`，每个 `taskId` 恰有一个
terminal。事件只含 task ID、阶段、受界整数进度和脱敏错误；任务 identity、取消和进度是 WP-4-01
Memory 域的窄契约，不提取为 WP-4-02 的通用 Operation/Action ID。

Memory 状态只允许 `ready/loading/degraded/read_only/failed/stopped`。检索响应在非 `ready` 时必须返回
空命中；聊天在其内部 `serviceStatus.memory` 记录状态但仍继续 Provider 请求。该状态不加入当前通用
Snapshot；设置窗口通过专用读取获得。

## 5. 聊天检索与整理语义

每轮聊天最多执行一次相关记忆检索。query 由当前用户输入和受界近期用户上下文构造；最多选 5 条，
去重、过期过滤和相关性阈值沿用 `MemoryRecallService`。记忆 fragment 标为 private，不能回显来源 ID
或进入日志。初始化中、锁冲突、模型缺失、Qdrant/SQLite 失败、超时或格式异常均返回空 fragment 与
降级状态，不能阻止 `chat.started`、Provider 请求或唯一 terminal。

只有 `chat.completed` 且兼容 history append 已落盘后，才把本轮计入整理阈值。达到阈值后，在当前
generation 的有界 Memory thread group 中串行整理；同一角色最多一个活动整理任务。整理失败保留现有
Memory 和可重试游标，不改变已完成回复；Core shutdown/强杀取消任务，新 generation 从已原子提交的
游标恢复，不重复处理已提交区间。角色 scope、人格 prompt 与模型槽在任务创建时冻结，迟到结果不得写入
新 generation 或其他角色。

手工 CRUD 与自动整理可以并发到达，但必须由同一 Memory owner 串行化写事务或安全拒绝；不得产生
半写、丢失其他 scope 或重复 terminal。敏感内容检测继续阻止自动写入；设置页手工写入必须显示明确
用户动作，且不能绕过 scope、长度和 schema 校验。

## 6. 设置 feature

本 WP 开放四个 feature key：

- `memory.manage`：搜索、筛选、新增、编辑、删除和刷新；
- `memory.curation`：读取/保存 `trigger_turns`，显示整理可用性与降级状态；
- `memory.embedding_model`：状态、固定模型 ZIP 导入、显式下载和取消；
- `model.memory_curation_slot`：选择既有 Provider/模型，未配置时明确显示自动整理停用。

页面归属冻结如下：

- “模型”页面统一承载 `model.memory_curation_slot` 与 `memory.embedding_model`，和聊天、视觉模型设置
  处于同一模型配置入口；Memory 领域仍分别拥有整理槽保存和 embedding 任务，不建立跨域“保存全部”。
- “记忆”页面只显示 `memory.curation` 的自动整理轮次，以及 `memory.manage` 的状态、搜索、筛选、列表和
  编辑器；不得显示整理 Provider、整理模型、embedding 状态、导入、下载或取消控件。
- 页面布局以迁移前既有设置页面为视觉和信息层级基线：状态与筛选保持紧凑，记忆列表和编辑器保持稳定
  双栏；窄窗口允许有界换行或滚动，但不得把状态徽标挤成逐字竖列、遮挡整理轮次或压缩主要编辑区。

Memory section 只有在真实 Core 协商成功且公开读取闭环可用时为 `available`；外部存储只读时 section 为
`read_only`，CRUD/保存/模型动作禁用但已有记录仍可查看；Core 断开或未迁移时为 `unavailable`。未知
feature 继续失败安全禁用。

WebView 只持有草稿、筛选、选中项和进度显示。get/validate/save/运行态生效由 Python Memory 域拥有；
Rust 负责设置窗口授权和 generation identity。保存 `trigger_turns` 成功后立即用于后续完成轮次；模型槽
变更返回 `core_restart_required` 并由现有 Supervisor 受控重建，失败时旧文件、旧 Core 和当前草稿保持。
不建立跨 Provider、Memory、外观或其他设置域的“保存全部”事务。

Core 受控重建或意外更换 generation 时，已打开的设置窗口必须原位重新绑定新 Core identity，不关闭、
重建或要求用户重新打开窗口。重绑定期间保存、搜索、CRUD 和模型动作必须稳定禁用或排队；新 generation
完成 `memory.settings.get` 后自动恢复可用并刷新服务端数据。筛选、选中项、编辑草稿及中文/日文 IME
composition 保留；未提交内容不得自动保存、提交或清空。旧 generation 的 response、deadline、Router
关闭和 identity mismatch 只能结束旧请求，不得清空已有列表、覆盖编辑草稿、显示为当前数据错误或触发
自动重发。若重绑定失败，页面显示稳定可重试状态并保留已有可读内容与草稿。

## 7. 故障矩阵与验收

自动门至少覆盖：

- 有/无命中检索、层过滤、去重、过期、scope 隔离和记忆注入；Memory loading/failed/锁冲突/损坏
  存储/embedding 缺失时聊天仍完成且不自动重发。
- CRUD 校验、敏感内容、并发 CRUD/整理、幂等删除、未知字段、未来/损坏配置、只读目录、磁盘满、
  原子 replace 中断以及旧 generation response/event/handle 丢弃。
- 自动整理阈值、仅完成回复计数、Provider/格式/写回失败、取消与 Core 强杀、游标恢复和不重复整理。
- Qdrant、SQLite、embedding loader/download/import、thread、waiter、文件锁、pipe 和临时目录在正常退出、
  关窗、取消、Core crash、Shell exit 后有界回收；共享应用锁立即重获。
- ZIP 路径逃逸、symlink、超大文件、错误模型/dimensions、下载超时/断流/校验失败保留旧缓存；不允许
  隐式联网。
- legacy headless reference oracle → Runtime v2 → reference oracle 往返；除预声明 Memory/配置写入外
  fixture manifest 不变，`memory.json` 字节不变；同一 SHA 三平台 locked workflow 通过。
- capability、窗口权限、IME composition、草稿保持、重复点击、关窗/Core crash/重启和重新打开状态
  一致；日志、Snapshot 与证据 secret scan 为零。
- 整理槽保存触发 Core restart 后，原设置窗口自动取得新 generation，并可继续搜索、新增、编辑、删除
  和保存；旧代迟到成功、`REQUEST_DEADLINE_EXCEEDED`、`GENERATION_INVALIDATED` 与
  `SETTINGS_CORE_GENERATION_MISMATCH` 不覆盖新代列表、状态或草稿，也不要求关闭重开设置。
- “模型”页包含整理模型槽与固定 embedding 模型任务，“记忆”页只含整理轮次和 Memory 管理；在
  900×800、1080×900 和 1520×787 视口下保持可读、可操作且主要列表/编辑器不被模型控件挤压。

Windows 人工验收必须直接启动当前 Runtime v2 EXE，在隔离验收根完成：创建含中文/日文 IME 的记忆、
搜索/编辑/删除、用确定性或负责人配置的 Provider 验证命中影响下一轮聊天、触发一次整理、导入模型失败
恢复、Core 强杀恢复和退出零残留。负责人还须审查同一候选 SHA 的 Windows/macOS/Linux 数据与资源
证据。Legacy Qt 可见 UI 不属于验收对象。

## 8. 明确非目标与回退

本 WP 不开放 Memory tools，不实现 `memory_remember` 等模型自主工具调用；它们随 WP-4-02 的真实
ToolRegistry/Action ID 冻结。不建设通用 Operation、任务图、优先级、resource token、下载平台、MCP、
插件、TTS、截图、主动互动、角色切换或首次设置；不修改 Legacy Qt 产品入口或删除旧实现。

回退顺序：先把四个 feature 恢复为 `unavailable` 并停止接受新 Memory/模型任务；取消当前 generation
的整理和模型任务，退出 Core 并确认资源与锁已释放；回退 Memory Gateway、Core owner 和前端接线，
恢复 `DisabledMemory` 降级聊天。不得删除、回滚、重建、迁移或手工修复用户 Qdrant/SQLite、Memory
JSON、embedding cache、配置或已完成聊天历史；已兼容写入的数据继续由冻结 headless oracle 读取。
