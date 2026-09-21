---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-09-20
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

Core 的生产装配与运行时应用是同一个对象，不再通过 `application.application` 或透明属性转发访问服务。
会话、关闭状态和资源清理由该对象统一持有。插件目录快照也由它持有，视觉目录、角色选择和普通状态查询
复用该快照；安装、卸载、重载、启停设置及明确读取插件设置时刷新。服务是否仍可用继续按当前 scope 判断，
目录快照不延长旧服务或资源的寿命。

`PluginRuntimeManager` 只允许提供通用操作，例如启动/停止插件、路由 Service 方法、派发 Host Event、调用
Host Service、应用配置和撤销 scope。不得出现 `call_tts()`、`register_memory()`、Provider ID 白名单或其他
领域分支。

插件清单还可声明静态 `ttsResources`，供角色包导入和工坊查询兼容格式。它不进入服务启动依赖，
不会改变引擎选择；字段与状态见[角色包插件需求](visual-plugin-boundary.md#角色包插件需求)。

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
- 未指定软件包源时，插件安装、源码依赖准备和本地发行 staging 默认使用阿里云 PyPI 镜像。
  `UV_*` 源配置、uv 配置文件和插件 requirements 中的源声明优先；没有 uv 源配置时，沿用显式的
  `PIP_INDEX_URL`。不改写锁文件、直接下载 URL 或 uv 的索引选择策略，也不混用多个默认镜像。
  海外 GitHub Actions 打包任务显式使用官方 PyPI。镜像缺包或不可用时明确报错，用户可指定其他源后重试。

标准 venv 与 `uv pip --target` 都可以作为 dependency root 的内部实现候选。实现选择不得改变插件包、SDK、
进程启动和故障 DTO；PoC 必须覆盖 console scripts、native wheels、卸载和三平台路径后再冻结一种。

官方预装插件可以随发行包携带已解析环境或 wheelhouse，保证首次启动离线可用。普通第三方包不强制为每个
平台和 CPython ABI 携带完整 wheelhouse。

### 3.1 展示分类与图标

Manifest 可以声明 `presentation: {kind, category, icon}`。`kind` 为 `extension`（功能扩展）、`provider`（功能引擎）
或 `infrastructure`（系统组件）；`category` 为 `model/voice/memory/visual/tools/connectivity/other`。
未声明、类型不符或未知的值逐字段回退为 `extension/other`，不影响插件的加载资格。

Inventory 将归一化后的分类放入公开 Plugin Settings Snapshot。Rust 和 WebView 接受省略 `presentation` 的旧快照，
并直接使用宿主投影的展示元信息。分类只用于分组、标签和筛选，不进入启动 IPC，也不参与依赖、服务选择、权限或业务判断。
前端不得根据插件 ID、作者或依赖猜测分类；安装来源仍由安装记录决定。

内置 Assistant、OpenAI 兼容模型接入、语音输入/输出 Hub 和 MCP 属于系统组件，默认折叠。
分类按用户的管理需要划分：用户直接选择和更换的语音、渲染引擎仍属于功能引擎，记忆、联网、手机连接等仍属于功能扩展。
内置身份本身不决定分类；系统组件仍可展开管理，其贡献的侧栏功能页和设置区块照常显示。

`icon` 从 Sakura 随包提供的 Lucide 图标中选择，例如 `brain`、`smartphone`、`audio-lines`。
Manifest 名称须匹配 `[a-z][a-z0-9-]{0,63}`；缺失或格式不符时 Inventory 输出空字符串，不影响加载资格。
快照中的图标允许为空；前端对空值及本地目录未收录的名称按领域回退，系统组件默认使用 `layers`。
插件只提供名称，颜色、尺寸、线宽和动效由 Sakura 统一控制，不接收图标 URL、路径或 SVG 代码。
本地目录与使用方式见 [Lucide 资源说明](../../../desktop/frontend/assets/lucide/README.md)。

### 3.2 表现能力声明

Manifest 可通过 `visuals` 声明资源类型、领域合同版本、Service 和渲染/编辑模块入口。
它与展示用的 `presentation` 分开，参与资源匹配，并随 Inventory、RuntimePluginSpec 和 PluginSpec 保留。
静态发现不执行插件代码；资源说明、专属解析、普通/主动回复、播放与工坊编辑均通过该合同接入。
预构建 JavaScript 来自可信插件安装目录，角色包中的脚本不能作为模块执行；模块与回调随绑定/进程 scope 失效。
字段、错误和实施边界见[表现插件合同](visual-plugin-boundary.md)。

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

SDK 保留 v3 的核心形状：`get/provide/on/effect/config/data_path`，增加显式进程绑定 `bind`。允许因跨进程而收紧参数、返回值和 cleanup
合同，但不把 RPC client、PID、pipe、模块名或进程地址暴露给插件作者。

SDK 提供不依赖 Core 的 `sakura_http.urlopen_direct_for_loopback` 和 `proxy_for_url`。内置插件的 HTTP API
与资源下载使用前者；每次请求、重试和重定向读取当前代理，本地回环直连，已开始的传输不换连接。
其他 HTTP 客户端可通过后者取得单次请求的代理。插件自带的外部程序不受 Python SDK 接管；宿主启动
`uv` 依赖下载任务时将当时的系统代理传入该子进程，运行中的安装任务保留启动时的配置。

插件不得从 `data_path()` 的物理位置反推 `user_root`。确需继续拥有现有共享用户数据的插件通过通用
`sakura.host.storage` 取得有界的 data/cache 目录 descriptor；当前角色及角色卡正文通过
`sakura.host.character.current()` 取得；Provider 公开目录、对话模型继承和三字段模型引用通过
`sakura.host.model_slots.v2.catalog()/resolve()/active()` 取得。默认 Assistant 读取 Core 发布的 session.modelSlots 快照，
并绑定所选 Model Service 实例，避免设置应用失败后隐式读取新值。推理消费者通过 SDK `ModelClient` 调用服务，
凭据仅由模型提供方保管；模型网络请求不经过 Core 专用客户端。上述能力对 bundled 与 user 插件使用同一合同，Generic
Runtime 不检查插件 ID，也不解释 Memory、TTS 等领域内容。插件私有配置和其他普通持久数据仍只使用
`config` 与 `data_path()`。

普通插件可通过 `sakura.host.screen` 获取受控截图，通过 `sakura.host.chat` 请求当前会话互动，
通过 `sakura.host.visual` 提交当前表现目标的控制。截图句柄、聊天操作和表现回执均绑定调用实例；
实例退出后撤销资源和过期结果。宿主只负责资源与受理，具体采样、提示词和触发策略归普通插件。
接口见[主动屏幕感知](WP-4-07-proactive-reminders-todos.md)与[表现插件合同](visual-plugin-boundary.md)。

### 4.1 统一宿主日志

主程序、Core、WebView 和插件共用 Rust `RuntimeLogService`。日志服务在插件系统之前启动，
在插件进程退出后刷新关闭；它不是可启停的插件。Python SDK 与 Core bridge 只负责提交，
不得打开日志文件，也不得在断连时回退到 Python 文件 writer 或独立 GUI 缓冲。

插件通过 `context.get("sakura.host.logging")` 获取 `PluginLogger`，manifest 可在 `requires` 中声明该宿主能力：

```python
logger = context.get("sakura.host.logging")
logger.info("资源加载完成", fields={"elapsed_ms": 320})
logger.error("连接失败", fields={"reason_code": "CONNECT_TIMEOUT"})
```

`debug/info/warning/error(message, *, fields=None)` 接受自定义文本与有界 JSON 字段，返回本地队列是否接收，
不代表已落盘。SDK 队列上限 128 条，每批最多 8 条，后台使用现有 Service RPC 发送；队列满优先保留
warning/error，传输失败不改变业务结果，后续批次报告丢弃数量。退出时在 effect 清理后最多等待 300 ms，
来不及发送的记录可丢弃。初始化、业务调用和清理期间都可使用；过期进程和已失效 generation 的请求被拒绝。

Core 从当前 RPC 调用上下文绑定插件身份，不信任插件传入的 ID；同一 generation 内重载也校验实际进程。
插件消息与宿主自定义消息使用同一 `LogEvent` 和 `SAKURA_RUNTIME_LOG_V1` bridge，增加可选 `custom` 与
`plugin_id`、`plugin_name` 字段，名称由宿主从 manifest 获取。自定义事件规范化为 `runtime.message`；已登记事件仍按固定目录投影。
Core 补齐交互关联编号，Rust 注入本次运行、generation 与 Core PID。插件不能选择文件路径或目标来源。

消息最多 1024 UTF-8 字节，字段字符串最多 256 字节；字段最多 3 层、嵌套集合最多 8 项。
顶层字段不设 8 项限制，共享含根对象的 32 项总遍历预算；普通字段连同截断标记的编码预算为 1800 字节。
`record_truncated` 不消耗字段遍历额度，避免 SDK、Core 和宿主重复清洗时继续挤掉诊断字段。
原始错误使用独立预算：`diagnostic` 4096 字符，异常链和调用栈各 8192 字符。超限明确标记 `truncated`，桥接整行连同前缀和换行最多 32 KiB。
凭据、敏感内容字段和绝对路径在传出插件进程前清洗，Rust 在文件与 UI 投影前再次清洗。
自定义文本不是私密 Trace：插件作者不得主动记录对话正文、模型内容、环境变量或原始异常对象，
文本清洗无法保证识别任意私密内容。UI 按纯文本显示，复制使用同一份清洗结果。

Rust 依据可信来源分流：插件主动记录写入 `sakura-plugins.log`，宿主/Core/WebView 写入
`sakura-runtime.log`；宿主记录单个插件的加载、停止、启动失败和异常退出时也附带可信插件身份，写入插件日志；应用级汇总仍写入运行日志。两者共用队列、序号、写入线程、
文本格式化与轮转实现，仅文件状态独立；默认各 10 MiB、5 个备份。窗口以同一快照提供插件筛选。

`sakura.host.diagnostics.emit()` 保留原调用格式，转交 `sakura.host.logging` 同一条日志链。
插件自行提供事件名和业务字段，事件名保存在消息和 `fields.event` 中；宿主不维护 Provider 名称、事件或源文件白名单，也不重解释耗时等业务字段。
调用方身份由宿主绑定，诊断采用通用日志的大小限制、凭据清洗和插件文件归属。
宿主日志适配层按 manifest 的 `sakura.tts` / `sakura.tts.provider.*` 声明将语音插件自定义记录归入 TTS，
插件名称用于筛选项和日志行展示，插件 ID 保留在详情和复制文本中。
ASR Hub 和语音输入 Provider 的记录归入“插件”页，按各自插件名称筛选，同样写入 `sakura-plugins.log`。
Mem0 的旧初始化 JSONL 停止追加，原文件保留，新诊断主动接入宿主日志。
插件进程 stderr（包括 runner 重定向的 stdout）由 Core 持续、有界读取并清洗后记录，按 info 显示为“插件诊断输出”，不因输出通道而计入问题数。下载进度和第三方提示保留原文；明确的警告、调用失败与异常退出由 SDK 或宿主对应事件报告。不拦截标准 `logging` 配置。Agent Trace 的实现保持独立。
SDK 的 warning/error 和兼容诊断入口在异常处理期间自动附加原文及调用栈。Service RPC error 通过可选 `diagnostics` 保留跨插件异常链，宿主再次清洗。Mem0 事件直接保留在插件日志中，不额外生成一条宿主业务记录。
插件启动失败在转换为状态码前提取原始异常，启动失败日志保留依赖检查或初始化阶段的异常链与调用栈。事件回调、清理回调和宿主资源回收失败也记录诊断，后续回调及资源回收继续执行。关闭期间允许插件注销自己已登记的宿主资源，随后由宿主完成剩余资源回收。

同一 Effect 的显式 disposer 和 context 关闭共用一次性清理，不重复释放资源。多个清理失败以原生异常组保留；
setup 和清理同时失败时，RPC 诊断同时包含主错误及清理错误，即使插件日志桥已关闭也可由宿主读取。
协作清理超时或返回错误后，宿主记录该错误并继续回收受控进程树；确认进程退出与清理回调成功分别表示。

关闭 generation 时，正在清理的插件进程还可调用其 `requires` 中声明且仍存活的插件服务，以完成 Provider 向 Hub 注销等清理。调用方必须是当前登记的关闭进程，目标仍须 active 且绑定同一进程；外部调用、旧进程身份和已停止的依赖继续拒绝。嵌套服务调用受剩余的统一关闭期限约束，不延长 generation 的退出预算。

插件 RPC 保留底层异常的 `cause_code` 和 `validation_field`，外层 `PLUGIN_CALL_FAILED` 协议码保持不变。只接受错误码和字段名，不序列化任意异常 details 或字段值；这些元数据沿用凭据清洗规则。


### 4.2 内置及随附插件的日志分级

默认记录有用的状态变化，不按定时器频率记录状态快照。`info` 用于启动、就绪、实际配置变化和有产出的后台操作；
`warning` 用于可恢复失败、请求未受理及资源清理异常；`error` 用于初始化、业务请求或后台任务失败。
正常取消不算错误。轮询成功、连接活动、缓存检查、缓存命中和重复进度使用 `debug`，或不记录。
不通过全局限流隐藏真实错误；同一任务的终态只在实际消费时记录一次。

| 插件 | 默认保留 | 默认不显示 |
| --- | --- | --- |
| TTS Hub | 提供方注册/移除、请求未受理、合成失败及请求编号 | 状态查询、运行中任务轮询、重复查询已消费终态 |
| ASR Hub | 引擎登记/移除、选择变化、识别路由及终态 | 可用性查询、状态查询、重复轮询 |
| SenseVoice | 模型安装/加载、识别开始及终态、取消、退出 | 音量帧、下载进度、状态查询和重复轮询 |
| Genie | 服务启动/就绪、模型就绪、转换开始/完成/失败、预热失败、配置变化 | 模型检查、缓存命中/复用、转换进度 |
| GPT-SoVITS | 服务及权重生命周期、预热失败、配置变化 | 状态查询和任务轮询 |
| Mem0 | 初始化开始/就绪/失败、有实际变更的整理结果、整理失败、停止自动重试、资源清理异常 | 初始化阶段进度、整理开始、无变更的整理结果 |
| Spine | 插件生命周期、资源加载失败、编辑器预览失败 | 成功的资源解析（debug）、逐帧渲染 |
| 立绘 | 插件生命周期、资源加载失败 | 正常切换表情、逐帧更新 |
| 联网工具 | 插件生命周期、网页工具执行失败及错误码 | 搜索词、URL、网页内容和成功的读取 |
| 手机端 | 服务启动/停止、监听失败、请求拒绝/失败 | TCP 连接、正常请求、状态刷新、客户端断开 |
| Playwright | 工具及页面就绪、配置变化、停止、操作失败、关闭超时/失败 | 成功的读取及页面操作 |

插件错误记录保留操作名、稳定错误码、异常类型及脱敏后的错误原文与调用栈；不主动写入浏览器参数、请求头或聊天内容。
手机端停止写入 `mobile-server.log`，已有文件保留。语音引擎与转换器原有外部进程输出文件继续保留，
失败时从本次启动的输出中提取有界错误或 traceback 片段进入日志窗口，不转发完整输出；其启动、就绪和失败由插件主动报告。日志队列拥塞及传输中断由接入层汇总丢弃数量，
Core 接收后的丢弃由 Core 汇总，SDK 不重复累计下游丢弃。


## 5. ServiceProxy 与跨进程数据

Host 贡献在 setup 提交前暂存，提交后新增贡献立即生效，均由 context.effect 回收。
提交、动态注册与关闭状态通过同一把锁协调；异步工具发现无需再次调用 commit，关闭后不得产生新贡献。

`context.caller_scope` 为 Core 注入的调用进程生命周期 ID，业务参数不能覆盖；Core 调用为 null。
调用结束后恢复上下文。插件停用、重载或崩溃完成 scope 清理时，Runtime 向仍活跃的插件发送
`sakura.host.scope.closed`，内容为 `pluginId` 和 `scopeId`。持有跨插件资源的服务应按两者撤销资源，
并拒绝已撤销 scope 的迟到注册；同名插件的新进程使用不同 scope。事件处理不得阻塞 Runtime 生命周期。

需要确认处理结果的生命周期事件沿用有期限的请求/响应通道。耗时回收由持有资源的插件任务继续完成，
该插件须保留任务，关闭时等待或明确报告未完成。`scope.closed` 不进入可丢弃的观察通知队列。

可从 Timeline 或实际状态重新读取的变化使用 `notify_host_event` 唤醒观察者，调用方不等观察者完成。
通知绑定当前插件进程，不会转交同名新实例。每个插件使用独立观察线程和最多 32 条的队列，保持接收顺序，
不占用 Service 请求线程。队满或发送失败记录 `plugin.notification.dropped`；关闭时丢弃尚未开始的通知。
消费者启动或重载后从事实源及已有 cursor 补读，通知仅作唤醒，不能承载唯一一份结果。

收到跨进程 Service 调用时，`context.caller_id` 是 Core 根据调用进程注入的插件 ID，Core 消费者为
`sakura.core`；调用结束恢复为空。它是当前调用的上下文，不从业务参数读取，也不自动传播到新线程。领域
登记接口可以据此拒绝冒用其他插件身份，例如 ASR Hub 同时核对登记者和目标 Service 所有者。Runtime 只传递
通用调用身份，不维护 ASR Provider 名单。

`context.get("example.service")` 返回可调用已声明方法的对象。提供者在本进程时可以使用本地代理优化；提供者
在其他插件进程或 Core 时返回 `ServiceProxy`。调用方仍使用：

```python
service = context.get("example.service")
result = service.some_method({"value": 1})
```

位置透明只保证相同的方法名、参数合同、结果合同和稳定错误，不保证对象 identity、属性反射、共享内存、
零延迟或无限调用时间。每次调用必须具有 deadline；超时或连接失效绝不自动重放。
远端代理也可使用 `service.invoke("method", args, timeout_seconds=10)` 为单次调用指定期限；
动态 `get()` 与固定进程 `bind()` 均支持。期限由调用方传入 RPC，领域客户端负责按操作 ID 清理超时后状态。

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

### 5.1 显式绑定插件进程

`context.get(service_key)` 的跨进程调用保持动态路由，每次调用解析当前提供者。`context.bind(service_key)` 返回固定到
当前 active 插件进程的 ServiceProxy，绑定身份为 `providerId + scopeId`。普通插件可以使用该接口，不需要
经过某个特定领域的 Host 适配器。内置 Host 服务不提供进程绑定，继续使用 `get()`。

Runtime 在绑定创建、派发及返回边界校验身份。停用、退出、同 ID 重载后旧绑定失效，不能把调用路由到替代进程；
原调用返回时若绑定已失效，也不能交付旧成功结果。过期身份不自动刷新，超时、断连及失效不自动重放。
新操作由实际消费者显式绑定新实例，旧操作保留原绑定。

| 情况 | 稳定错误 |
|---|---|
| 绑定时不存在 active 插件服务 | `SERVICE_MISSING` |
| 尝试绑定内置 Host 服务 | `SERVICE_BINDING_UNSUPPORTED` |
| 已绑定进程停用、退出或被同 ID 新进程替代 | `SERVICE_BINDING_EXPIRED` |
| RPC 携带畸形绑定身份 | `PLUGIN_PROTOCOL_INVALID` |

绑定只固定插件进程，不定义同进程内 Python 服务对象的代次。`provide()` 的 disposer 撤销本地服务后，
原绑定调用报告 `SERVICE_MISSING`；同一进程重新提供相同 key 和导出合同的服务仍属原绑定。
导出方法表仍是 setup 产物，本轮不增加服务 revision 或动态方法表。

`bind()` 不创建 Manifest 硬依赖、后台健康探测、重启或业务恢复关系，也不赋予额外权限。
绑定代理仅在当前消费者进程内使用，普通 Service 参数和结果的 JSON 边界保持不变。

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

Hub 保存 descriptor，在创建任务前通过 `context.bind(serviceKey)` 取得固定进程的 ServiceProxy。
就绪查询 `status`、`begin` 及该任务后续全部 `poll/cancel` 使用同一代理，并以 `jobId` 驱动任务。正常 cleanup 时
Provider 调用 `sakura.tts.unregisterProvider(providerId, serviceKey)`。Provider 崩溃后，即使没有执行 unregister、
用户又重载同 ID Provider，旧任务仍因绑定失效而失败，不能查询或取消新进程中的同名 `jobId`。
新的任务可以重新显式绑定；Runtime 不自动重启 Provider、不重绑或重放旧任务。

Hub 持有具名服务的 ServiceProxy；不跨进程交换 Provider 内部 Python 对象、Job 对象、callable、callback handle
或任意 Python 对象的远端引用。任务身份继续使用有界 JSON 的 `jobId`。
Generic Runtime 只执行普通服务调用与进程绑定检查，不理解 TTS 的 Provider descriptor、`jobId`、warmup 或合成状态。

失败任务和已接受取消的任务由 Provider 收尾：尚未执行的取消可以立即释放临时 artifact 和 Job Effect；
正在执行的任务必须等生产者停止写入后再释放。资源回收不依赖 Core 继续 `poll`，终态仍保留到调用方读取，
因此迟来的 `poll` 可以取得原来的 failed/cancelled 结果。已经成功的任务不接受取消，音频保留到 `poll`
将其交给 Core；此后的 Job cleanup 不得删除已经转交的 artifact。

本合同不自动淘汰无人读取的终态记录，也不丢弃未消费的成功音频；这些对象最终由插件或 generation 关闭
回收。它们与失败、取消任务所占的临时 artifact 额度分开处理，不引入后台轮询、TTL 或自动重试。

### 6.3 可并存 Contribution

Tools、Context contributors、Timeline observers、Settings sections 和模型槽等通过 Host Service 或 Host Event
注册，可以由多个插件同时贡献。它们按现有 descriptor、Effect cleanup、数量和 payload 上限管理，不创建
一个强制唯一的总 Service。

工具 descriptor 可指定 `timeoutSeconds`（有限正数，最大 120 秒），省略时使用原有的 15 秒回调期限。
该字段用于执行期限，不提供给模型作为工具参数。插件工具登记必须原子拒绝同名覆盖并报告
`TOOL_NAME_CONFLICT`。插件返回含 `isError=true` 的对象时，ToolRegistry 将调用标记为失败，保留结果给模型，
`reasonCode` 仅接受有界 ASCII 原因码后进入日志；正文与参数继续脱敏。

Memory 默认采用 Contribution 组合。官方 Mem0 可同时提供 Timeline 消费、Context、Tools、Settings 和
model slot；替代插件可以提供相同或部分贡献。用户既可以关闭 Mem0 完整替换，也可以启用多个不同 Memory
插件共同工作。Runtime 不预设唯一 `sakura.memory` Store/Search/Recall 协议。

### 6.4 Context 行为贡献

`sakura.host.context` 提供 `register/unregister/describe/catalog/collect`。当前 `describe()` 返回
`{schemaVersion: 2, scopes: ["step", "turn"], failurePolicies: ["skip", "abort"]}`，不包含 `fragmentKinds`。
新插件在启用时核实版本和所需能力；旧 Host 缺少方法或版本不符时明确失败。版本只通过能力查询检查，
注册描述不另设版本字段。没有分类字段的旧插件保持兼容。

Context 只提供内容与调用信息，不要求插件区分规则、资料或角色关系。Host 不根据内容判断用途、信任等级或角色卡冲突，
也不切换默认角色。插件自行组织文本；当前默认对话实现负责组合和模型消息格式，模型 role 降级也使用中性上下文说明。
文本不授予代码权限，公开操作和结果格式仍由实际能力入口检查。

Host 保留以下边界：

- 注册描述使用 `providerId/description/order/enabled/scope/failurePolicy`；片段必填非空 `content`，
  `required` 默认为 `false`。未知枚举、非布尔 required、非有限 order 等无效字段明确拒绝。
  `scope` 属于注册回调，不是片段字段。旧 `kind` 字段无论值为何都报 `CONTEXT_SCHEMA_INCOMPATIBLE`。
- 从调用身份绑定真实插件来源，从登记绑定 Provider；旧片段自报的 `source/trust` 忽略，不能覆盖实际来源。
  登记、回调和进程实例的有效性仍由运行底座检查，取消保持可传播。
- 在有界 JSON 与 IPC 总尺寸允许的范围内，传递全部片段及完整文本。Host 不执行前 16 项或每项 8192 字符的裁剪；
  结构或传输超限时明确拒绝，不把截断后的结果当作完整传输。

以下是默认 Assistant 插件的消费约定；采集与裁剪在 `sakura.assistant.default` 的独立进程中执行：

- 顶层用户或事件互动开始时固定贡献者集合与顺序。`scope: step` 为默认值，每次组装调用；`turn` 首次组装采集，
  后续工具步骤、最终总结与格式修复复用，退出后释放。配置更新或停用不改写本轮已采集结果，下一轮重新采集。
  未完成回调可能因插件退出而失败，按失败策略处理。
- `failurePolicy: skip` 为默认值，回调失败时记录并跳过；`abort` 产生 `CONTEXT_CONTRIBUTION_FAILED` 并终止本轮，
  错误与日志保留实际插件和 Provider，失败不生成助手历史。取消及 `CONTEXT_SCHEMA_INCOMPATIBLE` 不受 `skip` 影响。
- `required` 内容完整保留，模型预算不足时产生 `CONTEXT_WINDOW_EXCEEDED`。可选内容按每片段的
  `budgetHint` 和全局预算选择或裁剪，不再额外限制片段数量或字符数。
  `budgetHint` 只约束该片段正文，包装与正文共同消耗全局预算，不按插件、Provider 或 source 分配共享额度。
  `abort` 控制回调失败，`required` 控制完整性，需要完整贡献时同时声明。

Agent Trace 与 Prompt Inspection 保留必需性、采集范围及真实来源，不再输出 Context 的用途或信任分类。
运行日志只记录诊断信息，不承载内容正文。Context 不自动写入 Timeline，也不改变 Memory 的整理策略。
接口用法见 [SDK](../../devdocs/SAKURA_PLUGIN_SDK.md)，本次边界见
[ADR-0054](../../adr/0054-retire-executor-experiment-and-scope-collections.md)；早期薄宿主路线见
[ADR-0053](../../archive/adr/0053-thin-host-and-plugin-owned-policies.md)，初版取舍保留在
[ADR-0052](../../archive/adr/0052-unified-context-instructions.md)。

### 6.5 正常对话与插件服务的边界

正常聊天通过唯一 `sakura.assistant` 服务，默认实现 `sakura.assistant.default` 与第三方 Provider 使用相同的
普通插件进程和公开能力。Core 不再构造 Agent/ChatPipeline，也不按实现 ID 提供兜底。停用默认实现后可以
启用另一个提供该服务的插件；同时启用多个 Provider 按普通服务冲突处理。

prepare/begin/poll/result/cancel/release、输入与结果 artifact、历史分页、模型快照和固定实例最终提交见
[Assistant 插件合同](assistant-plugin-boundary.md)。未知 begin ACK 不重试；确认原进程停止前不回收它仍在读取
的输入。通用 Runtime 只负责调用、身份和回收，不理解 Prompt、模型预算或工具循环。

旧 `chat_executor` 配置仍直接忽略，不提供 `sakura.host.executors`、执行器登记表或第二套会话控制面。
旧实验撤回的历史取舍见 [ADR-0054](../../adr/0054-retire-executor-experiment-and-scope-collections.md)。

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

预装插件包括 `sakura_assistant`、`sakura_mem0`、`sakura_tts_hub`、`sakura_genie`、`sakura_gpt_sovits`、
`sakura_asr_hub`、`sakura_asr_sensevoice` 和 [`sakura_web`](web-plugin.md)。新用户默认关闭 Genie 语音合成和 GPT-SoVITS 语音合成；已有用户的显式开关和沿用清单的
隐式启用状态保持不变，初始化规则见[发行与存储](release-distribution-and-storage.md)。`playwright_browser` 和 `sakura_mobile` 为可选插件，不进入主安装包。手机聊天通过独立 ZIP 安装后显式启用；保留 `sakura_mobile` ID 和原用户配置路径，升级不删除其设置与数据。

## 8. 生命周期、失败与恢复

- Manager 按 capability dependency 拓扑启动插件；Python distribution dependency 只在安装阶段解析，两者
  不得混为一个全局求解器。
- Core 可按所需 Service 启动其硬依赖闭包：先加载当前角色的表现服务并发布角色，再加载 Assistant 并发布
  聊天状态，最后在同一个初始化 worker 中完成其他可选插件。每个阶段始终按完整启用清单检查服务冲突，
  不因先启动某个 Provider 而选出冲突赢家；已失败插件不会在下一阶段自动重试，已运行实例不会重新创建。
  启动切片只串行化启动 worker，不占用显式插件管理和精确实例回收的操作锁。创建进程前在状态锁内重核
  记录身份、开关、启动状态和服务冲突；停用、卸载或替换后的旧启动请求不能创建进程或发布结果。
  慢可选插件不拖延 Assistant 的未知调用回收；关闭也可直接停止正在初始化的进程，不等待启动结束。
- `setup()` 完成并兑现 `provides` 后插件才进入 `active`。失败时撤销该插件全部 Service、Host Contribution
  callback 和 Effect。
- `PluginRuntimeManager` 不运行后台 reconcile、health loop、retry counter、自动重新激活或依赖恢复调度。
  插件状态只在 generation 启动，用户显式 install/update/enable/disable/reload/uninstall、显式设置保存，以及
  显式角色切换或资源更新，以及插件进程退出时变化。
- manifest `requires` 是硬依赖。Provider 进程退出或被停止时，Runtime 只标记 Provider `failed`、失效它的
  ServiceProxy，并停止声明该硬依赖的 consumer；动态查找该 Service 的插件和无关插件继续运行。
- 普通配置的有效字段与本进程已应用配置相同时直接返回 `applied`，不调用更新回调或重载插件；显式覆盖默认值仍会保存。
  上次应用失败或仍要求重载时，相同配置可以再次应用，不能用磁盘值相同吞掉重试。
- 配置变化时在目标进程调用 `config.on_change()`：`applied` 保持进程；`restart_required` 只在本次用户操作
  内按硬依赖顺序停止 consumer、重启目标，再重启本次被停止且此前 active 的 consumer；`error` 明确失败。
  这是显式设置保存的同步步骤，不接收完整目标态 inventory，也不进入后台 reconcile。
- 插件调用超时、依赖安装失败、Service 冲突和进程崩溃均不自动重放、探测、重启、恢复 consumer 或静默选择
  替代实现。恢复只能由用户 reload、重新安装/重试或新 Core generation 触发。
- 正常停止先拒绝新调用并执行有界 LIFO cleanup；超时后只终止目标插件及其受控后代，不结束其他插件或
  扫描无关系统进程。
- Core 关闭也等待已经进入停用或退出清理的进程；并发、重复关闭不能因 Service 已移除而提前返回。
  单个插件清理失败时继续停止其余插件，再报告原始清理错误。失败实例保持 `PLUGIN_CLEANUP_FAILED`，
  不能在旧进程尚未确认退出时启动同 ID 的新实例，也不能把失败当作资源已可释放。

角色切换保持 Core generation，重建 Assistant Session。依赖当前角色服务、且不属于明确按角色参数工作的
表现/TTS Provider 的旧插件局部重载，详见[安全角色切换](WP-5-03-safe-character-switch.md)。

TTS Provider 可同时导出 `prepareResourceUpdate()` 与 `finishResourceUpdate()`，两者成功返回 `true`。
宿主按 Service 实际导出能力选择此路径，不按插件 ID 特判。prepare 拒绝新任务、取消并等待现有任务退出，
完成后允许宿主替换角色文件；finish 在发布成功或回滚后恢复接收任务，并失效路径未变但内容已变的权重缓存。
GPT-SoVITS 保留 Coordinator、Endpoint 和推理进程，下一次预热或合成复用 `set_gpt_weights`、
`set_sovits_weights`。prepare 失败时不写入角色文件；finish 失败与文件保存结果分开报告。
未实现完整接口的 Provider 使用局部插件生命周期回退，不重启整个 Core。

公开状态为 `disabled/starting/active/failed`。已启用但尚未启动、或正在执行 setup 的插件显示
`starting`；具体失败仍立即通过 `failed` 和稳定 `reasonCode` 表达。只要还有插件尚未完成启动，
全局状态保持 `starting`，完成后为 `ready` 或 `degraded`，前端按已有轮询读取实际能力状态。
Assistant 就绪不代表可选插件已注册完成；消费者等待自己需要的服务或 Collection，不以聊天状态替代。
语音预热由同一个后台启动线程在可选插件启动结束后触发，不阻塞角色和聊天发布；慢可选插件可能推迟预热。

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
`user_root/data/plugin-runtime/dependencies/<plugin-id>/`。两者使用相同格式的已安装 marker，
Runner 接收的仍只是当前插件自己的 dependency root。

`.sakura-dependencies.json` 保持 `schemaVersion: 1`，新标记只写入版本、声明类型 `kind` 和 Python
主次版本 `python`。启动检查标记可读、schema、声明类型及 Python ABI；不匹配返回
`PLUGIN_DEPENDENCIES_STALE`，标记缺失或不可读返回 `PLUGIN_DEPENDENCIES_MISSING`。
旧标记的 `fingerprint` 直接忽略，不重算或改写。依赖声明内容或换行变化不再使已安装环境失效；
需要更新依赖时执行显式安装或更新，入口导入与运行错误仍按原有路径报告。

## 10. 插件管理与设置窗口

插件页提供“已安装 / 市场”两个入口，切换位置固定；安装数量只显示在已安装分类中。
市场卡片统一提供“详情”，打开居中弹窗后才展示安装或更新操作。详情内容内部滚动，底部操作始终可达。
分类、简介与资料通过留白和浅底色分组，不在卡片、资料与版本记录之间重复添加分割线。
已安装管理继续使用本节的真实快照、草稿和本地安装流程；从市场进入管理或本地安装成功时，切回已安装并定位插件。

市场从 Sakura-Registry 最新 GitHub Release 的 `catalog.json` 读取目录，包含完整 manifest 和固定安装包地址。
收录源 `plugins.json` 仅保存 ID、仓库和已批准版本到 commit 的映射；客户端不从它推测插件资料。
推荐版本按 SemVer 选择最高的未撤回、非预发布版本，并匹配当前 Plugin API 和 Core 实际可用服务。
这些检查不保证系统、Python 依赖及运行资源在所有设备上均可用；缺少声明的信息不伪造，完整安装检查由现有安装器完成。

目录和 ZIP 共用下载源配置：`config/ui.json` 的 `settings.download_sources` 数组按排列顺序尝试。
每项为 `name`、`prefix`、`enabled`，空前缀代表官方直连；默认依次为 gitproxy.mrhjx.cn、ghproxy.vip、GitHub 官方。
设置页支持启停、排序和自定义前缀。只改写 GitHub、GitHub Raw、codeload 地址；其他 HTTPS 下载站直接访问，便于以后接入 R2。
每个源尝试一次，连接、超时或 HTTP 错误切换下一项；目录格式、包大小和插件身份不符直接报错，不循环重试。
镜像属于第三方下载通道，不新增包摘要或内容认证承诺。网络请求沿用 Shell 的系统代理支持。

下载阶段显示当前源并支持取消；进入安装后不能取消。Shell 仅按已加载目录选取安装包，不接收前端任意下载地址。
Core 使用 `plugins.marketplace.context` 返回当前 API 与服务，用 `plugins.marketplace.install` 接收 revision、临时包路径、目标 ID 和版本。
安装器解包后先核对 ID/版本，再创建依赖环境和发布代码。新安装默认停用。

更新仅允许已经停用的用户插件；内置插件随应用更新。已有新版本不降级。
更新复用现有代码、依赖备份和配置恢复流程，保留插件配置与数据，安装或运行时登记失败时恢复旧版。
未保存的设置草稿需先保存或还原。完整来源持久化、固定版本更新策略和磁盘目录缓存不在本次实现范围。


`installId` 为 `pi_<source>_<directory-encoding>`：来源为 `user` 或 `bundled`，目录名使用 UTF-8
字节的可逆小写十六进制编码（保留文件系统代理字符），最多 1024 字节。不包含绝对路径，不使用内容摘要。
同一来源和目录在不同 Inventory 实例、Core 重启后保持相同身份；目录重命名后身份改变。
桌面端将其作为不透明字符串传回 Core，Core 只按清单记录匹配，不将请求 ID 解码成操作路径。
旧摘要 ID 不持久化迁移；升级后重新读取清单，开关配置仍按 `pluginId` 保存。

inventory `revision` 直接比较安装记录和结构化开关配置，状态改变时生成新的进程内随机 token。
相同运行根的不同 Inventory 实例共享最新状态；无变化的扫描返回同一 token。token 保持 16 位小写
十六进制格式，只用于相等比较；Core 重启后重新获取。配置注释、换行和等价 YAML 格式不产生新版本，
可观察状态从 A 变成 B 再变回 A 时使用新的 token，旧请求仍判为版本冲突。

插件页按功能扩展、功能引擎和系统组件分组。顶部分类切换与名称、作者、ID、简介搜索同排，空间不足时换行；不再提供领域、来源和运行状态筛选面板。
三个分组在同一个列表中显示，以图标、浅色标签、数量和细分隔线区分。系统组件默认折叠，异常数量仍显示在标题旁；用户可通过标题按钮展开。搜索、切换到系统组件分类、安装定位和依赖跳转会展开目标所在分组。
每个插件保留独立卡片，选中时以边框和底色区分，不显示左侧色带。卡片标题行左侧显示名称，右侧显示运行状态，下方保留简介；领域、安装来源和插件角色集中在详情中。
领域 `visual` 表示角色表现，立绘和 Spine 按清单归入该领域。分类不影响依赖或运行顺序。
分类切换回到列表顶部。安装完成和依赖跳转清除阻挡目标的分类与搜索条件，并选中、滚动定位目标卡片；空结果同步清空详情。

详情标题右侧独立提供功能页跳转和“插件设置”按钮；没有底层设置时隐藏后者。运行状态统一显示在左侧列表卡片中，详情继续保留具体异常原因和诊断信息。
启用开关集中在详情，继续使用现有依赖确认、设置草稿和应用流程。状态来自真实快照，配置保存成功不代表运行就绪。
前端接收到 `starting`、`waiting`、`stopping` 时分别呈现“正在启动”“等待启动”“正在停止”，
具体过渡状态优先于通用的应用尚未就绪原因；此规则只约束展示，不新增 Runtime 状态或调和流程。
记忆不可用时只在标题下补充实际原因，不追加“普通聊天仍可继续”等无关说明。所有贡献文案遵循
[界面文案规范](../../devdocs/UI_COPY_GUIDELINES.md)。
下拉、详情切换和弹窗使用主程序主题与动效 token，不受系统减少动态效果设置影响。

设置窗口只渲染插件已有的 Settings Contribution，不根据展示分类增加字段或动作。字段名称、默认值、校验范围、
只读属性、`enabledWhen` 与 `placement=advanced` 均沿用声明。`enabledWhen` 可添加布尔字段 `hide`：
为 true 时，条件不满足的字段隐藏且禁用；默认仍显示为禁用。隐藏不清除已填写的值。长内容在窗口内部滚动，底部操作始终可达。

Settings Contribution 的展示规则由 Python 宿主解析并投影，文案长度按 Unicode 字符计数。
配置服务负责解析和公开投影，Rust 与前端直接消费快照，保留操作身份和过期结果隔离，不重复校验完整结构、业务容量、字段枚举或文案长度；新增展示元信息不导致整份快照被拒绝。RPC 在入队时编码一次，通信帧预算仍由协议层执行。
宿主忽略未知展示元信息，省略无效字段或动作，并在该区块标记 `SETTINGS_DESCRIPTOR_INVALID`。
依赖缺失字段的控件改为只读。加载值与 Action 返回的展示值使用相同投影：未知或已省略控件的值被忽略，
单个值无效时仅该控件回退到默认值并标记 `SETTINGS_VALUE_INVALID`；已有声明错误提示保留。
Action 的展示值是局部更新，未返回的字段不补默认值。其他控件和插件继续使用。
区块身份、Action ID、回调归属、保存和动作输入校验与文件访问边界保持有效；未知写入字段仍被拒绝。

插件通过 `sakura.host.settings.register_page()` 注册命名空间页面，通过 `place()` 将已有区块放入页面区域。
配置归属与展示位置分离，页面移动不得复制配置或改变 `load/save/actions`。页面 ID 包含贡献者；插件只能撤销自己的贡献，
其他插件只有在页面声明开放的 `regions` 中才能放置区块。停用撤下其贡献，宿主页面保留；加载失败与无设置分别显示。
侧栏仍使用角色、智能、行为、系统四组，新页面排在组内现有页之后。页面和放置沿现有 register/unregister 生命周期回收。

宿主声明式组件提供普通表单、折叠高级参数、列表详情、模型选择与继承、状态和动作。连接编辑组件同时支持模型发现弹窗、
勾选和手动添加，不允许插件提供任意 HTML、JavaScript、CSS。具体字段、绑定和示例见 [SDK](../../devdocs/SAKURA_PLUGIN_SDK.md#页面与区块贡献)。
内置插件与第三方使用同一公开接口，不按插件名称装配页面。

- 未放置且未注册 surface，或 `surface=plugin` 的区块放入插件设置窗口；普通字段、Action 和 Collection 保留原调用链。
- `surface=voice` 仍由 Voice controller 管理，保留在语音页；插件设置不借用或恢复这些控件与草稿。
- `surface=memory` 的内容管理保留在记忆页；历史 `surface=about` 资源的管理操作也放入插件设置窗口。
- “完成”保留当前草稿，由设置页底栏“应用”或“保存并关闭”提交。“取消”、关闭或 Esc 只恢复本插件打开窗口时的可编辑字段，
  不丢弃功能页或其他插件草稿，不回滚已经执行的 Action、Collection 操作或下载任务。底栏逐区块确认成功，部分失败时报告实际结果并保留未完成修改。
- 资源状态刷新不得覆盖正在编辑的字段；同一 generation、同一角色的语音草稿在普通插件刷新后保留。
  插件设置贡献或 Core generation 失效时关闭窗口，旧回调不得写回新实例。同角色重启时仍存在 Collection 的编辑草稿
  可随新快照保留，保留草稿不代表重新执行旧写入。
- 插件页面持有设置弹窗、集合草稿和释放逻辑；根入口只装配语音控件与关闭确认。页面释放时关闭弹窗、
  撤下贡献并取消探测；尚未完成的退出动画不得在释放后重新渲染页面。

Collection 的数据归属与进程生命周期分别处理：

- descriptor 可声明 `scope: "global" | "character"`，非法值拒绝。未声明时保留 Collection-v0 的 character 行为，旧安装包无需迁移。
  新贡献应显式声明归属；Host 不从 surface 或插件 ID 推断 scope。Mem0 与便签记忆均为 character。
- 草稿只比较可编辑字段与原值。打开已有记录、未修改的新建编辑器或改回原值，不算未保存修改。
  普通“应用”不代替 Collection 写入；可以保留全局草稿并提交其他设置，“保存并关闭”仍须先处理未保存记录。
- 只有 character 草稿阻止选择另一角色。待应用角色期间暂停 character 的查询和写入，global 仍可使用。
  实际角色切换、同角色局部刷新和 Core 转场期间所有 Collection 暂停旧请求；在途结果不得回填，绑定新的请求状态。
  即使 generation 和 Snapshot 内容都未改变，也不能让刷新前的查询、写入回执或编辑器回调重新生效。
- 实际角色变化时关闭 character 编辑器并清空其条目、筛选、选中项和分页；global 草稿与筛选保留。
  同角色重启保留仍存在集合的草稿与筛选。插件停用、贡献消失或归属变化时撤销原状态，不自动重放写入。
- 删除确认返回后仍须复核 Collection、编辑器、实例与当前转场状态。取消待切换选择或切换完成后恢复当前搜索，
  旧查询、错误及写入回执不得覆盖新状态。
- 用户停用或卸载插件前，若目标或受影响的依赖消费者仍有 Collection 修改，要求先保存或还原记录。
  不能因为允许保留全局草稿，就在插件失效后的新快照中静默丢弃它。
- 普通插件 Collection 删除标题为“删除记录”，Memory 页保留“删除记忆”；确认正文来自插件 `deleteConfirmation`。

scope 只描述设置草稿与角色的关系，数据分区与读写仍由插件实现，不构成权限授权或自动存储管理。

GPT-SoVITS 与 Genie 的现有 `aboutBundle` 区块改为 `surface=plugin`，在各自设置窗口展示整合包资源。
只迁移入口，保留 section ID、Resource 字段、load callback 和 Action；语音页不重复提供这两项下载。
推荐包选择、下载、取消、续传、校验、安装和配置更新继续由原插件实例负责。

“关于 → 组件”是只读总览，聚合已启用插件快照中的 `resource` 字段，不以 `surface=about` 作为筛选条件。
总览显示组件名称、所属插件、安装状态、适用状态及真实下载进度；停用插件的组件不显示，未应用的启停草稿不改变总览。
每项只提供“前往下载设置”，跳到对应插件、打开设置窗口并定位到该资源；总览不直接发起下载、重试或取消。
Mem0 的 `memory_embedding_component` 保留原声明和动作，管理入口移入插件设置，总览继续展示其状态。
下载进行时沿用已有快照刷新机制，安装状态与插件运行状态分别表达，不以“已安装”推断服务已经就绪。

## 11. 迁移与验收门

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
