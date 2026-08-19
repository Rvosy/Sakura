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
setup 整体回收和 shutdown 超时终止。`sakura.host.settings` 已接入现有声明式字段/Action 页面，v3 Config
会区分 `applied/restart_required/error`，并支持同 generation 动态启停和显式插件 reload。
`sakura.host.character` 与 `sakura.host.artifacts` 已按 plugin/generation scope 接入；Core 已能在授权的 TTS
segment 内一次性消费音频 artifact，并继续拥有 recording 与 Rust opaque playback。官方 `sakura.tts` Hub
检查点也已接入，fixture Provider 已证明角色级显式选择、动态注销和“不可用时不静默换声线”。真实
GPT-SoVITS 与 Genie Provider 的首个实现切片已接入：两者使用 scoped Character extension/resource、单
runtime coordinator、异步可取消 job 与明确 managed/custom ownership。Genie 的共享模型/参考状态严格
串行，ONNX 转换缓存位于受限 plugin-data，使用 staging、源模型 fingerprint 与完成标记原子提升。角色级
enabled/provider、copy-only 旧 TTS 投影和动态 Voice Provider 设置已经接入；Provider 配置通过
`surface=voice` 的普通声明式 section 展示，保存只要求目标插件 reload。旧 TTS factory 只作为紧接着的
原子 cutover 前兼容路径保留，本地安装、受限 Collection 及 Memory 迁移仍未完成，ADR/Spec 继续保持
`proposed`/`draft`。

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
- `host.call` 只允许 Worker dispatch owner thread 发起；普通 Service timeout 终止并重建 Worker，但不重试原
  调用。Artifact commit 将清理所有权转交 Core consumer，不能在 root scope 累积已完成 Effect。
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

当前检查点已完成 v3 字段/Action 注册、Config 应用状态、动态启停和显式 reload；本地 ZIP/文件夹安装与
受限 Collection 留给各自真实消费者检查点，不在 Settings Bridge 中预造文件安装或表格协议。

- 支持内置/用户目录扫描，以及安全的本地 ZIP/文件夹安装；代码与 plugin-data 分离。
- 插件管理页展示 disabled/waiting/active/failed/conflict、缺失依赖、可能提供者和冲突来源。
- 实现 config `applied/restart_required/error`，显式 reload 使用依赖级联生命周期。
- 将现有设置贡献迁至 `sakura.host.settings`，只实现字段、Action、状态、进度和受限 Collection。

退出条件：插件启停不要求 Core restart；故障插件 shutdown 卡死时 Worker 使用最新 desired state 重建；
设置保存状态与运行时应用状态不会混淆。

### D. TTS Hub、GPT-SoVITS 与 Genie

当前检查点已完成官方 `sakura.tts` Hub、角色 extension 选择、短 `begin/poll/cancel` job 和 committed audio
artifact 到 recording/playback 的 Core 消费链。延迟 fixture 已证明合成可超过单次 Bridge deadline 而不阻塞
Worker，两个并发 job 可独立取消，Provider disable 会清理 job、artifact 与 Effect。角色未配置 Hub extension
或 Hub 尚未安装时暂走 legacy TTS；角色已显式选择但 Provider 不可用时明确失败，不允许回退到 legacy
Provider。桌面现已用 operation identity 取消当前回复的全部在途/待执行 segment，Core 保留内部 job identity，
generation shutdown 也会先发出取消再等待 Router worker。GPT-SoVITS Provider 已证明严格串行切权重/
合成；Genie Provider 已证明严格串行角色模型/参考音频/合成、可取消且不晋升半成品的 ONNX 转换，以及
custom endpoint 不获得进程、端口、本地路径或状态切换所有权。两者停用都会清理 job/artifact/Effect 与
owned process tree。动态设置、角色选择和旧配置兼容投影已完成；下一检查点删除 legacy factory、warmup、
Provider-specific settings/bundle/test 分支，完成前不宣称 TTS cutover。

- 将 TTS Hub、GPT-SoVITS Provider、Genie Provider 拆成三个普通插件。
- Hub export `sakura.tts`，Provider 只通过 `registerProvider()` 接入；Core 删除具体 Provider factory 和 ID
  分支。
- `registerProvider()` 只返回 disposer，不冻结通用 `unregisterProvider()`；Hub 只选 Provider 并调用
  `provider.begin(request)`，耗时任务通过短 `poll/cancel` 驱动，不得读取或转交 Provider extension。
- 角色选择写入 `extensions["sakura.tts"]`，Provider 模型/参考音频写入自己的 extension；资源仅在
  `resolve_resource()` 时验证。
- 验证每个角色只使用显式选择的 Provider；故障不按安装顺序静默变声，未来 fallback 必须显式配置。
- 使用 `sakura.host.artifacts` 传递音频；Core 在已授权 segment 内消费 artifact，继续拥有 recording 与 Rust
  opaque playback，不向 Worker 暴露可绕过授权的路径播放入口。

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

- SDK 概念门：入门文档第一屏只出现 `provide/get/inject`、`on/emit`、`on_transform/transform`、`effect`、
  `config` 与受限插件私有 `data_path`。
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
