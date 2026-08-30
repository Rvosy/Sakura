---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-31
---

# WP-3-03D Windows 输入栏单管线液态折射 PoC 规范

## 范围

本规范验证 Windows 输入栏能否在 WP-3-03C 实时高斯基础上，以单一应用侧 GPU 管线产生连续桌面折射。
执行状态只读 [Work Package 总计划](../../plans/runtime-v2/work-packages.md)。本包在 `ui.json` 与
Appearance v1 的既有 `visual_effect_mode` 契约中新增 `liquid_glass`，不改变 schema 版本、capability
键或产品默认效果；缺字段仍默认 `gaussian_blur`。

## 启用与降级契约

- 设置页保留“纯色块 / 高斯模糊 / 液态玻璃”三项，但 Windows 发布阶段在完整实现和验收前必须把
  `appearance.input_visual_effect.liquid_glass` 标为 `unavailable` 并置灰液态玻璃。已有
  `liquid_glass` 偏好保持原值但当前有效模式回退为 `solid`，状态使用
  `WINDOWS_LIQUID_GLASS_NOT_IMPLEMENTED`；旧的 PoC 和实验开关均不能绕过该门禁。
- 临时门禁期间 Windows 不得创建液态捕获、D3D、交换链或 Composition surface。以下液态管线契约只在
  capability 正式解除门禁后生效。
- `SAKURA_WINDOWS_LIQUID_GLASS_DEBUG_STEP=0..9` 显示参考项目等价的 SDF、法线、折射系数、模糊、
  折射、Fresnel、glare 与最终合成阶段；无效值使用最终合成。
- 仅当前有效模式为 `liquid_glass` 时允许创建捕获/D3D 资源并显示液态 surface；切到 `gaussian_blur` 或
  `solid` 必须销毁液态管线。初始化或帧失败时本次进程熔断液态执行，但不得把有效模式改成
  `gaussian_blur`，也不得启用 HostBackdrop 高斯 visual；用户偏好保持不变。
- capability 正式开放后，在具备“不影响普通截图”的捕获隔离前，`liquid_glass` 必须以
  `LIQUID_GLASS_CAPTURE_ISOLATION_UNAVAILABLE` fail closed 停止捕获资源，且不得设置主 HWND 的
  `WDA_EXCLUDEFROMCAPTURE`；状态使用稳定错误码表达后端未运行，不能用高斯伪装液态成功。
- 任一液态资源、捕获、设备或 present 失败都永久熔断本进程的液态路径并保持液态模式，不启用高斯；共享
  HostBackdrop 失败仍回退纯色，偏好不改写。
- 只允许一个当前显示器 Windows Graphics Capture 会话；不得使用 GDI/PNG/base64 截图、辅助 HWND、
  WebView IPC 帧传输或多个并行捕获源。
- 系统截图、录屏与远程协助必须继续看见 Sakura；不得通过修改主 HWND display affinity 实现内部捕获隔离。

## 折射模型

- 玻璃形状为当前输入栏圆角矩形；bubble 永不创建液态 surface。
- 复用 Liquid Glass Studio 的四阶段语义：背景裁剪、纵向高斯、横向高斯、最终液态合成。
- 折射厚度为 20 logical px、折射系数 1.4、色散 7；对深度 `d` 使用参考项目的 Snell 风格曲线，并对
  `asin` 和零长度法线做有限值保护。
- 液态专用高斯按参考设置的 radius 10 实现，即 `sigma = 10 / 3 × scale_factor × content_scale`；普通
  `gaussian_blur` 仍保持 WP-3-03C 强度。中心采样模糊背景，边缘按 SDF 法线连续位移，不再使用离散
  band 或 sector。
- Fresnel range 30、hardness 20%、factor 20%；glare range 30、hardness 20%、factor 90.36%、
  opposite 80%、convergence 50%、角度 -46.1°。tint 使用透明白，阴影按参考值 25/15% 与 `(0,-10)`
  复刻；输入栏宽高和 28px 产品圆角不采用演示的 200×200/80px。固定值变更必须记录同背景 A/B 证据。
- 最终结果只通过一个普通 composition surface visual 交给 DWM；禁止自定义 Affine/Gaussian Composition
  effect graph。

## 资源与帧契约

- 资源上限为一个 capture session、一个 free-threaded frame pool、两个捕获缓冲、两个中间纹理、一个
  双缓冲 composition swap chain 和一个 surface visual。
- frame callback 同时最多处理一帧；忙时丢弃新帧，不排队、不累积延迟。
- composition back buffer 每帧先清为透明，且第一张完整有效桌面帧 present 成功前不得显示 liquid visual；
  旧启动帧或未初始化像素不得留在边缘。
- 捕获分辨率按显示器提供，但 shader 与中间纹理只覆盖输入栏扩展采样区；不得为每个 input sector 创建
  资源。
- 跨显示器、DPI、角色重绑定、布局和拖动更新采样 crop；只有显示器变化才重建捕获会话。
- 未选择 `liquid_glass` 时上述液态捕获/D3D 资源计数全部为零。

## 诊断与视觉契约

- normal/SDF 调试阶段使用高可见度鲜粉 `#ff00a8` 标识输入栏完整边界，避免透明背景误判。
- 正常 PoC 使用主题低透明 tint、Fresnel 亮边和固定 `-45°` glare，不保留鲜粉诊断色。
- 液态 surface 只作用于输入栏，不得影响气泡、textarea、发送按钮、focus、hover、busy 或命中布局。

## 验证契约

- Rust 覆盖折射曲线有限/单调/归零、捕获 crop、圆角、100%/150% DPI 与跨显示器坐标。
- 覆盖三种模式严格解析/往返、调试开关、未选择时零 GPU 资源、资源预算、窗口捕获属性恢复、液态失败不
  改写有效模式且不启用高斯，以及 bubble 永不创建。
- HLSL 离线编译全部通过，并以固定纹理做像素级阶段快照；静态门拒绝任何
  `AffineTransformEffectDescription`、离散 liquid brush/vector 或多 HostBackdrop 图重新进入。
- Windows 实机由负责人显式批准后，覆盖动态窗口、100%/150% DPI、短拖/长拖、松手、角色切换、跨屏、
  强制失败和应用退出；不得出现递归、漏带、闪影、黑块、角色轮廓或 DWM/显示驱动错误。
- 自动验证通过后可形成 `stabilizing` 候选；项目负责人完成视觉与系统安全确认前不得标记 `accepted`。
