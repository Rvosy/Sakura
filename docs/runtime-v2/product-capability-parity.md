# Runtime v2 产品功能等价规范与发布台账

> 状态：Normative / 持续更新
> 日期：2026-07-24
> 适用范围：从 legacy Qt Sakura 迁移到 Tauri Runtime v2 的全部现有用户能力
> 真相源职责：本文件记录“不能丢什么”和对应交付门禁；Work Package 文件记录“何时、在哪个范围实施”

## 目标

Runtime v2 是桌面运行时和 UI 重构，不是产品删减。内部开发分支可以暂时只有 Shell、Fake Core 或局部垂直链，但进入 `dev` 和发布前，现有用户可见功能、可配置能力、数据可读性和关键平台行为必须达到等价或获得项目负责人明确批准的替代体验。

“代码仍在仓库”“legacy Qt 可以回退”“未来 Phase 会做”均不等于功能已经迁移。每项能力必须拥有可执行映射：

```text
legacy 行为与数据
-> Python Core/领域服务
-> IPC command/event/Snapshot
-> Rust 权限与资源边界
-> WebView/原生 UI
-> Windows/macOS/Linux 平台行为
-> 自动测试与真实验收
```

## 状态定义

- `baselined`：已记录 legacy 行为和数据，但 v2 未接入。
- `planned`：已分配目标 Work Package 和退出门。
- `implemented`：生产实现存在，但完整门禁未通过。
- `architecture-validated`：真实领域实现已经跨 Python Core、IPC、Rust Gateway、最小 UI/acceptance harness、数据写入或状态恢复、故障和 generation 重建完成一条可重复纵向链；仍未达到发布等价。
- `platform-verified`：目标平台矩阵均通过该能力的平台门。
- `parity-accepted`：自动、真实应用、数据往返和 UX 门禁全部通过。
- `approved-replacement`：产品负责人批准了保持能力但改变具体交互的替代设计；必须附 ADR/记录，不能用于静默删功能。

`architecture-validated` 是早期架构门，不是最终状态的替代。只有 `parity-accepted` 或 `approved-replacement` 可以通过 Phase 7 产品等价审查。

## Assistant 与未来 Agent 的边界

当前代码位于 `app/agent/` 下的 Chat Pipeline、Memory、Tools、MCP、工具确认和相关插件能力属于现有 Sakura Assistant 产品能力，必须进入本台账。它们不能因为模块名包含 `agent` 而被延期为可选的新平台。

未来通用自治任务编排、多 Agent Runtime、任务图和通用 Capability Broker 不属于本轮必备功能，可以保持可选或延期。

## 强制功能台账

