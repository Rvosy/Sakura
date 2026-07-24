# Sakura Tauri + Python Core Runtime v2 计划

> 状态：Phase 1P 与 WP-1C-03 accepted；下一启动点为 WP-1C-04（planned）
> 工作分支：`refactor/tauri-runtime-v2`
> 基线：`dev` / `4e8dc7f0a6afbc391149046febeb0c796dd641b8`
> 目标：用 Tauri 替代 Qt 桌面运行时和 UI，复用现有 Python Assistant 能力，保持发布时全部现有用户能力，并从基础阶段持续支持 Windows、macOS 和 Linux。

## 1. 总体定义

Sakura Runtime v2 是一次桌面运行时和 UI 层重构，不是 Assistant 领域重写，也不是通用 Agent 平台建设。

```text
Tauri / Rust
  -> 唯一桌面生命周期根
  -> Runtime v2 启动链中的所有用户可见窗口
  -> 桌面与原生系统能力
  -> Python Core 监管

Python Core
  -> 复用现有 Assistant 领域服务
  -> 对话、角色、模型、记忆、工具、插件、TTS 合成和主动互动
  -> 无 Qt UI 依赖

WebView
  -> 桌宠表现、气泡、输入、设置和短期交互状态
```

首轮迁移以“基础聊天垂直链真实可用”为中心。生命周期、IPC 和恢复机制只建设到足以支撑可靠产品的程度，不借迁移之机提前建设完整桌面平台。

## 2. Assistant / Agent 产品边界

Runtime v2 当前迁移 Sakura 已有的 Assistant 运行模式。现有实现即使位于 `app/agent/` 命名空间，也不因此变成可选功能。

- Assistant 是默认且始终存在的核心产品能力。
- 现有 Tools、MCP、Memory、插件、TTS、截图、主动互动、提醒和桥接能力属于发布前必须恢复的 Sakura 能力，受产品功能等价台账约束。
- 本轮不建设的是新的通用自治任务平台、多 Agent Runtime 和任务图编排器，而不是删除现有 `AgentRuntime`/Assistant 能力。
- 未来新增的通用 Agent 平台可以通过默认关闭的可选插件或 Capability 扩展接入。
- Agent 插件不得拥有独立桌面生命周期根。
- Agent 插件不得绕过 Runtime v2 的 IPC、权限和进程监管。
- Agent 插件需要子进程时，必须属于当前受控进程树。
- Agent 不得改变 Assistant 基础启动、聊天和故障恢复门禁。

本轮明确不做：

- Agent 编排器。
- 自治任务平台。
- 多 Agent Runtime。
- 为未来 Agent 提前建设完整 Capability Broker 或任务图系统。

## 3. 不可妥协的架构边界

### 3.1 Tauri 是生命周期根

发布运行链必须是：

```text
Sakura Desktop
├─ Tauri / Rust Runtime
├─ WebView UI
└─ Python Core 子进程
```

不能是：

```text
常驻 Python 启动器
└─ Tauri
   └─ Python Core
```

Tauri 必须能够启动、监控、优雅关闭和强制回收 Python Core 及其后代进程。Core 崩溃不能带走桌面窗口。在 Runtime v2 启动链中，所有用户可见窗口均由同一个 Tauri App 创建和管理；legacy Qt 回退入口是独立兼容运行路径，不属于该启动链。

### 3.2 Python 是无 Qt Assistant Core

- Python Core 不创建 QApplication、QObject、QThread、QWidget 或 Qt UI。
- 现有对话、角色、模型、Memory、Tools、MCP、插件、TTS 合成和调度逻辑优先复用。
- Python 不负责原生窗口、托盘、WebView DOM、桌面截图或应用生命周期根。

### 3.3 单一桌宠组合体验与首选单窗口方案

产品硬约束是：主桌宠在用户感知上必须是一个连续的组合体验，立绘锚点、气泡、输入和状态提示不能成为互相独立、可漂移或拥有不同生命周期的桌面根；所有表面仍归同一个 Tauri App 管理。

各平台的首选技术方案是使用一个原生透明窗口承载：

```text
透明桌宠窗口
├─ 对话气泡
├─ 立绘
├─ 输入框
├─ 状态提示
└─ 基于 dev 主题色的背景与边框
```

设置、工作室、历史和诊断使用独立普通窗口。

“一个原生透明窗口”不是不可变产品约束。WP-1A-02/03 已形成 Windows backend 证据；WP-1P-05 必须在 macOS、Linux X11/Wayland 回补透明命中、拖动、焦点、IME、显示隐藏和 scale 门禁。出现以下任一情况时，该平台的单窗口方案判定失败，平台窗口 WP 不得 accepted：

