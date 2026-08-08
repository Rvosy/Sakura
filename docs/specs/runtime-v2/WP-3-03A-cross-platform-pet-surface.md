---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-08
---

# WP-3-03A：跨平台桌宠动态表面与精确命中规范

## 必须行为

- 900×996 只作为规范坐标系；原生窗口必须取立绘可见 alpha、可见气泡、输入框、菜单和其他可见
  控件的动态视觉外接矩形。全局安全边距为 2 逻辑像素，外部 focus outline 额外预留 4 像素。
- 隐藏组件不得占用视觉包络或输入区域。alpha 值大于零的立绘像素参与命中，透明洞和外围必须穿透。
- 立绘底部中心的物理屏幕坐标在表情、缩放、气泡高度、输入高度、菜单和 DPI 更新中保持不变。
- 只有立绘有效 alpha 像素可启动拖动；控件和气泡不得启动拖动。Rust 必须按当前 revision 和起点复核。
- 一次 revision 必须包含布局、可见性、alpha 和平台应用结果；失败保留上一版有效快照。

## 平台契约

- Windows 使用精确 Win32 window region，不得因预览或菜单恢复整窗命中，也不得把复杂 alpha 退成 bbox。
- macOS 根据当前逻辑命中模型切换 `NSWindow.ignoresMouseEvents`，透明点必须交给下层窗口。
- Linux X11/XWayland 使用 GTK/GDK input shape 并满足完整契约。native Wayland 同样应用 input region，
  但因无全局 surface 坐标，只声明 surface-local 锚点并发布 `wayland_degraded_anchor` 诊断。

## 验收

- 外围和内部透明洞点击到达背景窗口；有效 alpha、输入框和菜单由桌宠接收。
- 有效 alpha 可拖动，其他区域不能拖动；可见立绘顶部可距工作区顶部不超过 2 逻辑像素。
- 各状态循环 20 次，支持混合 DPI、负坐标多屏且完整平台路径的物理锚点漂移为零。
- Windows 现有真实透明穿透门保持通过；macOS、X11/XWayland 分别提供实机证据，native Wayland 单列。

## 非目标

不修改角色资源、用户布局配置、聊天协议、设置业务或 Legacy Qt 实现。
