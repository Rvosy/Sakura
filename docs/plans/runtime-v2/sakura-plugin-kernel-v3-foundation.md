---
kind: plan
status: active
audience: maintainer
source_of_truth: self
updated: 2026-08-20
---

# Sakura Plugin Kernel v3 Foundation 实施计划

## 1. 目标与当前状态

本计划把 [`ADR-0027`](../../adr/0027-thin-composable-plugin-kernel.md) 与
[`Plugin Kernel v3 Spec`](../../specs/runtime-v2/sakura-plugin-kernel-v3.md) 的 Freeze Candidate 转化为可验证
实现。当前状态为 `active`，但不修改 `work-packages.md` 的 `active_work_package`，也不把本计划伪装成
已经接受的新 Runtime v2 Work Package。

第一阶段只建立足以迁移 TTS、Memory 和未知能力的极薄组合内核，不建设插件治理平台。每一阶段必须先有
真实消费者，再增加 Host Service、Bridge 机制或声明式 UI 组件。

当前实现检查点：现有 generation 私有 Worker 已能并行承载 v2 回退路径与 v3 候选；v3 已实现本地
Service、Event/Transform、root EffectScope、Config、公开状态、依赖环/冲突诊断、动态启停和通用
`service.call`/`host.call`/`callback.invoke`/Transform Bridge。首批 `sakura.host.tools` 与
`sakura.host.context` 真实消费者已经接入，opaque callback handle 会随 generation、plugin 和 EffectScope
失效。Weather/Umbrella fixture 已通过真实 Worker 验证 `active → waiting → active`、新 Service 实例恢复、
setup 整体回收和 shutdown 超时终止。其余 `settings/character/audio/artifacts` Host Service、安装/设置表层及
TTS/Memory 迁移仍未完成，因此 ADR/Spec 继续保持 `proposed`/`draft`。

## 2. 实施顺序

### A. Plugin Kernel 与 Generic Bridge

- 在现有 generation 私有 Plugin Worker 内建立 Application Service Registry、Event/Transform、Effect、
  Config 和最小 Context API。
- 每次 setup 使用独立 root EffectScope，只有完整成功后才发布 active/依赖可用；异常、冲突或取消必须先
  回收整个 scope，禁止半激活插件。
- Event 与 Transform 使用独立注册表；Transform 输入只读并返回新值，Host DTO 使用 immutable/frozen
  形态。
- 用通用 lifecycle/status、Service/Host 调用、Event、Transform、Config 与 opaque callback handle 替代
  feature-specific worker 协议；保留 ADR-0016 的隔离、deadline、generation identity 和进程树清理。
- 固定 Bridge 三个调用方向：export 走 `service.call`、Worker 调 Host 走 `host.call`、Host 注册的 callable
  回调走 `callback.invoke`，禁止 export 同时生成 callback handle。
- Manifest 增加 `provides/requires/optional`，实现依赖图、cycle、Service 唯一性和五种公开状态。
- 只接入已有真实消费者需要的六个 Host Service；领域名不得进入 Bridge enum/router。

退出条件：Core control 在 Worker 卡死时仍可用；正常关闭、插件卸载和 Worker 重建后全部 Effect、callback、
pipe、thread、handle 和后代归零。

### B. Weather/Umbrella 未知能力证明

- Weather 提供 `com.example.weather` 并发送自己的事件。
- Umbrella required 依赖 Weather，验证 waiting/active、inject scope 和新 Service 实例恢复。
- 覆盖 setup 中途 conflict/异常的 root scope 回收、declared/runtime conflict、依赖环、Event/Transform
  注册表隔离、immutable Transform 失败、动态启停和 shutdown timeout。
- 加入架构门，证明 Core 与 Bridge 不引用 Weather 领域名或为它新增协议分支。

退出条件：两个 fixture 的安装、组合、禁用、恢复与删除完全通过通用机制；第三方作者可在约 30 行代码内
完成同类 Provider/Consumer。

### C. 插件管理、Config 与声明式设置

- 支持内置/用户目录扫描，以及安全的本地 ZIP/文件夹安装；代码与 plugin-data 分离。
- 插件管理页展示 disabled/waiting/active/failed/conflict、缺失依赖、可能提供者和冲突来源。
- 实现 config `applied/restart_required/error`，显式 reload 使用依赖级联生命周期。
- 将现有设置贡献迁至 `sakura.host.settings`，只实现字段、Action、状态、进度和受限 Collection。

退出条件：插件启停不要求 Core restart；故障插件 shutdown 卡死时 Worker 使用最新 desired state 重建；
设置保存状态与运行时应用状态不会混淆。