- 透明区域点击穿透与输入区域命中无法同时稳定成立。
- 中文 IME 候选框、焦点恢复或拖动/穿透切换存在可重复的 P1。
- 只有引入隐藏 Qt、第二生命周期根、管理员权限或范围外兼容层才能通过。
- 真实 WebView 与物理输入持续不符合自动测试中的契约。

失败后只允许整理证据、删除失败平台实现、缩小实现范围和提出不削减产品能力的替代架构。候选替代可以是同一 Tauri App 内受控的多原生窗口组合或平台原生命中层；必须先更新 ADR-0004、Work Package 和窗口架构记录，由项目负责人批准后重新拆分。不得静默关闭点击穿透、拖动或 IME，也不得引入第二生命周期根。

### 3.4 状态所有权分离

- Python 是 Assistant 领域状态的真相源。
- Rust 不复制和修改 Python 业务对象，只缓存当前 generation 的只读快照。
- WebView 只保存短期表现状态和未提交表单草稿。
- Core 重启后旧 generation 的事件和快照立即失效。
- Snapshot 和普通 UI 诊断数据不得包含 Credential、API Key、完整系统 Prompt、插件私密配置或可任意访问本地文件的裸路径。
- 截图、音频和大文件只通过当前 generation 有效的受控资源 token 暴露给 WebView。

### 3.5 IPC 不得阻塞生命周期

- 长业务任务不能阻塞 health、cancel、shutdown 和诊断。
- Core 必须先建立通信，再初始化重型 Assistant 组件。
- 请求、响应、事件和错误必须有明确结构。
- 大文件、截图和音频不直接塞进 JSON 消息。
- WebView 只能提交受控 command 和业务 payload；request ID、generation、priority、deadline 和协议字段由 Rust 构造。

### 3.6 用户数据兼容与 Qt 回退

- Phase 1–3 不执行破坏性用户数据迁移。
- 现有角色资源、Core 配置、历史、Memory 和用户目录优先原样复用。
- Runtime v2 专属的 `desktop.*`、`ui.*` 配置使用独立文件或独立命名空间，不污染 legacy Qt 可读数据。
- 需要修改共享数据 schema 时，必须先定义版本号、迁移前备份、失败恢复和回退策略。
- Qt 回退仍受支持期间，不得写入会使 legacy Qt 无法启动或无法读取原数据的格式。
- Tauri 与 legacy Qt 使用相同的应用互斥锁；同一用户会话中两个桌面入口不能同时运行，也不能出现两个共享数据写入者。
- “保留 Qt 代码”不视为回退完成，必须通过 Qt → Tauri → Qt 的真实兼容冒烟测试。

具体数据边界、迁移协议和兼容门禁见 ADR-0003。

### 3.7 跨平台目标矩阵

- Runtime v2 从 Phase 1P 起的正式基础矩阵是 Windows x64、macOS arm64 和 Linux x64；三者必须持续参与编译、共享契约和最小生命周期门禁。
- Windows 已完成的 WP-1A、WP-1B 和 WP-1C-01/02 证据保留为 Windows backend 历史证据，不自动代表 macOS/Linux 已接受。
- Linux 窗口验收必须区分 X11 与 Wayland；不能用 X11 结果替代 Wayland，也不能在发布时静默削减命中、拖动、IME 或截图能力。
- macOS x64、Windows ARM64、32 位、Wine 和 Windows Server 当前不是首个正式 target，但公共协议、数据和资源布局不得写死 CPU 架构。
- 平台 backend、Runtime 定位、CI 与真实验收的规范见 ADR-0004。WP-1C-03 及后续工作必须依赖 WP-1P-06。

### 3.8 Runtime v2 工具链与 bundled Python 来源

Windows Phase 1A 已使用以下可重复基线，继续作为 Windows backend 历史证据：

| 项目 | 初始基线 | 约束 |
|---|---|---|
| Rust / Cargo | `1.96.0`，`x86_64-pc-windows-msvc` | WP-1A-01 在 `desktop/` 内固定 toolchain，不依赖可漂移的 `stable` 别名 |
| Rust edition | `2021` | 与现有 Settings/Studio Tauri crate 一致 |
| Tauri | `2.11.3` | 初始值来自现有两个 Tauri 工具的锁文件；新 Shell 使用自己的 `Cargo.lock`，不复制其生产组合根 |
| `tauri-build` | `2.6.3` | 与 Tauri 版本一并锁定 |
| Tauri CLI | 当前未安装 | WP-1A-01 使用 `cargo build/run/test --locked`，CLI 不是前置；以后若用于 bundle，必须先固定精确版本 |
| Visual C++ / SDK | Visual Studio `18.4.1` C++ 工具链；Windows SDK `10.0.26100.0` | 参考环境；WP-1A-01 记录实际编译器和 SDK |
| Node / npm | `v22.14.0` / `11.18.0` | 静态 startup 页面不要求 Node；引入前端构建链时再固定 lockfile |
| WebView2 | `150.0.4078.65` | 真实 Shell 验收必须记录实际 Runtime 版本 |

