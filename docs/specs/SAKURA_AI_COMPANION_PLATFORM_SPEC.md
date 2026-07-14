# Sakura AI Companion Platform Specification

| 字段 | 内容 |
|---|---|
| Spec ID | SAP-001 |
| 状态 | Draft |
| 目标版本 | Sakura Platform 1.0 |
| 更新日期 | 2026-07-14 |
| 适用范围 | Windows x64 首发，后续扩展其他平台 |
| 当前最高优先级 | P0：完成 Qt 主应用与全部现有桌面功能到 Tauri 的迁移 |

## 1. 摘要

Sakura SHALL 从 Python/PySide6 桌宠应用迁移为由 Tauri 驱动的 AI Companion 平台。

Sakura 默认提供角色、人格、对话、声音、记忆、感知和主动互动。文件操作、终端、浏览器控制、鼠标键盘控制等 Agent 行动能力 SHALL 通过插件按需安装，并统一经过权限系统。

系统由两个核心部分组成：

```text
Sakura Platform Core
├─ Trusted Desktop Core：Rust/Tauri
└─ Companion Brain Host：Python
```

Rust/Tauri 负责桌面集成、感知、安全、插件管理和可信执行；Python 负责人格、记忆、上下文、LLM 和 Agent 规划。

## 2. 规范性术语

本文使用以下术语：

- MUST / SHALL：必须满足。
- MUST NOT / SHALL NOT：禁止。
- SHOULD：原则上应满足，除非存在明确理由。
- MAY：可选实现。
- Core：Sakura 官方可信代码。
- Brain：Python 伙伴大脑。
- Capability：可被 Brain 请求的外部能力。
- Observation：由系统或用户交互产生的感知事件。
- Plugin：扩展 Sakura 能力边界的代码或声明式组件。
- Pack：不包含可执行代码的角色、人格、模型或资源包。

## 3. 产品目标

Sakura 1.0 SHALL 支持三种用户体验层级。

### 3.1 Companion

默认提供：

```text
角色与人格
聊天
基础记忆
TTS
时间感知
空闲状态
Sakura 内部交互感知
主动互动
```

默认安装 SHALL NOT 包含文件写入、终端执行或输入控制能力。

### 3.2 Assistant

通过可选能力包增加：

```text
联网搜索
网页读取
文件读取
屏幕理解
资料整理
日历查询
知识库
```

Assistant 能力 SHOULD 以只读或低风险操作为主。

### 3.3 Agent

通过高权限能力包增加：

```text
文件写入
终端执行
浏览器控制
鼠标键盘控制
软件自动化
外部服务写入
```

Agent 能力 SHALL 经过运行时权限检查和审计。

“Companion / Assistant / Agent” SHALL 是能力和权限预设，而不是三套独立应用。

## 4. 非目标

Sakura Platform 1.0 不要求：

- 完全隔离任意第三方 Python 代码。
- 无人监督的完全自主 Agent。
- Windows、macOS、Linux 同步达到功能一致。
- 插件市场支付和商业分成。
- 云端账户和跨设备同步。
- 兼容旧版 Qt UI 插件。
- 首发支持 Windows ARM64。

## 5. 系统架构

```text
┌─────────────────────────────────────────┐
│                Tauri UI                 │
│ Pet / Chat / Settings / Studio / Market │
└───────────────────┬─────────────────────┘
                    │ Tauri Commands
┌───────────────────▼─────────────────────┐
│       Trusted Desktop Core — Rust       │
│                                         │
│ Window / Tray / Startup / Update        │
│ Observation Manager / Event Bus         │
│ Permission Manager / Capability Manager │
│ Plugin Manager / Runtime Manager        │
│ Audio / Screen Capture / Process Host   │
└───────────────────┬─────────────────────┘
                    │ Versioned IPC
┌───────────────────▼─────────────────────┐
│        Companion Brain — Python         │
│                                         │
│ Personality / LLM / Memory              │
│ Context Builder / Companion Policy      │
│ Agent Planning / Capability Selection   │
└───────────────────┬─────────────────────┘
                    │ Capability Request
┌───────────────────▼─────────────────────┐
│             Plugin Runtimes             │
│ Brain / Memory / Voice / Renderer       │
│ Filesystem / Browser / Terminal         │
│ Computer Control / Integrations         │
└─────────────────────────────────────────┘
```

Tauri WebView SHALL NOT 直接持有危险 OS 权限。所有能力调用必须由 Rust Core 验证。

## 6. Rust/Tauri 职责

Trusted Desktop Core SHALL 负责：

- 桌宠和应用窗口。
- 启动前置检查、首次设置和窗口路由。
- 托盘、单实例、快捷键和开机启动。
- Python Host 启停、监控和异常恢复。
- Runtime 安装、健康检查、切换和回滚。
- Observation 采集、裁剪和授权。
- Capability 注册、路由和执行。
- 权限授权、撤销和审计。
- 屏幕捕获和系统窗口状态。
- 音频播放和播放状态事件。
- 插件安装、签名校验和生命周期。
- 应用、Runtime、插件和模型更新。

Rust Core SHALL 是危险能力的最终授权边界。

## 7. Python Brain 职责

Python Brain SHALL 负责：

- 人格和角色 Prompt。
- LLM 调用。
- 对话管线。
- 记忆读写和召回。
- 上下文构建。
- 主动互动决策。
- Agent Planning。
- Capability 选择。
- Capability 结果总结。

Python Brain SHALL NOT 直接执行危险操作，例如：

