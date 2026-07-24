# WP-1P-01：跨平台 target、平台契约与错误分类

> 执行状态：仅见 `docs/superpowers/plans/2026-07-15-runtime-v2-work-packages.md` 第 2 节
> 日期：2026-07-22
> 规范来源：ADR-0001、ADR-0003、ADR-0004
> 适用实现：`desktop/src-tauri/src/platform/`

## 1. 范围与非目标

本规范冻结 Phase 1P 的首批正式 target、最低环境、五类平台服务契约、稳定错误分类、调用方向、证据责任和 Windows 既有实现迁移清单。它是 WP-1P-02 至 WP-1P-06 的共同输入。

本 Work Package 只增加 compile-only Rust 契约，不实例化 backend，不改变当前 Windows 生产调用路径。以下内容明确不属于 WP-1P-01：

- macOS/Linux 的 Runtime、锁、进程组、窗口或诊断实现。
- Windows 实现搬迁、行为修改或错误文案切换。
- Supervisor、generation、restart budget、IPC framing、CoreReadiness 或 Snapshot 修改。
- Assistant、聊天、Memory、Tools、MCP、插件、TTS、截图或其他产品领域能力。
- Runtime 下载、自动修复、Tauri bundle 生成或现有发布 workflow 改造。

## 2. 正式 target 与最低环境

三个 target 从 Phase 1P 起同时参与设计和持续门禁。最低用户环境、可重复构建环境和一次验收时的实际环境是三种不同证据，不得互相替代。

| 平台 ID | Rust target | 最低用户环境 | Phase 1P 可重复构建/验收基线 | WebView 基线 | 首个 Runtime v2 包格式 |
|---|---|---|---|---|---|
| `windows-x64` | `x86_64-pc-windows-msvc` | Windows 10 22H2 build 19045，x64 | Windows 11 23H2 build 22631.4890；MSVC tools 14.50.35717；Windows SDK 10.0.26100.0 | WebView2 Evergreen `150.0.4078.65` 为兼容下限；每次真实验收记录实际版本 | x64 NSIS installer；Phase 1P golden install tree 与其资源布局一致 |
| `macos-arm64` | `aarch64-apple-darwin` | macOS 13.0，Apple Silicon | macOS 14.7 系列 arm64；Xcode 16.2；deployment target `13.0` | 系统 WKWebView；证据必须记录完整 macOS build 和 Safari/WebKit build，不能用 Intel 编译结果替代 | arm64 `.app`，交付容器为 `.dmg` |
| `linux-x64` | `x86_64-unknown-linux-gnu` | glibc 2.39，x86_64；X11 或 Wayland | Ubuntu 24.04.2 LTS；GCC 13.3；`pkg-config`；GTK 3.24；WebKitGTK 4.1 API | WebKitGTK `2.44.0` 为兼容下限；每次证据记录包的完整版本 | x86_64 AppImage |

共同工具链固定为 Rust/Cargo `1.96.0`、Rust edition `2021`、Tauri `2.11.3`、`tauri-build` `2.6.3`。当前静态前端不需要 Node/npm。Phase 1P 使用 `cargo build/test --locked` 和 golden install tree，不以 Tauri CLI 生成发布工件，因此 Tauri CLI 不是 WP-1P-01 至 WP-1P-06 的隐式前置；首次进入真实 bundle Work Package 前必须单独固定精确 CLI 版本。

WebView 是受安全更新影响的系统组件，表中的版本是兼容下限而不是要求回退到旧补丁。CI 与实机证据必须记录实际完整版本；兼容下限或 OS floor 的任何调整都必须更新 ADR-0004、本规范、CI 镜像和发布台账。

`desktop/rust-toolchain.toml` 必须列出三个 Rust target。列出 target 表示正式支持责任，不表示 Windows 主机上的 cross-compile 可以替代 macOS/Linux 原生编译或真实验收。

## 3. bundled Python 来源冻结

三个 target 均使用 CPython `3.12.8`，不得使用 PATH、pyenv、Conda、Homebrew、系统 `/usr/bin/python` 或用户自己安装的 Python 作为 packaged 回退。

