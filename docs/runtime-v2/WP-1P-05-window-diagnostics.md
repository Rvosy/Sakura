# WP-1P-05：三平台窗口交互与原生诊断 backends

> 状态：Active
> 日期：2026-07-24
> 前置：WP-1P-04 accepted，提交 `a3dc6c0e`
> 规范来源：ADR-0004、`WP-1P-01-platform-contract.md`

## 1. 结果边界

本 Work Package 将共享布局/命中/scale 模型接入 `WindowInteractionBackend`，并建立
`NativeDiagnosticsBackend` 的三平台实现。平台层只执行原生窗口操作和稳定错误转换；共享
状态、revision、固定立绘锚点、命中区域以及失败后的恢复模型继续由公共层拥有。

允许目录：`desktop/src-tauri/src/platform/`、`desktop/src-tauri/src/main.rs`、既有
`window_interaction.rs` 的平台调用适配、相关 Rust 单元/合同测试、platform foundation
workflow 和本文/ADR-0004/Work Package 总计划。不得修改真实 `data/`、`runtime/`、角色、
插件、`.superpowers/`、Core/Supervisor/IPC/Snapshot 或产品业务语义。

## 2. 平台责任

- Windows：保留现有 Win32 region、drag loop、bounds、显示/隐藏和 focus 语义。
- macOS：使用 Tauri/NSWindow WebView 原生窗口操作，明确透明/命中路由、bounds、显示/隐藏、
  focus、Retina scale 和错误恢复；真实 Spaces/IME/多屏体验登记为 device deferred。
- Linux：区分 X11 与 Wayland display server；共享命中模型不分叉。Xvfb 可验证窗口创建、
  bounds、显示/隐藏和事件路由；真实 compositor 交互登记为 device deferred。
- Diagnostics：返回 target、CPU 架构、window backend、display server、WebView 版本、
  scale/display facts 和脱敏稳定信息；不得返回 credential、聊天内容、裸路径或环境变量。

## 3. 故障矩阵与恢复

原生 bounds、命中区域、drag、显示/隐藏或 focus 失败时，必须返回
`platform.window_interaction.*` 稳定错误；命中应用失败必须尝试恢复全窗口可交互，并在
恢复失败时同时返回恢复错误。display server 无法识别时 diagnostics 使用明确的
`unknown`，不得把 X11 identity 当作 Wayland identity。重复 revision、共享布局和命中纯模型
测试必须保持三平台一致。

## 4. 退出条件

- Windows、macOS arm64、Linux x64 backend 原生编译通过；Windows 现有窗口测试无回归。
- 共享布局、命中、scale、锚点、revision 和恢复模型测试通过；非 Windows 不再以永久
  `Unsupported` 作为正式窗口实现。
- macOS backend 合同/单元测试、Linux Xvfb 可执行窗口操作测试、Wayland cfg/diagnostics
  与共享模型测试通过。
- 同一最新 HEAD 的 Unit/UI 与 platform foundation CI 全绿，P0/P1 为 0。
- accepted 记录明确写出 macOS/X11/Wayland 的真实设备验证 deferred 项，不把 CI/Xvfb
  描述成真实设备体验。

## 5. 独立回退

整体回退本 WP 的 activation、backend、测试和 accepted 记录即可恢复 WP-1P-04 accepted
状态；不得回退 WP-1P-01/02/03/04，不删除普通 POSIX lock、真实 Runtime/data 或用户资源。