```python
os.remove(...)
subprocess.run(...)
pyautogui.click(...)
```

Brain SHALL 通过 IPC 请求已注册 Capability。

迁移期间 MAY 临时保留兼容适配层，但正式架构不得依赖该适配层。

## 8. Observation System

### 8.1 原则

Observation 基础设施 SHALL 属于 Core。

敏感感知 SHALL 经过隐私授权。Core 所有不代表默认开启。

### 8.2 感知级别

| 级别 | 示例 | 默认策略 |
|---|---|---|
| Level 0 | 时间、Sakura 点击、聊天状态、TTS 状态 | 默认允许 |
| Level 1 | 当前应用、空闲时间、全屏状态、输入活动 | 用户开启 |
| Level 2 | 截图、OCR、窗口标题、剪贴板、麦克风 | 明确授权 |

系统 SHALL NOT 记录全局键盘的实际按键内容。

系统 MAY 记录“最近发生键盘活动”之类的状态。

持续屏幕、麦克风或摄像头感知 SHALL 显示可见状态指示。

### 8.3 事件格式

Observation Event SHOULD 使用以下结构：

```json
{
  "id": "obs_01",
  "version": 1,
  "type": "desktop.active_window.changed",
  "timestamp": "2026-07-13T12:00:00+08:00",
  "source": "rust.window-observer",
  "sensitivity": "metadata",
  "retention": "ephemeral",
  "payload": {
    "application": "Visual Studio Code",
    "category": "development"
  }
}
```

### 8.4 数据最小化

Observation Manager SHALL：

- 在发送给 Python 前过滤不必要字段。
- 对事件限频和去重。
- 默认不持久化原始截图。
- 默认不将 Observation 上传到非用户配置的服务。
- 允许用户关闭每类 Observation。
- 允许用户查看当前启用的感知能力。

## 9. Event Bus

系统 SHALL 区分三类事件总线：

```text
Rust System Event Bus
    原生窗口、输入活动、更新、Runtime 状态

Observation Bus
    经过隐私过滤的感知事件

Python Domain Event Bus
    聊天、人格、记忆、TTS、Agent 状态
```

事件 SHALL 具有稳定的名称、版本和结构化 Payload。

系统 SHALL NOT 使用一个无类型全局事件总线承载所有数据。

## 10. Capability System

### 10.1 Capability 定义

Capability 是 Brain 可请求、由 Rust 管理的外部能力。

示例：

```text
filesystem.scan
filesystem.read
filesystem.move
terminal.execute
browser.navigate
browser.click
input.click
integration.github.create_issue
```

### 10.2 执行流程

```text
Brain 生成 Capability Call
        ↓
Capability Manager 查找提供者
        ↓
Permission Manager 检查授权
        ↓
必要时请求用户确认
        ↓
Executor 执行
        ↓
返回结构化结果
        ↓
Brain 继续推理或总结
```

### 10.3 请求格式

```json
{
  "request_id": "cap_01",
  "interaction_id": "chat_01",
  "capability": "filesystem.move",
  "plugin_id": "sakura.filesystem",
  "arguments": {
    "source": "D:/Desktop/a.txt",
    "target": "D:/Documents/a.txt"
  },
  "reason": "按照用户要求整理桌面文件"
}
```

用户确认 SHALL 与请求 ID、Capability 和参数摘要绑定。确认后插件不得替换关键参数。

## 11. Permission System

### 11.1 权限类型

首版 SHALL 至少支持：

```text
observation.window_metadata
observation.screen_capture
observation.clipboard
filesystem.read
filesystem.write
terminal.execute
input.control
network.access
account.read
account.write
```

### 11.2 风险等级

| 等级 | 示例 | 默认行为 |
|---|---|---|
| Low | 内部状态、已公开数据 | 可直接执行 |
| Medium | 限定目录读取、限定域名访问 | 首次或会话确认 |
| High | 文件写入、终端执行、账户写入 | 每次或精确规则确认 |
| Critical | 批量删除、输入控制、凭据访问 | 强制逐次确认 |

Critical 权限 SHOULD NOT 提供无期限的“始终允许”。

### 11.3 授权范围

权限 SHALL 支持 Scope：

```yaml
filesystem.read:
  paths:
    - D:\Documents

network.access:
  domains:
    - api.github.com

terminal.execute:
  executables:
    - git.exe
```

### 11.4 授权决策

系统 SHALL 支持：

```text
允许本次
允许本次会话
始终允许
拒绝
撤销
```

插件更新新增权限时 SHALL 重新授权。

### 11.5 审计

高风险操作 SHALL 记录：

```text
时间
会话
插件
Capability
参数摘要
权限决策
执行结果
错误信息
```

审计日志不得明文保存密码、Token 或完整敏感文件内容。

## 12. 插件模型

### 12.1 Character / Personality Pack

角色和人格 SHOULD 使用无代码资源包：

```text
角色设定
Prompt
立绘
主题
语音配置
行为偏好
```

资源包默认 SHALL NOT 执行代码。

### 12.2 Brain Plugin

运行于 Python Host，可提供：

```text
Prompt Patch
Context Provider
Companion Policy
Emotion Model
Conversation Strategy
```

### 12.3 Memory Provider

Memory API SHALL 支持可替换实现。

默认实现 SHOULD 为轻量存储。向量记忆作为可选组件安装。

### 12.4 Renderer Provider

Renderer Provider SHALL 使用跨进程或前端渲染协议，不再返回 Python QWidget。

