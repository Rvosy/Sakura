---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-3-03D Windows HostBackdrop 输入栏液态折射 PoC 规范

## 范围

本规范验证 Windows 输入栏能否在 WP-3-03C 实时高斯基础上，以离散边缘带产生可辨认的桌面折射。
执行状态只读 [Work Package 总计划](../../plans/runtime-v2/work-packages.md)。本包不改变 `ui.json`、
Appearance v3、capability manifest、设置页或产品默认效果。

## 启用与降级契约

- 只有 `SAKURA_WINDOWS_LIQUID_GLASS_POC=1|true|on` 时创建液态折射资源；未设置时行为必须与
  WP-3-03C 一致。
- `SAKURA_WINDOWS_LIQUID_GLASS_DEBUG_BANDS=1|true|on` 显示鲜粉/洋红分带诊断；该开关不得改变
  region 几何、命中或持久化配置。
- PoC 仅在当前有效模式为 `gaussian_blur` 时显示；纯色模式隐藏全部原生玻璃。
- 液态专用资源失败时记录稳定诊断并回退现有高斯。共享 HostBackdrop 失败仍回退纯色；偏好不改写。
- 禁止桌面截图、DXGI 捕获、捕获排除、辅助 HWND 和应用侧持续逐帧背景更新。

## 折射模型

- 玻璃形状为当前输入栏圆角矩形；bubble region 永不创建。
- 折射厚度初值为 20 logical px，折射系数为 1.4，分为 12 条由外到内的等距带。
- 对深度 `d` 使用 `x=clamp(1-d/20,0,1)`、`thetaI=asin(x²)`、
  `thetaT=asin(sin(thetaI)/1.4)`、`edgeFactor=-tan(thetaT-thetaI)`；结果必须有限、非负且向中心单调归零。
- 最大折射初值为 6 logical px。每带分为上、下、左、右及四角 sector，采样位移沿近似外法线方向。
- 相邻带和 sector 必须有亚像素重叠；最外层覆盖完整输入栏物理边界，DPI 取整不能产生漏带。
- 中心继续使用标准差 `8 × scale_factor × content_scale` 的现有高斯，不移动 WebView 内容或命中布局。

## 诊断与视觉契约

- 调试模式使用高可见度鲜粉 `#ff00a8` 与洋红交替显示全部折射带，sector 接缝使用亮色边界。
- 正常 PoC 使用主题低透明 tint、Fresnel 亮边和固定 `-45°` glare，不保留鲜粉诊断色。
- Fresnel/glare 只作用于输入栏，不得影响气泡、textarea、发送按钮、focus、hover 或 busy 状态。

## 验证契约

- Rust 覆盖折射曲线有限/单调/归零，12 条带的物理 inset、圆角、层序和 100%/150% DPI 几何。
- 覆盖 PoC 开关、调试开关、关闭时零行为变化、液态失败回退高斯及 bubble 永不创建。
- Windows 实机覆盖棋盘格、细文字、彩色图片与动态窗口，100%/150% DPI，短拖/长拖、松手、角色切换、
  缩放和强制失败；不得出现左侧漏带、右侧闪影、分层裂缝、黑块或角色轮廓。
- 自动门通过后只进入 `stabilizing`/`manual_pending`；项目负责人视觉确认前不得标记 accepted。