Runtime v2 的 Windows bundled Python 继续使用仓库发布流程生成的 `runtime/python.exe`：CPython `3.12.8` 64 位官方 Windows embeddable 包。WP-1P-01/02 必须同时冻结 macOS arm64 与 Linux x64 的 Python 来源、包内布局、完整性校验和开发/发布定位规则。公共代码不得依赖 `.exe` 或仓库 `target/debug` 目录；发布布局不得静默回退系统 Python。

## 4. Python Adapter / Facade 迁移原则

新 Core Host 的目标是适配现有 Assistant 服务，而不是重新实现一套 Assistant。

```text
app.core_host
  -> Adapter / Facade
  -> 现有无 Qt Assistant 服务
```

只有至少满足以下一项真实迁移需要时，才允许重构既有 Python 领域模块：

1. 直接依赖 Qt。
2. 无法在受监管子进程中运行。
3. 会阻塞 IPC 并发、取消或关闭。
4. 无法实现确定性的初始化与释放。

此外，任何领域模块修改都必须有独立测试证明修改前后业务语义等价。确需改变业务语义时，必须作为独立范围明确说明并获得项目负责人批准，不能借 Runtime 迁移顺手完成。

禁止：

- 为了适配新 Host 顺手重写 AgentRuntime、Memory、插件或配置领域。
- 在一个新的巨型 `application.py` 中重新聚合全部业务逻辑。
- 将 Qt 信号语义机械翻译为另一套隐式全局事件。

## 5. 职责边界

| 层 | 负责 | 不负责 |
|---|---|---|
| Tauri/Rust | 应用生命周期、窗口、托盘、单实例、更新入口、Core 监管、桌面截图、原生系统能力、桌面/UI 配置 | 人格、对话、Memory、角色业务和插件业务状态 |
| Python Core | Assistant、LLM、角色、历史、Memory、Tools、MCP、插件、TTS 合成、主动互动和 core 配置 | 原生窗口、托盘、WebView DOM、桌面生命周期根 |
| WebView | 立绘、气泡、输入、打字机、动画、表单草稿和页面状态 | 持久化业务状态、Core 生命周期和系统进程管理 |

### 5.1 配置所有权

```text
core.*
  Provider、模型、角色、Memory、MCP、插件、TTS、Scheduler
  所有者：Python Core

desktop.*
  窗口位置、置顶、启动行为、托盘、快捷键
  所有者：Tauri/Rust

ui.*
  主题、字体、气泡尺寸、打字机速度和布局
  所有者：Tauri/Rust UI Config Repository

audio.*
  播放设备、应用音量和音频输出策略
  所有者：Phase 4 音频 ADR 根据最终播放层确定
```

每个配置域独立 validate 和原子保存，并返回独立结果。不建设跨 Python/Rust 的分布式事务协调器。

若 `core.*` 保存失败：

- 不重启 Core。
- 不应用依赖该 Core 配置的运行时状态。
- 已成功保存且互不依赖的 `desktop.*`、`ui.*` 不要求回滚。
- 设置界面必须明确显示各域成功或失败结果。

### 5.2 角色切换

- 第一版允许通过受控 Core 重启实现角色切换。
- 这只是阶段性简化，不是长期架构原则。
- 长期目标是在 Python 内构建新的 Assistant Session/Context，成功后原子替换当前 Session。
- 不允许在旧对象图上逐字段修改角色、Prompt、Memory 和运行时引用。

## 6. Core 与 UI 状态模型

进程、Core 初始化和页面路由使用三个正交模型。

这里的 transport `generationId` 只标识一次 Core 进程生命周期；Assistant Session/Context 是 Python 领域对象，两者不得混用。

### SupervisorState

```text
stopped
spawning
running
stopping
exited
restarting
```

### CoreReadiness

```text
transport_unavailable
transport_ready
initializing
setup_required
ready
degraded
failed
```

### ShellRoute

```text
startup
pet
diagnostics
runtime_repair
fatal_error
```

`runtime_repair` 在早期阶段仅表示诊断和修复入口页面，不承诺自动下载、替换或回滚 Python Runtime。

### 6.1 关键与可选组件

进入 ready/degraded 前必须成功：

- Core 配置仓库可读取。
- 当前角色与 Assistant Session 可建立。
- Chat Pipeline 可运行。
- 已配置的基础模型 Provider 可创建。