Renderer 可采用：

```text
Web Module
WebGL
Live2D
VRM
受控 Sidecar
```

主窗口和窗口层级 SHALL 由 Tauri 管理。

### 12.5 Voice Provider

语音职责 SHALL 拆分为：

```text
Provider：语音合成
Rust Core：音频播放
```

Provider 返回音频文件、音频流或受控资源句柄。

### 12.6 Capability Plugin

Capability Plugin 提供工具定义和执行适配器。

危险原语 SHALL 通过 Rust Broker 执行。

### 12.7 Integration Plugin

Integration Plugin 可连接 GitHub、Notion、Calendar 等服务，并声明：

```text
网络域名
账户读取
账户写入
本地凭据需求
```

## 13. 插件清单

插件 SHALL 提供机器可读 Manifest：

```yaml
format: 1
id: sakura.filesystem
name: Filesystem Capability
version: 1.0.0
publisher: Sakura
plugin_api: 1
type: capability
runtime: rust-broker

permissions:
  - filesystem.read
  - filesystem.write

capabilities:
  - id: filesystem.scan
    risk: medium
    permission: filesystem.read

  - id: filesystem.move
    risk: high
    permission: filesystem.write
    confirmation: required
```

Manifest SHALL 包含兼容版本、文件哈希和签名信息。

## 14. 插件信任模型

插件 SHALL 标记为：

```text
Official
Verified
Community
Local Development
```

首版不得宣称任意 Python 插件已被完全沙箱隔离。

首版 SHOULD 提供：

- 数字签名。
- 独立进程运行。
- 权限声明。
- 能力白名单。
- 受控工作目录。
- 健康检查。
- 崩溃隔离。
- 权限审计。
- 安装和更新回滚。

强隔离 MAY 在后续版本使用 WASM、AppContainer 或其他 OS 沙箱实现。

## 15. IPC 协议

### 15.1 传输

Tauri SHALL 启动 Python Host，并通过本地进程管道通信。

协议 SHOULD 使用：

```text
4 字节消息长度
+
UTF-8 JSON Payload
```

stdout SHALL 保留给协议数据；日志写入 stderr 或日志文件。

### 15.2 消息类型

协议 SHALL 支持：

```text
request
response
event
```

### 15.3 握手

启动流程：

```text
Tauri 启动 Python
    ↓
system.hello
    ↓
交换协议和版本
    ↓
system.health
    ↓
backend.ready
```

握手 SHALL 校验：

```text
ipc_protocol
app_version
brain_version
runtime_id
plugin_api
data_schema
```

### 15.4 核心接口

首版 SHOULD 包含：

```text
system.hello
system.health
system.shutdown

chat.send
chat.cancel
chat.confirm_action

observation.push

settings.get
settings.apply

character.list
character.switch

memory.query
memory.status

capability.list
capability.result
```

截图、模型和音频等大型数据 SHOULD 使用受控临时文件或资源句柄传递。

### 15.5 异常恢复

Python Host 崩溃时，Tauri SHALL：

1. 保持 UI 可用。
2. 显示后端异常状态。
3. 有限次数自动重启。
4. 超过阈值后进入 Runtime 修复界面。
5. 避免无限崩溃循环。

### 15.6 应用启动状态机

Tauri 主程序 SHALL 在显示业务窗口前完成启动状态判定。启动状态至少包括：

| 状态 | 条件 | UI 路由 |
|---|---|---|
| `runtime_repair` | Python Runtime 缺失、损坏或协议不兼容 | Runtime 修复界面 |
| `onboarding_required` | 未配置可用聊天模型，或没有可用角色 | 首次设置界面 |
| `ready` | Runtime、聊天模型配置和角色均可用 | 桌宠主窗口 |
| `brain_recovering` | 已完成配置，但 Brain Host 意外退出 | 保持 Tauri UI 并显示恢复状态 |

业务配置缺失 SHALL NOT 被判定为 Brain Host 崩溃。`system.health` SHOULD 只表达进程、协议和基础服务健康；可用聊天模型与角色状态 SHOULD 由独立的启动状态或 Bootstrap Payload 返回。

首次设置界面 SHALL 由 Tauri 承载，并且在完整角色上下文尚未建立时仍可打开。首次设置流程至少 SHALL 支持：

1. 配置或选择可用聊天模型。
2. 创建、导入或选择角色。
3. 完成配置后在当前应用会话内进入桌宠，不要求用户手动重启。

启动路由 SHOULD 遵循：

```text
启动 Tauri
    ↓
检查 Runtime 和 Brain 基础健康
    ↓
检查聊天模型配置和角色
    ├─ Runtime 异常 → Runtime 修复界面
    ├─ 模型或角色缺失 → 首次设置界面
    └─ 配置完整 → 桌宠主窗口
```

## 16. 路径模型

系统 SHALL 使用独立路径结构：

```text
install_root
app_root
runtime_root
data_root
cache_root
plugins_root
models_root
```

业务代码不得假设所有资源都位于项目根目录。

推荐安装布局：

```text
Sakura/
├─ Sakura.exe
├─ app/
│  └─ versions/
├─ runtime/
│  └─ versions/
├─ plugins/
├─ models/
├─ state/
├─ cache/
└─ portable.flag
```

默认数据目录：

```text
%APPDATA%/Sakura/
```

存在 `portable.flag` 时：

```text
Sakura/data/
```

## 17. Runtime 管理

基础 Runtime SHALL 包含：

