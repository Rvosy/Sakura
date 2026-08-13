---
kind: adr
status: proposed
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-14
---

# ADR-0019：Windows 液态玻璃单一 GPU 管线

## 背景

ADR-0018 的 96 路 `HostBackdrop -> Border -> GaussianBlur -> AffineTransform` Composition 图在 Windows
11 `22621.4830` 上触发可复现的 `dwmcore.dll` `E_BOUNDS` 崩溃风暴。DWM 重启后仍恢复客户端图，导致
显示驱动降级和设备错误码 43。该路线已经永久否决。

Liquid Glass Studio 的视觉来自同一 GPU 上的背景 pass、纵向高斯、横向高斯与最终逐像素 SDF 合成；
离散 Composition 图不是其实现方式。Sakura 的额外问题只是 WebView 和普通 HostBackdrop brush 都不能把
桌面背景作为 shader texture 暴露给这条管线。

## 决策

- Windows Graphics Capture 只提供当前显示器的连续动态背景帧；Sakura HWND 使用
  `WDA_EXCLUDEFROMCAPTURE` 排除，避免把角色、输入栏和上帧液态结果递归采入背景。
- 单个 D3D11 device 完成背景裁剪、纵向高斯、横向高斯和最终液态合成。算法等价复用 MIT 项目 Liquid
  Glass Studio 的圆角矩形 SDF、有限差分法线、Snell 风格折射、RGB 色散、Fresnel 与 glare，并在 HLSL
  文件中保留上游版权与许可证说明。
- 最终结果写入一个 composition swap chain，并作为一个普通 `ICompositionSurface` / `SurfaceBrush`
  visual 放在现有 WebView 输入控件下方。DWM 不接收自定义 D2D/Composition effect graph。
- 捕获和渲染只覆盖当前输入栏所需区域。布局变化更新采样矩形；跨显示器时重建单个捕获会话，不并存旧
  会话。
- 任一初始化、捕获、设备移除、shader 或 present 错误都停止液态路径、恢复窗口捕获属性并继续 WP-3-03C
  高斯。不得循环重试 GPU 初始化。

## 安全预算

- 一个 `GraphicsCaptureSession`、一个 free-threaded frame pool、一个 composition swap chain。
- 一个 Composition surface visual；零 Affine/Gaussian 自定义 Composition effect graph。
- 最多两个捕获缓冲、两个中间纹理、两个 swap-chain back buffer。
- 帧回调忙时丢弃新帧，不排队；设备错误后熔断到进程退出。
- 环境变量关闭时不创建捕获、D3D shader 或 swap-chain 资源。

## 候选方案

- WebView WebGL/WebGPU：可直接复用 shader，但不能连续取得桌面纹理，拒绝作为完整方案。
- 多 HostBackdrop brush：已造成系统级 DWM 事故，永久拒绝。
- CPU/GDI 截屏再上传：持续跨 CPU/GPU 拷贝、容易递归并增加拖动延迟，拒绝。
- DXGI Desktop Duplication：技术可行但在锁屏、显示模式变化和多适配器间恢复成本更高；PoC 优先采用
  Windows Graphics Capture，并保留同一 D3D11 shader 后端作为可替换的输入边界。

## 后果

- Windows API 负责动态背景输入和 composition 交付，液态视觉不依赖 DWM effect graph 的表达能力。
- `WDA_EXCLUDEFROMCAPTURE` 会同时影响其他系统捕获；仅在显式 PoC 生命周期内设置，并在失败与析构时
  恢复。
- 首次 GUI Gate 之前必须完成 HLSL 编译、资源预算、坐标/DPI、熔断和非 GUI 回归；自动门不能替代负责人
  的显示系统安全确认。

## 参考

- [Liquid Glass Studio](https://github.com/iyinchao/liquid-glass-studio)（MIT，Copyright 2024 Charles Yin）
- [Screen capture](https://learn.microsoft.com/windows/uwp/audio-video-camera/screen-capture)
- [Composition native interoperation](https://learn.microsoft.com/windows/uwp/composition/composition-native-interop)
