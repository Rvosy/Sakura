---
kind: adr
status: proposed
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-09
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
- 同一立绘的缩放预览使用该立绘当前 alpha 在允许最大倍率 150% 下的动态包络。Linux 的实时 hit
  region 与真实包络继续逐次更新。Windows 的 `SetWindowRgn` 同时承担可见裁剪，逐刻度重建会阻塞
  WebView 视觉帧并用旧 alpha 裁掉新 transform，而手势两端 resize/reposition HWND 又会和 WebView
  offset 形成不可原子显示的中间帧。因此 Windows 在取得 alpha mask 后让底层 HWND/WebView 常驻最大
  稳定 bounds；静止态仍用精确 region 表达真实轮廓和点击穿透。缩放开始只清除一次复杂 region，数值
  刻度经专用轻量事件直接更新 WebView 合成 transform，不进入完整外观预览、bounds、surface offset
  或 alpha 模型。显式手势结束后，最新 revision 只提交一次当前倍率精确 region，不再改变窗口 placement；
  从放宽状态恢复时跳过旧新 region 桥接，避免连续两次 GDI 裁剪。放宽 region 不能成为常驻状态，也
  不能用相邻刻度的时间间隔推断手势已经结束。轻量帧采用 latest-wins 和内部有界追赶，单帧失败不作为
  设置连接故障；最终完整外观 publication 仍是可靠状态提交。
- macOS 只在缩放手势活跃期间临时使用“当前控件布局与 150% 立绘”的稳定包络；静止态必须收紧到
  当前倍率立绘 alpha 与当前控件的实际并集，不允许常驻最大倍率顶部余量。手势刻度只更新 WebView
  合成 transform 和同一稳定 envelope 内的精确光标路由，不调用原生 bounds 或 WebView offset；透明
  余量及 alpha 洞在手势中也必须穿透。松手、取消或失焦后，由最新 revision 一次提交最终倍率真实
  包络与精确命中。macOS 不扩展到 Windows 的全部合法控件布局极值；该策略不改变 Windows 或 Linux
  的平台路径。
- 对话框外框不再参与内容自适应，兼容字段 `bubbleMaxHeight` 解释为固定高度；内容增长只驱动内部滚动。
  Windows 稳定 HWND/WebView 包络扩大为 50%–150% 立绘与全部合法控制面板布局极值的并集。四个布局
  滑块与立绘倍率一样采用两端事务：刻度用 RAF/latest-wins 轻量事件直接绘制，结束时只做一次原生提交
  和精确 region 恢复。Windows 视觉帧不等待 region 放宽完成；立绘图层在首次交互前预先提升为
  transform 合成层。macOS 不复用 Windows 的全部布局极值包络，Linux 也没有稳定包络；两者收到布局
  轻量事件时仍逐帧提交对应命中模型，真实控件布局超出当前包络时仍更新原生表面。
- `content_scale` 按完整 900×996 规范视口和工作区计算，不得随立绘 alpha 外接矩形改变；动态包络
  只改变裁剪范围，不能借由重新缩放使气泡和输入框移动。
- alpha mask 由可信角色资源层按 portrait key 读取；WebView 只提交布局和可见性。
- Windows 保留 `SetWindowRgn`；Linux 使用 GTK/GDK `cairo::Region` input shape；macOS 使用
  `NSWindow.ignoresMouseEvents` 和当前光标位置路由。
- Linux 在未显式指定 `GDK_BACKEND` 且存在 `DISPLAY` 时优先 X11/XWayland，以获得完整全局定位；
  native Wayland 保留精确 input region，但明确标记全局锚点降级。
- 立绘有效 alpha 像素与气泡的非交互空白可拖动。气泡中的实际回复文字、滚动条、输入框、菜单及
  其他控件保持交互优先；WebView 在调用拖动命令前按 DOM 目标拦截这些交互点，Rust 再按同 revision
  的逻辑命中模型复核立绘或可见气泡起点。
- bounds、命中与 DOM 布局按同一 revision 提交；失败保留上一版有效快照。除 Windows 缩放预览
  期间的短暂放宽外，不得恢复整窗命中。过期立绘 revision 返回空结果，不得把旧 `active_bounds`
  重新提交给前端。
- 物理命中 snapshot 必须携带由同一 `active_bounds` 和 scale 推导的目标 envelope；平台不得在异步
  resize 后立即读取可能仍是旧值的原生窗口尺寸来裁剪新命中区域。

## 后果

平台 backend 必须承担不同原生机制，但共享同一逻辑模型、阈值和验收语义。macOS 光标路由需要可见期
事件监听和定时采样；native Wayland 因协议不提供 surface 全局坐标，无法声称与 X11 相同的绝对定位。
Windows 静止态继续使用窗口 region 同时裁剪可见和输入区域，复杂 alpha 不得静默退化成外接矩形；
稳定 HWND 包络会包含不可见透明余量，但精确 region 让该余量既不绘制也不接收点击。缩放手势期的
临时放宽是有明确开始、结束和最新 revision 恢复的视觉性能事务，不是降级兜底。
Tauri 2.11.3/WRY 0.55.1 在 macOS 根 `WebviewWindow` 上会忽略独立 WebView bounds，远程 WebKit
图层也不服从父 `NSView` 的几何平移；而 WebView eval 与窗口 placement 排队也不能证明没有可见
中间帧。因此本决策不把根 WebView 伪装成可负偏移子视口，也不把消息顺序作为缩放稳定性的保证：
Windows 与 macOS 缩放期间都直接消除逐刻度窗口几何更新；Windows 继续常驻其稳定 HWND 包络，macOS
只在手势开始扩到 150%，手势结束收紧到最终倍率真实并集。macOS 每个刻度仍替换稳定 envelope 内的
精确光标路由，不把透明余量变成整窗命中；Linux 继续以同一物理锚点逐刻度收口。真实控件布局变更或
手势首尾事务失败时仍恢复上一版舞台 offset、窗口与命中区域。

## 回退

整体回退 schema v3、surface transaction 和三平台 backend，恢复固定窗口候选；回退不修改用户配置、
角色包或数据格式。Work Package 状态以 Runtime v2 总计划为唯一来源。
