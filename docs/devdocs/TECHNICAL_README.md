---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-09-01
---

# Sakura 技术架构

Sakura 的桌面程序由 Shell、Core 和逐插件进程组成：

```text
Tauri Shell -> Python Core Host -> PluginRuntimeManager -> Plugin API v4 processes
```

Shell 拥有窗口和操作系统资源，Core Host 处理 Assistant、本地数据和通用插件路由。每个启用插件运行在自己的
进程和 dependency root 中；单插件退出不会直接结束 Core 或无关插件。

## Tauri Shell

`desktop/src-tauri/` 是桌面生命周期根。它负责：

- 创建桌宠、设置、截图和角色工作室窗口；
- 单实例锁、托盘、开机自启动和平台权限；
- 透明窗口、点击穿透、拖动、截图和音频播放；
- 启动、监管并停止 Core Host 及其后代进程；
- 保存统一运行日志。

`desktop/frontend/` 是静态 WebView 前端。前端只能调用 Tauri 注册的 command，并通过 event 接收 Snapshot、聊天状态和平台通知。它不直接读取用户文件，也不持有 Core 的进程句柄。

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

Core Host 负责角色、供应商、聊天、AgentRuntime、Tools、MCP、TTS 消费边界、设置 DTO 和 Timeline。Memory、
TTS Hub 与 TTS Provider 等可替换实现属于普通插件。主要入口在 `app/core_host/server.py`，真实聊天在
`app/core_host/real_chat.py`，领域实现位于 `app/agent/`、`app/config/`、`app/storage/` 和 `app/voice/`。

Core 初始化会发布 readiness 和 Snapshot。确定性配置错误会返回稳定原因码，等待用户修正；网络或单次模型错误只结束当前请求。

## Plugin Runtime v4

`app/core_host/plugin_application.py` 持有 generation 级插件应用，`app/plugins/runtime_v4.py` 管理逐插件进程和
Service 路由，`app/plugins/plugin_runner_v4.py` 在隔离 import path 中构造 `PluginContext` 并调用一次
`setup(context)`。

插件通过统一 SDK 使用 Service、Host Service、Event、Effect、Config 和 Data。官方与第三方插件使用同一个
Runner；`bundled` 只表示分发来源。跨插件 Service 传递有界 JSON 或 Host descriptor，不传真实 Python 对象。

enable、disable、install、uninstall、reload 和 `restart_required` 只处理目标插件及必要的硬依赖 consumer。
Manager 不运行后台 reconcile、自动恢复或调用重放。插件进程不是安全沙箱；隔离用于依赖、故障和可终止性，
不限制可信本地代码的操作系统权限。

## IPC 和 generation

Shell 与 Core 使用长度前缀帧传输 JSON request、response 和 event。握手协商 protocol major/minor 与 capabilities；major 不兼容或缺少必需 capability 时，Shell 不进入业务初始化。

每次 Core 启动都有新的 generation ID、序号和 credential。Rust Gateway 只接受当前 generation 的响应和事件。旧进程迟到的消息、资源 token、插件 callback 和设置结果不能进入新 generation。

Router 允许 response 与 event 交错。聊天的 request ID 会一直保留到 `chat.completed`、`chat.failed` 或 `chat.cancelled`；终态事件可以先于 accepted response 到达。

## 聊天数据流

```text
WebView chat.send
  -> Rust Gateway
  -> Core Router / RealChatBoundary
  -> Context + Memory + Tools + Provider
  -> Timeline commit
  -> chat terminal event
  -> WebView presentation / TTS authorization
```

`app/storage/timeline.py` 使用 SQLite 保存类型化记录。数据库位于 `data/chat_history/timeline.sqlite3`。Core 在构建请求时读取近期完整对话，合并 Memory 与运行上下文，再按 token 预算裁剪。被裁掉的记录仍保存在 Timeline。

截图和音频通过 generation 私有 Artifact 传递。生产边界只交换 opaque ID 和受限元数据，不把临时绝对路径交给 WebView。

## 数据目录

| 路径 | 内容 |
|---|---|
| `config/` | 供应商、模型、界面、MCP 和系统设置 |
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
