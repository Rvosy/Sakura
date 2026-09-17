---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-09-18
---

# Sakura 技术架构

Sakura 的桌面程序由 Shell、Core 和逐插件进程组成：

```text
Tauri Shell -> Python Core Host -> PluginRuntimeManager -> Plugin API v4 processes
```

Shell 拥有窗口和操作系统资源，Core Host 处理对话受理、本地数据和通用插件路由。默认 Assistant 也是普通插件，每个启用插件运行在自己的
进程和 dependency root 中；单插件退出不会直接结束 Core 或无关插件。

## Tauri Shell

`desktop/src-tauri/` 是桌面生命周期根。它负责：

- 创建桌宠、设置、截图和角色工作室窗口；
- 单实例锁、托盘、开机自启动和平台权限；
- 透明窗口、点击穿透、拖动、截图和音频播放；
- 启动、监管并停止 Core Host 及其后代进程；
- 保存统一运行日志。

`desktop/frontend/` 是静态 WebView 前端。前端调用 Tauri command，每轮聊天在调用前建立自己的 Channel，其他 Snapshot 和平台通知使用 event。它不直接读取用户文件，也不持有 Core 的进程句柄。

## Python Core Host

Shell 用 bundled Python 启动：

```text
<bundled-python> -m app.core_host \
  --distribution-root <distribution-root> \
  --user-root <user-root> \
  --generation-id <id> \
  --generation-number <n>
```

进程启动后先从 stdin 读取 16 字节 generation credential，再进入帧协议。stdout 只允许写协议帧；日志经 stderr bridge 交给 Shell。

Core Host 负责角色、模型设置、对话受理与取消、Tools 注册、音画消费边界和 Timeline。默认 Assistant、Memory、
TTS Hub 与 TTS Provider 等实现属于普通插件。主要入口在 `app/core_host/server.py`，共享聊天用例在
`app/core_host/real_chat.py`。默认模型循环、上下文预算、Prompt、回复解析和 Trace 位于
`plugins/builtin/sakura_assistant/sakura_assistant/`，Core 不再构造 AgentRuntime 或 ChatPipeline。

Core 初始化会发布 readiness 和 Snapshot。确定性配置错误会返回稳定原因码，等待用户修正；网络或单次模型错误只结束当前请求。
同一个初始化 worker 先启动当前角色表现及其硬依赖并发布 `characterPresentation`，再准备 Assistant 会话，最后完成其余已启用插件。
角色显示和聊天是否可用各自依据对应状态；慢的可选插件不阻塞前两阶段。关闭时先回收正在启动或已经发布的插件应用，再等待该 worker 结束。

## Plugin Runtime v4

`app/core_host/plugin_application.py` 是 generation 级插件应用的生产装配子类；它与 `plugin_runtime_application.py`
共用一个应用对象，不再通过 `application.application` 转发。`app/plugins/runtime_v4.py` 管理逐插件进程和
Service 路由，`app/plugins/plugin_runner_v4.py` 在隔离 import path 中构造 `PluginContext` 并调用一次
`setup(context)`。

插件通过统一 SDK 使用 Service、Host Service、Event、Effect、Config 和 Data。官方与第三方插件使用同一个
Runner；`bundled` 只表示分发来源。跨插件 Service 传递有界 JSON 或 Host descriptor，不传真实 Python 对象。

enable、disable、install、uninstall、reload 和 `restart_required` 只处理目标插件及必要的硬依赖 consumer。
Manager 不运行后台 reconcile、自动恢复或调用重放。插件进程不是安全沙箱；隔离用于依赖、故障和可终止性，
不限制可信本地代码的操作系统权限。

## IPC 和 generation

Shell 与 Core 使用长度前缀帧传输 JSON request、response 和 event。握手协商 protocol major/minor 与 capabilities；major 不兼容或缺少必需 capability 时，Shell 不进入业务初始化。

每次 Core 启动都有新的 generation ID、序号和 credential。Rust 传输层只接受当前 generation 的响应和事件；
`ChatBridge` 是 Shell 内唯一的聊天状态投影。旧进程迟到的消息、资源 token、插件 callback 和设置结果不能进入新 generation。

Router 允许 response 与 event 交错。聊天的 request ID 会一直保留到 `chat.completed`、`chat.failed` 或 `chat.cancelled`；终态事件可以先于 accepted response 到达。

## 聊天数据流

```text
WebView command + 本轮 Channel / Mobile 已解码输入
  -> RealChatBoundary 受理，固定 Session 和 operationId
  -> sakura.assistant 普通 Service
  -> 默认 Assistant 选择 Context / 完整历史轮次，调用模型与工具
  -> Core 解析表现控制，仲裁取消、插件实例与 Timeline 提交
  -> 本轮终态，字幕与表现；语音独立排队
```

`app/storage/timeline.py` 使用 SQLite 保存类型化记录。数据库位于 `data/chat_history/timeline.sqlite3`。Core 是聊天事实的写入者，
向 Assistant 提供固定角色与高水位的分页读取授权。默认 Assistant 从近到远读取完整轮次，按实际模型预算选择历史；
跳过一个放不下的大轮次后仍可选择更早的小轮次。未被纳入请求的记录继续保存在 Timeline。