“Provider 可创建”只表示配置格式有效、Provider 类型存在、必要字段已填写且客户端对象可构造。启动阶段不得强制要求远程 API、模型服务或网络认证请求成功；网络不可达应在实际聊天请求中返回可恢复错误，不能阻塞桌宠进入可交互状态。

以下情况进入 setup_required，不重启 Core：

- 没有角色。
- 没有有效 Provider。
- 首次配置未完成。

以下组件失败只进入 degraded：

- History 持久化。
- Memory。
- Tools 扩展。
- MCP。
- 插件。
- TTS 合成。
- Scheduler 和主动互动。

History 失败时允许聊天，但 UI 必须提示当前对话不会保存。

## 7. 桌宠表现范围

角色表现保持简单、精致和稳定。

允许：

- 立绘切换。
- 淡入淡出和轻微位移。
- 气泡出现、关闭、展开和内容切换。
- 输入框出现和收起。
- 完整回复返回后的打字机展示。
- 思考、错误、降级和重连状态反馈。

不做：

- Live2D。
- 骨骼动画。
- 复杂 Canvas 场景。
- 时间轴动画引擎。
- 粒子和高级角色动作系统。

窗口只使用少量稳定状态：

```text
idle
bubble
composer
expanded
```

立绘拥有固定桌面锚点。气泡和输入框展开时，窗口主要向上、向左扩展，不能让立绘在桌面上跳动。

Phase 1–3 沿用 `dev` 的主题色、透明度、边框和阴影，不实现局部模糊。新架构和截图/DPI 链路稳定后，可以把局部模糊作为可选增强重新评估，并始终提供主题色降级。

## 8. 前端工程策略

- Phase 1 不绑定 Vue、React 或完整 TypeScript 重构。
- 可以复用现有 HTML/CSS/JavaScript，但必须按窗口和领域拆分模块。
- 不允许再次形成数千行单文件脚本。
- 共享 IPC Client、DTO 校验、错误模型和设计 Token。
- 设置页和工作室迁移前，再决定长期前端框架。

## 9. 可复用与重写边界

### 9.1 候选复用

从 #140 逐文件审查后选择性迁移，不整提交复制：

- 已验证的无 Qt Assistant Adapter 或纯领域抽取。
- 帧编码、解码和协议测试案例。
- TTS 合成与播放解耦接口。
- 截图坐标、裁剪和资源隔离算法。
- Headless Backchannel、Scheduler 和 DTO 纯模块。
- 无 Qt 主题、配置和路径模型。
- UI 样式、图标和交互稿。
- 已发现的故障场景和测试夹具。

### 9.2 原则上重写

- Python 启动 Tauri 的默认链路。
- 同步串行的 Brain Supervisor。
- 混合 Rust/Python/WebView 状态所有权的 AppState。
- 巨型 BrainHostApplication 和 secondary window bridge。
- 巨型 Vanilla JavaScript 设置页和工作室脚本。
- 只检查源码字符串存在、但不运行真实链路的门禁测试。

## 10. 分阶段迁移

所有阶段都在当前 v2 分支上小步完成。Qt 代码始终保留为显式回退，直到后续单独决定删除。

实施过程同时受以下文件约束：

- `docs/superpowers/plans/2026-07-15-runtime-v2-delivery-governance.md`
- `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md`
- `docs/adr/0004-runtime-v2-cross-platform-foundation.md`
- `docs/runtime-v2/product-capability-parity.md`

Work Package 执行清单是 Phase 0–7 的顺序、状态和范围真相源；功能等价规范是发布能力范围真相源。当前项目采用个人开发模式：不为 Work Package 创建 PR，生产改动直接提交到 `refactor/tauri-runtime-v2`，通过单一目的、详细提交记录、退出门禁和稳定化检查控制范围。

开发分支 dogfooding 与 legacy Qt 回退：

