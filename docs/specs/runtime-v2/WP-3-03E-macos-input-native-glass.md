---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-15
---

# WP-3-03E macOS 输入栏原生玻璃规范

## 产品范围

Runtime v2 在既有 `solid | gaussian_blur | liquid_glass` 偏好上增加 macOS 原生有效模式。效果只覆盖
输入栏，不覆盖气泡、设置窗口、角色立绘或整个透明窗口；Appearance publication 保持 schema v3。

## 平台与模式

- macOS 13.0 及以上使用 `NSVisualEffectView` 提供 `gaussian_blur`，material 为 `HUDWindow`、blending 为
  `BehindWindow`、state 为 `Active`。强度由系统语义材质决定，不声明与 Windows sigma 完全一致。
- 只有运行时存在公开 `NSGlassEffectView` 类时才允许 `liquid_glass`；当前对应 macOS 26.0 及以上。
  使用适合立绘和动态桌面的 `Clear` style、输入栏 28 logical px 圆角，以及约 12% alpha 的角色主题
  主色 AppKit tint，让系统折射本身带有主题色。液态视图单独固定 `DarkAqua` appearance，以降低
  macOS 26 在输入取得焦点时对材质的白色提升；不改变应用或 WKWebView 的整体外观。WebView 不叠加
  可见背景、阴影或主题 focus 描边，但在输入栏范围使用透明的 2px `backdrop-filter`，配合轻度饱和度
  和对比度提升，只软化位于原生玻璃上方的 WebKit 立绘像素，不把立绘抹成高斯毛玻璃。macOS 26 没有
  公开的玻璃交互态开关，因此不能承诺 focus 前后材质完全一致；`effectIsInteractive` 从 macOS 27 才提供。
- macOS 26 以下在设置中禁用液态选项并显示“需要 macOS 26 或更高版本”。若跨平台配置已经保存液态
  偏好，保持原值但本机有效模式为纯色，状态返回 `LIQUID_GLASS_REQUIRES_MACOS_26`，不得启用高斯替代。
- Linux 继续只提供纯色；Windows 继续使用既有原生后端，WP-3-03D 保持 planned。

## 原生视图与生命周期

- AppKit 操作只在主线程执行。高斯视图是 WKWebView 同一 host 的下层 sibling；液态视图位于局部普通
  `NSView` 内、WKWebView 的 WebKit 内容视图下方。容器不得附加硬裁剪 CALayer，圆角由
  `NSGlassEffectView` 自己管理。由于单个 WKWebView 内容层不能让原生 subview 插入 DOM 元素之间，
  桌面由 AppKit 玻璃采样、立绘由输入栏透明轻量 `backdrop-filter` 采样。两种原生视图互斥显示，纯色隐藏两者。
- 同一个 `input_rect` 先生成 host AppKit 底部原点 frame，再通过 AppKit
  `convertRect:fromView:` 换算到实际 WKWebView 父子坐标系，不能假设 WebKit 内部视图是否 flipped；
  `content_scale` 作用于 AppKit point，Retina `backingScaleFactor` 由系统负责，不重复放大。
- 布局、主题和模式切换原位更新。退出时移除原生视图；初始化、主题或布局更新失败必须隐藏全部原生层、
  返回稳定错误码并回退纯色，不改变用户偏好，也不得让视觉失败阻断聊天、拖动、输入、IME 或退出。
- 禁止截图循环、辅助窗口、私有 AppKit 类、Metal/Core Image 自绘液态后端或 macOS 13–15 效果冒充。

## 设置与状态接口

- Settings capability v2 新增 `appearance.input_visual_effect.gaussian_blur` 与
  `appearance.input_visual_effect.liquid_glass`，不升级 capability schema。
- Tauri 内部命令统一为 `input_visual_effect_status` 和 `apply_input_visual_effect`；状态继续发布
  `initialized/effectiveMode/outcome/errorCode`。
- 禁用的 option 仍保留在列表中，以便展示并保留来自其他平台的既有偏好；用户可以切换到可用模式，
  但不能在不支持的系统上重新选择液态。

## 验证

- Rust 覆盖运行时类检测、互斥可见性、纯色失败回退、1x/2x 与内容缩放坐标。
- frontend 覆盖逐模式 capability、macOS 26 可选、旧系统置灰及既有偏好保留。
- macOS 26 实机覆盖动态桌面、拖动、输入栏扩缩、主题、IME/焦点、截图和退出；macOS 15 证明液态类
  缺失时保持锁定。负责人视觉验收前不得标记 `accepted`。
