# ADR-0004：Runtime v2 跨平台基础与平台后端边界

> 状态：Proposed（跨平台要求已批准，技术实现待 Phase 1P 验证）
> 日期：2026-07-22
> 适用范围：Tauri Shell、legacy Qt 回退、Python Core 监管、透明窗口、运行时定位、诊断、CI 和发布打包

## 背景

Runtime v2 已经在 Windows 上建立 Tauri Shell、共享应用锁、透明窗口、受控进程树、Supervisor 和最小无 Qt Python Core Host。上述成果证明了架构方向，但 Phase 1A–3 原计划把 Windows x64 作为唯一正式目标，并将 macOS/Linux 延后。这与“跨平台从基础阶段开始建设，而不是产品功能接近完成后再适配”的产品要求冲突。

本 ADR 不否定已有 Windows 证据。现有 Win32 实现继续保留为 Windows backend；需要纠正的是把平台 backend 当成 Runtime 抽象本身，以及用单平台证据接受影响多平台的 Work Package。

本 ADR 的跨平台要求是已批准的产品和架构约束。`Proposed` 仅表示具体 backend 尚未完成技术验证，不表示可以继续按 Windows-only 路线实现后续阶段。

## 决策

WP-1C-02 已按原 Windows 技术门完成并提交为 `a06e1dada66b02474f3d65d4124f31094cda5e9e`，不拆分、不回退这批成果。在 WP-1C-02 与 WP-1C-03 之间插入 Phase 1P；WP-1C-03 及其后的 Runtime、IPC、产品能力和发布 Work Package 必须以 Phase 1P 全部 `accepted` 为前置。

首个必须持续参与设计、编译和基础生命周期验收的平台矩阵为：

| 平台 | 首个正式 target | 最低基础门禁 |
|---|---|---|
| Windows | x86_64-pc-windows-msvc | Shell、共享锁、透明窗口、Core 进程树、hello/shutdown、打包定位 |
| macOS | aarch64-apple-darwin | Shell、共享锁、透明窗口、Core 进程组、hello/shutdown、app bundle/sidecar 定位 |
| Linux | x86_64-unknown-linux-gnu | Shell、共享锁、Core 进程组、hello/shutdown、包内 Runtime 定位；窗口分别记录 X11/Wayland 结果 |

macOS x64、Windows ARM64 和其他 Linux 架构不作为 Phase 1P 首个发布 target，但接口、协议、数据格式和资源布局不得写死为上述 CPU 架构。增加正式 target 时必须扩展同一矩阵，不复制第二套 Runtime。

## 平台服务边界

Rust/Tauri 必须通过稳定的平台服务边界使用原生能力：

```text
PlatformRuntime
├─ InstanceLockBackend
├─ ManagedProcessTreeBackend
├─ WindowInteractionBackend
├─ RuntimeLocator
└─ NativeDiagnosticsBackend
```

约束：

- `CoreSupervisor`、generation、restart budget、IPC Envelope、CoreReadiness 和 Snapshot 不依赖具体操作系统。
- 平台 backend 只拥有原生资源与错误转换，不拥有 Assistant 业务状态。
- 公共层不得以 `cfg(not(windows)) => Unsupported/Fatal` 作为已支持平台的最终实现。
- 平台失败必须映射到稳定、可诊断的错误类别；不得静默降级成未受监管 Core、并发数据写入者或不可关闭窗口。
- Windows backend 可以保留 Job Object、named mutex 和 Win32 window region，不要求为了形式统一而改写已经验证的实现。

## 共享应用锁

语义 identity 在所有平台均为：

```text
sakura.desktop.shared-user-data.v1
```

平台实现：

- Windows：保留 `Local\SakuraDesktop.SharedUserData.v1` named mutex。
- macOS/Linux：Rust 与 Python 使用同一个、位于平台用户 runtime/state 目录中的 advisory lock 文件，并冻结完全一致的路径解析、打开模式和锁语义。
- POSIX backend 必须以进程持有的 OS advisory lock 为权威；普通文件存在、PID 文本或手工 stale 判断不能代表锁仍被持有。
- 锁路径不能位于共享 `data/` 内，获取锁前不得为了日志、配置、migration 或探针修改共享用户数据。
- Qt/Tauri 双向冲突、正常释放、强杀释放、API/权限失败和获取前零写入必须在每个正式平台真实验证。

具体数据兼容与锁生命周期继续由 ADR-0003 约束。

## 受控进程树

公共语义继续使用 ADR-0001 的 `ManagedProcessTree`，但实现必须是可替换 backend：

- Windows：独立 Job Object、kill-on-close、受控 suspended spawn。
- macOS：spawn 时建立独立 session/process group；优雅期后向整组发送终止信号并使用 `waitpid`/等价机制验证退出。
- Linux：spawn 时建立独立 session/process group；使用 group signal 和 wait 验证。可以增加 parent-death signal 作为保险，但不能把它当成唯一回收机制。
- 所有平台都必须证明 Core 根退出后遗留后代仍会被 Tauri 回收，且旧 generation 完整退出前不会启动新 generation。
- spawn 与加入监管容器之间不能存在允许后代逃逸的未验证窗口；无法建立监管边界时安全失败。

## 透明窗口、命中、拖动、焦点和 IME

逻辑布局、命中矩形、状态 revision 和锚点算法保持共享；原生命中与拖动属于平台 backend。