- v2 分支默认入口只允许在 WP-1A-04 完成共享应用锁后切换到 Tauri；WP-1A-01 至 WP-1A-03 不改变当前默认入口。
- WP-1A-04 后、产品等价完成前，默认 Tauri 只用于 Runtime v2 已完成链路的 dogfooding；需要当前完整能力时，显式退出 Tauri 后使用当前平台冻结的 legacy Qt 回退入口。Windows 权威命令仍为 `.\runtime\python.exe .\legacy_qt_main.py`；macOS/Linux 的权威命令和 Runtime 路径由 WP-1P-02 冻结，不能复用 Windows `.exe` 规则。
- 这项切换成本只在 `refactor/tauri-runtime-v2` 开发分支内接受：开发者需要显式选择 legacy Qt，两个入口不能并行；`dev`、现有正式安装包和发布入口在 Phase 3 门禁前不改变。
- 数据安全责任由当前持锁的桌面生命周期根承担。Windows 使用已验证 named mutex；macOS/Linux 使用 ADR-0004 冻结的同语义 advisory lock。任一入口未获得平台共享锁时，必须在所有 `data/`、日志、配置、migration 和 Core 启动前退出；用户不承担删锁、合并数据或判断 stale PID 的责任。
- legacy Qt 回退适用于 v2 Shell、窗口、Core 启动或基础聊天尚未满足门禁时恢复当前产品能力，不用于并发运行或绕过未来 schema/损坏数据的 diagnostics/read-only 状态。
- WP-1A-04 的独立代码回退是整体 revert 默认入口和双方 named mutex 接入，恢复当前 Qt 入口；WP-3-06 负责证明切到 v2、退出并回到 legacy Qt 后共享数据仍兼容。

### Phase 0：冻结与基线

- [x] 固定旧迁移取证源为 `feat/tauri-assistant-migration` / `190dfafd24f5c5226bff8b4347837b6e45d9a331`，不依赖本地 stash。
- [x] 从最新 `dev` 创建 `refactor/tauri-runtime-v2`。
- [x] 记录 Qt 当前功能、已知问题和启动基线。
- [x] 建立 Qt 与 v2 共用的真实冒烟场景清单。
- [x] 记录现有角色、配置、历史、Memory 和用户目录 schema/路径基线。
- [x] 定义 Qt/Tauri 共用应用锁和 Qt → Tauri → Qt 数据兼容门禁。
- [x] 完成 #140 候选复用文件清单。
- [x] 完成架构交叉审查、决策缺口收口和 WP-1A-01 激活准备。

退出条件：`WP-0-01` 至 `WP-0-04` 全部 `accepted`；主计划通过最终审查；三份 ADR 经审查认可为 `Proposed` 技术基线；Qt 基线、共享数据基线、真实冒烟清单和选择性复用清单均已记录。未满足前不开始 Phase 1A 实现。

### Phase 1A：空 Shell 与透明窗口技术门

- [ ] 创建 `desktop/` 最小 Tauri 应用和静态前端。
- [ ] Tauri 不启动 Python 时也能立即显示 startup 页面并正常退出。
- [ ] 验证透明区域点击穿透、立绘区域拖动、输入框和按钮交互。
- [ ] 验证 idle/bubble/composer/expanded 状态和固定立绘锚点。
- [ ] 验证单屏、多屏、负坐标和 100%/125%/150% DPI。
- [ ] 验证中文 IME、焦点恢复、Alt+Tab、显示/隐藏和窗口展开不闪烁。
- [ ] 保存 Qt 入口为 `legacy_qt_main.py`，增加显式 Qt 启动脚本。
- [ ] 文档明确 legacy Qt 回退命令，并让 Qt/Tauri 入口竞争同一应用锁。
- [ ] 仅在 WP-1A-04 共享锁和 legacy Qt 回退门禁通过后，按已确认的 dogfooding 策略把当前 v2 开发分支默认启动入口切到 Tauri；不改变现有正式安装包入口。

历史退出条件：首选单透明窗口方案已在 Windows 环境通过技术门。该结果只接受 Windows backend；macOS/Linux 的窗口技术门由 WP-1P-05 回补。即使没有 Python，所有正式平台的 Shell 仍必须可见、可诊断、可退出。

### Phase 1B：进程监管与 Fake Core

- [ ] 建立最小 Core Supervisor 和受控进程树。
- [ ] 使用最小测试 transport 建立 Fake Core，覆盖正常启动、延迟 hello、初始化卡死和忽略 shutdown。
- [ ] 覆盖运行中崩溃、后代子进程、长任务不返回和受控进程树建立失败。
- [ ] 覆盖 spawn/hello/initialize/restart backoff 期间退出、旧树停止时手动重试、连续重试和重复 shutdown。
- [ ] 验证 Tauri 退出和 Core 重启后无遗留 Core 后代进程。
- [ ] 验证有限自动重启和手动重试。

历史退出条件：Supervisor 状态机和 Windows Job backend 故障矩阵已通过。跨平台总体退出条件由 WP-1P-04/06 回补：任何正式平台上的 Core 失败都不能带走 Shell 或留下未受控子进程。

### Phase 1C：最小真实 Core Host

