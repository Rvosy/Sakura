# Sakura AI Companion Platform Specification

| 字段 | 内容 |
|---|---|
| Spec ID | SAP-001 |
| 状态 | Draft for Review |
| 文档版本 | 0.2 |
| 目标版本 | Sakura Platform 1.0 |
| 更新日期 | 2026-07-13 |
| 首发平台 | Windows x64 |
| 后续平台 | Windows ARM64、macOS、Linux，按独立路线推进 |

## 1. 摘要

Sakura SHALL 从 Python/PySide6 桌宠应用迁移为由 Tauri 驱动的 AI Companion 平台。

Sakura Platform 1.0 SHALL 只交付 **Assistant** 产品形态。Assistant 默认提供角色、人格、对话、声音、记忆、感知、主动互动、上下文理解和低风险任务辅助。

“陪伴”是 Assistant 的基础产品特性，不作为独立产品层级或运行模式。

**Agent** SHALL 作为 Assistant 之上的后续可选扩展，通过 Agent Planner Plugin 和高权限 Capability Plugin 实现。Sakura 1.0 SHALL 预留 Agent 所需的平台接口、安全边界和插件模型，但 SHALL NOT 默认安装或依赖正式 Agent 实现。

系统由三个基础层组成：

```text
Sakura Bootstrap Layer
└─ Launcher / Updater

Sakura Platform Core
├─ Trusted Desktop Core：Rust/Tauri
└─ Companion Brain Host：Python
```

- Launcher / Updater 负责版本选择、更新切换、启动健康确认和回滚。
- Rust/Tauri 负责桌面集成、可信 UI、感知、安全、权限、能力执行、插件管理和 Runtime 管理。
- Python Brain 负责人格、记忆、上下文、LLM、主动互动决策和低风险任务规划。
- 外部行动 SHALL 通过受权限控制的 Capability Plugin 执行。

Rust/Tauri SHALL 是危险能力的最终授权边界。Python Brain、WebView、插件输出和外部内容均不得被视为天然可信授权来源。

## 2. 规范性术语

本文使用以下术语：

- **MUST / SHALL**：必须满足。
- **MUST NOT / SHALL NOT**：禁止。
- **SHOULD**：原则上应满足，除非存在明确且记录在案的理由。
- **SHOULD NOT**：原则上不应采用，除非存在明确且记录在案的理由。
- **MAY**：可选实现。
- **Core**：Sakura 官方可信代码，包括 Launcher、Updater 和 Rust/Tauri Desktop Core。
- **Brain**：运行于受 Core 管理的 Python Host 中的伙伴大脑。
- **Capability**：Brain 可请求、由 Rust Core 授权和路由的外部能力。
- **Observation**：由系统状态或用户交互产生、经过隐私策略处理的感知事件。
- **Plugin**：扩展 Sakura 行为、数据源、渲染或能力边界的组件。
- **Pack**：不包含可执行代码的角色、人格、主题、模型或资源包。
- **Provider**：某一 Capability、Memory、Voice 或 Renderer 接口的具体实现。
- **Assistant**：Sakura 1.0 默认产品形态。
- **Agent Extension**：建立在 Assistant 之上的后续可选自主执行扩展。
- **High-risk Capability**：可能产生文件、进程、输入、账户、网络或其他外部副作用的能力。
- **Trusted UI**：由 Rust/Tauri Core 控制、用于权限确认、更新、凭据和安全状态展示的界面。

## 3. 产品形态

### 3.1 Assistant

Assistant 是 Sakura 的默认且完整的产品形态。

Sakura 1.0 SHALL 提供：

```text
角色与人格
聊天
基础记忆
TTS
时间感知
空闲状态
Sakura 内部交互感知
用户授权后的桌面状态感知
主动互动
上下文理解
低风险任务辅助
```

Assistant MAY 通过内置能力或可选低风险插件增加：

```text
联网搜索
网页读取
文件读取
屏幕理解
资料整理
日历查询
知识库查询
外部账户只读访问
```

Assistant 能力 SHOULD 以信息获取、内容理解、建议生成、资料整理和用户协作为主。

Assistant MAY 执行有限、可解释的单步 Capability Call，但 SHALL NOT 在没有当前用户明确意图和必要授权的情况下执行高风险操作或连续自主操作。

Sakura 1.0 默认安装 SHALL NOT 包含：

```text
文件写入、移动或删除
终端执行
浏览器自动操作
鼠标键盘控制
软件自动化
外部账户写入
无人监督的多步骤执行循环
```

Assistant SHALL 能够在未安装任何 Agent Extension 的情况下独立、完整地运行。

### 3.2 Agent Extension

Agent 是建立在 Sakura Assistant 之上的后续可选扩展能力，不是独立应用，也不是 Sakura 1.0 默认交付目标。

Agent Extension SHOULD 由多个彼此解耦的插件组成：

```text
Agent Planner Plugin
Agent Policy Plugin
Filesystem Capability Plugin
Terminal Capability Plugin
Browser Automation Plugin
Computer Control Plugin
Integration Write Plugin
```

Agent Extension MAY 提供：

```text
多步骤任务规划
连续 Capability 调用
文件写入
终端执行
浏览器自动操作
鼠标键盘控制
软件自动化
外部服务写入
```

Agent Planner 只负责生成、维护和推进任务计划。具体外部操作 SHALL 由独立 Capability Plugin 提供，并经过 Rust Core 的 Permission Manager 和 Audit System。

安装 Agent Planner SHALL NOT：

```text
自动安装高权限 Capability Plugin
自动授予任何高风险权限
绕过 Rust Core 直接访问危险 OS 原语
将插件安装视为永久操作授权
替换用户已经确认的关键参数
```

未安装或未启用 Agent Extension 时，系统 SHALL 保持 Assistant 形态。