```text
Python
基础网络依赖
配置与存储
LLM Client
基础 Memory
Brain Host
IPC
基础插件系统
```

以下组件 SHOULD 可选安装：

```text
memory-vector
browser-playwright
voice-local
backchannel-local-model
agent-terminal
agent-computer-control
```

锁文件 SHALL 按平台、架构和 Python ABI 生成：

```text
locks/win-x64-cp312/core.lock
locks/win-x64-cp312/memory-vector.lock
locks/win-x64-cp312/browser.lock
```

锁文件 SHALL：

- 固定直接和传递依赖版本。
- 固定 Wheel 哈希。
- 只允许二进制 Wheel。
- 禁止在用户电脑现场编译。
- 由同一次统一依赖解析生成。
- 在 CI 的干净系统中验证。

安装器 SHALL 携带独立、签名后的 `uv.exe`。pip MAY 作为兼容兜底。

## 18. 安装

`Sakura-Setup.exe` SHALL：

1. 安装 Tauri 主程序和 Runtime Manager。
2. 安装最小 Python Runtime。
3. 检测系统架构和 WebView2。
4. 检查安装目录可写性。
5. 启动可恢复的 Runtime 配置流程。
6. 创建快捷方式和卸载信息。
7. 完成基础健康检查。

依赖配置失败时，Tauri 修复界面仍 SHALL 能够启动。

安装过程 SHOULD 支持：

```text
国内镜像
官方源
自定义源
代理
断点续传
离线组件导入
```

PyPI 之外的 Playwright 浏览器、模型、TTS 包和 WebView2 SHALL 由统一资源管理器处理。

## 19. 更新

系统 SHALL 分离：

```text
App Update
Brain Update
Runtime Update
Plugin Update
Model Update
Data Migration
```

推荐版本结构：

```text
app/versions/1.0.0
app/versions/1.0.1

runtime/versions/win-x64-cp312-<hash>
```

更新流程：

```text
下载
 ↓
校验签名和哈希
 ↓
安装到新目录
 ↓
健康检查
 ↓
原子切换 current.json
 ↓
正式启动
 ↓
失败则回滚
```

Runtime 更新不得原地破坏当前环境。

数据迁移 SHALL：

- 在迁移前备份。
- 记录 `data_schema`。
- 定义旧版本兼容策略。
- 在应用回滚时检测数据版本。
- 不得假设应用回滚等于数据回滚。

## 20. 兼容版本

系统 SHALL 独立维护：

```text
ipc_protocol
plugin_api
runtime_schema
data_schema
```

插件或 Runtime 不兼容时 SHALL 被拒绝激活，而不是尝试带病运行。

## 21. 现有 SDK 迁移

现有能力映射如下：

| 当前接口 | 新接口 |
|---|---|
| `ToolContribution` | `CapabilityDeclaration` |
| `ContextProvider` | Brain Context Provider |
| `RendererContribution` | Renderer Provider |
| `PluginSettingsContribution` | Declarative Settings Schema |
| `requires_confirmation` | Permission Policy |
| Python Tool Executor | Capability Broker/Executor |

纯推理和上下文插件 MAY 保留 Python 执行模式。

危险工具执行 SHALL 迁移到 Rust Broker 或受控 Sidecar。

## 22. Tauri 主应用完整迁移规范

### 22.1 优先级与规范关系

完成 Qt 主应用到 Tauri 的完整迁移 SHALL 是当前 P0 里程碑。

在本里程碑退出前，Phase 2 及后续工作 MAY 只实施完成迁移所必需的最小接口，不得以新增平台能力替代现有功能等价、启动恢复、实机验收或 Qt 退出工作。

本节定义迁移完成的规范性条件。以下文档用于实施和记录证据，但不得降低本节要求：

- `docs/migration/tauri-phase-1-parity-matrix.md`：功能等价与验收证据矩阵。
- `docs/superpowers/plans/2026-07-14-tauri-assistant-phase-1-migration.md`：实施任务和分支偏差记录。
- `docs/adr/0001-tauri-desktop-technical-gate.md`：受限实机环境下的技术门决策。

所有新增桌面界面和桌面集成功能 SHALL 只在 Tauri 路径实现。迁移期间对旧 Qt 代码的修改 SHALL 限于提取无 UI 业务逻辑、修复用于等价对照的缺陷、补充迁移测试或删除旧实现。

### 22.2 当前基线

截至 2026-07-14，仓库处于以下状态：

| 范围 | 当前状态 | P0 收口要求 |
|---|---|---|
| 生产入口 | `main.py` 已只启动 `sakura-desktop`，不自动回退 Qt | 保持 Tauri 为唯一生产入口，并完成干净安装验证 |
| 桌宠主窗口 | 透明窗口、立绘、字幕、输入、聊天、确认和截图入口已进入 `desktop/` | 补齐多 DPI、多显示器、拖动、置顶、IME 和真实交互签字 |
| 次级窗口 | 设置、工作室、历史和诊断已合并到同一 Tauri App | 完成首次设置、Runtime 修复、重复打开聚焦和窗口层级收口 |
| Brain Host | 长期运行 Python Host 和主要 Assistant 服务已无 Qt 运行依赖 | 保证正常生产导入图不加载 `PySide6` 或 `app.ui` |
| 原生能力 | Rust 已承担主要窗口、托盘、单实例、音频播放和截图职责 | 完成真实设备、物理托盘、混合 DPI 和故障恢复验收 |
| 数据与业务 | 角色、人格、聊天、记忆、插件、MCP、TTS 合成和主动互动继续复用 | 保持现有数据格式和行为，不允许出现只在 Qt 可用的功能 |
| Qt 遗留 | `legacy_qt_main.py`、`app/ui/`、Qt UI 插件和旧 Qt 测试仍作为迁移对照存在 | 等价验收后删除或退出所有发布、安装、运行和 CI 必需链路 |
| 启动路由 | `ready`、`onboarding_required`、`runtime_repair` 的基础协议已建立 | 完成同会话首次设置切换、修复界面和恢复循环隔离 |

