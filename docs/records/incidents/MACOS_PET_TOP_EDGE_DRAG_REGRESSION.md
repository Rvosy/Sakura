---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
priority: P1
resolution: awaiting-live-validation
updated: 2026-09-04
---

# macOS 桌宠无法拖到工作区顶端回归

> 当前结论：问题来自缩放手势使用的临时最大包络，而不是拖动事件本身。当前工作树会在 macOS 上裁掉超出工作区的临时透明 backing，并在手势结束后恢复当前倍率的真实包络。自动测试已锁定几何不变量；macOS 原生窗口的最终行为仍需每次相关改动后做实机验收。

## 影响与回归特征

- 有可见立绘的角色无法继续向上拖到菜单栏下方，立绘顶部会留下明显空位。
- 问题多次在“修复缩放抖动或闪烁”之后重新出现，因此不能只检查拖动监听器。
- 全透明角色不容易暴露这类问题。它可以检查气泡、输入栏和命中区域，但不能证明可见立绘已经贴顶。
- 问题曾与调整立绘大小时的下跳、闪烁同时出现，但两者不是同一个验收项。

## 根因

macOS 的静止态曾常驻立绘最大倍率包络，或在缩放预览开始后保留了向当前可见内容上方扩展的透明区域。用户向上拖动时，AppKit 约束的是整个无边框 `NSWindow`，不是其中可见立绘的 alpha 顶边。透明 backing 先碰到工作区顶边后，AppKit 不再允许窗口继续向上，所以立绘顶部仍离工作区顶边一段距离。

这类回归容易反复出现，原因是两个目标互相牵制：

- 稳定的大包络可以减少缩放过程中频繁 resize 带来的抖动和错位。
- 大包络如果在静止态长期保留，或在屏幕边缘不裁剪，就会改变拖动边界并扩大透明窗口。

因此，“不抖”不能靠 macOS 常驻 150% 包络解决。设置页打开时取消整个窗口的遮罩也不是可接受方案：在 Windows 的稳定大窗口中，这会让大块透明区域吞掉其他窗口的点击。

## 当前修正

相关实现位于：

- `desktop/src-tauri/src/main.rs`：`clip_portrait_scale_preview_application_to_work_area()` 在缩放预览开始时合并当前表面和稳定包络，再调用几何裁剪。
- `desktop/src-tauri/src/window_geometry.rs`：`clip_expanded_surface_bounds_to_work_area()` 只裁掉工作区外的手势临时 backing，不能裁掉已经提交的可见表面。
- `desktop/src-tauri/src/window_interaction.rs`：计算当前倍率真实包络和手势期间的稳定包络。

当前策略如下：

1. macOS 静止态只保留当前倍率立绘、气泡、输入栏和其他可见控件的真实并集。
2. 调整立绘大小时允许临时扩大包络，但先按当前显示器工作区裁掉不可见的外围 backing。
3. 扩大和裁剪前后保持同一个全局物理立绘锚点；不能通过移动整个窗口来容纳透明区域。
4. 手势结束后恢复最终倍率的精确动态包络，不留下最大倍率的顶部空位。
5. Windows 继续使用稳定 HWND/WebView 包络，并以粗粒度 region 覆盖立绘与可见控件；稳定包络中的其他透明区域必须穿透。

## 自动回归门

Rust 测试 `window_surface_regression_scale_preview_keeps_visible_surface_at_macos_top_edge` 模拟以下场景：

1. 工作区从菜单栏下方开始，当前可见桌宠已经贴住工作区顶边。
2. 缩放手势请求一个向上扩展的 150% 临时包络；未裁剪时，其原生窗口顶边会越过工作区。
3. 应用裁剪后，原生窗口顶边不能低于或越过工作区顶边，当前可见表面的全局顶边仍保持贴顶。
4. 裁剪前后的全局物理立绘锚点必须完全一致，窗口仍可向下扩展容纳手势预览。

测试名带 `window_surface_regression` 前缀，因此会进入 `runtime-v2-window-surface` Harness profile。运行方式：

```bash
./runtime/bin/python -m harness run runtime-v2-window-surface
```

需要只跑这条测试时：

```bash
cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked \
  window_surface_regression_scale_preview_keeps_visible_surface_at_macos_top_edge \
  -- --nocapture
```

该测试能防止几何算法再次把可见内容向下推，但无法观察 AppKit、WKWebView 和 WindowServer 的真实合成帧。不要用它证明实机拖动、透明穿透或缩放无闪烁。

2026-09-04 的自动验证结果：

- 定向 Rust 测试：1 passed，0 failed。
- `runtime-v2-window-surface`：4/4 cases 通过。其中前端 249 项、表面回归 14 项、窗口几何 29 项、窗口交互 37 项全部通过。
- `git diff --check`：通过。
- `tools/check_docs.py`：仍被仓库原有的 `docs/.DS_Store` 拒绝；没有新增文档错误，也没有为本次记录清理该文件。

## macOS 实机验收

每次修改动态包络、缩放预览、原生 frame、stage offset 或透明命中后，至少执行一次：

1. 使用有可见立绘的 N.A.V.I.，先关闭气泡和输入栏干扰，将桌宠拖到菜单栏下方。
2. 确认可见立绘顶部距工作区顶部不超过 2 个逻辑像素，松手后不会被弹回。
3. 打开设置，把立绘大小连续拖动 `50% → 150% → 50%`，再松手并重新拖到顶边。
4. 重复 20 轮，确认拖动中立绘不裁剪，气泡和输入栏不抖，松手后顶部空位不残留。
5. 点击立绘外围和内部透明洞，确认点击到达背景窗口；可见立绘、气泡和输入栏仍能正常交互。
6. 在 Windows 上复查设置页打开及滑条操作期间的大块透明区域可以点击穿透，不能用取消 region 的方式换取稳定。

若第 2 或第 3 步失败，即使所有自动测试通过，也应重新打开本事件。验收视频或结果应记录构建 SHA；不要只写“新版”或引用本机对话链接。

## 不得恢复的方案

- macOS 静止态常驻 150% 最大包络。
- 为了避免滑条抖动，在设置页打开期间取消 Windows 整个窗口的 region。
- 分别调用窗口 `set_size` 和 `set_position` 来补偿位置；两步之间可能暴露错位帧。
- 用全透明角色作为“可见立绘已经贴顶”的验收证据。

## 关闭条件

- 上述 Rust 回归测试进入 `runtime-v2-window-surface` 并通过。
- macOS 有可见立绘的角色完成 20 轮顶边拖动和缩放实机验收。
- Windows 完成透明区域穿透与立绘、气泡尺寸和位置滑条稳定性复查。
- 故障记录中保存构建 SHA、日期和结果；不依赖 Codex 深度链接或某台电脑的本地会话。
