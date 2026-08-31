---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-28
---

# Sakura Plugin Runtime v4

本规范是当前 Plugin Runtime 合同。生产 Runtime 只激活 API v4；逐插件进程、dependency root、
ServiceProxy 和官方/第三方同机制均为当前实现边界。[Plugin API v3](sakura-plugin-kernel-v3.md) 只保留为
cutover 前的历史参考。

## 1. 目标与不变量

Plugin Runtime v4 必须同时满足：

1. Sakura Core 只提供加载、组合、路由、生命周期、配置、数据和 Host 能力，不把可替换的默认策略实现固化
   在 Core。
2. 官方插件与第三方插件的唯一区别是分发来源。
3. 每个插件独立进程、独立 dependency root，共享一份基础 CPython、标准库和 uv cache。
4. Core 和插件消费者依赖 Service/Contribution 契约，不依赖提供者 ID、类或私有配置结构。
5. 进程位置对插件源码中的 Service 方法调用透明，但跨进程数据、deadline 和失败保持显式、有界。

以下内容不属于 v4：通用 CallbackRef/Remote Object、全局 async Plugin API、Worker Pool、兼容依赖自动
分组、多基础 Python 版本、环境自动修复、HealthMonitor/RetryPolicy/RecoveryScheduler、长期 v3/v4 双栈、
恶意插件沙箱、签名市场、远程或多语言 Runtime、Service 版本协商、Profile/Bundle/Patch 配置层和冲突自动
选主。

## 2. 最小 Core 与运行拓扑

与插件体系相关、不可再由插件实现的最小核只有：

- Tauri/Rust Shell、Python Core bootstrap 和受控进程树；
- Plugin inventory、安装状态与 `PluginRuntimeManager`；
- Service/Event 路由、Effect/Lifecycle、Config/Data primitives；
- Core、Rust、WebView 或系统设备真正拥有的 `sakura.host.*` 能力。

目标拓扑：

```text
Tauri Shell
    └── Python Core generation
          └── PluginRuntimeManager
                ├── Plugin A process + dependency root A
                ├── Plugin B process + dependency root B
                └── Plugin C process + dependency root C
```

一个插件 ID 在一个 generation 内最多有一个 active 进程。Plugin process、ServiceProxy、Effect、
artifact/resource token，以及仅供 Host Contribution 使用的 callback handle，全部绑定 generation 和插件
scope；旧 generation 或已退出插件的身份立即失效。

`PluginRuntimeManager` 只允许提供通用操作，例如启动/停止插件、路由 Service 方法、派发 Host Event、调用
Host Service、应用配置和撤销 scope。不得出现 `call_tts()`、`register_memory()`、Provider ID 白名单或其他
领域分支。

## 3. 插件包、Python 与 dependency root

v4 插件至少包含 `plugin.yaml` 和 Python entry，manifest 使用 `api: 4`。`provides/requires` 继续只表达
capability dependency；Python distribution dependency 单独通过 `pyproject.toml`、requirements 或锁文件
声明，并可选择携带 wheelhouse。最终依赖字段与文件优先级在第一个安装器实现切片中冻结，但必须满足以下
行为：

- 依赖只安装到目标插件的 staging dependency root；成功后才发布为该插件当前环境。
- 基础 Python Runtime、其他插件环境和用户已有插件代码不得被 pip/uv 改写。
- 普通启动只验证环境，不联网、不安装、不升级、不降级，也不自动切换到系统 Python。
- 只有用户发起安装、更新或重试时才允许解析和下载依赖。
- 同一个分发包在目标 CPython ABI 或平台没有可用 wheel 时明确失败，不尝试污染主 Runtime 作为回退。
- uv cache 可以共享下载文件并使用 hardlink/clone；每个插件的 import 可见集合仍然独立。

标准 venv 与 `uv pip --target` 都可以作为 dependency root 的内部实现候选。实现选择不得改变插件包、SDK、
进程启动和故障 DTO；PoC 必须覆盖 console scripts、native wheels、卸载和三平台路径后再冻结一种。

官方预装插件可以随发行包携带已解析环境或 wheelhouse，保证首次启动离线可用。普通第三方包不强制为每个
平台和 CPython ABI 携带完整 wheelhouse。

## 4. Plugin SDK 边界

插件进程的 import path 只包含：