`BoundAssistant` 固定 `providerId + scopeId`，通过 `prepare/begin/poll/result/cancel/release` 管理一次任务。
取消会等待旧 worker 停止；启动确认丢失或回收通道失败时，由 Manager 终止对应实例，不重放请求。
Core 在持有服务绑定锁时提交结果，插件停用或重载后的旧回复不能再写历史。空回复也要完成同一仲裁。

默认模型客户端使用官方 OpenAI SDK，网络自动重试关闭。协议参数兼容与格式修复分别记录实际请求，
工具副作用不会因网络错误自动再执行一次。每轮读取固定模型配置，设置保存不改写进行中的请求。

截图和音频通过 generation 私有 Artifact 传递。生产边界只交换 opaque ID 和受限元数据，不把临时绝对路径交给 WebView。

## 配置所有权

供应商和模型由 `ProviderModelSettingsRepository` 保存到 `config/api.yaml`，界面设置由 Rust
`UiConfigRepository` 保存到 `config/ui.json`；工具设置和插件配置分别由对应的 Core 边界与 Plugin Runtime
处理。`AppSettingsService` 保留 Core 和旧版导入使用的 YAML 读取方法，只写入当前角色选择和屏幕感知设置。

保存与应用在设置操作中完成。磁盘保存成功但运行态忙碌或应用失败时，界面收到明确结果；下一次聊天不会代为修复上次保存。
插件管理操作改变 Assistant 服务身份时，立即重新发布可用会话；旧任务仍引用受理时的实例。

TTS 配置和角色声线清单由各 Provider 插件解析。显式 0.9.x 导入通过 `app/legacy_import/configuration.py`
生成插件配置，再由 `importer.py` 调用各插件的 `_parse_config` 校验；普通启动不读取旧 `api.yaml.tts`。

## 数据目录

| 路径 | 内容 |
|---|---|
| `config/` | 供应商、模型、界面和系统设置 |
| `data/chat_history/` | Timeline 与聊天数据 |
| `data/memory/` | Memory 插件数据和本地向量存储 |
| `data/plugins/` | 插件私有配置与运行数据 |
| `data/user_plugins/` | 用户安装的插件代码 |
| `data/logs/` | 运行日志和 Agent Trace |

写入使用临时文件、校验和原子替换。测试必须设置独立 app root，不能把仓库中的真实 `data/` 当 fixture。

## 启动与退出

开发启动入口：

```bash
bash scripts/start.sh
```

macOS/Linux 的 `scripts/start.sh` 与 Windows 的 `scripts/start.bat` 都会增量编译并启动 debug Shell；release 只用于完整发行布局。

退出由 Shell 协调：停止接收新请求，排空终态事件，Core 有界关闭各插件进程，再回收 Core 后代进程并释放
单实例锁。单个插件 cleanup 不能让退出无限等待。

需要返回值的 Service 调用与观察通知分开：普通观察通知进入插件的有界队列；生命周期撤销仍等待清理。
日志保留原始异常、阶段和 operation，公开错误只做安全投影。遥测关闭、语音失败或普通观察者缓慢不阻塞正文。

## 按责任找代码

| 需要排查的行为 | 入口与责任 |
|---|---|
| 受理、取消、历史提交、Mobile 共用聊天 | `app/core_host/real_chat.py`、`mobile_host.py` |
| Assistant 初始化、固定插件实例、任务回收 | `app/core_host/assistant_adapter.py` |
| 默认模型循环、Prompt、工具策略、Trace | `plugins/builtin/sakura_assistant/sakura_assistant/service.py`、`agent/`、`llm/` |
| 历史存储与分页、默认历史预算 | `app/storage/timeline.py`、默认 Assistant 的 `history.py` |
| 插件进程、Service/Callback、依赖失效 | `app/plugins/runtime_v4.py` |
| 公共值对象与插件可导入能力 | `app/plugin_sdk/` |
| 角色草稿、文件事务与发布 | `app/config/character_studio.py`；运行应用由 `app/core_host/character_studio.py` 接收结果 |
| 桌面聊天投影与每轮 Channel | `desktop/src-tauri/src/chat_bridge.rs` |

架构取舍见 [ADR-0060](../adr/0060-single-chat-boundary-and-plugin-assistant.md)。

## Sakura Service

`https://sakura.cialloo.cn/service/v1/` 是可选静态控制面，当前只公开版本、空公告和空已知问题 JSON。它不属于
Shell → Core → Plugin 的本地运行链，不处理模型或用户数据；不可用时不得影响启动、聊天、设置或 GitHub Updater。
安装资产与签名 `latest.json` 继续由 GitHub Release 持有，阿里云只提供很小的控制元数据。

正式稳定版的全部 GitHub 资产完成后，Release CI 使用只能调用服务端校验程序的 SSH forced command 原子更新版本
JSON。详细 schema 与失败语义见 [Sakura Service 静态控制面合同](../specs/runtime-v2/sakura-service.md)，维护操作见
[Sakura Service 运维与发布](SAKURA_SERVICE.md)。

## 验证

下面使用 macOS/Linux 路径；Windows 使用 `.\runtime\python.exe`。

```bash
./runtime/bin/python3 -m harness list
./runtime/bin/python3 -m harness run smoke
./runtime/bin/python3 -m harness run core-host
./runtime/bin/python3 -m harness run runtime-v2-shell
./runtime/bin/python3 -m harness run python-full
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
```

先运行受影响能力的 focused tests。完整平台矩阵由 CI 执行；透明窗口、系统权限和真实桌面交互仍需在目标平台验证。