用户禁用或卸载 Agent Extension 后，Sakura SHALL 恢复为完整可用的 Assistant。

### 3.3 Assistant 与 Agent 的关系

Assistant 是基础产品，Agent 是可选增量扩展。

二者共享：

```text
Launcher / Updater
Tauri UI
Rust Trusted Desktop Core
Python Brain Host
角色与人格
记忆
对话
Observation System
Capability System
Permission System
Plugin System
Audit System
```

Agent SHALL 复用 Assistant 的平台基础设施，不得建立第二套权限系统、插件系统、更新通道或执行通道。

Sakura 1.0 SHALL 完成 Agent 所需的接口、权限、审计、停止和插件边界设计，但 MAY 不发布正式 Agent Planner 和任何高权限 Agent Capability Plugin。

## 4. 产品目标

Sakura Platform 1.0 SHALL 达成以下目标：

1. 将桌面、窗口、进程、安装和权限边界迁移到 Rust/Tauri。
2. 在不依赖系统 Python 的情况下完成 Windows x64 安装和运行。
3. 保留 Python 在人格、记忆、LLM 和上下文生态上的开发效率。
4. 默认提供可信、低权限、可感知且具有陪伴感的 Assistant。
5. 建立统一的 Observation、Capability、Permission、Plugin 和 Update 基础设施。
6. 支持 Runtime、插件和资源组件的独立安装、校验、健康检查和回滚。
7. 为后续 Agent Extension 预留安全、可审计、可撤销的执行边界。
8. 即使 Python Runtime 损坏，用户仍能进入 Tauri 修复和更新界面。

## 5. 非目标

Sakura Platform 1.0 不要求：

- 提供正式可用的 Agent Planner。
- 提供文件写入、终端执行、输入控制或外部账户写入插件。
- 支持无人监督的连续自主 Agent。
- 完全隔离任意第三方 Python 代码。
- Windows、macOS、Linux 同步达到功能一致。
- 插件市场支付和商业分成。
- 云端账户和跨设备同步。
- 兼容旧版 Qt UI 插件。
- 首发支持 Windows ARM64。
- 允许 Character Pack 或普通资源包执行代码。
- 通过一个“超级 Agent 插件”同时获得规划、授权和所有危险能力。

## 6. 设计原则

系统 SHALL 遵守以下原则：

### 6.1 最小权限

默认安装只包含完成 Assistant 所需的最低权限和组件。

### 6.2 规划与执行分离

Brain 或 Agent Planner 可以提出 Capability Call，但不能自行授权或直接执行危险原语。

### 6.3 可信确认路径

高风险权限确认 SHALL 由 Trusted UI 渲染。Brain、插件和外部内容提供的描述只能作为不可信辅助说明。

### 6.4 数据最小化

Observation、日志、审计和临时资源 SHALL 只保留完成当前功能所需的数据。

### 6.5 可恢复更新

App、Runtime 和插件更新 SHALL 安装到新位置，经过健康检查后再切换，不得原地破坏当前可用版本。

### 6.6 默认安全失败

版本不兼容、权限不确定、签名失败、IPC 非法或 Scope 无法可靠验证时，系统 SHALL 拒绝执行，而不是尝试带病运行。

### 6.7 外部内容不是用户指令

网页、文件、邮件、插件结果和模型输出 SHALL 默认视为不可信数据，不得自动成为授权指令。

## 7. 威胁模型与信任边界

### 7.1 需要防御的威胁

系统设计 SHALL 考虑至少以下威胁：

```text
恶意或被攻陷的社区插件
被篡改的插件、Runtime、模型或更新包
恶意 Character Pack、Renderer 或 Web 内容
网页、文件、邮件中的 Prompt Injection
异常或被操纵的 LLM 输出
Python Brain 被注入、失控或崩溃
IPC 消息伪造、重放、超长或畸形输入
未授权本地进程尝试冒充 Python Brain
路径穿越、符号链接、Junction 和 TOCTOU
更新服务器回滚旧版本或投递过期元数据
日志、审计和崩溃报告泄露敏感信息
插件借助子进程、Shell 或环境变量扩大权限
```

### 7.2 信任等级

以下组件被视为可信计算基：

```text
SakuraLauncher.exe
SakuraUpdater.exe
Rust/Tauri Trusted Desktop Core
官方签名验证逻辑
Permission Manager
Capability Broker
Trusted UI
```

以下来源 SHALL NOT 被视为授权主体：

```text
Python Brain
LLM 输出
Agent Planner
插件代码
插件 Manifest 中的自然语言说明
Renderer Web Module
网页、文件、邮件或剪贴板内容
用户未明确确认的模型推断
```

Rust Core SHALL NOT 因 Capability Request 来自 Python Brain 而自动信任该请求。

### 7.3 不承诺的安全属性

Sakura 1.0 SHALL NOT 宣称任意第三方 Python 插件已被完全沙箱隔离。

独立进程、受控目录、权限声明和审计只提供风险降低，不等同于强安全沙箱。

强隔离 MAY 在后续版本采用 WASM、Windows AppContainer 或其他 OS 沙箱实现。

## 8. 系统架构