```text
Python 标准库
+ Sakura Plugin SDK
+ 当前插件代码
+ 当前插件 dependency root
```

第三方插件以及完成迁移的官方插件不得导入 `app.*`、Core 私有 bootstrap 或其他插件的源码目录。需要的
宿主信息必须通过 `context` 和 `sakura.host.*` 取得；可复用的领域代码应搬入插件自身包或独立的公开库。

SDK 保留 v3 的核心形状：`get/provide/on/effect/config/data_path`。允许因跨进程而收紧参数、返回值和 cleanup
合同，但不把 RPC client、PID、pipe、模块名或进程地址暴露给插件作者。

插件不得从 `data_path()` 的物理位置反推 `user_root`。确需继续拥有现有共享用户数据的插件通过通用
`sakura.host.storage` 取得有界的 data/cache 目录 descriptor；当前角色及角色卡正文通过
`sakura.host.character.current()` 取得；Provider 目录、对话模型继承和调用凭据通过
`sakura.host.model_slots.catalog()/resolve()` 取得。三者对 bundled 与 user 插件使用同一合同，Generic
Runtime 不检查插件 ID，也不解释 Memory、TTS 等领域内容。插件私有配置和其他普通持久数据仍只使用
`config` 与 `data_path()`。

插件需要把异步准备失败写入统一运行日志时，只能通过 `sakura.host.diagnostics.emit()` 提交固定事件和
有界、无正文的诊断字段。当前 Host 仅接受已登记的 TTS service/weights 事件以及
`provider/reason_code/stage/status/error_type`；插件不得直接写 `sakura-runtime.log`，也不得提交路径、异常
message、traceback、角色文本或模型内容。Host 自动附加调用插件 ID，诊断通道失败不得改变插件业务行为。

## 5. ServiceProxy 与跨进程数据

`context.get("example.service")` 返回可调用已声明方法的对象。提供者在本进程时可以使用本地代理优化；提供者
在其他插件进程或 Core 时返回 `ServiceProxy`。调用方仍使用：

```python
service = context.get("example.service")
result = service.some_method({"value": 1})
```

位置透明只保证相同的方法名、参数合同、结果合同和稳定错误，不保证对象 identity、属性反射、共享内存、
零延迟或无限调用时间。每次调用必须具有 deadline；超时或连接失效绝不自动重放。

Manifest 只声明 `provides/requires` Service key，不声明方法表。插件在 setup 中调用
`context.provide(service_key, service, exports=...)`，`exports` 是唯一方法导出来源；Runtime 在 IPC 边界拒绝
未导出方法。不建设 IDL、Schema Registry 或 manifest/setup 双重一致性校验。

普通插件 Service 的参数和返回值只允许有界 JSON，以及 Host 签发的 artifact/resource descriptor。不得传递
callable、callback handle、真实 Python 对象、类、模块、generator、文件句柄、裸本地路径、pickle 或异常
对象。

opaque callback handle 只允许在插件向 `sakura.host.*` 注册现有 Tools、Context、Settings、model slots 等
Contribution 时，由 Host Service 的既有合同创建和消费。它不能作为普通插件 Service 参数或结果，不能由
Plugin A 转交 Plugin B，也不形成通用 CallbackRef 类型系统。

Manifest `requires` 表示启动和失败传播使用的硬依赖。已知固定依赖必须声明；从领域 JSON descriptor 取得的
Service key 可以由 `context.get()` 动态解析，但这种查找只返回当前 ServiceProxy 或明确缺失，不创建硬依赖、
后台重绑或恢复关系。

## 6. 能力组合与替换

### 6.1 替换型 Service

一个 Service key 同时只能有一个 active 提供者。`priority` 只用于稳定展示或启动排序，不能选择 Service
赢家。如果 desired state 同时启用两个提供者：

- 所有冲突参与者均不得发布该 Service；
- 状态明确报告 `SERVICE_CONFLICT` 和冲突 Service key；
- UI 可以让用户在一次明确确认中关闭旧实现并启用新实现，但不能静默改开关或 fallback。

TTS Hub 是典型替换型能力：Core 消费 `sakura.tts`，Provider 消费 Hub 契约；Core 和 UI 不得检查
`sakura_tts_hub`、Genie 或 GPT-SoVITS 的插件 ID 来决定语音是否可用。

### 6.2 TTS Provider 跨进程合同

