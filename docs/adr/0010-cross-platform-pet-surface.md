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
- Rust 持有唯一 `LogicalSurfaceSnapshot`，同时派生原生 bounds、精确命中区域和规范舞台的
  `active_bounds` 偏移。根 WebView 继续随顶层窗口 resize；Rust 在提交 bounds 前先把舞台偏移和
  surface revision 排入同一 WebView/窗口主循环，前端收到匹配回包后只做幂等确认和立绘缩放。
- 同一立绘的缩放预览使用该立绘当前 alpha 在允许最大倍率 150% 下的动态包络。50%–150% 的实时
  hit region 继续逐次更新，但顶层窗口、根 WebView 和舞台偏移保持不变；事务发现几何完全相同时
  必须跳过原生 bounds、WebView offset 与桥接区域提交，只替换精确命中区域。设置窗口以 pointer/
  keyboard 手势明确标记连续交互；松手、取消或失焦后，最新 revision 才以当前倍率真实 alpha 包络
  执行一次收口。稳定包络不能成为常驻窗口范围，也不能用相邻刻度的时间间隔推断手势已经结束。
- `content_scale` 按完整 900×996 规范视口和工作区计算，不得随立绘 alpha 外接矩形改变；动态包络
  只改变裁剪范围，不能借由重新缩放使气泡和输入框移动。
- alpha mask 由可信角色资源层按 portrait key 读取；WebView 只提交布局和可见性。
- Windows 保留 `SetWindowRgn`；Linux 使用 GTK/GDK `cairo::Region` input shape；macOS 使用
  `NSWindow.ignoresMouseEvents` 和当前光标位置路由。
- Linux 在未显式指定 `GDK_BACKEND` 且存在 `DISPLAY` 时优先 X11/XWayland，以获得完整全局定位；
  native Wayland 保留精确 input region，但明确标记全局锚点降级。
- 只有立绘有效 alpha 像素可拖动；气泡、输入框、菜单及其他控件永不作为拖动区。
- bounds、命中与 DOM 布局按同一 revision 提交；失败保留上一版有效快照，不恢复整窗命中。
  过期立绘 revision 返回空结果，不得把旧 `active_bounds` 重新提交给前端。
- 物理命中 snapshot 必须携带由同一 `active_bounds` 和 scale 推导的目标 envelope；平台不得在异步
  resize 后立即读取可能仍是旧值的原生窗口尺寸来裁剪新命中区域。

## 后果

平台 backend 必须承担不同原生机制，但共享同一逻辑模型、阈值和验收语义。macOS 光标路由需要可见期
事件监听和定时采样；native Wayland 因协议不提供 surface 全局坐标，无法声称与 X11 相同的绝对定位。
Windows 继续使用窗口 region 同时裁剪可见和输入区域，复杂 alpha 不得静默退化成外接矩形。
Tauri 2.11.3/WRY 0.55.1 在 macOS 根 `WebviewWindow` 上会忽略独立 WebView bounds，远程 WebKit
图层也不服从父 `NSView` 的几何平移；而 WebView eval 与窗口 placement 排队也不能证明没有可见
中间帧。因此本决策不把根 WebView 伪装成可负偏移子视口，也不把消息顺序作为缩放稳定性的保证：
缩放期间直接消除逐刻度窗口几何更新，停止交互后再以同一物理锚点收口到当前真实包络。真实几何
变更失败时仍恢复上一版舞台 offset、窗口与命中区域。

## 回退

整体回退 schema v3、surface transaction 和三平台 backend，恢复固定窗口候选；回退不修改用户配置、
角色包或数据格式。Work Package 状态以 Runtime v2 总计划为唯一来源。
