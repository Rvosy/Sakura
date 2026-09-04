---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-05
---

# WP-1P-05：三平台窗口交互与原生诊断 backends

> 工作包进度见 `docs/plans/runtime-v2/work-packages.md`，不作为开发许可。
> 日期：2026-07-24
> 前置：WP-1P-04 accepted，提交 `a3dc6c0e`
> 规范来源：ADR-0004、`WP-1P-01-platform-contract.md`

## 1. 结果边界

本 Work Package 将共享布局/命中/scale 模型接入 `WindowInteractionBackend`，并建立
`NativeDiagnosticsBackend` 的三平台实现。平台层只执行原生窗口操作和稳定错误转换；共享
状态、revision、固定立绘锚点、命中区域以及失败后的恢复模型继续由公共层拥有。

窗口和诊断测试使用隔离数据。修改可以涉及真实调用链上的公共层与平台实现；仍需保持窗口所有权、
共享布局模型、错误转换和数据保护契约。

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

## 6. Accepted 记录（2026-07-24）

实现提交：`3e23285`（`feat(runtime): 实现三平台窗口交互与诊断后端`）。

最新 Draft PR #147 HEAD：`3e23285f90c40cd45d6817918d9a4fdf8aebb127`。

最新三平台 platform foundation 证据：

- push run `30066486490`：Windows x64、macOS arm64、Linux x64 全部通过；
- pull_request run `30066488599`：Windows x64、macOS arm64、Linux x64 全部通过，包含 Linux Xvfb window backend 合同测试；
- Unit/UI run `30066488685`：Unit 与 UI 全部通过。

证据覆盖正式 backend 编译、Windows 原生窗口回归、macOS/NSWindow WebView 接口编译、Linux
X11/Wayland diagnostics 分类、共享布局/命中/scale/revision 恢复模型、Xvfb 有界合同测试和
脱敏 diagnostics DTO。CI/Xvfb 结果只证明 platform foundation，不等价于真实 macOS、X11
或 Wayland 设备体验。

device validation deferred：macOS 透明命中、拖动、IME、Retina、Spaces、多屏；Linux X11
透明命中、拖动、IME、多屏；Linux Wayland compositor、窗口身份、透明命中、拖动、IME。
这些项目保留在 WP-7-02/WP-7-02-HW 发布前硬门禁中。

审查确认没有修改 `data/`、`runtime/`、角色、插件、`.superpowers/` 或 Core/Supervisor/IPC
语义，P0/P1 为 0。独立回退为 revert `3e23285`、`962eab4`，保留 WP-1P-04 accepted。