```text
┌──────────────────────────────────────────────┐
│        Sakura Launcher / Updater             │
│ Version Select / Switch / Health / Rollback  │
└──────────────────────┬───────────────────────┘
                       │ Launch Contract
┌──────────────────────▼───────────────────────┐
│                  Tauri UI                    │
│ Pet / Chat / Settings / Studio / Market      │
│ Trusted Permission / Update / Repair UI      │
└──────────────────────┬───────────────────────┘
                       │ Tauri Commands
┌──────────────────────▼───────────────────────┐
│       Trusted Desktop Core — Rust            │
│                                              │
│ Window / Tray / Startup                      │
│ Observation Manager / Typed Event Buses      │
│ Permission Manager / Capability Manager      │
│ Plugin Manager / Runtime Manager             │
│ Audio / Screen Capture / Credential Broker   │
│ Process Host / Audit / Resource Manager       │
└──────────────────────┬───────────────────────┘
                       │ Authenticated Versioned IPC
┌──────────────────────▼───────────────────────┐
│        Companion Brain Host — Python         │
│                                              │
│ Personality / LLM / Memory                   │
│ Context Builder / Companion Policy           │
│ Low-risk Planning / Capability Selection     │
│ Optional Agent Planner Plugin Host            │
└──────────────────────┬───────────────────────┘
                       │ Capability Request
┌──────────────────────▼───────────────────────┐
│              Plugin Providers                │
│ Brain / Memory / Voice / Renderer            │
│ Read-only Assistant Capabilities             │
│ Optional Future Agent Capabilities           │
└──────────────────────────────────────────────┘
```

Tauri WebView SHALL NOT 直接持有危险 OS 权限。

所有危险能力调用 SHALL 由 Rust Core 重新验证，且不得只依赖前端隐藏、Python 判断或插件自报风险等级。

## 9. 组件所有权与版本边界

Sakura 1.0 SHALL 采用以下组件归属：

| 组件 | 归属 | 更新单位 |
|---|---|---|
| Launcher / Updater | Bootstrap Layer | Bootstrap Update |
| Tauri UI / Rust Core | App | App Update |
| Python Brain 业务代码 | App | App Update |
| Python 解释器与基础第三方依赖 | Runtime | Runtime Update |
| Brain / Memory / Voice 等插件 | Plugin | Plugin Update |
| 模型和大型资源 | Model / Resource | Model Update |
| 用户数据结构 | Data Schema | Data Migration |

Sakura 1.0 SHALL NOT 将 Brain 业务代码作为独立更新通道。Brain 业务代码跟随 App 发布，以降低 App、Brain 和 IPC 的组合兼容复杂度。

后续版本 MAY 引入独立 Brain Distribution，但必须先定义明确的兼容矩阵和回滚策略。

## 10. Launcher / Updater 职责

Launcher / Updater SHALL 负责：

- 根据可信状态文件选择当前 App 版本。
- 启动指定 App 并传入受控启动参数。
- 检测启动健康标志和连续崩溃。
- 在更新失败或新版本无法健康启动时回滚。
- 在 App 退出后完成文件切换。
- 拒绝签名、哈希、平台、架构或回滚索引不符合要求的更新。
- 在主 App 损坏时仍可进入修复流程。

推荐结构：

```text
Sakura/
├─ SakuraLauncher.exe
├─ SakuraUpdater.exe
├─ app/
│  └─ versions/
│     ├─ 1.0.0/
│     └─ 1.0.1/
├─ runtime/
│  └─ versions/
├─ plugins/
├─ models/
├─ state/
│  ├─ current.json
│  └─ health/
├─ cache/
└─ portable.flag
```

根目录的 `SakuraLauncher.exe` SHALL 是稳定启动器，不是具体版本的业务 App。

## 11. Rust/Tauri Trusted Desktop Core 职责

Trusted Desktop Core SHALL 负责：

- 桌宠和应用窗口。
- 托盘、单实例、快捷键和开机启动。
- Trusted UI 和危险操作确认界面。
- Python Host 启停、鉴权、监控和异常恢复。
- Runtime 安装、健康检查、切换和回滚。
- Observation 采集、裁剪、限频和授权。
- Capability 注册、Provider 选择、路由和执行。
- 权限授权、撤销、过期和审计。
- 屏幕捕获和系统窗口状态。
- 音频播放和播放状态事件。
- 插件安装、签名校验、隔离和生命周期。
- App、Runtime、插件、模型和资源更新协调。
- 凭据存储和受控网络请求所需的 Credential Broker。
- 临时文件、音频、截图和大对象资源句柄管理。

Rust Core SHALL 是危险能力的最终授权边界。

## 12. Python Companion Brain 职责

Python Brain SHALL 负责：

- 人格和角色 Prompt。
- LLM 调用。
- 对话管线。
- 记忆读写和召回。
- 上下文构建。
- 主动互动决策。
- 低风险任务理解和有限规划。
- Capability 选择和请求生成。
- Capability 结果解释与总结。
- 可选 Agent Planner Plugin 的加载和协调接口。

Python Brain SHALL NOT 直接执行危险操作，例如：

```python
os.remove(...)
subprocess.run(...)
pyautogui.click(...)
```

Brain SHALL 通过 IPC 请求已注册 Capability。

默认 Python Brain SHALL NOT 包含无限自主执行循环。

迁移期间 MAY 临时保留兼容适配层，但正式架构不得依赖该适配层，且适配层不得绕过 Rust 权限检查。

## 13. Event Bus

系统 SHALL 区分三类事件总线：

```text
Rust System Event Bus
    原生窗口、输入活动、更新、Runtime、插件和进程状态

Observation Bus
    经过隐私过滤、带来源和敏感等级的感知事件

Python Domain Event Bus
    聊天、人格、记忆、TTS、Assistant 和可选 Agent 状态
```

事件 SHALL 具有：

```text
稳定名称
独立版本
结构化 Payload
来源标识
时间戳
必要时的顺序号
```

系统 SHALL NOT 使用一个无类型全局事件总线承载所有数据。

跨边界事件 SHALL 明确是否允许重放、是否持久化以及接收方的兼容策略。

## 14. Observation System

### 14.1 原则

Observation 基础设施 SHALL 属于 Core。

敏感感知 SHALL 经过用户授权。功能由 Core 提供不代表默认开启。

### 14.2 感知级别

| 级别 | 示例 | 默认策略 |
|---|---|---|
| Level 0 | 时间、Sakura 点击、聊天状态、TTS 状态 | 默认允许 |
| Level 1 | 当前应用、空闲时间、全屏状态、输入活动 | 用户开启 |
| Level 2 | 截图、OCR、窗口标题、剪贴板、麦克风 | 明确授权 |

