---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-30
---

# Runtime v2 产品功能等价规范与发布台账

> 状态：Normative / 持续更新
> 日期：2026-07-24
> 适用范围：从 legacy Qt Sakura 迁移到 Tauri Runtime v2 的全部现有用户能力
> 真相源职责：本文件记录“不能丢什么”和对应交付门禁；Work Package 文件记录“何时、在哪个范围实施”

## 目标

Runtime v2 是桌面运行时和 UI 重构，不是产品删减。进入 `dev` 和发布前，现有用户可见功能、可配置能力、数据可读性和关键平台行为必须达到等价或获得项目负责人明确批准的替代体验。

2026-08-27 产品方向修订：Legacy Qt 已按 ADR-0034 从当前源码退役。旧行为基线只由 Git 历史保存；当前
Runtime v2 使用全新的 v1 数据契约，正常运行不保留旧 parser、migration或双读。ADR-0038 唯一允许首次启动
显式、离线、事务化的 0.9.x importer；旧应用和旧插件宿主仍不通过第二套运行链保活。

“Git 历史里曾经存在”“未来 Phase 会做”均不等于功能已经进入当前产品。每项保留能力必须拥有可执行映射：

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

当前代码位于 `app/agent/` 下的 Chat Pipeline、Memory、Tools、MCP 和相关插件能力属于现有 Sakura Assistant 产品能力，必须进入本台账。它们不能因为模块名包含 `agent` 而被延期为可选的新平台。

未来通用自治任务编排、多 Agent Runtime、任务图和通用 Capability Broker 不属于本轮必备功能，可以保持可选或延期。

## 强制功能台账

