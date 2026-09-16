---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-05
---

# ADR-0025：macOS 桌宠静止态动态包络与过渡稳定包络边界

## Context

Runtime v2 的桌宠窗口使用当前可见内容的动态外接矩形。macOS 为避免 WKWebView 在立绘缩放和表情
切换期间出现裁剪或闪帧，需要在短暂的视觉事务中保持原生窗口包络稳定；这不等于 macOS 可以永久
保留 150% 立绘和所有控件极值的隐藏窗口空间。

提交 `4c79a94` 将 macOS 的 `resident_stable_bounds` 改为常驻，修复了立绘切换时序问题，却重新引入
了顶部拖动边界和静止态透明余量回归。全透明测试立绘会让这块不可见空间尤其明显。

## Decision

> Supersedes the macOS resident-envelope exception introduced in ADR-0010's implementation boundary;
> the rest of ADR-0010 remains in force.

- macOS 的普通布局、拖动和静止态使用当前立绘 alpha、当前可见气泡、输入框和控件的真实动态并集；
  `resident_stable_bounds` 保持关闭。
- macOS 在显式立绘缩放手势期间使用“当前控件 + 当前立绘 150% alpha 包络”的临时稳定包络。
  手势刻度只更新合成变换和当前倍率的精确光标路由，不逐刻度调整原生窗口或 WebView offset；
  手势结束、取消或失焦后一次性收紧到最终倍率的真实包络。
- macOS 调整气泡宽度、高度、上下位置和输入栏偏移时，第一次真实数值变化才预留当前可见表面与
  控件合法轨迹的临时并集；按下但未改变数值时不扩窗。临时并集按工作区裁剪，保留当前可见内容、
  物理立绘锚点和 `content_scale`。连续刻度和 ResizeObserver 刷新复用这个原生 frame，只更新 DOM、
  输入栏材质和精确光标路由；结束后恢复最终动态包络。该布局包络不增加 150% 立绘余量，也不常驻。
- macOS 的 WKWebView 保持完整规范画布和底部工具菜单预留，共 900×1490 逻辑像素，再乘当前
  `content_scale`。外层 NSWindow 仍使用动态包络；在一次 AppKit 调用中调整外窗并移动 WebView，
  由外窗裁出当前可见部分。DOM stage 保持规范原点，指针和菜单坐标按同一裁剪范围换算。
  这块固定渲染画布不参与原生窗口的顶部拖动边界，不等于常驻大 NSWindow。
- 必须同时关闭 Tauri 子视图按比例 resize、AppKit autoresizing 和 WebKit 自动 content inset。
  前两者会改写 WKWebView 尺寸；后者会把被裁掉的顶部误当作内容 inset，改变 `innerHeight` 并使页面
  再次偏移。当前通过有 selector 检查的 `_setAutomaticallyAdjustsContentInsets:` 关闭 WebKit
  自动 inset；这是私有 SPI，缺失时报 `MACOS_SURFACE_FIXED_INSETS_UNAVAILABLE`，不得继续提交错误
  裁剪。macOS 26 的公开 inset API 尚不能代替对旧系统的兼容验证。
- 不再使用快照遮挡或切换主窗口透明度。独立覆盖窗已被实机反馈判定不足：仍闪且出现轻微缩放。
  连续手势串行提交原生 frame 与对应前端状态，丢弃过期排队任务；已生效的 native frame 仍须同步到
  前端，下一事务才能开始。2026-09-05 固定画布方案收到用户“这一版可以了”的实机确认；
  不同系统版本、混合 DPI 与历史立绘切换回归仍按各自验收项记录。
- macOS 立绘交叉淡入期间使用旧立绘与新立绘 150% alpha 包络的并集，并保留到新立绘完成绘制；
  绘制完成后再提交新立绘的最终动态包络和精确命中区域。过渡不使用完整 900×996 画布，也不使用
  Windows 的全部控件布局极值。
- 全透明立绘是合法资源。它对视觉包络和立绘拖动区域的贡献为零，但气泡、输入框和控件仍参与包络
  和交互；几何计算不得因没有可见 alpha 像素而失败。
- 右键产品菜单是当前 WebView 内的临时可见表面。菜单完成测量后，原生窗口包络扩展为当前动态包络
  与菜单矩形的并集，精确命中区域同步扩展；菜单关闭、动作执行或表面事务中断时，必须恢复打开前的
  `active_bounds`、窗口 placement 和命中区域。扩展只能改变窗口尺寸，必须保留打开前的左上角、物理
  立绘锚点和 `content_scale`；异步原生拖动完成后，缓存 placement 也必须先随原生移动事件同步。不得
  通过恢复完整 900×996 窗口或整窗命中来绕过裁剪。原生扩展开始前，若 WebView 内的输入控件仍有焦点，
  必须先主动清理该焦点；菜单尺寸事务不得与输入控件的 focus/blur 生命周期并发。
- Windows 的常驻稳定 HWND 包络和 Linux 的现有缩放生命周期保持不变。本 ADR 补充并修正
  [ADR-0010](0010-cross-platform-pet-surface.md) 的 macOS 边界，不改写其跨平台命中模型。

## Consequences

macOS 静止态可以让可见内容贴近工作区顶部，透明测试立绘不会制造隐藏拖动边界；缩放和表情切换仍有
明确、有限的稳定包络事务，因此不会把修复时序问题重新转化为常驻窗口尺寸回归。代价是 macOS
表情过渡结束时必须执行一次最终动态包络提交，且真实 AppKit/WKWebView 时序仍需要实机验收。
右键菜单打开期间会有一次可逆的 native surface 扩展；该扩展只覆盖已测量菜单，不改变 canonical
坐标系，关闭后回到原始动态包络。

Rust 的 `window_surface_regression` 测试和 `runtime-v2-window-surface` Harness profile 负责锁定
平台能力、全透明 alpha、过渡并集、普通可见 alpha 以及右键菜单临时扩展/恢复的几何不变量；macOS
拖动和视觉抖动由同一提交上的人工证据补充。
macOS 光标路由直接检查当前光标所覆盖的 alpha 样本，保留保守采样与透明洞语义；不为每个刻度生成
全立绘的矩形集合。异步路由只排队一个 AppKit 回调，并在执行时读取最新命中模型。

## Rollback

回退本决策时必须同时回退 macOS 的常驻包络选择和对应测试、Spec；不得通过恢复完整透明窗口来绕过
WKWebView 时序问题，也不得修改角色资源或用户布局数据。
