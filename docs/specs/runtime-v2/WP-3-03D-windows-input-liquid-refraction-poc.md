---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-14
---

# WP-3-03D Windows 输入栏单管线液态折射 PoC 规范

## 范围

本规范验证 Windows 输入栏能否在 WP-3-03C 实时高斯基础上，以单一应用侧 GPU 管线产生连续桌面折射。
执行状态只读 [Work Package 总计划](../../plans/runtime-v2/work-packages.md)。本包不改变 `ui.json`、
Appearance v3、capability manifest、设置页或产品默认效果。

## 启用与降级契约

- 只有 `SAKURA_WINDOWS_LIQUID_GLASS_POC=1|true|on` 时创建单一捕获与液态渲染资源；未设置时行为必须与
  WP-3-03C 一致。
- `SAKURA_WINDOWS_LIQUID_GLASS_DEBUG_STEP=0..9` 显示参考项目等价的 SDF、法线、折射系数、模糊、
  折射、Fresnel、glare 与最终合成阶段；无效值使用最终合成。
- PoC 仅在当前有效模式为 `gaussian_blur` 时显示；纯色模式隐藏并暂停液态 surface。
- 任一液态资源、捕获、设备或 present 失败都永久熔断本进程的液态路径并回退现有高斯；共享
  HostBackdrop 失败仍回退纯色，偏好不改写。
- 只允许一个当前显示器 Windows Graphics Capture 会话；不得使用 GDI/PNG/base64 截图、辅助 HWND、
  WebView IPC 帧传输或多个并行捕获源。
- 当前 Sakura HWND 仅在 PoC 生命周期内设为 `WDA_EXCLUDEFROMCAPTURE`；失败、关闭和析构必须恢复。

## 折射模型

- 玻璃形状为当前输入栏圆角矩形；bubble 永不创建液态 surface。
- 复用 Liquid Glass Studio 的四阶段语义：背景裁剪、纵向高斯、横向高斯、最终液态合成。
- 折射厚度为 20 logical px、折射系数 1.4、色散 7；对深度 `d` 使用参考项目的 Snell 风格曲线，并对
  `asin` 和零长度法线做有限值保护。
- 高斯等效标准差保持 `8 × scale_factor × content_scale`；中心采样模糊背景，边缘按 SDF 法线连续位移，
  不再使用离散 band 或 sector。
- Fresnel range 30、hardness 20%、factor 20%；glare 角度 -45°、factor 90%、opposite 80%、
  convergence 50%。固定值变更必须记录同背景 A/B 证据。
- 最终结果只通过一个普通 composition surface visual 交给 DWM；禁止自定义 Affine/Gaussian Composition
  effect graph。

## 资源与帧契约

- 资源上限为一个 capture session、一个 free-threaded frame pool、两个捕获缓冲、两个中间纹理、一个
  双缓冲 composition swap chain 和一个 surface visual。
- frame callback 同时最多处理一帧；忙时丢弃新帧，不排队、不累积延迟。
- 捕获分辨率按显示器提供，但 shader 与中间纹理只覆盖输入栏扩展采样区；不得为每个 input sector 创建
  资源。
- 跨显示器、DPI、角色重绑定、布局和拖动更新采样 crop；只有显示器变化才重建捕获会话。
- 环境变量关闭时上述液态资源计数全部为零。

## 诊断与视觉契约

- normal/SDF 调试阶段使用高可见度鲜粉 `#ff00a8` 标识输入栏完整边界，避免透明背景误判。
- 正常 PoC 使用主题低透明 tint、Fresnel 亮边和固定 `-45°` glare，不保留鲜粉诊断色。
- 液态 surface 只作用于输入栏，不得影响气泡、textarea、发送按钮、focus、hover、busy 或命中布局。

## 验证契约

- Rust 覆盖折射曲线有限/单调/归零、捕获 crop、圆角、100%/150% DPI 与跨显示器坐标。
- 覆盖 PoC/调试开关、关闭时零 GPU 资源、资源预算、窗口捕获属性恢复、设备失败永久回退高斯及 bubble
  永不创建。
- HLSL 离线编译全部通过，并以固定纹理做像素级阶段快照；静态门拒绝任何
  `AffineTransformEffectDescription`、离散 liquid brush/vector 或多 HostBackdrop 图重新进入。
- Windows 实机由负责人显式批准后，覆盖动态窗口、100%/150% DPI、短拖/长拖、松手、角色切换、跨屏、
  强制失败和应用退出；不得出现递归、漏带、闪影、黑块、角色轮廓或 DWM/显示驱动错误。
- 自动门通过后只进入 `stabilizing`/`manual_pending`；项目负责人视觉与系统安全确认前不得 accepted。