系统 SHALL NOT 记录全局键盘的实际按键内容。

系统 MAY 记录“最近发生键盘活动”之类的状态。

持续屏幕、麦克风或摄像头感知 SHALL 显示持续可见的状态指示和快速关闭入口。

### 14.3 事件格式

Observation Event SHOULD 使用以下结构：

```json
{
  "id": "obs_01",
  "version": 1,
  "type": "desktop.active_window.changed",
  "timestamp": "2026-07-13T12:00:00+08:00",
  "sequence": 42,
  "source": "rust.window-observer",
  "sensitivity": "metadata",
  "retention": "transient",
  "provenance": {
    "trust": "local_observation"
  },
  "payload": {
    "application": "Visual Studio Code",
    "category": "development"
  }
}
```

### 14.4 保留策略

`retention` SHALL 使用固定枚举：

| 值 | 含义 |
|---|---|
| `transient` | 只存在于当前处理调用，不落盘 |
| `session` | 当前会话有效，会话结束清理 |
| `short_term` | 有明确 TTL，到期自动删除 |
| `persistent` | 用户明确允许持久化 |

Level 2 原始截图默认 SHALL 使用 `transient` 或带短 TTL 的 `short_term`，不得默认持久化。

### 14.5 数据最小化

Observation Manager SHALL：

- 在发送给 Python 前过滤不必要字段。
- 对事件限频和去重。
- 默认不持久化原始截图、麦克风流或剪贴板内容。
- 默认不将 Observation 上传到非用户配置的服务。
- 允许用户关闭每类 Observation。
- 允许用户查看当前启用的感知能力。
- 在崩溃恢复后清理过期临时资源。
- 对临时文件使用随机名称、受控 ACL 和不可猜测资源句柄。

## 15. 数据来源与 Prompt Injection 防护

所有进入 Brain 的外部内容 SHOULD 带有来源信息：

```json
{
  "content": "...",
  "provenance": {
    "source": "browser.page",
    "trust": "untrusted_external",
    "uri": "https://example.com"
  }
}
```

来源类型至少包括：

```text
user_direct
local_observation
trusted_core
plugin_output
untrusted_external
model_generated
```

系统 SHALL 遵守：

- 网页、文件、邮件、剪贴板和插件结果中的指令默认视为数据，而不是用户命令。
- 外部内容提出的能力调用 SHALL NOT 被视为用户授权。
- 高风险 Capability Request SHOULD 可追踪至用户原始请求、Brain 计划和触发数据来源。
- Permission UI SHALL 区分“用户请求的目标”和“外部内容建议的操作”。
- 当当前用户意图与外部内容要求不一致时，系统 SHALL 停止并请求用户确认。

## 16. Capability System

### 16.1 Capability 定义

Capability 是 Brain 可请求、由 Rust Core 管理的外部能力。

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

### 16.2 执行流程

```text
Brain 生成 Capability Request
        ↓
Capability Manager 查找兼容 Provider
        ↓
Core 规范化并验证参数
        ↓
Permission Manager 计算授权需求
        ↓
必要时由 Trusted UI 请求用户确认
        ↓
绑定确认摘要并锁定关键参数
        ↓
Executor 执行前再次验证 Scope
        ↓
返回结构化结果和审计记录
        ↓
Brain 继续推理或总结
```

### 16.3 Provider 选择

Brain SHOULD 只请求 Capability，不直接决定最终 Provider。

请求格式 MAY 包含 `preferred_provider` 作为提示，但 Rust Core SHALL 根据以下条件选择最终 Provider：

```text
用户默认设置
插件信任等级
权限与 Scope
兼容版本
Provider 健康状态
策略优先级
```

`preferred_provider` SHALL NOT 作为授权依据。

### 16.4 请求格式

```json
{
  "request_id": "cap_01",
  "interaction_id": "chat_01",
  "capability": "filesystem.move",
  "preferred_provider": "sakura.filesystem",
  "arguments": {
    "source": "D:/Desktop/a.txt",
    "target": "D:/Documents/a.txt"
  },
  "reason": "按照用户要求整理桌面文件",
  "provenance_refs": ["user_msg_01", "plan_step_02"]
}
```

`reason` 是不可信说明文本，不得决定权限等级或确认内容。

### 16.5 结果格式

```json
{
  "request_id": "cap_01",
  "status": "success",
  "provider_id": "sakura.filesystem",
  "result": {
    "moved": true
  },
  "audit_id": "audit_01"
}
```

结果 SHALL 区分：

```text
success
permission_denied
user_cancelled
invalid_arguments
provider_unavailable
timeout
execution_failed
partial_success
```

## 17. Permission System

### 17.1 权限类型

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
credential.use
```

### 17.2 风险等级

| 等级 | 示例 | 默认行为 |
|---|---|---|
| Low | 内部状态、明确公开数据 | 可直接执行 |
| Medium | 限定目录读取、限定域名访问 | 首次、会话或规则确认 |
| High | 文件写入、终端执行、账户写入 | 每次或精确短期规则确认 |
| Critical | 批量删除、输入控制、凭据导出 | 强制逐次确认 |

Critical 权限 SHALL NOT 提供无期限的“始终允许”。

### 17.3 授权范围

权限 SHALL 支持结构化 Scope：

```yaml
filesystem.read:
  paths:
    - D:\Documents

network.access:
  domains:
    - api.github.com
  methods:
    - GET

terminal.execute:
  executables:
    - git.exe
  cwd:
    - D:\Projects\Sakura
