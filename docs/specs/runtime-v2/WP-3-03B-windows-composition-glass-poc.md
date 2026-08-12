---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-3-03B Windows Composition 实时玻璃 PoC 规范

## 范围

本规范只定义 Windows 11 上当前 Tauri 2/WebView2 透明主窗口的最小兼容性验证。执行状态只读
[Work Package 总计划](../../plans/runtime-v2/work-packages.md)。

## 启用与降级契约

- 默认不创建玻璃视觉层，现有 Runtime v2 表现保持不变。
- 设置 `SAKURA_WINDOWS_GLASS_POC=1` 时，Windows 主窗口尝试初始化 PoC。
- 非 Windows 平台忽略该开关，不改变编译、启动和窗口行为。
- 初始化任一步失败时，必须返回稳定诊断并继续显示现有 WebView UI；不得 panic 或终止 Core。
- 不允许截图、窗口显隐循环、捕获排除或 Rust 到 JavaScript 的连续图像传输。

## 最小视觉契约

- 非 UWP 的 Win32 HWND 在创建 host backdrop visual 前，必须成功设置
  `DWMWA_USE_HOSTBACKDROPBRUSH=TRUE`；缺少该前置条件的黑屏或黑块不得作为技术 Gate 失败证据。
- 原生 visual 使用 host backdrop brush、Border clamp 和 Gaussian blur，只覆盖气泡与输入框 region；
  允许设置鲜艳但半透明的诊断 tint，以便明确观察未覆盖像素。
- WebView 根页面和 stage 保持透明，气泡、输入框在 PoC 模式降低不透明底色，使 backdrop 可见。
- 角色立绘、文字、按钮、textarea 和普通动画继续由 WebView 渲染。
- 角色 alpha mask 或 DPI 使活动窗口原点变化时，原生 region 必须按最终 `activeBounds` 重新定位；
  拖动完成时不得为未变化的局部几何重复提交 HWND/region。
- PoC 不把整个 WebView 重写为原生 UI，也不实现任意 mask 或产品化完整效果图。

## 技术 Gate

以下项目必须作为实机观察项，自动测试不能代替：

1. 纯 Win32 基线窗口在启用必需 DWM 属性后，能看见窗口背后真实内容，而不是黑色、固定截图或
   窗口自身内容；该项通过前不得把 Tauri/WebView2 层级结果归因于 HostBackdrop 本身。
2. 后方窗口滚动或播放动态内容时，玻璃持续变化。
3. 拖动 Sakura 跨越不同窗口时背景连续变化，不冻结、不递归、不闪烁。
4. WebView 文字、立绘和控件在 visual 上方，textarea、按钮和拖动命中不被破坏。
5. resize/布局变更后 visual 尺寸正确；100% 与 150% DPI 至少各观察一次。
6. Win+Shift+S 能捕获最终显示的 Sakura；不得依赖捕获排除。
7. 强制初始化失败后基础桌宠仍能显示、交互和退出。

## 自动验证

- Rust 单元测试覆盖开关解析、非 Windows/关闭分支和失败分类的纯逻辑。
- Windows 编译验证所需 API feature 和对象持有关系。
- 既有 `runtime-v2-shell` 与 `runtime-v2-window-surface` profile 必须保持通过。
- 文档索引、链接和元数据由 `docs` profile 验证。

## 分阶段边界

- Gate 1 只包含纯 Win32 HWND、DWM 属性、DesktopWindowTarget 和 HostBackdropBrush，不启动 Tauri、
  WebView2、Python Core 或角色。
- Gate 1 通过后，Gate 2 才在同一纯 Win32 visual graph 中加入 Gaussian Blur。
- Gate 2 通过后，才允许进入普通 WebView2 层级复核或 Composition Controller/root visual 调研。

## 非目标

产品化完整效果图、自定义任意 mask、Liquid Glass、设置 UI、默认启用、性能承诺、旧版
Windows、macOS/Linux 实现、Legacy Qt 修改和发布验收均不属于本 WP。
