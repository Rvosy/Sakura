---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-08-28
---

# ADR-0037：官方功能作为可替换默认插件，并隔离每个插件的 Python 运行环境

> Sakura Core 提供组合机制，不提供不可替换的领域实现；官方功能只是随 Sakura 分发的默认插件。
>
> 架构用于提供能力边界，保险丝用于保护用户和进程；除此之外，让错误直接发生、直接暴露、由用户显式恢复。

## 背景

Plugin API v3 已建立具名 Service、Host Service、Effect、配置和 generation 私有 Worker。它允许关闭官方
插件，也允许第三方提供 Sakura 事先不知道的能力，但仍有两个根本限制：

1. 一个 Core generation 内的全部插件运行在同一个 Python Worker，共享 `sys.path`、`sys.modules` 和
   `site-packages`。两个插件依赖同一个库的不同版本时，无法同时满足。
2. Worker 内的 Service Registry 保存真实 Python 对象。官方 TTS Hub 与 Provider 可以直接交换对象，部分
   官方插件也继续导入 `app.*` 私有模块。这些行为不能跨逐插件进程工作，也让官方实现获得第三方插件没有的
   隐性特权。

依赖冲突不只是安装体验问题。如果第三方替代 Memory 必须迁就官方 Mem0 的 Qdrant、ONNXRuntime 或
Pydantic 版本，那么 API 层虽然允许替换，依赖层仍然由官方实现决定。为了实现 Cordis 式“依赖能力而不是
依赖实现”，运行隔离、依赖所有权和默认实现可替换必须作为同一个决策处理。

## 候选方案

### 方案 A：保留单 Worker，并改进共享依赖安装器

插件声明 requirements，Sakura 把缺失依赖安装到一个共享目录，并识别 pip 冲突。实现成本最低，也能改善
缺包提示，但同一解释器最终只能导入一个版本，无法让真正冲突的插件同时运行。

### 方案 B：主程序统一大型依赖版本

把 PyTorch、ONNXRuntime、Pydantic 等依赖固定在主 Runtime，插件只使用宿主版本。安装包可控，但官方
依赖会继续扩大主程序体积，也把插件自由度限制在 Sakura 选择的全局版本集合中。

### 方案 C：每个插件独立进程和依赖根，共享基础 CPython

Sakura 只分发一份基础 CPython、标准库、Plugin SDK 和 Core 必需依赖。每个插件进程只加载自己的代码和
dependency root；相同版本可由 uv cache 的 hardlink/clone 去重，不同版本互不影响。跨插件 Service 通过
通用路由和对象式代理调用。

### 方案 D：同时建设容器、WASM、多语言 SDK 和完整插件市场

隔离更强，但会把当前真实问题扩张为签名、权限、远程 Runtime、版本协商和市场治理平台，不符合 Sakura
当前的维护规模。

## 决策

采用方案 C，并保持以下边界。

### 1. 官方插件只有分发特权，没有运行特权

- 官方插件和第三方插件使用相同的 Plugin API、Runner、Service/Event/Host Service、Effect、配置、数据
  目录和 dependency root 规则。
- `bundled` 只表示由 Sakura 安装器和更新器分发。官方插件可以不可卸载，但默认实现必须允许停用。
- Core、Rust 和 WebView 不得根据 Mem0、Genie、GPT-SoVITS 等实现 ID 改变产品行为、开放私有 API 或
  解释插件私有配置。
- Core 的领域消费者可以理解稳定能力契约，例如 TTS 播放方可以消费 `sakura.tts`；Plugin Runtime Manager
  只能理解 Service、Event、Lifecycle、Config、Effect 和 Host 调用，不得增加 TTS、Memory、ASR 等分支。

### 2. 替换型能力与贡献型能力分开组合

- 替换型能力使用唯一 Service，例如 `sakura.tts`。同一个 Service 出现多个启用提供者时不按 priority、
  安装顺序或健康状态选择赢家；冲突参与者明确失败，由用户关闭旧实现并启用新实现。
- 贡献型能力通过 Host Service 和 Host Event 登记，可以同时存在多个提供者。只有插件向 `sakura.host.*`
  注册现有 Contribution 时可以使用 scope 绑定的 callback handle；普通插件 Service 之间不传递 callback。
  Memory 可以分别贡献 Timeline 消费、Context、Tools 和 Settings，不强制收缩为唯一 `sakura.memory`。
- 任何默认实现的替换不得要求修改 Core。关闭官方实现、通过普通入口安装替代插件并启用后，既有消费者
  只通过能力契约继续工作。
- TTS Provider 固定为独立 Service：Provider 向 Hub 登记只含 `providerId/serviceKey/label` 的 JSON
  descriptor，Hub 通过 `serviceKey` 调用 `status/warmup/begin/poll/cancel`，合成任务只跨边界传 `jobId`。
  Hub 不保存 Provider/Job 对象，不接受 Provider callback，也不要求 Generic Runtime 理解 TTS。

### 3. 每插件拥有独立进程和 Python 依赖

- 每个 active 插件拥有一个 generation 私有进程，以及仅属于该插件的 dependency root。
- 插件进程共享 Sakura 分发的一份基础 CPython 和标准库，但彼此不共享可导入的 `site-packages`。
- uv 下载缓存可以跨插件物理去重；缓存命中不改变逻辑隔离，也不能把依赖暴露为全局可导入模块。
- dependency root 的物理实现可以在首个实现切片中比较标准 venv 与 `uv pip --target`；无论选择哪一种，
  对 Plugin API 和安装包格式呈现的都是同一“插件私有依赖根”合同。
