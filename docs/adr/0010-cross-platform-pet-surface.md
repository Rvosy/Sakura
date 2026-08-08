---
kind: adr
status: proposed
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-08
---

# ADR-0010：跨平台桌宠动态表面与精确命中

## 背景

Runtime v2 把 900×996 规范舞台直接作为原生透明窗口。Windows 另用 `SetWindowRgn` 应用 PNG alpha
区域，macOS/Linux 则忽略共享命中模型并保持整窗可点。这同时造成大块透明空间、macOS 无法贴顶、
非 Windows 透明像素拦截鼠标，以及布局预览期间 Windows 临时恢复整窗命中。

旧 PySide6/PR #82 证明了内容包络和立绘底部中心锚点的可行性，但其矩形 `QRegion` 不能表达 PNG
内部透明洞，不能替代逐像素命中。

## 决策

- 保留 900×996 为规范布局坐标系；原生窗口只承载当前可见内容的动态外接矩形。
- Rust 持有唯一 `LogicalSurfaceSnapshot`，同时派生原生 bounds、精确命中区域和前端 active offset。
- alpha mask 由可信角色资源层按 portrait key 读取；WebView 只提交布局和可见性。
- Windows 保留 `SetWindowRgn`；Linux 使用 GTK/GDK `cairo::Region` input shape；macOS 使用
  `NSWindow.ignoresMouseEvents` 和当前光标位置路由。
- Linux 在未显式指定 `GDK_BACKEND` 且存在 `DISPLAY` 时优先 X11/XWayland，以获得完整全局定位；
  native Wayland 保留精确 input region，但明确标记全局锚点降级。
- 只有立绘有效 alpha 像素可拖动；气泡、输入框、菜单及其他控件永不作为拖动区。
- bounds、命中与 DOM 布局按同一 revision 提交；失败保留上一版有效快照，不恢复整窗命中。

## 后果

平台 backend 必须承担不同原生机制，但共享同一逻辑模型、阈值和验收语义。macOS 光标路由需要可见期
事件监听和定时采样；native Wayland 因协议不提供 surface 全局坐标，无法声称与 X11 相同的绝对定位。
Windows 继续使用窗口 region 同时裁剪可见和输入区域，复杂 alpha 不得静默退化成外接矩形。

## 回退

整体回退 schema v3、surface transaction 和三平台 backend，恢复固定窗口候选；回退不修改用户配置、
角色包或数据格式。Work Package 状态以 Runtime v2 总计划为唯一来源。
