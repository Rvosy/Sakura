---
kind: plan
status: active
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-15
---

# WP-3-03E macOS 输入栏原生玻璃实施计划

## 实施

1. 记录 WP-4-04 负责人验收并建立 macOS 原生 API、版本门控与纯色降级契约。
2. 把 Windows 专用状态提升为平台无关协调层，接入 AppKit 高斯与 macOS 26 液态视图。
3. 扩展逐模式 capability，让旧 macOS 锁定液态同时保留跨平台偏好。
4. 完成 Rust/frontend/docs 回归与 macOS 26 实机检查，自动门通过后进入 `stabilizing` 等待负责人验收。

## 回退

先在 capability 中关闭 macOS 两种原生模式并回到纯色，再移除 AppKit 后端和平台协调接线。回退不得
改写 `ui.json` 中已有的 `visual_effect_mode`，不得影响 Windows 后端或恢复 WP-3-03D 的危险管线。