| ID | 现有能力 | Runtime v2 目标 | 目标 WP | 平台敏感点 | 当前状态 |
|---|---|---|---|---|---|
| CAP-001 | 默认启动与单实例 | Tauri 是唯一产品桌面根 | WP-1P-02、WP-1P-03、WP-1P-06、WP-7-03 | 可执行/Runtime 定位、锁、退出 | implemented |
| CAP-002 | 桌宠立绘、气泡、输入、展开状态 | 固定渲染包络内的真实立绘、常驻气泡与常驻输入框；首次放置按可见表面留在工作区，用户拖拽后的显式锚点不做屏幕边界夹取，后续状态与缩放保持该位置 | WP-1P-05、WP-3-03、WP-3U-02、WP-3-04 | 透明窗口、scale、多屏 | implemented |
| CAP-003 | 点击穿透、拖动、焦点、IME、显示隐藏 | 平台 backend 保持相同用户语义 | WP-1P-05、WP-3-03 | Win32、NSWindow、X11/Wayland | implemented |
| CAP-004 | 真实聊天、思考、完成与错误 | 无 Qt Core、IPC/Gateway/Snapshot 和当前 WebView 共同承载聊天 | WP-3-01、WP-2-01、WP-2-02、WP-3-02、WP-3-04 | Provider/网络失败不阻塞 Shell | architecture-validated |
| CAP-005 | 取消、跳过打字机、请求唯一终态 | 最小聊天 cancel 与 UI 表现动作分离；不以前置通用 Operation 为条件 | WP-2-02、WP-3-02、WP-3-03、WP-3-04 | 旧 generation、晚到事件 | planned |
| CAP-006 | 角色、初始消息、主题、立绘/表情切换 | WP-3-03 先用真实角色冻结表现，WP-3U-02 完成可见能力与外观设置，真实聊天随后只投影 portrait/tone | WP-2-02、WP-3-03、WP-3U-02、WP-3-04、WP-3-05、WP-5-03 | 资源路径、scale、编码 | planned |
| CAP-007 | 聊天历史读取、追加和分页 | Python 数据真相源；Rust 只读 DTO | WP-3-02、WP-3-06、WP-5-03 | 文件锁、原子写、路径 | planned |
| CAP-008 | Memory 检索、写入、整理和外部存储 | 无 Qt Memory Adapter；错误降级不破坏聊天 | WP-4-01 | 本地模型、Qdrant、SQLite、子进程 | planned |
| CAP-009 | 内置 Tools 与工具结果 | Core ToolRegistry 直接执行；参数、generation 和 contribution identity 由边界校验 | WP-4-02 | 长任务、路径、错误返回 | implemented |
| CAP-010 | 工具授权交互 | 当前响应式助手不做二次确认；未来自主 Agent 权限另行设计 | ADR-0031 | 不保留未启用协议 | approved-replacement |
| CAP-011 | MCP 配置、启动、工具调用和清理 | Core MCP bridge 属于受控 generation 进程树 | WP-4-03 | command、进程组、stdio、凭据 | planned |
| CAP-012 | Python 插件、context/event/tool 扩展 | Plugin v3 一次拓扑加载、三态插件和整 Worker 重建 | WP-4-04 | 插件私有数据、子进程、路径 | implemented |
| CAP-013 | TTS 合成、参考音频、本地服务 | Python 合成 + 已批准播放 backend | WP-4-05 | 音频设备、codec、模型子进程 | planned |
| CAP-014 | 播放、停止、队列和设备错误恢复 | `audio.*` 所有权明确；聊天不被播放失败拖垮 | WP-4-05 | Windows/macOS/Linux 音频栈 | planned |
| CAP-015 | 手动截图与受控图像资源 | Core/原生捕获 + generation resource token | WP-4-06 | 权限、多屏、DPI、Wayland portal | parity-accepted |
| CAP-016 | 屏幕感知、自动观察和主动互动 | WebView 普通 timer；Rust 捕获鼠标所在屏幕并保留有界内存批次；复用普通聊天链 | WP-4-07 | 截图权限、休眠、计时器 | parity-accepted |
| CAP-017 | 提醒与待办 | 尚无已确认的 Runtime v2 产品需求；出现真实需求后单独立项 | 未排期 | 时区、休眠恢复、开机启动 | unscheduled |
| CAP-018 | Core/API/模型/MCP/插件/TTS 配置 | 设置按领域纵向迁移：WP-3S-01 先接 Provider/模型，MCP/插件/TTS 随所属能力 WP 开放，WP-5-01 只做仓库与 change plan 收口 | WP-3S-01、WP-4-03、WP-4-04、WP-4-05、WP-5-01 | 密钥存储、文件权限 | planned |
| CAP-019 | 桌面、主题、气泡、字体和音频配置 | WP-3U-02 先接角色外观/ui 窄子集；聊天/音频设置随真实消费者迁移，Phase 5 收口剩余 `desktop.*`/`ui.*` 一致性 | WP-3U-02、WP-3-04、WP-4-05、WP-5-01、WP-5-04 | 平台默认值、字体、scale | planned |
| CAP-020 | 设置窗口、首次设置和 0.9.x 数据迁移 | 同 App 设置宿主提供首次导航与三步指路；0.9.x 入口在 Core paused期间显式选择、检查、事务迁移并后置校验 | WP-3U-01、WP-3U-02、WP-3S-01、WP-4-01 至 07、WP-5-02、ADR-0038 | 窗口管理、磁盘空间、密钥、原子回滚 | implemented |
| CAP-021 | 角色切换与运行中 Session | 设置页原子保存目标后受控 Core restart；旧 generation 的 Session、Memory、历史游标、TTS、资源和迟到回调全失效，新 generation 完整水合 | WP-5-03 | 资源、历史、Memory/TTS 状态 | implemented |
| CAP-022 | 托盘、右键菜单、置顶、快捷键、开机启动 | WP-3U-01 提供 Rust 管控的主题自绘桌宠菜单、原生托盘和可持久化的桌宠置顶；未迁移项只显示禁用态，其余由平台服务补齐 | WP-3U-01、WP-5-04 | 三平台 API 和权限 | planned |
| CAP-023 | 浏览器自动化和相关受控进程 | Core Operation + 受控浏览器进程树 | WP-5-05 | 浏览器定位、sandbox、子进程 | planned |
| CAP-024 | 移动端/本地桥接插件能力 | 保留现有协议和安全边界，不另建生命周期根 | WP-5-05 | 端口、网络权限、防火墙 | planned |
| CAP-025 | 诊断、日志、手动修复和安全重试 | Rust 单写者提供默认开启、全层脱敏的本地统一日志；WP-5-06 日志查看器切片展示本次运行的安全事件；历史读取、设置、导出和完整 Runtime Repair 后移 | WP-1D-01、WP-4L-01、WP-5-06 | 路径、日志、权限 | implemented |
| CAP-026 | 角色 Studio、草稿和预览 | Workspace/Draft 独立模型，预览与运行态隔离 | WP-6-01、WP-6-02、WP-6-03 | 大文件、资源预览、窗口 | planned |
| CAP-027 | 角色导入、发布、回滚 | 校验、原子保存、Operation 和故障恢复 | WP-6-02、WP-6-04、WP-6-05 | ZIP 路径安全、文件替换 | planned |
| CAP-028 | 更新包、安装和回退 | 三平台包、签名、完整性和干净安装门禁 | WP-7-04 | 签名、notarization、包格式 | planned |
| CAP-029 | 长时间运行、重复启停和故障恢复 | 三平台 soak + Core/MCP/TTS/browser 故障注入 | WP-7-05 | 休眠、多用户、资源泄漏 | planned |
| CAP-030 | Runtime v2 v1 数据完整性 | 当前 v1 fixture -> parser/repository -> Runtime v2 直接验证 | WP-7-03 | 路径、锁、原子替换、编码 | planned |
| CAP-031 | 官方默认插件替换与 Python 依赖隔离 | 官方/第三方使用同一 SDK、Runner 和 Host 能力；每插件独立进程与 dependency root，Core 只依赖能力契约 | WP-4-09 | 原生 wheel、进程树、跨进程 Service、安装失败 | planned |

