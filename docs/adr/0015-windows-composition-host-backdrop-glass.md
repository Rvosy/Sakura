---
kind: adr
status: proposed
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# ADR-0015：Windows Composition Host Backdrop 实时玻璃

## 背景

Runtime v2 的透明 Tauri/WebView2 桌宠需要在气泡和输入框等自由形状区域显示窗口背后的实时内容。
单次或连续桌面截图会冻结、捕获自身或要求窗口显隐；窗口捕获排除又会破坏第三方截图与录屏。
Windows 系统 Acrylic 可作降级，但不能证明未来自由 mask 和自定义效果图的能力。

## 候选方案

1. CSS `backdrop-filter`：只能处理 WebView 已有的后方像素，不能自行取得桌面合成输入。
2. 桌面截图后传入 WebView：存在递归、显隐、CPU/IPC 和实时性问题。
3. 顶级窗口系统 Acrylic：实时且便宜，但效果、区域和扩展能力受系统控制。
4. Windows Composition `HostBackdropBrush`：从系统合成阶段取得宿主后方内容，再由独立视觉层承载。

## 决策

在 Windows 上以 `Windows.UI.Composition` 的 host backdrop 作为自定义实时玻璃的首选架构方向，
并先用 WP-3-03B 验证它与当前 Tauri 2/WebView2 透明窗口的层级兼容性。

PoC 必须满足：

- 原生视觉层属于 Desktop/Platform Presentation，不进入 Python Core；
- 不截图、不隐藏窗口、不设置 `WDA_EXCLUDEFROMCAPTURE`；
- WebView 的透明区域露出原生 visual，非透明内容与输入继续由 WebView 承担；
- 初始化失败只记录诊断并降级，不阻止 Shell 显示、交互或退出；
- 默认产品路径保持关闭，只有显式 PoC 开关启用。

实机结果确认当前顶级 Tauri/Wry HWND 能把 Composition visual 稳定放在透明 WebView 内容下方，
无需 Composition Controller 或辅助 HWND。系统 Acrylic 仍可在后续 ADR 中作为独立降级决定；不得
改回连续截图。

## 后果

- 正面：背景由 GPU/系统合成实时更新，没有应用侧帧抓取和图像 IPC。
- 正面：后续可在同一 visual graph 上评估 tint、saturation、mask、noise 与 distortion。
- 代价：Windows 原生 API、COM apartment、窗口层级和对象生命周期需要专门维护。
- 风险：全窗口 visual 会透过角色透明像素显出轮廓，且重复提交未变化的 HWND/region 会在拖动松手时
  触发 DWM 重组闪影；实现必须只裁剪到气泡和输入框，并跳过同一局部几何的冗余提交。
- 实机结论：CSS backdrop 只能采样 WebView 内部像素；设置必需 DWM 属性后，纯 Win32 Gate 与当前
  Tauri/Wry 顶级 HWND 均能以 `HostBackdropBrush -> Border clamp -> GaussianBlur` 采样并模糊桌面。
  原生 region 必须在角色 alpha mask 改变 `activeBounds` 后重新按最终 surface origin 定位，否则会在
  左侧形成可见漏带。无需辅助 HWND 或 Composition Controller。
- 边界：本决策不承诺跨平台同构实现，也不把 PoC 视为发布功能。