### D. TTS Hub、GPT-SoVITS 与 Genie

- 将 TTS Hub、GPT-SoVITS Provider、Genie Provider 拆成三个普通插件。
- Hub export `sakura.tts`，Provider 只通过 `registerProvider()` 接入；Core 删除具体 Provider factory 和 ID
  分支。
- `registerProvider()` 只返回 disposer，不冻结通用 `unregisterProvider()`；Hub 只选 Provider 并调用
  `provider.synthesize(request)`，不得读取或转交 Provider extension。
- 角色选择写入 `extensions["sakura.tts"]`，Provider 模型/参考音频写入自己的 extension；资源仅在
  `resolve_resource()` 时验证。
- 验证每个角色只使用显式选择的 Provider；故障不按安装顺序静默变声，未来 fallback 必须显式配置。
- 使用 `sakura.host.artifacts` 传递音频，继续由 `sakura.host.audio` 播放。

退出条件：删除 GPT-SoVITS 插件并安装 fixture Provider 后，聊天、合成和播放调用方无需修改；不同角色可
选择不同 Provider，且不依赖全局 mutable selection。

### E. Mem0 与可组合 Memory

- 把 Mem0、向量库、embedding、整理和管理 Collection 全部迁入官方 Mem0 插件。
- 移除公共 Memory Store/Search/Recall/Curation 假设；只保留 `sakura.host.*` 会话事实 Event 和 Context
  Contributor 连接点。
- 增加一个非向量 Memory fixture，与 Mem0 同时贡献上下文。
- Context 调度删除 Memory/Plugin 固定配额，保留 Host required facts、总预算和结构上限。

退出条件：两种不同存储模型可以同时工作；移除任一 Memory 插件不改变 Core 业务逻辑，普通聊天和另一
Memory 插件继续运行。

### F. v3 收口与状态评审

- 迁移所有内置 v2 插件，删除 `PluginCapabilityRegistry`、permission 白名单和 feature-specific 注册/RPC。
- 更新长期 Spec/ADR 和相关 Harness journeys；不把开发期 v2/v3 并存变成发布双轨。
- 运行相关 Python、Rust、frontend、Harness 和三平台生命周期验证。

退出条件：Weather/Umbrella、替代 TTS Provider、双 Memory、动态启停、Worker crash/recovery 和零残留
全部通过；随后单独评审 ADR-0027 为 `accepted`、Plugin Kernel v3 Spec 为 `normative`。

## 3. 非目标

- 不在本计划中建设权限、签名、OS sandbox、WASM、逐插件进程、多语言 SDK 或远程 Runtime。
- 不建设在线市场、自动更新、依赖下载或版本求解。
- 不开放自定义 HTML/JS/CSS、Graph UI 或通用前端插件 Runtime。
- 不增加 Session Service override、Service semver 或 Kernel Slot Registry。
- 不自动扫描或迁移旧程序 Memory、外部旧角色包和旧程序目录；后续由独立迁移模块处理。
- 不因本计划处于 `active` 改变当前 WP-4-06 或其他 Work Package 的执行状态。

## 4. 验证与架构门

- SDK 概念门：入门文档第一屏只出现 `provide/get/inject`、`on/emit`、`on_transform/transform`、`effect`
  与 `config`。
- Bridge 领域无关门：协议/router 不包含 TTS、Memory、Weather、Renderer 或 Provider 实现名。
- 官方同 API 门：GPT-SoVITS、Genie、Mem0 不获得第三方无法使用的注册或 Host 内部对象入口。
- 生命周期门：禁用、reload、依赖消失、Worker timeout、Core crash 和应用退出后资源归零。
- 数据门：插件代码、plugin-data、Character extension、artifact 和旧用户数据所有权互不混淆。
- UI 门：Collection 只实现当前真实消费者需要的有限能力，不演变为自定义前端 Runtime。

每个阶段先运行 focused tests，再按风险运行 `journey-plugins`、`journey-tts`、Memory/Core Host、
Runtime v2 Shell 和相关 Harness profile。完整矩阵仍由 CI 负责。

## 5. 回退

每阶段保持可独立回退：先更新 desired state 停止接收新插件调用，再正常 dispose 当前 Worker；超时只终止
当前 generation 的 Worker/后代。回退代码不得删除插件数据、Character extension、Memory 数据、用户安装
目录或 artifact 之外的用户文件。

在 v3 正式 cutover 前，当前 accepted Plugin API v2 仍是产品回退点。v3 候选失败时恢复 v2 Router 与设置
入口，并保留 v0.3 文档作为未采纳/待修订候选，不伪造 accepted/normative 状态。
