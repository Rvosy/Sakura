---
kind: adr
status: proposed
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# ADR-0018：Windows HostBackdrop 离散液态折射

## 背景

WP-3-03C 已用 `HostBackdropBrush -> Border clamp -> GaussianBlur` 提供稳定的实时桌面输入栏。Liquid
Glass Studio 的最终合成依赖同一 GPU 管线中的原始及模糊背景纹理，以圆角矩形 SDF 计算法线，并只在
约 20 px 的内侧边缘按折射曲线偏移采样。Windows Composition 不向应用暴露 HostBackdrop 纹理，且
Win2D `DisplacementMapEffect` 标记为 `[NoComposition]`，因此不能把该逐像素 shader 直接接到现有 brush。

## 决策

- 保留 ADR-0015/0017 的单 HWND、input-only HostBackdrop 架构，不增加桌面截图、DXGI Desktop
  Duplication、窗口显隐循环或辅助 HWND。
- 以同心圆角 clip 将约 20 logical px 的输入栏内侧边缘离散为固定数量的折射带；每条带根据参考项目的
  Snell 风格曲线预计算强度，并以多个方向 sector 近似 SDF 法线方向。
- PoC 复用现有高斯 brush 作为背景源，中心仍显示 WP-3-03C 高斯；折射带仅改变边缘的 backdrop
  采样变换。诊断模式以鲜粉/洋红显示带和 sector 覆盖。
- 本包只由 `SAKURA_WINDOWS_LIQUID_GLASS_POC` 显式启用，不增加持久化设置。液态资源初始化或更新
  失败时隐藏折射层并继续现有高斯；共享 HostBackdrop 失败时仍按 WP-3-03C 降级纯色。
- 算法根据公开 MIT 项目 Liquid Glass Studio 独立重写；本包不复制其 GLSL、WebGL renderer 或资产。

## 候选方案

- 直接在 WebView 运行参考 GLSL：只能采样 WebView 内容，不能采样桌面，拒绝。
- DXGI 桌面捕获加 D3D11 shader：能逐像素折射，但重新引入递归捕获、同步和黑块风险，拒绝用于本 PoC。
- 仅用 CSS 高光：不改变真实桌面采样，不能回答折射技术 Gate，拒绝。

## 后果

- 该实现是连续 SDF 位移的离散近似，视觉验收必须明确检查分带、接缝、DPI 和动态背景。
- Composition visual 数量增加，但没有持续应用侧逐帧渲染循环；布局不变时不重复提交。
- PoC 成功只证明技术路线可用，不自动批准第三个设置值或默认启用；产品化必须另建 Work Package。

## 参考

- [Liquid Glass Studio](https://github.com/iyinchao/liquid-glass-studio)（MIT，Charles Yin）
- [Composition effects](https://learn.microsoft.com/en-us/windows/apps/develop/composition/composition-effects)
- [Win2D DisplacementMapEffect](https://microsoft.github.io/Win2D/WinUI2/html/T_Microsoft_Graphics_Canvas_Effects_DisplacementMapEffect.htm)