TTS Provider 固定采用“Provider Service + JSON descriptor + `jobId`”，不保留其他并行方案。以 Genie 为例：

```yaml
provides:
  - sakura.tts.provider.genie
requires:
  - sakura.tts
```

Provider 在 setup 中提供 `sakura.tts.provider.genie`，唯一导出：

```text
status()
warmup(characterId)
begin(request) -> jobId
poll(jobId)
cancel(jobId)
```

然后调用 `sakura.tts.registerProvider(descriptor)` 登记普通 JSON：

```json
{
  "providerId": "sakura.tts.genie",
  "serviceKey": "sakura.tts.provider.genie",
  "label": "Genie"
}
```

Hub 只保存 descriptor，通过 `serviceKey` 动态取得当前 ServiceProxy，并以 `jobId` 驱动任务。正常 cleanup 时
Provider 调用 `sakura.tts.unregisterProvider(providerId, serviceKey)`；Provider 崩溃时 Hub 对该
`serviceKey` 的后续调用明确失败并把 Provider 呈现为不可用，不触发 Runtime 自动重启或重绑。

Hub 不保存 Python Provider 对象、Python Job 对象、callable、callback handle 或通用远端对象引用。
Generic Runtime 只执行普通 `service.call`，不理解 `providerId`、`jobId`、warmup 或合成状态。

### 6.3 可并存 Contribution

Tools、Context contributors、Timeline observers、Settings sections 和模型槽等通过 Host Service 或 Host Event
注册，可以由多个插件同时贡献。它们按现有 descriptor、Effect cleanup、数量和 payload 上限管理，不创建
一个强制唯一的总 Service。

Memory 默认采用 Contribution 组合。官方 Mem0 可同时提供 Timeline 消费、Context、Tools、Settings 和
model slot；替代插件可以提供相同或部分贡献。用户既可以关闭 Mem0 完整替换，也可以启用多个不同 Memory
插件共同工作。Runtime 不预设唯一 `sakura.memory` Store/Search/Recall 协议。

## 7. 官方默认插件

官方插件满足和第三方完全相同的运行合同：

| 项目 | 官方插件 | 第三方插件 |
|---|---|---|
| Plugin API / SDK | 相同 | 相同 |
| process runner / dependency root | 相同 | 相同 |
| Service / Event / Host Service | 相同 | 相同 |
| Effect、配置和数据目录 | 相同 | 相同 |
| `app.*` 私有导入 | 禁止 | 禁止 |
| Core/UI 根据实现 ID 特判 | 禁止 | 禁止 |
| 分发 | Sakura 预装或可选包 | 用户安装 |

`bundled` 可以让安装器拥有插件文件并禁止卸载，但不能隐含 privileged API。默认领域插件必须允许停用，以便
替代实现接管能力；插件关闭后保留文件用于恢复默认是允许的。

当前迁移范围中的预装默认插件为 `sakura_mem0`、`sakura_mobile`、`sakura_tts_hub`、`sakura_genie` 和
`sakura_gpt_sovits`。`playwright_browser` 改为可选插件，不进入主安装包。

## 8. 生命周期、失败与恢复

- Manager 按 capability dependency 拓扑启动插件；Python distribution dependency 只在安装阶段解析，两者
  不得混为一个全局求解器。
- `setup()` 完成并兑现 `provides` 后插件才进入 `active`。失败时撤销该插件全部 Service、Host Contribution
  callback 和 Effect。
- `PluginRuntimeManager` 不运行后台 reconcile、health loop、retry counter、自动重新激活或依赖恢复调度。
  插件状态只在 generation 启动，用户显式 install/update/enable/disable/reload/uninstall、显式设置保存，以及
  插件进程退出时变化。
- manifest `requires` 是硬依赖。Provider 进程退出或被停止时，Runtime 只标记 Provider `failed`、失效它的
  ServiceProxy，并停止声明该硬依赖的 consumer；动态查找该 Service 的插件和无关插件继续运行。
- 普通配置先在目标进程调用 `config.on_change()`：`applied` 保持进程；`restart_required` 只在本次用户操作
  内按硬依赖顺序停止 consumer、重启目标，再重启本次被停止且此前 active 的 consumer；`error` 明确失败。
  这是显式设置保存的同步步骤，不接收完整目标态 inventory，也不进入后台 reconcile。
