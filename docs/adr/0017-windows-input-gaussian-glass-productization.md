---
kind: adr
status: proposed
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-09-02
---

# ADR-0017：Windows 输入栏实时高斯玻璃产品化

## 背景

ADR-0015 与 WP-3-03B 已证明当前 Tauri/Wry 顶级 HWND 可以直接承载
`HostBackdropBrush -> Border clamp -> GaussianBlur`，并确认了最终 surface origin 同步和拖动松手
去重这两个必要条件。PoC 同时覆盖气泡和输入栏、依赖环境开关并使用诊断粉色，尚不是设置可控的产品能力。

## 决策

- 复用 ADR-0015 验证过的 HostBackdrop 架构，只为输入栏创建原生 region；气泡继续完全由 WebView
  按当前主题绘制。
- 用户偏好与平台有效模式分离。配置保存 `solid | gaussian_blur` 偏好；Windows 11 可把高斯偏好解析为
  原生高斯，macOS/Linux 的有效模式固定为纯色但不得重写用户偏好。
- Windows 后端在隐藏窗口阶段初始化，region 初始不可见；最终布局与外观就绪后，在首次 reveal 前提交
  模式、主题和输入栏 region。
- HostBackdrop Visual 创建后保持活跃，不动画容器或 Visual 的 `Opacity`，也不通过 `IsVisible` 反复停用
  和重新激活。实机上重新激活该 Visual 后，窗口捕获仍能得到模糊画面，但显示器直出可能只剩透明输入栏。
  高斯效果链末端使用 D2D Opacity effect；该输出透明度和两层着色与 WebView 控件使用同一时长与缓动。
  隐藏后保留上一版裁剪几何，但效果链输出为全透明，不等待后续布局 IPC，也不重新激活 HostBackdrop。
- 初始化或运行时更新失败时记录稳定错误、隐藏原生 region 并降级为 WebView 纯色输入栏；降级不得阻止
  显示、输入、拖动或退出，也不得把偏好改写为 `solid`。
- 不使用桌面截图、捕获排除、窗口显隐循环或辅助 HWND。

## 视觉与强度

旧版在二分之一分辨率上使用 `radius=4`，本实现以 `8 × scale_factor × content_scale` 作为 D2D
Gaussian 标准差的固定初值。原生层叠加主题主色 RGB 各乘 0.35、alpha 24/255 的暗色遮罩，以及
气泡背景色 alpha 55/255 的 tint；WebView 输入控件再叠输入背景色 alpha 55/255 和 alpha 90/255
的 1 px 白色描边，focus 仍使用主题主色。

## 后果

- Windows 获得不依赖截图的实时桌面高斯输入栏，并可即时切换为纯色。
- 后端需同时响应外观、角色重绑定、布局、DPI、拖动和 alpha mask 导致的原点变化。
- macOS/Linux 暂不实现实时桌面高斯；设置页明确显示能力不可用，同时保留跨机器偏好。
- 本决策不扩展到气泡、强度滑块、Liquid Glass 或 Legacy Qt。