| ID | 现有能力 | Runtime v2 目标 | 目标 WP | 平台敏感点 | 当前状态 |
|---|---|---|---|---|---|
| CAP-001 | 默认启动、单实例、显式 legacy Qt 回退 | Tauri 为默认根；Qt/Tauri 共用锁；安全切换 | WP-1P-02、WP-1P-03、WP-1P-06、WP-3-06 | 可执行/Runtime 定位、锁、退出 | implemented |
| CAP-002 | 桌宠立绘、气泡、输入、展开状态 | 同一 Tauri App 下连续组合体验 | WP-1P-05、WP-3-03、WP-3-04 | 透明窗口、scale、多屏 | implemented |
| CAP-003 | 点击穿透、拖动、焦点、IME、显示隐藏 | 平台 backend 保持相同用户语义 | WP-1P-05、WP-3-03 | Win32、NSWindow、X11/Wayland | implemented |
| CAP-004 | 真实聊天、思考、完成与错误 | 无 Qt Assistant Adapter + 最小 IPC/Gateway/Snapshot 纵向链 | WP-2-01、WP-2-02、WP-3-01、WP-3-02、WP-3-04、WP-3V-01 | Provider/网络失败不阻塞 Shell | planned |
| CAP-005 | 取消、跳过打字机、请求唯一终态 | 最小聊天 cancel 与 UI 表现动作分离；不以前置通用 Operation 为条件 | WP-2-02、WP-3-02、WP-3-03、WP-3-04、WP-3V-01 | 旧 generation、晚到事件 | planned |
| CAP-006 | 角色、初始消息、立绘/表情切换 | 最小 Snapshot + WebView 状态；资源平台按真实消费者后移 | WP-2-02、WP-3-03、WP-3-04、WP-3-05、WP-5-03 | 资源路径、scale、编码 | planned |
| CAP-007 | 聊天历史读取、追加和分页 | Python 数据真相源；Rust 只读 DTO | WP-3-02、WP-3-06、WP-3V-01、WP-5-03 | 文件锁、原子写、路径 | planned |
| CAP-008 | Memory 检索、写入、整理和外部存储 | 无 Qt Memory Adapter；错误降级不破坏聊天 | WP-4-01 | 本地模型、Qdrant、SQLite、子进程 | planned |
| CAP-009 | 内置 Tools 与工具结果 | Core ToolRegistry + Operation | WP-4-02 | 权限、长任务、路径 | planned |
| CAP-010 | 有副作用工具确认 | Action ID 确认，不允许 WebView 伪造执行参数 | WP-4-02 | 原生提示、焦点、超时 | planned |
| CAP-011 | MCP 配置、启动、工具调用和清理 | Core MCP bridge 属于受控 generation 进程树 | WP-4-03 | command、进程组、stdio、凭据 | planned |
| CAP-012 | Python 插件、context/event/tool 扩展 | 保留现有插件语义并受 Core 生命周期控制 | WP-4-04 | 插件私有数据、子进程、路径 | planned |
| CAP-013 | TTS 合成、参考音频、本地服务 | Python 合成 + 已批准播放 backend | WP-4-05 | 音频设备、codec、模型子进程 | planned |
| CAP-014 | 播放、停止、队列和设备错误恢复 | `audio.*` 所有权明确；聊天不被播放失败拖垮 | WP-4-05 | Windows/macOS/Linux 音频栈 | planned |
| CAP-015 | 手动截图与受控图像资源 | Core/原生捕获 + generation resource token | WP-4-06 | 权限、多屏、DPI、Wayland portal | planned |
| CAP-016 | 屏幕感知、自动观察和主动互动 | Scheduler/Backchannel 通过 Operation 和事件路由 | WP-4-07 | 截图权限、休眠、计时器 | planned |
| CAP-017 | 提醒、任务和定时调度 | Core 持久化，Tauri 生命周期与唤醒状态可诊断 | WP-4-07 | 时区、休眠恢复、开机启动 | planned |
| CAP-018 | Core/API/模型/MCP/插件/TTS 配置 | `core.*` validate/change plan/原子保存 | WP-5-01 | 密钥存储、文件权限 | planned |
| CAP-019 | 桌面、主题、气泡、字体和音频配置 | `desktop.*`/`ui.*`/`audio.*` 独立仓库 | WP-5-01、WP-5-02 | 平台默认值、字体、scale | planned |
| CAP-020 | 设置窗口和首次设置 | Tauri 普通窗口，逐域保存和可恢复错误 | WP-5-02 | 窗口管理、IME、密钥输入 | planned |
| CAP-021 | 角色切换与运行中 Session | 受控 Core restart；旧 generation 全失效 | WP-5-03 | 资源、历史、TTS 状态 | planned |
| CAP-022 | 托盘、置顶、快捷键、开机启动 | Tauri 原生平台服务 | WP-5-04 | 三平台 API 和权限 | planned |
| CAP-023 | 浏览器自动化和相关受控进程 | Core Operation + 受控浏览器进程树 | WP-5-05 | 浏览器定位、sandbox、子进程 | planned |
| CAP-024 | 移动端/本地桥接插件能力 | 保留现有协议和安全边界，不另建生命周期根 | WP-5-05 | 端口、网络权限、防火墙 | planned |
| CAP-025 | 诊断、日志、手动修复和安全重试 | 基础聊天前只有最小可见性；完整 Runtime Repair 后移 | WP-1D-01、WP-5-06 | 路径、日志、权限 | planned |
| CAP-026 | 角色 Studio、草稿和预览 | Workspace/Draft 独立模型，预览与运行态隔离 | WP-6-01、WP-6-02、WP-6-03 | 大文件、资源预览、窗口 | planned |
| CAP-027 | 角色导入、发布、回滚 | 校验、原子保存、Operation 和故障恢复 | WP-6-02、WP-6-04、WP-6-05 | ZIP 路径安全、文件替换 | planned |
| CAP-028 | 更新包、安装和回退 | 三平台包、签名、完整性和干净安装门禁 | WP-7-04 | 签名、notarization、包格式 | planned |
| CAP-029 | 长时间运行、重复启停和故障恢复 | 三平台 soak + Core/MCP/TTS/browser 故障注入 | WP-7-05 | 休眠、多用户、资源泄漏 | planned |
| CAP-030 | 用户数据与 Qt 双向兼容 | Qt -> Tauri -> Qt 全量读取/允许写入门禁 | WP-3-06、WP-3V-01、WP-7-03 | 路径、锁、原子替换、编码 | planned |

## 早期 Architecture Validation 门禁

`architecture-validated` 必须至少通过：

```text
真实领域实现
-> Python Core
-> IPC
-> Rust Gateway
-> 最小 UI 或 acceptance harness
-> 数据写入或状态恢复
-> 故障和 generation 重建
```

CAP-004 必须由 `WP-3V-01 Runtime v2 Assistant Architecture Validation Slice` 使用真实 Sakura Assistant 领域代码达到 `architecture-validated`，之后才能激活 WP-4-01 或继续建设大量通用 Phase 2/完整 Phase 1D 能力。Fake Core、测试 fixture、直接 Python 调用、仅真实 UI 表现或仅平台 lifecycle 证据都不能单独推进此状态。

该门禁证明当前架构能够承载真实产品，不代表功能/平台/UX/数据的最终等价；CAP-004 仍须在 Phase 7 达到 `parity-accepted` 或取得明确批准的替代设计。

## 每个能力 WP 必须补充的字段

激活任何上表目标 WP 前，必须把对应行扩展为可执行记录，至少包括：

- legacy 入口、操作步骤、正常结果和错误结果。
- 涉及的数据文件、schema、资源和子进程。
- Python、Rust、WebView 与平台 backend 的所有权。
- command、event、Snapshot、Operation 和 resource token 契约。
- Windows、macOS、Linux 的平台差异；Linux 同时说明 X11/Wayland。
- 自动测试、故障注入、真实应用验收和数据往返步骤。
- 性能、动画、焦点、IME、无障碍或音频等 UX 门禁。
- 独立回退方式和回退后仍可使用的能力。

## 发布等价门禁

Phase 7 的 WP-7-03 必须逐行审查本台账：

1. 不允许存在 `baselined`、`planned` 或仅 `implemented` 的发布必备行。
2. `platform-verified` 只能证明平台实现，不能替代真实产品语义和数据门禁。
3. `approved-replacement` 必须链接项目负责人批准记录、用户体验说明和数据兼容结果。
4. legacy Qt 回退保留期间，Tauri 写入不得让 Qt 无法启动或读取原数据。
5. 全部能力通过后仍需 WP-7-04、WP-7-05 的打包、更新、长时间运行和故障恢复验收。

任何能力无法保持时，必须在对应功能开发前提出替代设计并获得批准；不得在 Phase 7 才以时间不足为理由删除或降级。
