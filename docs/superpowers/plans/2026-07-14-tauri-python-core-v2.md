# Sakura Tauri + Python Core Runtime v2 计划

> 状态：Draft / 已按评审修正，等待最终审查
> 工作分支：`refactor/tauri-runtime-v2`
> 基线：`dev` / `4e8dc7f0a6afbc391149046febeb0c796dd641b8`
> 目标：用 Tauri 替代 Qt 桌面运行时和 UI，同时尽量复用现有 Python Assistant 能力。

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

Runtime v2 当前只实现 Assistant 运行模式。

- Assistant 是默认且始终存在的核心产品能力。
- Tools、MCP、Memory、插件、TTS 和主动互动是 Assistant 的辅助能力，不等同于 Agent Runtime。
- Agent 不属于 Phase 1–7 的基础 Runtime 必备能力。
- Agent 将来通过默认关闭的可选插件或 Capability 扩展接入。
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

### 3.3 一个透明桌宠窗口

主桌宠使用一个透明窗口承载：

```text
透明桌宠窗口
├─ 对话气泡
├─ 立绘
├─ 输入框
├─ 状态提示
└─ 基于 dev 主题色的背景与边框
```

设置、工作室、历史和诊断使用独立普通窗口。

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

### 3.7 首轮目标平台

- Phase 1A–3 的正式目标平台是 Windows。
- 进程树和原生能力保留跨平台抽象边界，但 Linux/macOS 仅保留设计占位和必要编译边界，不作为首轮验收门禁。
- Linux 或 macOS 进入交付范围前，分别建立平台 ADR、透明窗口验证和真实进程树测试。

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

Work Package 执行清单是 Phase 0–3 的顺序、状态和范围真相源。当前项目采用个人开发模式：不为 Work Package 创建 PR，生产改动直接提交到 `refactor/tauri-runtime-v2`，通过单一目的、详细提交记录、退出门禁和稳定化检查控制范围。

### Phase 0：冻结与基线

- [x] 固定旧迁移取证源为 `feat/tauri-assistant-migration` / `190dfafd24f5c5226bff8b4347837b6e45d9a331`，不依赖本地 stash。
- [x] 从最新 `dev` 创建 `refactor/tauri-runtime-v2`。
- [x] 记录 Qt 当前功能、已知问题和启动基线。
- [x] 建立 Qt 与 v2 共用的真实冒烟场景清单。
- [ ] 记录现有角色、配置、历史、Memory 和用户目录 schema/路径基线。
- [ ] 定义 Qt/Tauri 共用应用锁和 Qt → Tauri → Qt 数据兼容门禁。
- [ ] 完成 #140 候选复用文件清单。

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
- [ ] 按已确认的 dogfooding 策略，只将当前 v2 开发分支的默认启动入口切到 Tauri，不改变现有正式安装包入口。

退出条件：单透明窗口方案在目标 Windows 环境可行；即使没有 Python，Shell 仍可见、可诊断、可退出。

### Phase 1B：进程监管与 Fake Core

- [ ] 建立最小 Core Supervisor 和受控进程树。
- [ ] 使用最小测试 transport 建立 Fake Core，覆盖正常启动、延迟 hello、初始化卡死和忽略 shutdown。
- [ ] 覆盖运行中崩溃、后代子进程、长任务不返回和受控进程树建立失败。
- [ ] 覆盖 spawn/hello/initialize/restart backoff 期间退出、旧树停止时手动重试、连续重试和重复 shutdown。
- [ ] 验证 Tauri 退出和 Core 重启后无遗留 Core 后代进程。
- [ ] 验证有限自动重启和手动重试。

退出条件：Supervisor 故障矩阵通过，Core 的任何失败都不会带走 Shell 或留下未受控子进程。

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

退出条件：真实 Python Core 可以在不加载 Qt UI 和重型领域模块的情况下建立通信、上报状态并可靠关闭。

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

- [ ] Python TTS 合成与无 Qt 播放层接通。
- [ ] 播放失败不拖垮基础聊天，资源和子进程可回收。
- [ ] 优先评估 Rust 原生播放；最终实现由 Phase 4 设计和真实设备验证决定。
- [ ] 建立音频 ADR，确认 `audio.*` 最终所有者；Rust 播放归 Rust，Python 无 Qt 播放归 Python。
- [ ] Action ID 工具确认。
- [ ] 手动截图、自动观察和主动事件。
- [ ] 长任务使用 Operation，不阻塞控制通道。

### Phase 5：设置、历史和诊断

- [ ] 按 `core.*`、`desktop.*`、`ui.*` 和已确认的 `audio.*` 所有权拆分设置读写。
- [ ] 每个配置域独立 validate、原子保存和返回结果。
- [ ] Core 配置 change plan 和受控重启。
- [ ] 第一版角色切换允许重启 Core，并为原子 Session 切换保留接口。
- [ ] 历史分页和诊断快照。
- [ ] 首次设置流程。

### Phase 6：角色工作室

- [ ] Workspace/Draft 独立模型。
- [ ] 导入、预览、保存、发布和回滚。
- [ ] 大文件操作使用 Operation。
- [ ] 工作室写入不直接修改运行中 Assistant 对象。

### Phase 7：发布验收