- 进程隔离用于依赖、故障和可终止性，不是恶意代码安全沙箱。可信本地插件仍以当前用户权限运行。

### 4. SDK 是插件唯一 Python 宿主边界

- 第三方插件只能依赖轻量 Plugin SDK、Host Services 和自己的依赖，不得导入 Sakura 的任意 `app.*` 私有
  模块。
- 完成 v4 迁移的官方插件遵守同一规则。现有官方插件对 `app.*` 的导入是迁移债务，不是 builtin 特权。
- `context.get(service_key)` 对作者继续返回对象式接口；远端提供者对应 `ServiceProxy`，方法调用由 Runtime
  路由。插件不直接操作进程 ID、RPC client 或远端模块。
- Manifest 只声明 `provides/requires` Service key；`context.provide(..., exports=...)` 是唯一方法导出来源。
  不在 manifest 重复方法表，也不建设 IDL 或 Schema Registry。
- 普通插件 Service 的参数和返回值只允许有界 JSON 和 Host 管理的 resource/artifact descriptor，不得携带
  callable 或 callback handle。opaque callback handle 只沿用现有“插件向 `sakura.host.*` 注册
  Contribution”的路径，不能在插件之间转发。真实 Python 对象、模块、类、裸路径、pickle 和异常对象不能
  穿过边界。

### 5. 插件包不强制携带完整 wheelhouse

- 开发目录和普通第三方包可以声明 `pyproject.toml`、requirements 或锁文件，由 Sakura 内置 uv 安装到
  插件私有 dependency root。
- wheelhouse 是官方预装、离线包或作者主动选择的交付形式，不是所有 `.sakplugin` 的强制组成。
- 安装或用户显式重试可以联网；普通启动不得静默安装、升级、降级或修复依赖。
- 依赖解析、平台 wheel 缺失和安装失败必须明确归因于目标插件，不得改写基础 Runtime 或其他插件环境。

### 6. 生命周期只响应明确事件

- `PluginRuntimeManager` 不运行后台 reconcile、health loop、retry counter、自动重新激活或依赖恢复调度。
  插件状态只在 generation 启动、用户显式 install/update/enable/disable/reload/uninstall、显式设置保存，
  以及插件进程退出时发生变化。
- manifest `requires` 只表达硬依赖。Provider 失败时失效其 ServiceProxy，并停止声明该硬依赖的 consumer；
  动态查找该 Service 的其他插件不被隐式停止、重绑或恢复。
- 普通设置先调用目标插件的 `config.on_change()`。`applied` 不重启进程；`restart_required` 只在当前用户操作
  内按硬依赖顺序停止 consumer、重启目标，再重启本次被停止且此前 active 的 consumer；`error` 明确返回。
  这是一次显式操作的同步步骤，不读取完整目标态 inventory；任何一步失败就停在失败状态，不进入后台调和。
- 插件崩溃后只执行“标记失败、失效代理、停止硬依赖 consumer”。不定时探测、不自动重启、不自动恢复
  consumer，也不重放之前的调用。

## 与既有决策的关系

本 ADR 延续 [ADR-0027](0027-thin-composable-plugin-kernel.md) 的薄 Kernel、具名 Service、Effect 和领域无知
原则。它保留 [ADR-0032](0032-runtime-hot-application-and-local-plugin-lifecycle.md) 的用户可见结果：普通设置
热应用、无关插件和重资源不重启；不承诺保留 v3 的完整 inventory、reload ID 或
`lifecycle.reconcile` 实现。

本 ADR 替代 [ADR-0016](0016-runtime-v2-generation-private-plugin-worker.md)、ADR-0027 的 v3 Worker
实现、ADR-0032 的 `lifecycle.reconcile` 实现，以及 ADR-0005/0011/0014 中由 Core 或 Memory 专属子进程
拥有 Memory 的边界；不会推翻 ADR-0001 的唯一进程树所有权、ADR-0023/0024 的音频所有权，或 ADR-0033
的 Host Timeline/Context 边界。Plugin API v3 只保留为 cutover 前的历史合同，当前 Runtime 只激活 API v4。

## 后果

收益是插件可同时使用冲突版本，移除官方插件不再要求修改 Core requirements，第三方替代实现也不会被官方
依赖间接限制。Provider 崩溃可以隔离到自己的进程和显式消费者，插件作者仍使用熟悉的对象式 Service API。
这会缩小并稳定 Core Runtime 的依赖闭包；五个官方插件仍完整预装，因此完整安装包体积是否下降取决于预装
集合，明确减少的部分主要来自 Playwright 可选化，而不是“依赖隔离”本身。

代价是插件间调用都成为有 deadline、序列化和失败可能的本机 RPC；官方插件必须搬出 `app.*` 私有实现，
TTS 等当前传递真实对象的协议必须改为 JSON descriptor、Service 方法和 `jobId`。逐插件进程会增加少量
常驻内存和进程管理成本，安装器也需要维护插件私有依赖根与共享 uv cache。

本决策不引入 Service semver negotiation、通用 CallbackRef/Remote Object、全局 async Plugin API、Worker
Pool、兼容依赖自动分组、多基础 Python 版本、自动赢家选择、环境自动修复、Profile/Bundle/Patch layer、
多语言或远程 Runtime、OS sandbox、在线市场和 v3/v4 长期双栈。遇到依赖或 Service 冲突时优先明确失败并
让用户选择，不建设自动调和。