- 插件调用超时、依赖安装失败、Service 冲突和进程崩溃均不自动重放、探测、重启、恢复 consumer 或静默选择
  替代实现。恢复只能由用户 reload、重新安装/重试或新 Core generation 触发。
- 正常停止先拒绝新调用并执行有界 LIFO cleanup；超时后只终止目标插件及其受控后代，不结束其他插件或
  扫描无关系统进程。

公开状态继续保持简单的 `disabled/active/failed`；具体原因通过稳定 `reasonCode` 和有界详情表达，不新增
waiting、self-healing 或复杂调和状态机。

## 9. 安装与发行

安装流程保持最小：校验包和 manifest、准备插件代码、解析目标插件依赖、构建 staging dependency root、
验证 entry import，最后发布代码与环境并更新 desired state。失败时保留旧版本和清晰错误，不自动修复或
反复重试。

主发行 Python 只包含 Core 真正需要的库、Plugin SDK 和安装工具。插件依赖不因官方插件预装而进入全局
`site-packages`。大型模型、浏览器、本地 TTS Runtime 等仍属于插件资源，不因为 dependency root 而自动
塞进主 Python 环境。

依赖隔离的体积收益是缩小并稳定 Core Runtime 的依赖闭包。五个官方插件及其 Python 依赖仍随完整安装包
交付，因此总下载体积不保证大幅下降；Playwright 可选化会直接减少主安装包，未来增删插件也不再改变 Core
依赖集合。

发行集合和两根存储所有权见[发行与存储合同](release-distribution-and-storage.md)。预装插件的已解析环境位于
只读的 `distribution_root/plugins/dependencies/<plugin-id>/`；普通用户插件环境位于可写的
`user_root/data/plugin-runtime/dependencies/<plugin-id>/`。两者使用同一声明 fingerprint 与 Python ABI marker，
Runner 接收的仍只是当前插件自己的 dependency root。

## 10. 迁移与验收门

v4 至少通过以下门后才能替代 v3：

1. 两个测试插件分别依赖同一库的不兼容版本，并能在同一 generation 同时 active、各自报告正确版本。
2. `context.get()` 跨两个插件进程完成成功、错误、超时、旧 generation 和 provider crash 验证；没有真实
   Python 对象或 `app.*` 穿过边界。
3. 启用两个同 Service 提供者时没有隐式赢家；用户明确切换后既有 consumer 无需修改即可使用替代实现。
4. 把与预装内容相同的官方插件包放入隔离测试根，经普通插件安装入口安装后，使用普通 Runner 和 dependency
   root 正常运行；没有 builtin 私有 API、Core plugin-ID 分支或预装到主 Runtime 的隐藏依赖。临时移出
   `plugins/builtin/` 只是可选测试手段，不规定永久仓库目录名。
5. 使用不同插件 ID 的替代 TTS Hub 时，Core 播放链和 UI 只按 `sakura.tts` 工作；Genie/GPT-SoVITS 使用
   `providerId/serviceKey/label` descriptor 与 `jobId` 协议，不传 Provider/Job 对象或 callback。
6. 关闭官方 Mem0 后，替代 Memory 插件能贡献 Context/Tools/Settings；两个 Memory contribution 插件也能
   同时工作。
7. 基础发行 Runtime 不再包含 Mem0、Playwright、Genie 或 GPT-SoVITS 仅需的 Python distributions；五个
   官方默认插件离线可用，Playwright 通过可选安装取得。
8. 单插件 reload、crash、cleanup 超时和卸载不会改变无关插件 PID/scope，也不会触碰其他 dependency root
   或用户数据。
9. `PluginRuntimeManager` 和通用 IPC 中不存在 Memory、TTS、ASR、Emotion、Playwright 等领域分支，也
   不存在后台 reconcile、HealthMonitor、RetryPolicy 或 RecoveryScheduler。
10. 每迁移一个官方插件，都审查并删除仅因“共享 Plugin Worker”存在的旧隔离、桥接和兼容代码。没有新的
    真实故障边界需求时，不保留“Plugin Process 内再套同用途 Isolated Process”的双重隔离。

实现必须先迁移一条真实纵向切片验证 Runner、ServiceProxy、依赖根和安装失败，再逐个迁移官方插件；不得先
复制出一套长期并存的完整 v3/v4 管理界面或自动治理层。