“代码已经存在”不等于“迁移完成”。任何标记为部分覆盖、受限或仅自动覆盖的用户链路，在取得所需实机证据前 SHALL 视为未完成。

### 22.3 “完整迁移”的定义

只有同时满足以下条件，才可声明 Qt 主应用迁移完成：

1. Tauri 是唯一生产桌面进程、窗口宿主和系统托盘宿主。
2. 所有用户可见界面均由 Tauri/WebView 或受控原生系统界面承载。
3. 桌宠现有功能不存在只能通过 `legacy_qt_main.py` 或 QWidget 使用的路径。
4. Python Brain 的正常启动、运行、恢复和关闭不导入 `PySide6`、`QApplication`、`QObject`、`QThread`、`QTimer` 或 `app.ui`。
5. Qt 原先承担的计时器、工作线程、Signal/Slot、音频播放、屏幕捕获和窗口生命周期均迁移到明确的新所有者。
6. 设置、工作室、历史、诊断、首次设置和 Runtime 修复均在同一个 Tauri App 内运行，不依赖独立 Qt 桥或独立旧版 Tauri Sidecar。
7. 生产依赖、安装包和基础 Runtime 不包含 PySide6。
8. 不存在静默功能降级。旧 Qt 功能必须被迁移、以等价产品方案替换，或通过明确产品决策删除并更新验收矩阵。
9. 自动化测试和 Windows 实机验收均达到本节定义的退出门槛。
10. 在完成回退窗口后，旧 Qt 入口和不可复用 UI 代码从可执行产品代码中删除。

### 22.4 迁移后的所有权

| Qt 时代职责 | 新所有者 | 规范要求 |
|---|---|---|
| `QApplication` 和主事件循环 | Tauri Runtime | Python SHALL NOT 创建第二套桌面事件循环 |
| QWidget、Dialog、Overlay | Tauri WebView / 受控原生对话框 | 所有窗口由 Rust 注册、创建、聚焦和关闭 |
| `QSystemTrayIcon` | Rust Tray | 菜单、显示/隐藏、设置、历史、工作室和退出由 Rust 路由 |
| Qt Window Flags | Rust Window Manager | 透明、无边框、置顶、穿透、任务栏和窗口层级由 Rust 管理 |
| `QTimer` UI 调度 | Python Scheduler 或 Rust Runtime | 归属按领域决定，不得为了计时继续保留 Qt |
| `QThread` / Qt Worker | Python 标准并发或 Rust Task | 取消、关闭和迟到结果必须有显式语义 |
| Signal / Slot | 版本化 IPC 和类型化 Tauri Event | 事件必须携带 session、interaction 或 request 标识 |
| `QMediaPlayer` / Qt Audio | Rust Audio Service | Rust 单一拥有播放、停止、音量、队列和播放状态 |
| `QScreen` / `QPixmap` 截图 | Rust Capture Service | WebView 和 Brain 只接收受控资源句柄或经过裁剪的数据 |
| Qt 文件、目录和颜色选择器 | Tauri Plugin 或受控原生命令 | 返回值必须规范化并经过路径或格式校验 |
| Qt 插件设置页 | 声明式 Settings Schema / Web Module | Python 插件不得向主应用返回 QWidget |
| Qt 样式、主题和动画 | Web Theme Tokens / CSS / JS | 角色主题和用户设置仍使用统一配置源 |

Python SHALL 继续拥有角色、人格、Prompt、LLM、聊天管线、记忆、主动互动决策、TTS 合成 Provider、现阶段的插件/MCP 业务逻辑和兼容工具执行。

第一阶段允许 Python 继续执行现有工具，但不得因此保留 Qt。正式 Capability Broker 建立后，再按第 10、11 和 21 节迁移危险执行边界。

### 22.5 必须迁移的界面范围

