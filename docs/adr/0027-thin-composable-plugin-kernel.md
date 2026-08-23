---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
partially_superseded_by: 0029-coarse-plugin-worker-lifecycle
updated: 2026-08-21
---

# ADR-0027：Sakura 使用极薄的可组合插件内核

> 2026-08-23：transform、Session scope、动态 reconcile、sticky/conflict 传播和局部 reload 已由
> [ADR-0029](0029-coarse-plugin-worker-lifecycle.md) supersede。Service 组合与 generation 私有 Worker
> 边界继续有效。

## 背景

Sakura 当前 Plugin API v2 已能通过 generation 私有 worker 加载 Python 插件，并开放 Tool、Prompt
Patch、Context Provider、Event 和声明式设置等贡献。它解决了 Runtime v2 的进程隔离与可终止性，
但扩展能力仍由 `PluginCapabilityRegistry`、权限常量和 feature-specific RPC 预先枚举。每增加 TTS、
Memory、ASR、Emotion 或未来未知能力，都需要 Sakura Core 先理解这种能力并增加专门注册入口。

TTS 也已有 Provider Registry，但 GPT-SoVITS 与 Genie 的 factory、设置和运行时仍由 Host 写死；
Character Core Model 直接理解 GPT/SoVITS 模型字段。Memory 则把向量存储、检索、整理和固定 DTO
当作公共领域边界。这些实现无法满足“删除或替换具体实现时 Core 业务逻辑不变”的目标。

本决策以“**Sakura 是桌宠能力平台，而不是写死功能的集合**”为原则，但不把第一阶段扩张成权限、
沙箱、多语言 SDK、多 Runtime 和完整插件市场组成的治理平台。判断一个机制是否进入第一阶段的标准是：
没有它，第三方是否无法编写和组合当前真实需要的 TTS、Memory 或未知能力插件。

## 候选方案

### 方案 A：继续扩展 feature-specific Plugin API

沿用 `register_tool()`、`register_renderer()`、`register_context_provider()` 等模式，并为 TTS、Memory、
ASR、Emotion 继续增加专用入口。该方案容易接入现有消费者，但插件能做什么仍取决于 Sakura 是否预先
设计了对应 capability，不能形成开放的插件组合生态。

### 方案 B：首期建设完整插件运行与治理平台

同时引入四级 Context、Service 版本协商、Slot、权限、签名、OS sandbox、WASM、逐插件 Worker、
统一 Sidecar/Remote Runtime 和自定义前端模块。该方案覆盖更远的市场化需求，但会迫使第一个真实插件
先理解和实现大量当前没有消费者的机制。

### 方案 C：保留现有隔离底座，建立极薄组合内核

继续使用 ADR-0016 已验证的 generation 私有 Plugin Worker，在 Worker 内提供具名 Service、事件与
转换 Hook、可逆 Effect 和简单 Config。插件之间优先本地组合；只有 Core/Worker 边界上的真实调用才
经过不理解领域名称的通用 Bridge。

## 决策

采用方案 C。

### 极薄内核

Plugin Kernel 第一阶段只提供以下开发者概念：

- `provide/get/inject`：提供、获取和响应式接入具名 Service；
- `on/emit` 与 `on_transform/transform`：监听或发送事实通知，并注册或执行顺序数据转换；
- `effect`：注册可逆副作用并在插件、依赖或 Worker 生命周期结束时清理；
- `config`：读取、原子保存并监听插件配置变化。

Service key 是普通命名空间字符串，例如 `sakura.tts` 或 `com.example.weather`。第一阶段不做 Service
major version、semver negotiation 或 Kernel Slot Registry。同一 Application Context 内一个 key 只能
绑定一个 Service；多 Provider 由对应 Service 自己管理。

第一阶段所有 Service 都是 Application-scoped。Session 只承载当前角色、会话数据、请求状态与生命周期，
可以消费 Application Service，但不能提供、覆盖或 shadow Service。单次请求继续使用既有 request ID、
deadline 和取消信号，不建立 Operation Context。