- [ ] 创建 `app.core_host` Adapter/Facade。
- [ ] Core 先完成 hello/health，再后台初始化假组件。
- [ ] 实现 SupervisorState、CoreReadiness 和 ShellRoute 组合。
- [ ] 实现最小 Core Snapshot 和 generation 隔离。
- [ ] 实现协议 major/minor、版本与 capability 协商及不兼容诊断。
- [ ] 持续排空 stderr，并验证日志限流、generation/PID 标记和敏感信息脱敏。
- [ ] 覆盖损坏帧、stdout 污染、旧 generation 事件、初始化永不完成和 Rust 主动关闭 stdin。
- [ ] 覆盖 initialize 期间 shutdown，且协议不兼容不会触发无限自动重启。
- [ ] 使用 bundled Python 完成 hello、initialize、snapshot 和 shutdown 冒烟测试。

WP-1C-01 和 WP-1C-02 已按既有 Windows 技术门完成基础 Host 与 initialize/readiness/Snapshot。现在暂停本 Phase，先执行 Phase 1P；WP-1C-03/04 不得提前开始。

退出条件：真实 Python Core 可以在不加载 Qt UI 和重型领域模块的情况下建立通信、上报状态并可靠关闭；WP-1C-04 还必须使用 WP-1P-02/04 冻结的三平台 Runtime 与进程树 backend 完成同语义端到端。

### Phase 1P：跨平台基础回补

- [ ] 冻结 Windows x64、macOS arm64、Linux x64 target matrix、最低系统环境、平台接口和错误分类。
- [ ] 建立开发/测试/发布 `RuntimeLocator`，冻结三平台包内 Python 和 Core 布局。
- [ ] 建立 Rust/Tauri 与 legacy Python 共用的 Windows/POSIX 应用锁 backends。
- [ ] 把现有 Windows Job Object 实现降为 backend，补齐 macOS/Linux process group backends。
- [ ] 把透明命中、拖动、焦点、IME 和原生诊断降为平台 backends；分别验证 macOS、X11 与 Wayland。
- [ ] 建立三平台 CI 和真实 `Shell -> Core hello/health -> shutdown -> 零残留` 总门禁。

退出条件：WP-1P-01 至 WP-1P-06 全部 accepted；ADR-0004 至少更新为 `Technically Validated`；后续 Work Package 不再依赖 `.exe`、WinDLL、Win32 region 或 Windows Job 的公共语义。

### Phase 1D：恢复、诊断和修复入口

- [ ] Shell 展示启动、初始化、失败、降级和重启状态。
- [ ] Runtime missing 页面显示错误原因、运行目录和日志位置。
- [ ] 提供重试、打开诊断、打开安装说明/文件位置和退出。
- [ ] 不在本阶段实现 Runtime 自动下载、在线替换、版本迁移或回滚。

退出条件：Core 缺失、损坏、初始化失败或崩溃时，用户始终能理解当前状态并执行安全操作。

### Phase 2：并发 IPC 与只读快照

- [ ] 支持并发请求、事件、取消和长任务 Operation。
- [ ] health、cancel、shutdown 不受业务长任务阻塞。
- [ ] 建立受控 IPC Gateway、窗口权限和运行时校验。
- [ ] 新 generation 建立时立即废弃旧事件和快照。
- [ ] 建立协议 golden fixtures、背压和错误注入测试。
- [ ] 使用同步 sleep、阻塞文件 I/O、CPU 密集循环、大量 progress、慢 writer 和窗口关闭中的请求验证真实隔离。

退出条件：任意长操作期间控制请求和 UI 始终响应；业务冲突明确 queued 或 busy。

### Phase 3：基础聊天垂直链

- [ ] 从现有 Python 服务适配角色、Chat Pipeline 和基础 Provider。
- [ ] 显示现有立绘和初始消息。
- [ ] 打开输入框并发送真实聊天请求。
- [ ] 展示思考、完成、错误和取消状态。
- [ ] 完整回复返回后由 WebView 执行打字机展示。
- [ ] 支持立即跳过打字机动画，不影响已完成的 Core 请求。
- [ ] 根据回复段或表情状态切换立绘。
- [ ] Core 崩溃后保持窗口、恢复 Core 并重新水合 UI。
- [ ] 正常退出和强制恢复均清理完整进程树。
- [ ] 完成 legacy Qt → Tauri v2 → legacy Qt 的真实用户数据兼容门禁。

视觉体验门禁：

- 气泡出现和关闭没有突兀跳变。
- 气泡内容变化不移动立绘桌面锚点。
- 输入框打开和关闭具有稳定过渡。
- 立绘切换无明显白闪、尺寸跳变或布局抖动。
- 长文本不会无限扩大原生窗口。
- 中文输入法候选框位置正确。
- 125%/150% DPI 下文本和立绘清晰。
- Core 重启状态与正常气泡使用一致的视觉语言。
- CSS 动画不能阻塞输入、取消或关闭。