- [ ] 完整 Python 和 Rust 测试。
- [ ] 真实 Tauri WebView E2E。
- [ ] Windows 多 DPI、多屏、托盘、IME、音频和截图人工验收。
- [ ] Core、MCP、TTS 和浏览器子进程故障注入。
- [ ] 长时间运行、重复启停和更新包验收。
- [ ] 干净 Windows 安装验收。

退出条件：全部门禁通过后，才允许合并到 `dev` 并进入正式发布链。Qt 仍保留为显式回退，删除时间另行决策。

## 11. 核心验收指标

- Shell 不出现等待 Core 的空白期。
- 参考机器冷启动可见时间 p95 目标不高于 1 秒；500 ms 是优化目标。
- 同一用户会话只能有一个 Sakura Desktop 实例和一个受监管 Python Core 根进程。
- Tauri 与 legacy Qt 不能同时持有应用锁或写入共享用户数据。
- Core 领域子进程属于同一受控进程树。
- Tauri 退出后 5 秒内无 Core、MCP、TTS 或浏览器后代进程残留。
- Python 初始化卡死时可以取消或强杀，不阻塞 Tauri 主线程。
- 长操作期间 health、cancel、shutdown、诊断和 UI 保持响应。
- 有资源冲突的业务请求明确 queued 或 busy，不能无响应。
- Core 重启后旧 generation 事件、Operation、资源 token 和快照全部失效。
- 配置保存失败不产生不可解释的运行时半更新。
- Qt 创建的数据可被 Tauri v2 读取；退出 v2 后 legacy Qt 仍可启动并读取兼容数据。
- Provider 网络不可达不阻塞 Shell 和 Core 启动，只影响对应业务请求。
- 真实应用验收失败时，不得以静态契约或单元测试通过为由切发布入口。

## 12. 已确认决策摘要

1. Tauri 是唯一桌面生命周期根，Python 是无 Qt Assistant Core。
2. Runtime v2 首轮只实现 Assistant；Agent 延后为可选插件。
3. Python 通过 Adapter/Facade 复用现有领域服务，不重写 Assistant。
4. 主桌宠采用单透明窗口和简单立绘表现。
5. v2 分支在 Phase 1A 后默认启动 Tauri，Qt 保留显式回退。
6. Core 崩溃不能带走 UI，Tauri 拥有进程树最终回收权。
7. 第一版 IPC 使用 stdin/stdout framed transport，具体协议实现由 ADR 约束并允许技术验证后调整。
8. Phase 1 不绑定前端框架重构。
9. `core.*`、`desktop.*`、`ui.*` 各自独立持久化；`audio.*` 在 Phase 4 根据播放层确定所有者。
10. 复杂 Core 配置通过 change plan 受控重启；角色重启只是第一版简化。
11. 完整回复由 WebView 使用打字机展示，不要求真实 token streaming。
12. 局部模糊延期为可选增强，不属于基础聊天门禁。
13. Runtime Repair 早期只提供诊断和安全操作入口。
14. Phase 1–3 不做破坏性用户数据迁移，Qt/Tauri 使用同一应用锁并通过双向回退门禁。
15. Phase 1A–3 正式目标平台是 Windows，其他平台延后单独验证。

## 13. 交付治理与技术 ADR

以下内容是当前推荐实现，不是不可变产品约束。若技术验证发现更简单、同样满足硬边界的方案，可以通过更新 ADR 调整。

强制交付治理：

- `docs/superpowers/plans/2026-07-15-runtime-v2-delivery-governance.md`
- `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md`

治理文件约束 Work Package、允许列表、提交粒度、证据门禁、Bug Budget 和停止条件；执行清单记录 Phase 0–3 的 Work Package 顺序、状态、范围、证据和回退。即使实现技术方向正确，违反治理边界也不得进入下一 Work Package。

技术 ADR：

- `docs/adr/0001-runtime-v2-process-supervision.md`
- `docs/adr/0002-runtime-v2-ipc.md`
- `docs/adr/0003-runtime-v2-data-compatibility.md`

ADR 状态按以下流程演进：

```text
Proposed
-> Technically Validated
-> Accepted
-> Superseded
```

- ADR-0001 在 Phase 1B 的 Windows 进程树与 Fake Core 门禁通过后进入 Accepted。
- ADR-0002 在 Phase 1C/2 的协议、并发、阻塞隔离和背压门禁通过后进入 Accepted。
- ADR-0003 在 Phase 3 的 Qt → Tauri → Qt 数据兼容门禁通过后进入 Accepted。
- 技术验证失败时更新或替代 ADR，不为了符合文档而强行保留失败方案。

## 14. 最终审查重点

最终审查应重点确认：

- 是否仍然以“替换 Qt 桌面层、复用 Python Assistant”为中心。
- 是否明确排除了 Agent 平台和 Python Assistant 重写。
- Phase 1A–1D 是否足够小且可独立验收。
- Phase 3 是否同时具备真实聊天和明确视觉收益。
- 可靠性基础设施是否只建设到支撑当前产品所需的程度。
- 技术 ADR 是否保留了根据验证结果简化实现的空间。
- 旧用户数据、legacy Qt 回退和双入口互斥是否拥有可执行门禁。
- WebView、Rust 控制面和 Python 领域执行面是否保持明确权限边界。