| 界面或交互面 | 必须保持的能力 | 完成门槛 |
|---|---|---|
| 桌宠主窗口 | 透明、无边框、立绘、字幕、输入栏、底边锚点、窗口拖动、置顶、穿透、隐藏和恢复 | 单屏与混合 DPI 多屏均无黑边、尺寸漂移、错误点击区域或窗口丢失 |
| 角色表现 | 默认立绘、表情映射、tone/portrait 映射、预加载、过渡、缩放和角色切换 | 不泄露本地绝对路径；角色切换不需要重启 |
| 字幕和气泡 | 分段、打字机速度、等待态、语言切换、排队、取消和迟到回调隔离 | 连续多段、取消和新旧会话交错时顺序稳定 |
| 聊天输入 | 中文/日文 IME、发送、取消、空消息规则、忙碌态和视觉附件 | composition 阶段 Enter 不误发送；同时仅一个前台聊天 |
| 工具确认 | 展示工具、原因和参数摘要；确认或拒绝 | 前端只回传 action ID；不得回传或替换真实执行参数 |
| 手动截图 | 全屏遮罩、跨屏框选、取消、附件预览和发送 | 物理坐标、逻辑坐标和截图像素在多 DPI 环境一致 |
| 设置 | 模型、TTS、主题、屏幕观察、记忆、MCP、运行循环、日志、插件声明式设置和启动项 | 保存原子化；只重载受影响服务；失败可恢复原配置 |
| 首次设置 | 配置聊天模型，创建、导入或选择角色 | 缺配置时可独立打开；完成后同会话进入桌宠 |
| 角色工作室 | 创建、编辑、导入、导出、立绘、布局、语音和草稿/发布流程 | 路径校验和角色包格式兼容；重复打开聚焦现有窗口 |
| 历史 | 分页、旧记录兼容、角色/语气/立绘信息和刷新 | 不一次载入全部历史；主窗口不被阻塞 |
| 诊断 | Brain、插件、MCP、TTS、资源、调度器、错误和恢复状态 | Brain 不可用时诊断仍可打开，输出不得泄露凭据 |
| Runtime 修复 | Runtime 缺失、损坏、协议不兼容和恢复指引 | 不依赖 Python UI；修复失败不会进入无限重启循环 |
| 托盘和系统菜单 | 显示、隐藏、设置、工作室、历史、退出和状态反馈 | 托盘可恢复穿透或隐藏窗口；退出执行统一关闭流程 |

`app/ui/` 中未在上表单独列出的辅助界面或行为，包括资源安装提示、TTS Bundle 迁移、颜色选择、调试覆盖层、窗口背景和插件设置控件，也 SHALL 在删除旧实现前逐项归类为“迁移”“受控替代”或“明确移除”。不得因其不是主窗口而遗漏。

### 22.6 必须迁移的功能链路

| 功能链路 | Python Brain 职责 | Tauri/Rust/Web 职责 | 等价要求 |
|---|---|---|---|
| 聊天 | 请求编排、LLM、工具循环、历史写入、回复分段 | 输入、状态呈现、事件接收、取消和确认 UI | 进度、最终回复、错误、取消和晚到事件不跨会话污染 |
| TTS | 文本选择、Provider、合成、语言守卫和取消 | 音频资源消费、播放队列、停止、音量和状态事件 | 角色/设置切换和退出立即停止旧音频；临时资源按 TTL 清理 |
| 快速接话 | 选择、缓存策略和正式回复仲裁 | 与正式 TTS 共用播放链和字幕呈现 | 不重叠播放，不抢占正式回复，失败时可只显示字幕 |
| 自动屏幕观察 | 空闲/冷却决策、编码策略、视觉消息和总结 | 屏幕捕获、区域/显示器信息和私有资源管理 | 关闭后停止调度；不抢占用户聊天；原始截图默认不持久化 |
| 主动互动与提醒 | 事件决策、提醒消费、上下文和回复生成 | 统一事件展示、音频和忙碌状态 | 提醒只消费一次；主动事件服从前台聊天仲裁 |
| 记忆与历史 | 写入、召回、整理、分页 DTO | 设置和历史 UI | 继续兼容现有数据；UI 不直接访问存储文件 |
| 角色和主题 | 角色解析、人格和配置服务 | 资源 URL、渲染、预览和窗口布局 | 同一角色状态同步到桌宠、设置、工作室和 Mobile |
| 插件与 MCP | Headless 插件装配、工具注册、MCP 生命周期 | 声明式设置呈现和诊断状态 | 原生 Qt UI 插件在导入前被拒绝；非 UI 插件继续工作 |
| Sakura Mobile | 共享聊天服务、忙碌态、角色和历史 | 无独立 Qt Bridge | 桌面与 Mobile 不得创建两套互相冲突的聊天状态 |
| 启动与恢复 | Bootstrap 状态、业务配置检查和 Brain 健康 | 进程监管、路由、窗口呈现和有限重启 | 配置缺失、Runtime 损坏和运行时崩溃进入不同路径 |
| 退出 | 停止新任务、插件、MCP、TTS 合成和调度器 | 停止音频、关闭窗口、终止 Host 和清理资源 | 正常退出码为 0，且无 Brain、MCP、TTS、浏览器或 WebView 残留 |

### 22.7 启动、首次设置与恢复

第 15.6 节的启动状态机 SHALL 在 P0 阶段完整落地，并满足：

1. 主窗口在 Bootstrap 判定完成前保持隐藏。
2. `ready` 只在 Runtime、Brain 基础健康、聊天模型和角色均可用时成立。
3. `onboarding_required` 是健康业务状态，不增加 Brain 崩溃计数，不触发退避重启。
4. 首次设置 SHALL 能在完整 `AppContext` 尚未建立时读取和保存最小配置。
5. 首次设置完成后 SHALL 重新评估 Bootstrap，并在当前进程内关闭首次设置、加载角色上下文和显示桌宠。
6. `runtime_repair` SHALL 在 Python 不可启动、协议不兼容或基础 Runtime 损坏时仍可呈现。
7. `brain_recovering` SHALL 保留 Tauri UI，并有限次数重启 Host；超过阈值后转入诊断或修复状态。
8. 任何状态切换 SHALL 幂等，重复事件不得创建重复窗口或第二个 Brain Host。

### 22.8 IPC、事件与前端安全边界

迁移不得使用新的隐式跨边界共享状态替代 Qt Signal/Slot。

所有 Tauri Command、Brain Request 和异步事件 SHALL：