```

### 17.4 授权决策

系统 SHALL 支持：

```text
允许本次
允许本次会话
允许限定时间
始终允许（仅适用于允许永久授权的权限）
拒绝
撤销
```

插件更新新增权限、扩大 Scope 或提高风险等级时 SHALL 重新授权。

### 17.5 规范化授权绑定

用户确认 SHALL 与规范化后的请求绑定，而不是只绑定自然语言摘要。

授权摘要 SHOULD 至少覆盖：

```text
Capability ID
最终 Provider ID
规范化后的关键参数
解析后的 Scope
Interaction ID
有效期
随机 Nonce
```

推荐计算：

```text
authorization_digest = SHA-256(
  capability
  + provider_id
  + canonical_arguments
  + resolved_scope
  + interaction_id
  + expiry
  + nonce
)
```

确认后，Brain、插件和 Provider SHALL NOT 替换关键参数。

执行前 Core SHALL 重新计算并验证授权摘要。

### 17.6 文件系统 Scope

Windows 文件路径授权 SHALL 处理：

```text
大小写不敏感
绝对路径规范化
符号链接
Junction / Reparse Point
UNC 路径
8.3 短路径
Alternate Data Streams
相对路径
路径穿越
```

Core SHALL 在执行时解析最终目标并重新验证 Scope，避免确认与执行之间的 TOCTOU。

仅通过字符串前缀判断路径 SHALL NOT 被视为有效安全校验。

### 17.7 终端执行 Scope

仅限制可执行文件名不足以构成安全授权。

`terminal.execute` Scope SHOULD 支持：

```yaml
terminal.execute:
  executable: git.exe
  arguments:
    command_allowlist:
      - status
      - diff
  cwd:
    paths:
      - D:\Projects\Sakura
  environment:
    inherit: minimal
  shell: false
  network: denied
  timeout_seconds: 30
```

系统 SHOULD 优先提供结构化 Capability，例如：

```text
git.status
git.diff
archive.extract
process.list
```

而不是默认暴露通用 Shell。

### 17.8 审计

高风险操作 SHALL 记录：

```text
时间
会话和 Interaction
用户原始请求引用
Brain 计划步骤引用
数据来源引用
插件和 Provider
Capability
规范化参数摘要
权限决策
授权 Scope
执行结果
错误信息
```

审计日志 SHALL NOT 明文保存密码、Token、Cookie、完整敏感文件内容或可直接复用的凭据句柄。

## 18. 主动互动边界

主动互动 MAY：

```text
主动问候
提出建议
提醒用户
展示观察到的低敏感状态
准备草稿或计划
询问是否执行某项操作
```

主动互动 SHALL NOT 在缺少当前用户明确意图的情况下执行 Medium、High 或 Critical Capability。

主动互动 SHALL NOT 自动启动 Agent 多步骤执行循环。

用户关闭主动互动后，系统 SHALL 停止非用户触发的主动消息和相关非必要 Observation 处理。

## 19. 插件模型

### 19.1 插件运行模型

| 类型 | 默认运行位置 | 可执行代码 | 默认可访问资源 |
|---|---|---:|---|
| Character / Personality Pack | Core 资源加载器 | 否 | 声明的静态资源 |
| Brain Plugin | Python Host 或独立 Python Sidecar | 是 | Brain Plugin API |
| Agent Planner Plugin | Python Host 或独立 Sidecar | 是 | 计划 API，不含 OS 原语 |
| Memory Provider | Python Host / Sidecar | 是 | 专属数据目录和 Memory API |
| Voice Provider | Sidecar / Provider Host | 是 | 语音合成输入与资源输出 |
| Renderer Provider | 隔离 WebView / 受控 Sidecar | 是 | Renderer Protocol |
| Capability Plugin | Rust Broker / 受控 Sidecar | 是 | 已声明 Capability 和 Scope |
| Integration Plugin | Broker / Sidecar | 是 | 声明的网络域名和凭据句柄 |

### 19.2 Character / Personality Pack

角色和人格 SHOULD 使用无代码资源包：

```text
角色设定
Prompt
立绘
主题
语音配置
行为偏好
```

资源包默认 SHALL NOT 执行 JavaScript、Python、Rust 或其他代码。

### 19.3 Brain Plugin

Brain Plugin 可提供：

```text
Prompt Patch
Context Provider
Companion Policy
Emotion Model
Conversation Strategy
```

Brain Plugin SHALL NOT 直接获得危险 OS 能力。

### 19.4 Agent Planner Plugin

Agent Planner Plugin 可提供：

```text
任务分解
计划状态管理
步骤推进
失败恢复策略
停止条件
用户确认节点
```

Agent Planner Plugin SHALL NOT 直接执行文件、终端、浏览器或输入原语。

Sakura 1.0 MAY 只实现 Agent Planner Plugin API 和测试 Provider，不发布正式 Agent Planner。

### 19.5 Memory Provider

Memory API SHALL 支持可替换实现。

默认实现 SHOULD 为轻量本地存储。向量记忆作为可选组件安装。

### 19.6 Renderer Provider

Renderer Provider SHALL 使用跨进程或受控前端协议，不再返回 Python QWidget。

Renderer 可采用：

```text
Web Module
WebGL
Live2D
VRM
受控 Sidecar
```

主窗口、透明层级、输入穿透和显示器定位 SHALL 由 Tauri 管理。

### 19.7 Voice Provider

语音职责 SHALL 拆分为：

```text
Provider：语音合成
Rust Core：音频资源管理与播放
```

Provider 返回音频文件、音频流或受控资源句柄。

### 19.8 Capability Plugin

Capability Plugin 提供工具定义和执行适配器。

危险原语 SHALL 通过 Rust Broker 或受控 Sidecar 执行。

### 19.9 Integration Plugin

Integration Plugin 可连接 GitHub、Notion、Calendar 等服务，并声明：

```text
网络域名
HTTP 方法
账户读取
账户写入
本地凭据需求
OAuth Scope
```

## 20. Renderer 安全模型

可执行 Renderer SHALL：

- 运行于隔离 WebView、iframe 或受控 Sidecar。
- 默认不拥有 Tauri Command Bridge。
- 默认不拥有文件系统访问。
- 默认不拥有网络访问。
- 不与设置页、权限页或主 UI 共享 DOM 上下文。
- 使用独立 Content Security Policy。
- 只能通过版本化 Renderer Protocol 接收状态和发送交互事件。
- 无法伪造 Trusted UI 权限确认窗口。

Character Pack 中包含可执行 Web 代码时，安装器 SHALL 将其识别为 Renderer Plugin，而不是普通资源包，并展示相应风险和权限。

## 21. 插件清单

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

compatibility:
  app: ">=1.0.0 <2.0.0"
  runtime_schema: 1

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

artifacts:
  - path: plugin.dll
    sha256: "..."

signature:
  algorithm: ed25519
  key_id: "sakura-official-2026"
  value: "..."
```