退出条件：在已有开发配置下，直接启动 Tauri、真实聊天、打字机、取消、立绘切换、Core 强杀恢复、长时间运行和 Qt 双向回退兼容全部通过。

### Phase 4：TTS、工具确认、截图和主动事件

- [ ] WP-4-01：Memory 检索、写入、整理和外部存储等价。
- [ ] WP-4-02：内置 Tools、Operation 与 Action ID 工具确认。
- [ ] WP-4-03：MCP 配置、启动、工具调用、故障恢复与进程树清理。
- [ ] WP-4-04：现有 Python 插件、context/event/tool 扩展等价。
- [ ] WP-4-05：TTS 合成、播放、设备错误、audio ADR 和本地服务回收。
- [ ] WP-4-06：手动截图、受控资源、权限和多屏/DPI/Wayland portal。
- [ ] WP-4-07：自动观察、主动互动、提醒、任务和休眠/时区恢复。
- [ ] WP-4-08：Phase 4 组合稳定化、背压和完整资源回收。

### Phase 5：设置、历史和诊断

- [ ] WP-5-01：`core.*`、`desktop.*`、`ui.*`、`audio.*` 配置仓库、validate、change plan 和原子保存。
- [ ] WP-5-02：设置窗口、逐域保存结果和首次设置流程。
- [ ] WP-5-03：角色切换、受控 Core 重启、历史分页和 Session 等价。
- [ ] WP-5-04：托盘、置顶、全局快捷键、显示隐藏和开机启动。
- [ ] WP-5-05：浏览器自动化与移动/本地桥接插件的受控生命周期。
- [ ] WP-5-06：扩展诊断、手动 Repair、安全重试和更新前置检查。

### Phase 6：角色工作室

- [ ] WP-6-01：Workspace/Draft 独立模型。
- [ ] WP-6-02：角色导入、资源和 schema 校验。
- [ ] WP-6-03：预览与运行中 Assistant/generation 隔离。
- [ ] WP-6-04：原子保存、发布和回滚。
- [ ] WP-6-05：大文件 Operation、取消和故障恢复。

### Phase 7：发布验收

- [ ] WP-7-01：完整 Python、Rust、前端、协议和三平台 CI 矩阵。
- [ ] WP-7-02：Windows、macOS、Linux 真实 Tauri WebView E2E 与平台 UX 验收。
- [ ] WP-7-03：逐行关闭产品功能等价台账和 Qt -> Tauri -> Qt 数据门禁。
- [ ] WP-7-04：三平台打包、签名/notarization、更新、完整性和干净安装。
- [ ] WP-7-05：长时间运行、休眠恢复、重复启停及 Core/MCP/TTS/browser 故障注入。
- [ ] WP-7-06：最终发布审查、回退演练和进入 `dev` 决策。

退出条件：三平台、全部产品等价行和数据门禁通过后，才允许合并到 `dev` 并进入正式发布链。Qt 仍保留为显式回退，删除时间另行决策。

## 11. 核心验收指标

- Shell 不出现等待 Core 的空白期。
- 每个正式平台的参考机器冷启动可见时间 p95 目标不高于 1 秒；500 ms 是优化目标。
- 同一用户会话只能有一个 Sakura Desktop 实例和一个受监管 Python Core 根进程。
- Tauri 与 legacy Qt 不能同时持有应用锁或写入共享用户数据。
- Core 领域子进程属于同一受控进程树。
- 任一正式平台的 Tauri 退出后 5 秒内无 Core、MCP、TTS 或浏览器后代进程残留。
- Python 初始化卡死时可以取消或强杀，不阻塞 Tauri 主线程。
- 长操作期间 health、cancel、shutdown、诊断和 UI 保持响应。
- 有资源冲突的业务请求明确 queued 或 busy，不能无响应。
- Core 重启后旧 generation 事件、Operation、资源 token 和快照全部失效。
- 配置保存失败不产生不可解释的运行时半更新。
- Qt 创建的数据可被 Tauri v2 读取；退出 v2 后 legacy Qt 仍可启动并读取兼容数据。
- Provider 网络不可达不阻塞 Shell 和 Core 启动，只影响对应业务请求。
- 真实应用验收失败时，不得以静态契约或单元测试通过为由切发布入口。
- 平台敏感 Work Package 不得以 Windows 单平台证据标记为全局 accepted。
- `docs/runtime-v2/product-capability-parity.md` 的发布必备行全部达到 `parity-accepted` 或获批替代。

## 12. 已确认决策摘要

