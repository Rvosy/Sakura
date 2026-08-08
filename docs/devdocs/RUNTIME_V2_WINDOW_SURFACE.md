---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-08-08
---

# Runtime v2 桌宠窗口表面开发指南

本文面向修改 Tauri 桌宠布局、立绘切换或平台窗口后端的开发者。规范行为以
[`WP-3-03A spec`](../specs/runtime-v2/WP-3-03A-cross-platform-pet-surface.md) 为准，架构理由见
[`ADR-0010`](../adr/0010-cross-platform-pet-surface.md)。

## 数据流与边界

前端在 900×996 规范坐标系中测量可见控件并提交 revision。Rust 从角色资源层读取可信 PNG alpha，
把控件矩形、立绘 alpha 包络和当前显示状态合成动态 `active_bounds`，再换算物理窗口与平台输入区域。
平台提交成功后，前端才以 `-active_bounds.left/top` 平移规范舞台并显示这一帧。失败时 Rust 回滚到
上一版 bounds 和精确输入区域；首次提交失败时窗口保持隐藏。

`visual bounds` 与 `hit regions` 是两个独立结果：前者缩小原生矩形窗口，后者决定窗口内部哪些像素
接收事件。不要用控件外接矩形代替立绘 alpha，也不要仅依赖 DOM `pointer-events` 实现跨进程穿透。

拖动命令必须携带当前 revision 和规范坐标点。Rust 对控件优先级及当前 alpha mask 二次分类，只有
`drag` 才能进入平台拖动；`interactive`、`neutral`、`transparent` 都拒绝。表情交叉淡入先提交旧、新
mask 的精确并集，动画结束后再收窄为新 mask。

## 锚点与坐标

规范锚点为立绘底部中心 `A`，动态包络为 `[L,T,R,B]`，物理比例为 `S`。相对边界按左上向下取整、
右下向上取整，窗口原点为全局物理锚点加相对左上，窗口本地锚点为相对左上的相反数。因此窗口原点
与本地锚点之和始终等于同一个物理锚点，不会在反复 DPI 换算中积累误差。首次启动才按工作区钳制；
已有锚点在表情、缩放和控件变化时不重新钳制。所有全局坐标计算必须保留有符号值以支持负坐标屏幕。

## 平台后端

- Windows 使用 `SetWindowRgn`；alpha 行段经 `ExtCreateRegion` 建区，重叠过渡矩形先规范化为不重叠
  带区。圆角控件再以 `CombineRgn` 合并。任何失败都保留或回滚上一版精确区域，禁止恢复整窗命中。
- macOS 在 AppKit 主线程维护当前精确矩形快照，根据鼠标位置切换
  `NSWindow.ignoresMouseEvents`。local/global event monitor 加 8ms 采样兜底；按键拖动期间锁定路由，
  防止拖动途中过早穿透。
- Linux 以 `cairo::Region` 合并 alpha 行段和控件，通过 GTK `input_shape_combine_region` 应用。
  X11/XWayland 是完整全局锚点路径；native Wayland 保留 input region 和交互式拖动，但报告
  `wayland_degraded_anchor`，且不得宣称绝对定位能力。

目标依赖必须放在 `Cargo.toml` 对应 target 区域，避免把 AppKit 或 GTK 依赖带入其他平台。

## 验证入口

```bash
npm test --prefix desktop/frontend
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1
python3 -m harness run runtime-v2-window-surface
python3 -m harness check WP-3-03A
python3 -m harness verify WP-3-03A
```

Windows 还必须运行 `runtime-v2-windows-interaction`。macOS 和 native Wayland 的无窗口 CI 只能证明模型、
生命周期和原生编译；系统级路由必须使用真实桌面完成。实机证据需要记录同一候选 SHA、系统与桌面
会话、DPI/显示器排列，以及 surface 诊断快照。