- 使用稳定方法名和结构化 DTO。
- 携带足以隔离重启和并发请求的 `session_generation`、`interaction_id` 或 `request_id`。
- 对取消、重复确认、迟到回复和 Host 重启定义幂等行为。
- 对消息大小、超时和错误格式设置边界。
- 不向 WebView 暴露本地绝对路径、执行参数对象、凭据或内部 continuation context。
- 对角色图片、截图和音频使用受控 Asset URL、临时资源句柄或单次消费 Token。

WebView SHALL NOT 直接读取 `data/`、`characters/`、`plugins/` 或任意文件系统路径。所有读取、写入、导入和导出均通过受控 Command 完成。

### 22.9 数据与配置兼容

P0 迁移 SHALL 默认复用现有：

```text
data/
characters/
plugins/
聊天历史格式
角色包格式
设置文件格式
记忆存储
MCP 配置
插件 Manifest
```

迁移期间 SHALL NOT 为了 UI 重写执行破坏性数据迁移。

配置保存 SHALL 采用校验后写入；失败时保留最后一个可用配置。角色、主题、TTS、观察和插件设置应用后 SHALL 只重建受影响的服务，并向所有相关窗口发送一致的状态变更事件。

### 22.10 Qt 遗留退出规则

迁移期间允许以下代码作为临时对照存在，但不得进入生产启动图：

| 遗留范围 | 迁移期间用途 | P0 退出动作 |
|---|---|---|
| `legacy_qt_main.py` | 显式开发回退和行为对照 | 完成实机签字和回退窗口后删除 |
| `app/ui/` Qt 模块 | 行为参考、旧测试和纯逻辑提取来源 | 纯逻辑迁出后删除不可执行 UI 实现 |
| `requirements-legacy-qt.txt` | 临时运行旧 UI 测试 | Qt UI 测试退出后删除或移出正式仓库依赖入口 |
| `tools/settings-tauri`、`tools/studio-tauri` | 旧独立窗口实现和迁移参考 | 生产功能合并后删除独立 Sidecar 构建链 |
| 插件内 QWidget 设置页 | 兼容识别和迁移来源 | 转换为声明式 Schema/Web Module，或标记不兼容并移除 |
| `tests/ui` Qt 测试 | 旧行为基线 | 等价契约迁到 Python Headless、Rust 或 Web 测试后删除 |

P0 退出时，以下检查 SHALL 成立：

1. 生产 Python 文件和插件不包含可达的 `PySide6` 导入。
2. `main.py`、安装脚本、启动脚本和开机启动项只指向 Tauri。
3. CI 的产品测试不要求安装 PySide6。
4. 发布包不包含 Qt DLL、Qt Plugin、QML 或 PySide6 Wheel。
5. 仓库中如仍保留历史文档对 Qt 的文字引用，不得存在可执行回退入口。

### 22.11 P0 收口门禁

P0 SHALL 按以下门禁顺序退出：

#### Gate A：功能所有权完成

- Tauri 覆盖所有可见窗口。
- Rust/Python 新所有者覆盖所有 Qt 后台职责。
- 首次设置、Runtime 修复、诊断和崩溃恢复可独立工作。
- 等价矩阵中不存在“待迁移”。

#### Gate B：功能等价完成

- 自动测试覆盖聊天、取消、确认、TTS、截图、主动事件、设置、工作室、历史、插件、MCP 和退出。
- Windows 实机完成透明、拖动、置顶、托盘、IME、真实音频和真实在线聊天链路。
- 100%、125%、150%、200% DPI 和至少一组混合 DPI 多显示器完成窗口与截图验收。
- 所有“部分通过”和“受限”项均补充证据或由明确发布决策接受风险。

#### Gate C：Qt 生产链退出

- 正常启动导入图中 `PySide6` 和 `app.ui` 数量为 0。
- 删除旧 Qt 启动入口、独立桥、不可复用 QWidget 和对应生产依赖。
- Qt 行为测试已转换为 Headless、Rust、Web 或端到端测试。
- 安装包、升级包、开机启动和快捷方式只使用 Tauri 主程序。

#### Gate D：发布候选验收

- 在干净 Windows x64 系统完成安装、首次设置、聊天、TTS、重启、更新前置检查和卸载验证。
- 正常退出返回 0，故障恢复后无重复 Brain，退出后无残留子进程和临时敏感资源。
- 完整 Python 测试、Rust 测试、前端测试、格式检查和 release build 全部通过。
- `docs/migration/tauri-phase-1-parity-matrix.md` 更新为最终证据，不再包含未处置项。

### 22.12 当前执行顺序

基于第 22.2 节的当前基线，后续工作 SHALL 按以下顺序推进：

1. 收口 Bootstrap 路由：完成 `ready`、`onboarding_required`、`runtime_repair` 和 `brain_recovering` 的窗口行为与同会话切换。
2. 补齐真实主链：使用真实模型、真实工具和真实音频设备验收聊天、取消、确认、TTS、快速接话和主动互动。
3. 补齐桌面物理场景：完成拖动、置顶抑制、托盘、截图框选、100% 至 200% DPI 和混合 DPI 多显示器验收。
4. 清理 Qt-only 辅助能力：迁移资源提示、颜色选择、TTS Bundle、调试覆盖层和插件 QWidget 设置页。
5. 退出遗留链：删除旧 Qt 入口、旧独立窗口桥、Qt UI 测试和发布依赖，收敛安装及启动脚本。
6. 完成干净机发布候选验收，并冻结 Phase 1 的最终等价矩阵。