Manifest SHALL 包含：

```text
格式版本
插件 ID 和版本
发布者
插件 API 版本
运行类型
兼容版本
权限声明
Capability 声明
文件列表与哈希
签名信息
```

Manifest 中声明的风险等级 SHALL 由 Core 策略校验，插件不得自行降低系统定义的最低风险等级。

## 22. 插件信任模型

插件 SHALL 标记为：

```text
Official
Verified
Community
Local Development
```

首版 SHOULD 提供：

- 数字签名。
- 发布者身份和密钥 ID 展示。
- 独立进程运行选项。
- 权限声明和变更提示。
- 能力白名单。
- 受控工作目录。
- 健康检查。
- 崩溃隔离。
- 权限审计。
- 安装和更新回滚。
- 已撤销签名密钥拒绝机制。

签名系统 SHALL 支持密钥轮换、吊销和过期策略。

本地开发插件 MAY 使用开发模式加载，但 SHALL 显示持续可见的非生产状态，并与正式插件数据隔离。

## 23. 凭据管理

Integration Plugin 使用的 Token、Cookie、API Key 和 OAuth 凭据 SHALL 由 Core Credential Broker 管理。

Sakura 1.0 SHOULD 使用 Windows Credential Manager 或等价系统安全存储。

系统 SHALL 遵守：

- 配置文件、普通日志和审计日志不得保存明文凭据。
- 插件 SHOULD 获取 opaque credential handle，而不是原始 Token。
- 只有授权的 Broker 或请求执行器可以解析并使用凭据。
- OAuth 回调 SHOULD 由 Core 或受信任 Broker 处理。
- 用户可以查看账户身份、授权 Scope 和过期状态。
- 插件卸载时用户可以选择保留、删除或撤销凭据。
- 凭据导出 SHALL 视为 Critical 操作并逐次确认。

## 24. IPC 协议

### 24.1 传输

Tauri SHALL 启动 Python Host，并通过本地进程管道通信。

协议 SHOULD 使用：

```text
4 字节无符号消息长度（固定字节序）
+
UTF-8 JSON Payload
```

正式协议 SHALL 明确字节序和最大消息长度。

stdout SHALL 仅用于协议数据；日志写入 stderr 或日志文件。

### 24.2 连接鉴权

Core 启动 Python Host 时 SHALL 创建不可预测的一次性会话凭据，并通过受控启动环境或受限句柄传递。

握手 SHALL 校验会话凭据，未授权本地进程不得冒充 Brain 连接。

会话凭据 SHALL 在进程重启后失效。

### 24.3 消息 Envelope

```json
{
  "protocol": 1,
  "kind": "request",
  "id": "req_01",
  "session_id": "ipc_session_01",
  "sequence": 101,
  "method": "chat.send",
  "timestamp": "2026-07-13T12:00:00+08:00",
  "deadline_ms": 30000,
  "payload": {}
}
```

协议 SHALL 支持：

```text
request
response
event
cancel
stream_chunk
stream_end
```

### 24.4 错误格式

```json
{
  "protocol": 1,
  "kind": "response",
  "id": "req_01",
  "ok": false,
  "error": {
    "code": "CAPABILITY_PERMISSION_DENIED",
    "message": "Permission denied",
    "retryable": false,
    "details": {}
  }
}
```

错误码 SHALL 稳定、机器可读，并与自然语言消息分离。

### 24.5 握手

启动流程：

```text
Tauri 启动 Python
    ↓
system.hello
    ↓
校验会话凭据并交换版本
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
runtime_schema
data_schema
session_credential
```

### 24.6 核心接口

首版 SHOULD 包含：

```text
system.hello
system.health
system.shutdown
system.cancel

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
capability.request
capability.result
```

### 24.7 流式、取消和背压

协议 SHALL 定义：

- 请求超时和 Deadline 行为。
- Chat 和长任务的取消语义。
- 流式消息顺序和结束标志。
- 消费方过慢时的背压或限流。
- 重复请求 ID 的处理。
- Python 重启后未完成请求的终止状态。
- Event 是否允许丢弃、合并或重放。

### 24.8 大型数据

截图、模型、音频和大型文档 SHOULD 使用受控临时文件或资源句柄传递。

资源句柄 SHALL：

```text
不可猜测
绑定会话或请求
限制读写方向
具有 TTL
使用后可撤销
不能解析为任意本地路径
```

### 24.9 非法输入处理

Core 和 Python Host SHALL 对以下情况安全失败：

```text
消息长度超过上限
不完整帧
非法 UTF-8
非法 JSON
未知消息类型
序列号异常
握手超时
凭据错误
连续协议错误
```

协议错误不得导致无限阻塞或无限重启循环。

### 24.10 异常恢复

Python Host 崩溃时，Tauri SHALL：