1. Tauri 是唯一桌面生命周期根，Python 是无 Qt Assistant Core。
2. Runtime v2 迁移现有 Assistant 及其 Memory、Tools、MCP、插件、TTS、主动互动等全部能力；仅未来通用自治/多 Agent 平台可延期为可选插件。
3. Python 通过 Adapter/Facade 复用现有领域服务，不重写 Assistant。
4. 主桌宠维持单一组合体验；一个原生透明窗口是 Phase 1A 首选技术方案，失败时按 3.3 停止并重新批准替代架构。
5. v2 分支只在 WP-1A-04 accepted 后默认启动 Tauri，Qt 保留 `.\runtime\python.exe .\legacy_qt_main.py` 显式回退。
6. Core 崩溃不能带走 UI，Tauri 拥有进程树最终回收权。
7. 第一版 IPC 使用 stdin/stdout framed transport，具体协议实现由 ADR 约束并允许技术验证后调整。
8. Phase 1 不绑定前端框架重构。
9. `core.*`、`desktop.*`、`ui.*` 各自独立持久化；`audio.*` 在 Phase 4 根据播放层确定所有者。
10. 复杂 Core 配置通过 change plan 受控重启；角色重启只是第一版简化。
11. 完整回复由 WebView 使用打字机展示，不要求真实 token streaming。
12. 局部模糊延期为可选增强，不属于基础聊天门禁。
13. Runtime Repair 早期只提供诊断和安全操作入口。
14. Phase 1–3 不做破坏性用户数据迁移，Qt/Tauri 使用同一应用锁并通过双向回退门禁。
15. WP-1C-02 完成后先执行 Phase 1P；Windows x64、macOS arm64、Linux x64 从基础生命周期开始持续参与门禁，不能在产品功能接近完成时再适配。
16. 已完成的 Windows WP 保留为 Windows backend 证据，不代表跨平台总体 accepted；ADR-0004/WP-1P 负责回补。
17. 发布功能范围以产品功能等价台账为准，不能以代码仍存在或 legacy Qt 可回退代替迁移完成。

## 13. 交付治理与技术 ADR

以下内容是当前推荐实现，不是不可变产品约束。若技术验证发现更简单、同样满足硬边界的方案，可以通过更新 ADR 调整。

强制交付治理：

- `docs/superpowers/plans/2026-07-15-runtime-v2-delivery-governance.md`
- `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md`

治理文件约束 Work Package、允许列表、提交粒度、证据门禁、Bug Budget 和停止条件；执行清单记录 Phase 0–7 的 Work Package 顺序、状态、范围、证据和回退。即使实现技术方向正确，违反治理边界也不得进入下一 Work Package。

技术 ADR：

- `docs/adr/0001-runtime-v2-process-supervision.md`
- `docs/adr/0002-runtime-v2-ipc.md`
- `docs/adr/0003-runtime-v2-data-compatibility.md`
- `docs/adr/0004-runtime-v2-cross-platform-foundation.md`

ADR 状态按以下流程演进：

```text
Proposed
-> Technically Validated
-> Accepted
-> Superseded
```

- ADR-0001 的 Supervisor 与 Windows backend 已 accepted；macOS/Linux backend 和跨平台总体门禁由 ADR-0004/WP-1P 承担。
- ADR-0002 在 Phase 1C 的握手、版本、stderr 和故障 transport 门禁通过后进入 `Technically Validated`，在 Phase 2 并发、阻塞隔离、取消和背压门禁通过后进入 `Accepted`。
- ADR-0003 的 Windows named mutex 已 `Technically Validated`；POSIX 共享锁和三平台 Qt/Tauri 双向门禁必须在 accepted 前补齐。
- ADR-0004 在 WP-1P-01 至 WP-1P-06 通过后进入 `Technically Validated`，在首个三平台真实产品垂直链通过后进入 `Accepted`。
- 技术验证失败时更新或替代 ADR，不为了符合文档而强行保留失败方案。

## 14. 最终审查重点

最终审查应重点确认：

- 是否仍然以“替换 Qt 桌面层、复用 Python Assistant”为中心。
- 是否明确排除了 Agent 平台和 Python Assistant 重写。
- 是否没有把现有 `app/agent/` 能力误当成可删除的未来 Agent 平台。
- Phase 1A–1D 是否足够小且可独立验收。
- Phase 3 是否同时具备真实聊天和明确视觉收益。
- 可靠性基础设施是否只建设到支撑当前产品所需的程度。
- 技术 ADR 是否保留了根据验证结果简化实现的空间。
- 旧用户数据、legacy Qt 回退和双入口互斥是否拥有可执行门禁。
- WebView、Rust 控制面和 Python 领域执行面是否保持明确权限边界。
- Windows、macOS、Linux 是否从平台底座起使用同一公共生命周期和产品语义。
- 产品功能等价台账是否逐项拥有目标 WP 和发布证据。