| 平台 ID | 冻结 source ID | 上游工件 |
|---|---|---|
| `windows-x64` | `cpython.org/3.12.8/windows-embed-amd64` | Python.org 官方 `python-3.12.8-embed-amd64.zip` |
| `macos-arm64` | `python-build-standalone/20250106/cpython-3.12.8+aarch64-apple-darwin-install_only` | Astral `python-build-standalone` release `20250106` 的 arm64 Apple Darwin install-only 工件 |
| `linux-x64` | `python-build-standalone/20250106/cpython-3.12.8+x86_64-unknown-linux-gnu-install_only` | Astral `python-build-standalone` release `20250106` 的 x86_64 GNU/Linux install-only 工件 |

WP-1P-02 已在 `desktop/src-tauri/runtime-layouts/` 为上述精确工件建立受版本控制的 SHA-256 manifest、归档顶层结构和 golden install tree；source ID、版本或 target 不匹配必须报 `integrity_mismatch` 或 `incompatible_architecture`，不能选择同一 release 中另一个模糊匹配的 asset。macOS 现有 legacy workflow 以字符串包含关系选择 asset，只能作为历史输入，不能直接成为 Runtime v2 的可重复 locator。

## 4. 公共层与平台层依赖方向

```text
Tauri composition root
  -> PlatformRuntime（每个进程只选择一次 target backend）
       -> InstanceLockBackend
       -> RuntimeLocator
       -> ManagedProcessTreeBackend
       -> WindowInteractionBackend
       -> NativeDiagnosticsBackend

CoreSupervisor -> ManagedProcessTreeBackend
Shell lifecycle -> InstanceLockBackend + RuntimeLocator
Window commands -> 共享布局/命中纯模型 -> WindowInteractionBackend
Diagnostics route -> NativeDiagnosticsBackend + 结构化 PlatformError
```

调用方向只能从 composition/common/runtime 层指向平台契约，再由契约分派到当前 target backend。平台 backend 可以持有 Win32 handle、POSIX fd/process group、NSWindow/X11/Wayland/Tauri window reference，但不得：

- 调用或拥有 `CoreSupervisor`、generation、restart budget、IPC Router 或 Python Snapshot。
- 构造 Assistant 业务状态、修改用户配置、执行 schema migration 或写聊天/Memory 数据。
- 反向调用 WebView command，或把 OS 特有字段放入 Core IPC Envelope。
- 在正式支持 target 上以公共 `cfg(not(windows)) => Unsupported` 作为最终实现。

唯一选择平台 backend 的位置是 Tauri composition root。业务和生命周期模块不得自行读取 `target_os` 后改变语义。

## 5. 五类平台服务契约

Rust 真相源位于 `desktop/src-tauri/src/platform/`。trait 必须保持 object-safe，使 composition root 能注入一个 backend 集合，而不把 target 泛型扩散到 Supervisor、Shell command 或测试。

| 契约 | 输入/输出与资源所有权 | 保持的语义 |
|---|---|---|
| `InstanceLockBackend` | 输入稳定 application ID；返回 `Acquired(lease)`、`AlreadyRunning` 或结构化错误；lease 的 Drop 是最后释放保险 | 锁必须早于任何共享数据/日志/配置动作；`AlreadyRunning` 是确定性结果，不进入 Core restart |
| `RuntimeLocator` | 输入显式 `ExplicitDevelopment` 或 `Packaged` 模式、target、exe/resource root；输出唯一 Python、应用根、Core module 和 source ID | packaged 不查 PATH；development 必须给显式 root；架构、完整性和布局错误可诊断 |
| `ManagedProcessTreeBackend` | 输入 program/args/cwd/env override/stdio；返回完整树控制权及可选三条 pipe；tree 句柄拥有终止和验证责任 | 根退出不等于树退出；新 generation 前旧树必须 verified exited；release 前不得仍有后代 |
| `WindowInteractionBackend` | 操作 Tauri 主窗口，消费共享物理 placement/hit regions；拥有原生 bounds、命中、拖动、显示、焦点/IME 激活 | 共享布局与固定立绘锚点不按平台 fork；失败恢复为全窗口可交互或明确 diagnostics，不静默失能 |
| `NativeDiagnosticsBackend` | 输入可选稳定 window label；输出 target、window backend、display server、WebView 版本和脱敏 facts | 不返回 credential、用户内容或可任意读取的裸路径；Linux 必须明确 X11/Wayland |

`PlatformRuntime` 只聚合这五个 service 和当前 `PlatformTarget`，不拥有它们的业务调用顺序。调用顺序仍由 Tauri 生命周期根和现有 Supervisor 决定。

