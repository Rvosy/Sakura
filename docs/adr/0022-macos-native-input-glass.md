---
kind: adr
status: proposed
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-15
---

# ADR-0022：macOS 输入栏使用公开 AppKit 原生玻璃

## 背景

Windows 输入栏已经具有实时高斯和实验液态路径，但其 DWM/WGC 技术与安全问题不应移植到 macOS。
AppKit 从 macOS 10.10 提供 `NSVisualEffectView`，并从 macOS 26 提供公开 `NSGlassEffectView`。

## 决策

- macOS 高斯使用语义化 `NSVisualEffectView`，保留 `HUDWindow` 的桌面采样但以 22% view alpha 合成，
  让前端的角色主题轻量 tint 成为主视觉，避免原生材质和 8px WebView 软化叠加成厚重的第二块面板；液态只在
  运行时确认 `NSGlassEffectView` 存在后启用。
- 高斯视图作为透明 WKWebView 下方的输入栏局部 sibling；液态视图放入不附加 CALayer 硬裁剪的普通
  AppKit 容器，并作为 WKWebView 内部、WebKit 内容视图下方的局部 subview；玻璃自身使用 `Clear`
  style 管理圆角、折射和边缘高光，并通过约 12% alpha 的主题主色 AppKit tint 让玻璃本体着色；WebView
  保持透明，不用前端蒙版替代原生材质。原生玻璃位于 WebKit 内容层下方，无法采样层上方的立绘，因此
  输入栏另用透明 2px `backdrop-filter` 配合轻度饱和度与对比度提升来软化 WebKit 内部像素；它不承担
  桌面采样或可见底色，也不模拟液态折射。由于
  macOS 26 的玻璃会随输入焦点切换基础明度，液态视图
  自身固定公开的 `DarkAqua` appearance 来降低白色提升；其他界面仍跟随系统外观。macOS 26 没有
  `state`、`emphasized` 或 `effectIsInteractive` 公开控制，后者从 macOS 27 才加入，因此公开 API 不能
  保证 focus 前后像素一致。
  macOS 26 实机证明把
  `NSGlassEffectView` 直接作为 host sibling 会出现整窗 backdrop 晋升或仅剩平面 tint，不能作为
  可接受实现。
- macOS 13–15 不实现应用侧捕获或 shader，也不把液态偏好显示成高斯；设置锁定液态，运行时回到纯色
  并保留偏好。
- 版本门以类可用性为最终依据，使 deployment target 继续保持 13.0，并允许同一二进制运行在新旧系统。

## 后果

实现不需要截图权限、额外窗口或自管 GPU 帧管线，系统自动适配桌面、外观和辅助功能。输入栏先按宿主
AppKit 底部原点计算，再由 `convertRect:fromView:` 换算到实际 WKWebView 坐标系；右键菜单临时扩展或恢复
原生窗口包络时，也必须用新 `LayoutApplication` 立即重算输入栏 frame，避免原生材质停留在旧的局部坐标。
代价是高斯模糊核仍由系统语义材质决定，应用只控制最终合成占比；液态只在 macOS 26 及以上可用。这两个
限制作为公开平台能力展示，而不是隐式降级。
