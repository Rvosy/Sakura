---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-3-03C Windows 输入栏实时高斯玻璃规范

## 范围

本规范把 WP-3-03B 的 Windows HostBackdrop 技术结论收敛为输入栏产品能力。执行状态只读
[Work Package 总计划](../../plans/runtime-v2/work-packages.md)。气泡、Legacy Qt、角色资源和插件不在范围内。

## 设置与发布契约

- `ui.json` 保持 schema v1，并在 `settings` 中接受可选全局字段 `visual_effect_mode`；合法值严格为
  `solid` 或 `gaussian_blur`，缺失时默认为 `gaussian_blur`，未知值拒绝。
- 该字段不得写入 `character_theme_overrides`，也不迁移旧 `system_config.yaml`。
- Appearance publication 使用 schema v3，`values.visualEffectMode` 为必填字段。
- capability `appearance.input_visual_effect` 在 Windows 为 `available`；macOS/Linux 为 `unavailable`，
  原因为“实时桌面高斯仅支持 Windows”。
- 偏好和有效模式是两个状态：非 Windows 或原生失败时有效模式为 `solid`，但保存其他外观字段必须原样
  保留偏好，不能静默改写。
- 设置页选项名称固定为“纯色块 / 高斯模糊”。预览即时生效，取消恢复打开页面时的 baseline，保存后
  重启仍保持；generation rebind 不得回退到旧值。

## Windows 后端契约

- 后端不依赖 `SAKURA_WINDOWS_GLASS_POC`；在窗口隐藏阶段初始化，默认隐藏 input region。
- 第一次最终布局和外观均可用后，必须在 `reveal_pet_window` 前提交模式、主题和 input region。
- 只有 input region 可以显示，bubble region 永不创建或启用。
- `gaussian_blur` 显示原生 region，`solid` 隐藏它并使用 WebView 不透明输入栏。
- 初始化或更新失败返回稳定诊断、隐藏 region、有效模式降级纯色并继续产品生命周期。
- 保留最终 `activeBounds` 后的 surface-local 同步及未变化拖动松手提交去重；角色切换、缩放、布局、DPI、
  主题和 alpha mask 原点变化均重新提交当前参数与 region。
- 禁止截图模拟、捕获排除和辅助 HWND。

## 视觉契约

高斯模式从底到顶为：

1. 原生实时桌面高斯，标准差固定为 `8 × scale_factor × content_scale`。
2. 主题主色 RGB 各乘 0.35、alpha 24/255 的暗色遮罩。
3. 气泡背景色 alpha 55/255 的主题 tint。
4. WebView 输入控件以输入背景色 alpha 55/255 叠加。
5. 1 px 白色描边，目标 alpha 90/255；focus 描边继续使用主题主色。

当前 textarea、发送按钮、布局、字体、hover 与 busy 状态保持不变。纯色模式保持 Runtime v2 当前不透明
输入栏。PoC dataset、诊断粉色和气泡玻璃 CSS 必须移除。

## 验证契约

- Rust 覆盖严格解析、默认值、偏好/有效值分离、失败降级、仅 input region、DPI 模糊尺度及各类几何同步。
- 前端覆盖 Appearance v3、平台能力、预览/取消/保存/rebind 和 CSS 不泄漏到气泡。
- 配置覆盖旧文档默认、两值往返、角色主题隔离及非 Windows 保存保留偏好。
- Windows 实机以 `origin/main` 同背景 A/B，覆盖 100%/150% DPI、动态背景、短拖/长拖、角色切换、
  布局变化、模式切换、取消、重启与强制初始化失败。不得出现左侧漏带、右侧闪影、角色轮廓或黑块。
- 自动门通过后只可进入 `stabilizing`/`manual_pending`；项目负责人视觉验收前不得标记 accepted。