Core generation 直接拥有 `PluginApplicationHost`、Worker、Host Services、inventory 与 desired state；
Assistant Session 不是 Plugin Worker owner。`ctx.on_session(setup)` 只创建随 bind/unbind 清理的 child
EffectScope，因此 Assistant 初始化失败时插件管理、Settings 与模型槽位仍可用，Session 切换也不会重建
Application Service。

### Worker 与通用 Bridge

保留 ADR-0016 的以下不变量：

- Core 主解释器不导入或执行第三方插件代码；
- 每个 Core generation 最多拥有一个可终止的私有 Plugin Worker；
- 插件阻塞、崩溃或 shutdown 卡死不能阻塞 Core health、cancel 和 shutdown；
- Worker 及其后代继续归 Rust Supervisor 的唯一桌面进程树所有。

Bridge 只承载 lifecycle/status、Service/Host 调用、Event、Transform、Config 和 opaque callback handle 等
通用机制，不枚举 TTS、Memory、Weather、Renderer 或具体 Provider。Worker 内普通 Service 是本地 Python
对象；只有显式 export 的方法才能由 Core 通过通用 Service 调用。

显式 export 的 Service 方法只通过 `service.call(service_key, method, args)` 调用，不产生 callback handle。
跨桥 callback 只能在插件把 callable 注册给 Host Service 时产生 opaque handle。handle 必须绑定当前
generation、Plugin 与 Effect，
插件卸载或 Effect 结束时立即失效，并受有界序列化、调用方 deadline 和重建约束。Generic Bridge 不增加
通用 cancel frame；需要取消的领域 Service 自行定义 cancellable job。Bridge 不允许模块名、函数名、
pickle、任意反射或裸 callable 穿透。

真正实现在 Core、Rust、WebView 或系统设备的能力作为 `sakura.host.*` Service Proxy 注入 Worker。第一
阶段仅冻结已有真实消费者的 `context`、`tools`、`settings` 基础能力、`model_slots`、`character` 和
`artifacts`。音频录制与播放由 Core TTS consumer 持有，不承诺 `sakura.host.audio`。
`sakura.host.*` 同时是 Host Event 保留命名空间。用户消息、角色变化和 Session 开始/结束等由 Host 确认的
事实必须使用该命名空间，普通插件不得伪造；插件自己的事实事件继续使用自己的命名空间。

### 信任、依赖和 UI

第一阶段采用可信本地插件模型。插件是用户主动安装、以当前账户权限执行的 Python 代码；不保留不能阻止
直接 OS 调用的 permission 白名单，也不宣称存在 OS sandbox。未来公开市场或陌生作者一键安装需要新的
ADR 冻结信任、签名、隔离与升级模型。

Manifest 的 `provides/requires/optional` 是加载、依赖诊断和冲突预检元数据，不是访问控制。插件仍可
创造 Sakura 未知的 Service；稳定对外提供的 Service 应声明在 `provides` 中，运行时 `provide()` 始终
执行唯一性检查。

稳定设置能力只开放宿主渲染的基础字段、Action、状态和单 section load/save，不加载插件 HTML、JavaScript、
CSS 或任意前端 Runtime。Collection 与 surface 作为显式 `-v0` experimental Host 扩展继续服务当前
Memory/Voice 消费者，但不随 Kernel Core 一起冻结。

### TTS、Memory 与 Character

TTS Hub、GPT-SoVITS Provider 和 Genie Provider 成为三个普通插件。Hub 提供并 export `sakura.tts`；
Provider 通过 Hub 的普通 `registerProvider()` 注册并由 Effect 注销。Hub 按请求的 `character_id` 读取
`extensions["sakura.tts"]` 选择 Provider，不维护隐藏的全局 mutable Provider 选择。具体 Provider 只读取
自己的 Character extension；Hub 只调用选中的 `provider.begin(request)` 并通过短 `poll/cancel` job 驱动，
不读取或转交 Provider extension。耗时合成属于 Provider 后台工作，Generic Bridge 不扩张为并发 RPC
Runtime。第一阶段每个角色只选择一个 Provider；合成失败必须显式返回，不得按安装
顺序或健康状态静默切换声线。未来 fallback 只能作为 TTS Hub 的显式角色配置引入。TTS 继续遵守
ADR-0023/0024 的合成、播放、Endpoint 和进程所有权边界。

