---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-08
---

# WP-3-03A：跨平台桌宠动态表面与精确命中规范

## 必须行为

- 900×996 只作为规范坐标系；原生窗口必须取立绘可见 alpha、可见气泡、输入框、菜单和其他可见
  控件的动态视觉外接矩形。全局安全边距为 2 逻辑像素，外部 focus outline 额外预留 4 像素。
- 隐藏组件不得占用视觉包络或输入区域。alpha 值大于零的立绘像素参与命中，透明洞和外围必须穿透。
- 立绘底部中心的物理屏幕坐标在表情、缩放、气泡高度、输入高度、菜单和 DPI 更新中保持不变。
- 动态包络只允许改变顶层原生窗口的裁剪范围；900×996 规范坐标不得随当前 alpha 包络重缩放。
  气泡、输入框及立绘锚点在缩放事务的任何可见中间帧中都不得抖动、闪跳或短暂错位。
- 同一立绘在 50%–150% 缩放预览中必须使用其 150% alpha 外接矩形与当前控件的并集作为稳定动态
  包络。实时缩放只更新立绘 transform 与对应精确命中区域；若新旧 surface 几何相同，平台层不得
  重复设置顶层窗口 bounds、根 WebView offset 或过渡桥接区域。设置窗口必须显式发布缩放手势开始与
  结束；手势活跃期间任何收口请求都必须被拒绝。松手、取消或失焦后，最新 revision 才收口到当前倍率
  的真实包络；旧 revision 的收口不得生效，稳定预览包络不得留下永久透明上边距。
- 只有立绘有效 alpha 像素可启动拖动；控件和气泡不得启动拖动。Rust 必须按当前 revision 和起点复核。
- 立绘当前帧和过渡帧不得触发 WebView 图片拖拽或元素选择。只有气泡正文和输入框文本可选择，
  两者的选择高亮必须使用当前角色主题色，不得回退为平台默认颜色。
- 一次 revision 必须包含布局、可见性、alpha、顶层窗口、规范舞台偏移和平台应用结果；舞台偏移
  必须在对应窗口 bounds 前预提交，失败时与窗口、命中区域一起恢复上一版。旧 revision 不得返回
  可重新提交的几何，指针分类必须读取已预提交的 surface offset。
- 精确命中区域必须携带同 revision 的目标物理 envelope；Windows、macOS、Linux 应使用该值裁剪和
  路由，不得以 resize 后的即时窗口 readback 代替，否则首次扩大窗口可能按旧尺寸截空控件。

## 平台契约

- Windows 使用精确 Win32 window region，不得因预览或菜单恢复整窗命中，也不得把复杂 alpha 退成 bbox。
- macOS 根据当前逻辑命中模型切换 `NSWindow.ignoresMouseEvents`，透明点必须交给下层窗口。
- Linux X11/XWayland 使用 GTK/GDK input shape 并满足完整契约。native Wayland 同样应用 input region，
  但因无全局 surface 坐标，只声明 surface-local 锚点并发布 `wayland_degraded_anchor` 诊断。

## 验收

- 外围和内部透明洞点击到达背景窗口；有效 alpha、输入框和菜单由桌宠接收。
- 有效 alpha 可拖动，其他区域不能拖动；可见立绘顶部可距工作区顶部不超过 2 逻辑像素。
- 拖动立绘不得出现矩形选择层或图片拖拽预览；气泡和输入框文本仍可选择、复制且高亮跟随主题。
- 连续拖动缩放滑块 50%→150%→50%，气泡与输入框的全局物理坐标必须保持不变且无中间错位帧。
- 以超过 120ms 的慢速刻度间隔往返拖动 50%↔55% 时，手势中途不得误收口或向上闪动；只在真实
  pointer/key 手势结束后收口一次。
- 上述缩放循环的活动预览中 `active_bounds`、物理窗口 placement、本地立绘锚点和 `content_scale`
  必须逐次相等；立绘有效像素命中仍须随每个实时倍率变化。停止后 `active_bounds` 与 placement 必须
  收紧到最终倍率的真实包络，物理立绘锚点保持不变，可见顶部仍可贴近工作区上沿。
- 各状态循环 20 次，支持混合 DPI、负坐标多屏且完整平台路径的物理锚点漂移为零。
- Windows 现有真实透明穿透门保持通过；macOS、X11/XWayland 分别提供实机证据，native Wayland 单列。

## 非目标

不修改角色资源、用户布局配置、聊天协议、设置业务或 Legacy Qt 实现。