- Windows：保留已验证的 Win32 region 与 move loop backend。
- macOS：必须验证透明 NSWindow/WebView 的命中、拖动、焦点恢复、中文/日文 IME、Retina scale 和 Spaces/多屏行为。
- Linux：X11 与 Wayland 分开记录。不能用 X11 自动测试代表 Wayland，也不能把 compositor 内部窗口误认为 Sakura 主窗口。
- 若某平台无法用单原生透明窗口保持点击穿透、输入和拖动语义，必须在 Phase 1P 内评估同一 Tauri App 管理的受控多窗口组合。产品硬约束是连续桌宠体验和单一生命周期根，不是所有平台必须使用同一种窗口拓扑。
- 不允许在接近发布时以“该平台暂不支持点击穿透/IME/拖动”静默削减用户能力。

## Runtime 与 Python 定位

`RuntimeLocator` 必须区分开发、测试和发布布局，并返回结构化结果：

- Tauri 可执行文件、资源根、Python 可执行文件、Core module、工作目录和架构均由平台 locator 解析。
- Windows 不得让公共逻辑依赖 `.exe`；macOS/Linux 不得模拟 Windows 仓库目录。
- 发布路径只使用包内受控 Python/sidecar，不静默回退系统 Python。
- 开发路径可以显式使用仓库 Runtime，但必须由开发配置选择，不能通过扫描任意 PATH 猜测。
- macOS app bundle、代码签名/notarization，Linux AppImage/deb/rpm 或最终选定包格式，以及 Windows bundle 的资源布局必须有 golden fixture。

## CI 与真实验收

Phase 1P 建立后，所有平台敏感 Work Package 至少需要：

1. Windows、macOS、Linux 三平台编译和平台单元测试。
2. 共享协议、generation、Snapshot 和纯布局 golden fixtures 在三平台字节/语义一致。
3. 对受控进程树、共享锁和 RuntimeLocator 运行对应平台的真实子进程测试。
4. 真实 Tauri Shell + 最小 Python Core 执行 `hello -> health -> shutdown`，并证明根、后代、管道、锁和临时资源零残留。
5. 窗口、IME、透明命中、拖动、多屏和 DPI/scale 由对应平台实机或可重复的受控 GUI 环境验收；无 GUI 的 CI 不能替代该证据。
6. Linux 报告必须明确当前 session 是 X11 还是 Wayland，并分别登记支持状态。

单个平台通过只能记录为该 backend 的证据，不能把影响多平台的 Work Package 标记为全局 `accepted`。

## Phase 1P Work Package

- WP-1P-01：在 WP-1C-02 accepted 后冻结 target matrix、平台接口、错误分类和测试责任。
- WP-1P-02：跨平台 RuntimeLocator 与包内 Python 布局。
- WP-1P-03：Rust/Python 共享应用锁 backends。
- WP-1P-04：Windows/macOS/Linux 受控进程树 backends。
- WP-1P-05：透明窗口、命中、拖动、焦点、IME 与原生诊断 backends。
- WP-1P-06：三平台最小 Shell + Core lifecycle 和 CI 总门禁。

每个 WP 的允许目录、故障矩阵、真实环境和独立回退以 Work Package 真相源为准。

## WP-1P-05 CI platform foundation 记录（2026-07-24）

WP-1P-05 已在 Draft PR #147 最新 HEAD `3e23285f90c40cd45d6817918d9a4fdf8aebb127` 完成
三平台窗口交互与原生诊断 backend 的 CI platform foundation：Windows x64、macOS arm64、
Linux x64 push run `30066486490` 与 pull_request run `30066488599` 全绿，Unit/UI run
`30066488685` 全绿。Linux run 包含有界 Xvfb window backend 合同测试；共享布局、命中、scale、
revision 和失败恢复模型保持平台无关，diagnostics 只输出脱敏结构化 facts。

本记录不把 CI/Xvfb 结果描述成真实设备体验。macOS 的透明命中、IME、Retina、Spaces、多屏，
Linux X11 的透明命中/拖动/IME/多屏，以及 Linux Wayland compositor、窗口身份和输入体验，
均登记为 WP-7-02/WP-7-02-HW 发布前硬门禁。ADR-0004 仍等待 WP-1P-06 完成后，才可更新为
`Technically Validated for CI platform foundation`。

## 结果与代价

收益：

- 平台差异在产品功能迁移前暴露。
- 已验证 Windows 实现得到保留，同时避免它成为公共协议。
- 后续聊天、TTS、截图、托盘和 Studio 不需要在接近完成时重做生命周期与资源定位。

代价：

- WP-1C-03 及后续功能延期，必须提前建设 macOS/Linux 测试环境。
- Wayland 透明命中和 macOS 原生窗口行为可能迫使窗口拓扑调整。
- 打包、签名和 bundled Python 布局需要比原 Windows-first 计划更早冻结。

这些代价属于满足跨平台产品要求的基础成本，不能通过延后验证消失。

## ADR 状态门禁

本 ADR 从 `Proposed` 更新为 `Technically Validated` 前，WP-1P-01 至 WP-1P-06 必须全部完成自动和真实技术门，且三平台最小生命周期链均无 P0/P1。

本 ADR 更新为 `Accepted` 前，还必须在至少一个真实产品垂直链中证明三个平台共用相同 Supervisor、IPC、Snapshot 和能力语义；平台 backend 只处理原生差异，没有产品能力静默降级。最终发布仍受功能等价台账和 Phase 7 门禁约束。