Memory 不获得统一 Store/Search/Recall/Curation 公共协议。Memory 插件自行决定向量、图谱、SQLite、
时间线或总结实现，通过 `sakura.host.*` 会话事实事件观察输入，并向 `sakura.host.context` 注册 Context
Contributor。Host
继续拥有最终 Prompt 组装权，但不再按 Memory/Plugin 身份预切固定配额；普通插件贡献使用同一调度规则，
Host required facts 与结构性 payload 上限保留。

### 激活完整性

每次插件 setup 都先创建独立 root EffectScope。`provide()`、Handler、inject child scope 与自有 Effect 全部
归入该 root scope；setup 完整成功后插件才进入 `active`，并向其他插件发布依赖可用通知。setup 中任何
异常、Service 冲突或取消，以及 active 后发生的 runtime Service conflict，都必须先完整 dispose root
scope，再进入与原因对应的 `failed`、`conflict`、`waiting` 或 `disabled`，不得留下半激活插件。

Event Handler 与 Transform Handler 使用不同注册表。`on()` 只注册事实 Event，`on_transform()` 只注册
数据转换；`emit()` 不会触发 Transform Handler。Transform 输入必须视为只读并返回新值，不支持原地修改；
Host Transform DTO 应使用 immutable/frozen 形态，使 Handler 抛错时可以继续使用上一个有效值而无需通用
deep copy。

Character extension 对 Kernel 是 opaque JSON。Kernel 只负责大小、JSON compatibility、插件 ID 隔离和
原样读写；插件需要使用资源时调用 `sakura.host.character.resolve_resource()`，由该操作验证相对路径仍在
角色包内。Kernel 不通过字段名猜测 extension 中哪些字符串是路径。

## 与既有决策的关系

本 ADR **部分替代 ADR-0016**：保留 generation 私有 Worker、可终止性、Core 不导入插件和进程树所有权；
替代其中 permission 校验以及 tool/prompt/context/event/settings 等 feature-specific 私有协议，目标协议
改为通用机制 Bridge。Runtime v2 已完成 Plugin API v3 cutover，只激活 v3 manifest；其他 API 版本只投影为
不受支持的诊断状态，不再进入旧 Capability Registry 或 feature-specific RPC。WP-4-04 的历史实现与验收
事实不改写。

本 ADR 不推翻 ADR-0023/0024。TTS Provider 与 Endpoint/Managed Runtime 继续分离，合成、录音和 Rust 默认
设备播放所有权不变；只把 Provider factory、配置和运行时所有权从 Host 硬编码迁到普通插件。

## 后果

收益是插件可以创造 Sakura 未知的 Service 并与其他插件组合，官方实现必须使用相同 API；Kernel 不再因
新增能力持续增长 `register_xxx()` 和专用 RPC。插件作者第一阶段不需要理解 Slot、权限图、Service 版本、
多 Runtime 或跨语言协议。

代价是第一阶段不提供恶意插件隔离、Service 版本治理、Session Service override、自定义前端、在线市场或
依赖自动安装。可信插件仍可访问当前账户资源；未声明的运行时 Service 冲突只能在第二个 `provide()` 时
发现并隔离对应插件。

Weather/Umbrella 未知能力、TTS 替代 Provider、双 Memory Contributor、本地 ZIP/文件夹安装和本地故障门曾
形成候选验收证据。候选 `000d3483aaeed616114ac7ade5f4c0a2bc3f9312` 的
[Test run 32364807958](https://github.com/Rvosy/Sakura/actions/runs/32364807958) 全绿，
[Runtime v2 platform foundation run 32364807962](https://github.com/Rvosy/Sakura/actions/runs/32364807962)
attempt 2 的 Windows、macOS、Linux 结果保留为历史证据。ADR 的极薄组合内核方向仍接受为 `accepted`；
这些历史运行不再表述为“当前实现已经完整符合”。实现成熟度、未闭合验收门与重新冻结状态由拆分 Spec 和
active 实施计划记录。