前一项未达到相应门禁时，后一项 MAY 并行准备测试或文档，但不得通过删除对照实现、扩大新功能范围或改变数据格式规避未完成验收。

### 22.13 自动化与实机验证要求

P0 至少 SHALL 运行：

```powershell
.\runtime\python.exe -m pytest
cargo test --manifest-path desktop/src-tauri/Cargo.toml
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
cargo clippy --manifest-path desktop/src-tauri/Cargo.toml --all-targets -- -D warnings
cargo build --release --manifest-path desktop/src-tauri/Cargo.toml
```

前端 SHALL 具备可在 CI 中运行的语法或行为测试，至少覆盖：

```text
IME composition
聊天发送与取消
字幕队列和迟到事件
工具确认
角色切换
窗口重复打开
设置应用
截图选择
启动路由
Host 重启后的 session 隔离
```

Windows 实机验收 SHALL 使用同一角色和配置记录冷启动、热启动、首个窗口、Brain Ready、空闲资源、退出码和残留进程。硬件或外部服务受限时，相关项只能标记为“受限”，不得标记为“通过”。

## 23. 实施阶段

### Phase 0：技术验证

完成透明窗口、输入法、多显示器、DPI、托盘、音频、截图、IPC 和 Python 生命周期原型。

### Phase 1：Tauri 主应用完整迁移 — 当前 P0

按第 22 节迁移全部 Qt 界面和功能，完成启动路由、功能等价、Qt 生产链退出和发布候选验收。

Phase 1 完成前，项目 SHALL NOT 宣称桌面迁移结束，也 SHALL NOT 删除仍用于未验收行为对照的 Qt 代码。

### Phase 2：平台 API

实现 IPC、Event Bus、Observation、Capability、Permission、Path Layout 和 Audit。

### Phase 3：能力迁移

将浏览器、桌面工具、TTS 播放、屏幕捕获和 Renderer 迁移到新边界。Phase 1 已由 Rust 接管的播放与捕获实现 SHALL 直接演进，不得重新引入 Python UI 或 Qt 所有权。

### Phase 4：安装更新体系

完成 Runtime Manager、锁文件、组件安装、签名、离线包和双版本回滚。

### Phase 5：Agent 能力

实现 Filesystem、Terminal、Browser Automation 和 Computer Control 插件。

### Phase 6：生态

建设角色、插件、模型和能力市场。

## 24. 验收标准

Sakura Platform 1.0 必须满足：

1. 干净 Windows x64 系统无需安装系统 Python 即可安装。
2. Tauri UI 在 Python Runtime 损坏时仍能进入修复界面。
3. 默认安装可完成角色、聊天、TTS、基础记忆和主动互动。
4. 默认安装不具备文件写入、终端执行和输入控制能力。
5. Python Host 可由 Tauri 启动、关闭、监控和恢复。
6. 用户拒绝权限后，对应操作不会产生 OS 副作用。
7. 高风险操作可在审计日志中查询。
8. 用户可以关闭屏幕、窗口和输入活动感知。
9. Runtime 安装失败不会破坏当前可用 Runtime。
10. App 更新失败可以恢复旧版本。
11. 插件新增权限时必须重新授权。
12. 本机数据和便携数据模式均可工作。
13. 基础 Runtime 不再强制依赖 PySide6、Torch、Playwright。
14. 应用、Runtime、插件和离线组件均经过签名或可信哈希验证。
15. 无可用聊天模型配置或无角色时，应用进入 Tauri 首次设置界面，不显示桌宠且不触发 Brain Host 自动重启。
16. 聊天模型配置和角色均可用时，应用直接显示桌宠主窗口。
17. 首次设置完成后，应用无需重启即可切换到桌宠主窗口。
18. 业务配置缺失与 Runtime 损坏进入不同的恢复路径和界面。
19. 所有用户可见桌面界面均由同一个 Tauri App 承载，不存在生产 QWidget 窗口。
20. 正常生产启动、运行、恢复和关闭路径不导入 PySide6 或 `app.ui`。
21. 桌宠、聊天、确认、TTS、截图、主动互动、提醒、设置、工作室、历史、诊断、托盘、插件、MCP 和 Mobile 不存在仅 Qt 可用的功能。
22. 透明、拖动、置顶、IME、真实音频、物理托盘和混合 DPI 多显示器均具备 Windows 实机验收证据。
23. 发布包、基础 Runtime、安装脚本、快捷方式和开机启动项不包含或引用 Qt 主程序与 PySide6。
24. Qt 插件设置页已转换为声明式设置或明确拒绝，不会在 Brain Host 中导入 QWidget。
25. 角色、设置、历史、记忆、插件和 MCP 数据在迁移前后兼容，且不要求用户重建配置。
26. WebView 不接收本地绝对路径、凭据、工具真实执行参数或内部 continuation context。
27. 正常退出返回 0，且无 Brain、MCP、TTS、浏览器、WebView 或临时敏感资源残留。
28. 旧 Qt 入口只可在未完成验收的临时回退窗口存在；Gate D 完成后必须从可执行产品代码中删除。

## 25. 最终架构定义

> Sakura 是一个由 Tauri 驱动的 AI Companion 平台。Rust/Tauri 构成可信桌面核心，负责桌面、感知、权限、能力执行、插件和运行环境；Python 构成伙伴大脑，负责人格、记忆、上下文、LLM 和 Agent 规划；外部行动通过受权限控制的 Capability Plugin 扩展。