1. 保持 UI 可用。
2. 显示后端异常状态。
3. 终止所有未完成请求并返回稳定错误。
4. 在有限次数内自动重启。
5. 使用退避策略避免快速崩溃循环。
6. 超过阈值后进入 Runtime 修复界面。

## 25. 路径与存储模型

系统 SHALL 使用独立路径：

```text
install_root
bootstrap_root
app_root
runtime_root
data_root
cache_root
plugins_root
models_root
logs_root
```

业务代码不得假设所有资源都位于项目根目录或当前工作目录。

默认数据目录：

```text
%APPDATA%/Sakura/
```

存在 `portable.flag` 时：

```text
Sakura/data/
```

敏感数据、缓存、日志和插件数据 SHALL 使用独立子目录和访问策略。

插件默认 SHALL 只访问自身受控数据目录，除非通过 Capability 获得其他 Scope。

## 26. Runtime 管理

基础 Runtime SHALL 包含：

```text
Python 解释器
基础网络依赖
配置与存储库
LLM Client 基础依赖
基础 Memory 依赖
IPC 依赖
插件宿主基础依赖
```

Python Brain 业务代码属于 App，不属于 Runtime。

以下组件 SHOULD 可选安装：

```text
memory-vector
browser-readonly
voice-local
backchannel-local-model
```

以下未来 Agent 组件 SHALL NOT 属于 Sakura 1.0 基础 Runtime：

```text
agent-planner
agent-terminal
agent-browser-automation
agent-computer-control
```

锁文件 SHALL 按平台、架构和 Python ABI 生成：

```text
locks/win-x64-cp312/core.lock
locks/win-x64-cp312/memory-vector.lock
locks/win-x64-cp312/browser-readonly.lock
```

锁文件 SHALL：

- 固定直接和传递依赖版本。
- 固定 Wheel 哈希。
- 只允许二进制 Wheel。
- 禁止在用户电脑现场编译。
- 由同一次统一依赖解析生成。
- 在 CI 的干净系统中验证。

安装器 SHALL 携带独立、签名后的 `uv.exe`。pip MAY 作为受控兼容兜底。

## 27. 安装

`Sakura-Setup.exe` SHALL：

1. 安装 Launcher、Updater、Tauri App 和 Runtime Manager。
2. 安装最小 Python Runtime。
3. 检测系统架构和 WebView2。
4. 检查安装目录可写性。
5. 启动可恢复的 Runtime 配置流程。
6. 创建快捷方式和卸载信息。
7. 完成基础健康检查。
8. 不安装任何 Agent Extension 或高风险 Capability Plugin。

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

PyPI 之外的浏览器资源、模型、TTS 包和 WebView2 SHALL 由统一资源管理器处理。

离线组件 SHALL 经过签名或来自已签名清单中的可信哈希验证。

## 28. 更新

系统 SHALL 分离：

```text
Bootstrap Update
App Update
Runtime Update
Plugin Update
Model / Resource Update
Data Migration
```

Sakura 1.0 不设独立 Brain Update；Brain 业务代码随 App Update 发布。

### 28.1 更新流程

```text
获取签名元数据
 ↓
校验元数据有效期、签名、平台和回滚索引
 ↓
下载到临时目录
 ↓
校验文件哈希和签名
 ↓
安装到新版本目录
 ↓
离线健康检查
 ↓
记录待切换版本
 ↓
退出当前 App
 ↓
Launcher / Updater 原子切换 current.json
 ↓
启动新版本并等待健康标志
 ↓
失败则回滚
```

Runtime 更新不得原地破坏当前环境。

### 28.2 更新元数据

更新元数据 SHOULD 包含：

```json
{
  "channel": "stable",
  "component": "runtime",
  "version": "1.2.0",
  "rollback_index": 12,
  "published_at": "2026-07-13T12:00:00Z",
  "expires_at": "2026-07-20T12:00:00Z",
  "platform": "windows-x64",
  "artifacts": [],
  "signature": "..."
}
```

客户端 SHALL 拒绝：

```text
签名无效的元数据
过期元数据
低于允许回滚索引的版本
平台或架构不匹配的包
哈希不一致的下载内容
新增权限但未重新授权的插件更新
```

单独发布的哈希文件不得被视为可信来源；哈希必须由可信签名元数据覆盖。

### 28.3 App 启动健康

新 App 版本启动后 SHALL 在限定时间内写入健康标志。

Launcher SHOULD 结合以下信号判断启动健康：

```text
进程存活时间
Core 初始化完成
UI 可响应
Python Host 可用或可进入修复模式
未出现连续崩溃
```

### 28.4 数据迁移

数据迁移 SHALL：

- 在迁移前备份必要数据。
- 记录 `data_schema`。
- 定义旧版本读取策略。
- 在应用回滚时检测数据版本。
- 不得假设应用回滚等于数据回滚。
- 对不可逆迁移明确提示并保留恢复路径。

## 29. 兼容版本

系统 SHALL 独立维护：

```text
ipc_protocol
plugin_api
renderer_protocol
runtime_schema
data_schema
manifest_format
```

App SHALL 声明支持的版本范围。

插件、Runtime、Renderer 或数据不兼容时 SHALL 被拒绝激活，而不是尝试带病运行。

兼容检查 SHALL 在安装、更新、启动和插件激活时执行。

## 30. 现有 SDK 迁移

现有能力映射如下：

| 当前接口 | 新接口 |
|---|---|
| `ToolContribution` | `CapabilityDeclaration` |
| `ContextProvider` | Brain Context Provider |
| `RendererContribution` | Renderer Provider |
| `PluginSettingsContribution` | Declarative Settings Schema |
| `requires_confirmation` | Permission Policy |
| Python Tool Executor | Capability Broker / Executor |

纯推理、上下文和对话策略插件 MAY 保留 Python 执行模式。

危险工具执行 SHALL 迁移到 Rust Broker 或受控 Sidecar。