2026-07-24 的 WP-1P-05A 是 CAP-001、CAP-002、CAP-003 的窄范围 macOS 基础纠正稳定化：
它只修正默认入口、透明 Shell 和拖动后的固定立绘锚点，不改变本表任何能力状态，也不接入
CAP-004 及之后的 Assistant、聊天、Memory、Tools、TTS 或设置能力。它的真实单显示器证据不替代
WP-7-02 的 Spaces、多屏、IME、Retina、签名和发布门禁。

2026-07-26 的产品方向调整把“设置窗口宿主”和“角色包可见表现”提前到 Phase 3；2026-07-28 又把
设置交付从 Phase 5 集中补齐改为持续的 feature 级迁移，规范见
`docs/specs/runtime-v2/settings-incremental-migration.md`。WP-3U-01 只建立同一 Tauri App 的右键菜单、唯一
settings 窗口和能力门控；WP-3U-02 只开放当前角色的名称、初始消息、主题、立绘/表情和外观设置；
WP-3S-01 在其 accepted 后迁移供应商与模型。TTS、Memory、Tools、MCP、插件、主动互动等设置仍随各自
领域 WP 开放，首次设置由 WP-5-02 编排，不能因旧页面或控件已经存在而标记完成。

## 当前 Architecture Validation 门禁

`architecture-validated` 必须至少通过：

```text
真实领域实现
-> Python Core
-> IPC
-> Rust Gateway
-> 当前 WebView
-> 数据写入或状态恢复
-> 故障和 generation 重建
```

CAP-004 由当前 Core Host lifecycle、真实本地 Provider 聊天链、Rust Gateway/Supervisor 和前端状态机分别
覆盖同一产品链。测试必须驱动公开行为、失败和资源清理；不得用读取源码字符串、固定文件布局或恢复一套
Legacy oracle 代替运行时证据。

该门禁证明当前架构能够承载真实产品，不代表功能、平台、UX 或数据的最终等价；CAP-004 仍须在 Phase 7
达到 `parity-accepted` 或取得明确批准的替代设计。

## 每个能力 WP 必须补充的字段

激活任何上表目标 WP 前，必须把对应行扩展为可执行记录，至少包括：

- 当前入口、操作步骤、正常结果和错误结果。
- 涉及的数据文件、schema、资源和子进程。
- Python、Rust、WebView 与平台 backend 的所有权。
- command、event、Snapshot、Operation 和 resource token 契约。
- Windows、macOS、Linux 的平台差异；Linux 同时说明 X11/Wayland。
- 自动测试、故障注入、真实应用验收和当前 v1 数据回读步骤。
- 性能、动画、焦点、IME、无障碍或音频等 UX 门禁。
- 独立回退方式和回退后仍可使用的能力。

## 发布等价门禁

Phase 7 的 WP-7-03 必须逐行审查本台账：

1. 不允许存在 `baselined`、`planned` 或仅 `implemented` 的发布必备行。
2. `platform-verified` 只能证明平台实现，不能替代真实产品语义和数据门禁。
3. `approved-replacement` 必须链接项目负责人批准记录、用户体验说明和当前数据结果。
4. Tauri 写入必须通过当前 v1 fixture 和 parser/repository 测试；测试不得依赖历史 GUI 入口。
5. WP-7-03 必须确认当前源码、依赖、测试和发布工件没有重新引入第二套桌面入口或 Qt 运行时。
6. 全部能力通过后仍需 WP-7-04、WP-7-05 的打包、更新、长时间运行和故障恢复验收。

任何能力无法保持时，必须在对应功能开发前提出替代设计并获得批准；不得在 Phase 7 才以时间不足为理由删除或降级。