## 6. 稳定错误模型

稳定码格式为 `platform.<service>.<category>`。`service` 固定为 `instance_lock`、`managed_process_tree`、`window_interaction`、`runtime_locator`、`native_diagnostics`。Win32 code、`errno`、`OSStatus`、X11/Wayland/WebKit 错误只进入可选 `nativeCode`，不得成为 UI、Supervisor 或测试判断语义的依据。

| Category | 典型情况 | 默认处置边界 |
|---|---|---|
| `invalid_input` | 空 program、非法 target/mode 组合、越界窗口参数 | `Never`；修复调用方，不自动重试 |
| `not_found` | bundled Runtime/入口/资源不存在 | `AfterExternalChange`；进入 diagnostics/repair |
| `permission_denied` | 文件、exec、mutex/fd/native API 权限拒绝 | `AfterUserAction`；不得提权后静默继续 |
| `unsupported_environment` | OS/session/WebView/compositor 不满足已冻结 floor | `Never` 或环境改变后手动重试；不得降级产品能力 |
| `incompatible_architecture` | Python/sidecar/包 CPU 与 target 不一致 | `Never`；换正确工件 |
| `integrity_mismatch` | hash、source ID、布局或入口校验失败 | `AfterUserAction`；不得执行损坏工件 |
| `resource_busy` | 原生资源被占用但不等同共享实例冲突 | `AfterExternalChange`；应用锁冲突仍用 `AlreadyRunning` |
| `resource_exhausted` | handle/fd/process/内存等资源耗尽 | 只有明确瞬时故障才可 `WithinSupervisorBudget` |
| `temporarily_unavailable` | spawn、WebView/native service 的已知瞬时失败 | `WithinSupervisorBudget`，必须受现有 budget/deadline 约束 |
| `timed_out` | wait、terminate、native operation 超过调用方 deadline | 是否 budgeted 由调用场景写入 `retry`；不能自行无限重试 |
| `identity_changed` | PID/PGID/window identity 与已持有资源不再一致 | `Never`；放弃旧资源并由上层建立新 generation |
| `native_failure` | 无法安全映射到更窄类别的 OS API 失败 | 默认 `Never`，保留脱敏 native code 供诊断 |

`RetryAdvice` 固定为 `Never`、`AfterUserAction`、`AfterExternalChange`、`WithinSupervisorBudget`。category 不直接授权重试；backend 在构造错误时必须同时给出 retry，只有 Supervisor 可以消费 `WithinSupervisorBudget`，并继续使用现有 restart budget。

## 7. CI 与实机证据责任

| 证据 | Windows x64 | macOS arm64 | Linux x64 |
|---|---|---|---|
| Rust fmt、合同单测、native compile/test | CI required | 原生 arm64 CI required | 原生 x64 CI required |
| RuntimeLocator/golden layout | WP-1P-02 native test | WP-1P-02 native test | WP-1P-02 native test |
| Rust/Python 双向锁与 crash release | WP-1P-03 实机/原生 CI | WP-1P-03 实机/原生 CI | WP-1P-03 实机/原生 CI |
| 进程树、后代、pipe/fd/handle 零残留 | WP-1P-04 实机/原生 CI | WP-1P-04 实机/原生 CI | WP-1P-04 实机/原生 CI |
| 透明窗口、拖动、IME、scale、多屏 | WP-1P-05 实机 | WP-1P-05 Apple Silicon 实机 | WP-1P-05 X11 实机和 Wayland 实机分别登记 |
| 两轮 Shell + Core lifecycle 总门 | WP-1P-06 | WP-1P-06 | WP-1P-06，X11/Wayland 会话元数据随证据记录 |

compile-only、mock、cross-compile、Xvfb 或嵌套 compositor 可以补充诊断，但不能替代目标平台真实窗口和生命周期证据。每个 CI job 必须输出 OS build、CPU、Rust/Cargo、编译器、WebView/GTK/WebKit、Python source ID、包布局和 session/compositor；滚动 runner 标签不能单独充当可重复环境身份。

## 8. Windows 既有实现逐文件迁移清单

以下迁移只允许在标注的后续 Work Package 发生。WP-1P-01 不移动代码，不改变当前调用路径。