旧插件迁移适配层 SHALL 有明确移除版本，不得成为长期安全旁路。

## 31. 实施阶段

### Phase 0：技术验证

完成：

```text
透明窗口
输入法
多显示器
DPI
托盘
音频
截图
IPC 帧和鉴权原型
Python 生命周期
Launcher / App 版本切换原型
```

### Phase 1：Tauri Assistant 主应用

迁移：

```text
桌宠
聊天
设置
工作室
历史
托盘
角色和人格
TTS
基础记忆
主动互动
```

### Phase 2：平台安全 API

实现：

```text
IPC Protocol
Typed Event Buses
Observation
Provenance
Capability
Permission
Audit
Path Layout
Credential Broker 基础接口
```

### Phase 3：Assistant 能力迁移

迁移或实现：

```text
只读网页能力
只读文件能力
TTS 播放
屏幕捕获
Renderer Protocol
低风险 Integration
```

### Phase 4：安装与更新体系

完成：

```text
Launcher / Updater
Runtime Manager
锁文件
组件安装
签名和密钥轮换
离线包
双版本回滚
数据迁移框架
```

### Phase 5：Agent Extension 预留

完成：

```text
Agent Planner Plugin API
任务状态协议
停止和取消机制
用户确认节点
Capability 编排接口
Agent 审计链路
```

本阶段 MAY 使用测试 Capability 验证架构，但 Sakura 1.0 SHALL NOT 要求发布正式高权限 Agent 插件。

### Post 1.0：Agent Extension

后续按独立计划实现并按需发布：

```text
Agent Planner
Filesystem Write
Terminal
Browser Automation
Computer Control
Integration Write
```

### Phase 6：生态

建设：

```text
角色市场
插件市场
模型与资源市场
发布者验证
权限和安全评价体系
```

## 32. 验收标准

Sakura Platform 1.0 必须满足：

1. 干净 Windows x64 系统无需安装系统 Python 即可安装。
2. Tauri UI 在 Python Runtime 损坏时仍能进入修复界面。
3. 默认安装可完成角色、聊天、TTS、基础记忆、感知和主动互动。
4. 默认安装中不存在文件写入、终端执行、浏览器自动化和输入控制 Provider。
5. Assistant 在未安装任何 Agent Extension 时完整可用。
6. Python Host 可由 Tauri 启动、鉴权、关闭、监控和有限次数恢复。
7. 未授权本地进程无法连接或冒充 Python Brain。
8. 非法、超长或不完整 IPC 消息不会导致 Core 崩溃或无限阻塞。
9. 用户拒绝权限后，对应操作不会产生 OS 副作用。
10. 用户确认后关键 Capability 参数被修改时，Core 拒绝执行。
11. 高风险操作可在审计日志中查询，并能追踪至用户请求和计划步骤。
12. 审计日志不包含明文密码、Token、Cookie 或完整敏感文件内容。
13. 用户可以关闭屏幕、窗口、剪贴板和输入活动感知。
14. 原始截图默认不持久化，过期临时资源可在正常退出和崩溃恢复后清理。
15. 文件 Scope 校验可以抵御路径穿越、符号链接和 Junction 越权。
16. Provider 无法访问其权限 Scope 之外的路径、域名或账户。
17. Renderer Plugin 无法直接调用危险 Tauri Commands 或伪造 Trusted UI。
18. 网页、文件和插件输出中的文本不会被自动当作用户授权指令。
19. Runtime 安装失败不会破坏当前可用 Runtime。
20. App 更新失败或新版本连续启动失败时可以恢复旧版本。
21. 更新元数据过期、签名错误、哈希错误或回滚索引过低时，客户端拒绝更新。
22. 插件新增权限、扩大 Scope 或提高风险等级时必须重新授权。
23. 本机数据和便携数据模式均可工作。
24. 基础 Runtime 不强制依赖 PySide6、Torch、Playwright 或任何 Agent 组件。
25. 应用、Runtime、插件和离线组件均经过可信签名或由签名元数据覆盖的哈希验证。
26. 安装 Agent Planner 不会自动安装或授权高风险 Capability。
27. 禁用或卸载 Agent Extension 后，应用恢复为 Assistant。
28. Agent Extension 只能通过统一 Capability、Permission 和 Audit 系统执行操作。
29. Critical 权限不存在无期限“始终允许”选项。
30. Sakura 1.0 的安装、启动、更新和基础 Runtime 不依赖 Agent 组件。

## 33. 建议的子规范

SAP-001 是平台总规范。以下实现细节 SHOULD 形成独立子规范：

```text
SAP-002  IPC Protocol
SAP-003  Capability and Permission Model
SAP-004  Plugin Manifest and Trust Model
SAP-005  Runtime Packaging and Dependency Locks
SAP-006  Bootstrap and Update Protocol
SAP-007  Observation and Privacy Model
SAP-008  Path, Storage and Resource Handle Model
SAP-009  Threat Model and Security Test Plan
SAP-010  Component Compatibility Matrix
SAP-011  Renderer Protocol
SAP-012  Agent Extension API
```

子规范不得改变 SAP-001 中确立的可信边界、默认产品形态和最小权限原则。

## 34. 最终架构定义

> Sakura 是一个由 Tauri 驱动、默认以 Assistant 形态运行的 AI Companion 平台。陪伴、人格、对话、记忆、语音、感知、主动互动和低风险辅助属于 Assistant 基础能力。Launcher / Updater 提供可恢复的版本启动和更新链；Rust/Tauri 构成可信桌面核心，负责桌面、感知、权限、能力执行、插件、凭据和运行环境；Python 构成伙伴大脑，负责人格、记忆、上下文、LLM 和有限任务规划。多步骤自主执行和高风险操作不属于 Sakura 1.0 默认能力，而由后续可选 Agent Planner 与受权限控制的 Capability Plugin 扩展。