| 当前文件/区域 | 目标边界 | 执行 WP | 无语义变化约束 |
|---|---|---|---|
| `desktop/src-tauri/src/shared_instance.rs` 的 named mutex、guard Drop、冲突/fatal 分类 | Windows `InstanceLockBackend` 实现（后续 WP 新建） | WP-1P-03 | mutex 名仍映射 `sakura.desktop.shared-user-data.v1`；锁仍先于 Tauri/Core/data；same-name 非 mutex 仍 fatal |
| `main.rs` 的 `SharedInstanceGuard::acquire` 分支 | composition root 注入 `InstanceLockBackend` | WP-1P-03 | `AlreadyRunning`/fatal 的启动结果和退出码不改变；不借迁移接入 Core |
| `managed_process_tree.rs` 的 spec/wait/pipes 公共 DTO | `desktop/src-tauri/src/platform/contracts.rs` 的进程契约适配层 | WP-1P-04 | Supervisor 使用的 spawn/wait/terminate/verify/release 顺序不变 |
| `managed_process_tree.rs` 的 Win32 handle、pipe、suspended spawn、Job Object、rollback、kill-on-close | Windows `ManagedProcessTreeBackend` 实现（后续 WP 新建） | WP-1P-04 | assignment/resume 失败仍先回收；根退出后仍验证整个 Job；Drop 只是最终保险 |
| `core_host_runtime.rs`、`fake_core_runtime.rs`、`phase_1b_runtime_acceptance.rs` 对 `ManagedProcessTree` 的直接调用 | 注入 `ManagedProcessTreeBackend` | WP-1P-04 | 不修改 framing、deadline、generation、Snapshot 或 restart budget |
| `window_interaction.rs` 的 logical/physical hit model | 留在共享纯模型层 | WP-1P-05 | 四状态命中、透明区和 scale rounding oracle 不按平台 fork |
| `window_interaction.rs` 的 `SetWindowRgn`、`SendMessageW` drag、full-region restore | Windows `WindowInteractionBackend` 实现（后续 WP 新建） | WP-1P-05 | 失败继续恢复全窗口交互；不静默关闭拖动/IME |
| `main.rs` 的 `SetWindowPos`、显示/隐藏、focus、native startup message | Windows window/diagnostics backend 与公共 Shell 调用 | WP-1P-05 | 固定立绘锚点、原子 bounds、焦点和失败提示语义不改变 |
| `desktop/tests/windows_*acceptance.ps1` | 保留 Windows backend 历史证据，新增同契约三平台入口 | WP-1P-03 至 06 | 旧 accepted 证据不撤销，也不冒充 macOS/Linux 证据 |
| `.github/workflows/` Runtime v2 job | 三平台持续门禁 | WP-1P-06 | 不修改 legacy 发布 job 的产品语义；Runtime v2 required checks 独立命名 |
| `.github/workflows/package.yml`、`release.yml` 的 Python 下载逻辑 | 精确 source manifest 的输入证据 | WP-1P-02；正式发布接线仍属 WP-7-04 | 禁止字符串模糊匹配 asset、PATH 回退和在线运行时修复 |

`core_supervisor.rs`、`core_host_protocol.rs`、Python Core Host、共享数据 schema 和产品功能代码不在这份迁移清单中，因为平台化不需要改动它们。如果后续 backend 迁移要求修改这些文件，必须先停止当前 WP、解释不可避免性并更新 ADR/Work Package，而不是顺手扩大范围。

## 9. WP-1P-01 可验证退出条件

- 三个 target 在 `rust-toolchain.toml`、Rust `PlatformTarget::ALL` 和本文中一一对应。
- 五个 backend trait 均 object-safe；compile-only mock 同时实现五个契约。
- 稳定 service/category/retry 可序列化，native code 不参与稳定码。
- 共享锁 identity、development 显式选择和 target 映射有可执行契约测试。
- 本文覆盖最低环境、Python source、调用方向、错误表、CI/实机责任和逐文件迁移清单。
- 当前 Windows 实现与产品调用路径没有被搬迁或修改；Supervisor、IPC、Snapshot 和 Assistant 语义零变化。

独立回退是整体 revert WP-1P-01 提交：删除 `desktop/src-tauri/src/platform/` compile-only 模块和本规范，恢复单 target toolchain 声明与 WP-1C-02 accepted 状态；不得回退 WP-1C-02 或删除任何用户 Runtime/data。
