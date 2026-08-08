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
- 隐藏组件不得占用视觉包络或输入区域。alpha 值大于零的立绘像素参与命中，透明洞和外围必须穿透；
  Windows 缩放预览活跃期间仅允许按下述性能事务短暂放宽，预览结束后必须恢复精确穿透。
- 立绘底部中心的物理屏幕坐标在表情、缩放、气泡高度、输入高度、菜单和 DPI 更新中保持不变。
- 动态包络只允许改变顶层原生窗口的裁剪范围；900×996 规范坐标不得随当前 alpha 包络重缩放。
  气泡、输入框及立绘锚点在缩放事务的任何可见中间帧中都不得抖动、闪跳或短暂错位。
- 同一立绘在 50%–150% 缩放预览中必须使用其 150% alpha 外接矩形与当前控件的并集作为稳定动态
  包络。macOS/Linux 实时缩放更新立绘 transform、真实包络与对应精确命中区域。Windows 一旦取得当前
  立绘 alpha mask，底层 HWND/WebView 必须常驻该稳定包络；静止态仍由当前倍率精确 window region
  裁出真实视觉和点击范围。手势开始只允许清除一次旧复杂 region，不得 resize/reposition HWND 或改变
  WebView surface offset。手势期每个数值刻度必须经独立轻量帧通道直接更新 WebView 合成 transform，
  不得进入完整外观预览、原生 bounds、alpha 行段构建或 `SetWindowRgn`。手势活跃期间任何精确 region
  提交都必须被拒绝。松手、取消或失焦后，最新 revision 只恢复一次当前倍率的精确 region；从放宽状态
  恢复时不得先提交过渡桥接 region，也不得改变稳定 HWND placement。旧 revision 不得生效。若下一轮
  pointer/key 手势在上一轮预览队列排空前开始，两轮必须共享已开启的原生 guard，不得在中间发布
  `active=false` 或产生无 guard 刻度。轻量帧允许丢弃旧值并有界追赶最新值，单帧失败不得显示连接报警；
  最终完整外观预览仍须可靠提交最新值。
- `bubbleMaxHeight` 持久化字段为兼容保留，但设置页和运行时语义必须是固定对话框高度。回复内容、逐字
  输出、历史切换和语言切换不得改变外框高度，只允许改变对话框内部滚动；输入框仍可按输入行数在契约
  范围内自适应。设置页拖动 `controlPanelWidth`、`bubbleMaxHeight`、`controlPanelVerticalOffset` 或
  `inputBarOffset` 时，Windows 的底层 HWND/WebView 包络必须同时覆盖全部合法布局极值与 50%–150%
  立绘倍率。Windows 每个数值帧必须立即更新 WebView 布局，不得等待 region 放宽、完整 appearance
  publication 或原生布局回包。每轮手势只允许一次最终原生布局提交和一次精确 region 恢复。
  macOS/Linux 不具备该 Windows 稳定
  包络时，轻量事件仍必须逐帧提交对应原生表面，不得永久停留在仅 DOM 预览状态。
- 只有立绘有效 alpha 像素可启动拖动；控件和气泡不得启动拖动。Rust 必须按当前 revision 和起点复核。
- 立绘当前帧和过渡帧不得触发 WebView 图片拖拽或元素选择。只有气泡正文和输入框文本可选择，
  两者的选择高亮必须使用当前角色主题色，不得回退为平台默认颜色。
- 一次 revision 必须包含布局、可见性、alpha、顶层窗口、规范舞台偏移和平台应用结果；舞台偏移
  必须在对应窗口 bounds 前预提交，失败时与窗口、命中区域一起恢复上一版。旧 revision 不得返回
  可重新提交的几何，指针分类必须读取已预提交的 surface offset。
- 原生窗口 bounds 提交可能同步产生窗口移动事件；该事件不得等待或重入当前几何事务。程序化移动由
  提交方写入 session，只有未被事务占用的拖动移动事件才观察 deferred drag 位置。
- 精确命中区域必须携带同 revision 的目标物理 envelope；Windows、macOS、Linux 应使用该值裁剪和
  路由，不得以 resize 后的即时窗口 readback 代替，否则首次扩大窗口可能按旧尺寸截空控件。

## 平台契约

- Windows 静止态和非缩放预览使用精确 Win32 window region，不得因菜单或失败兜底恢复整窗命中，也
  不得把复杂 alpha 退成 bbox。只有受 revision 和显式手势约束的缩放预览可以临时清除 region，且
  必须由最新 revision 在手势结束时恢复。
- macOS 根据当前逻辑命中模型切换 `NSWindow.ignoresMouseEvents`，透明点必须交给下层窗口。
- Linux X11/XWayland 使用 GTK/GDK input shape 并满足完整契约。native Wayland 同样应用 input region，
  但因无全局 surface 坐标，只声明 surface-local 锚点并发布 `wayland_degraded_anchor` 诊断。

## 验收

- 外围和内部透明洞点击到达背景窗口；有效 alpha、输入框和菜单由桌宠接收。
- 冷启动的首次 bounds 提交不得阻塞窗口事件循环；主窗口必须在 15 秒内可见并保持响应。
- 有效 alpha 可拖动，其他区域不能拖动；可见立绘顶部可距工作区顶部不超过 2 逻辑像素。
- 拖动立绘不得出现矩形选择层或图片拖拽预览；气泡和输入框文本仍可选择、复制且高亮跟随主题。
- 连续拖动缩放滑块 50%→150%→50%，气泡与输入框的全局物理坐标必须保持不变且无中间错位帧。
- 对话回复从空文本增长到超过可视范围时，对话框外框高度必须保持设置值；连续拖动四个布局滑块时
  第一帧即可见、高频刻度不闪回，最终 DOM、原生表面和精确命中均等于最后一个值。Windows 首次拖动
  与后续拖动的事件路径相同，不得要求重复拖动后才响应。
- 以超过 120ms 的慢速刻度间隔往返拖动 50%↔55% 时，手势中途不得误恢复精确 region 或向上闪动；
  只在真实 pointer/key 手势结束后恢复一次。
- 上述缩放循环的活动预览中 `active_bounds`、物理窗口 placement、本地立绘锚点和 `content_scale`
  必须逐次相等；Windows 不得逐刻度重建 region 或以旧 region 裁剪新视觉帧，macOS/Linux 的有效像素
  命中仍须随每个实时倍率变化。停止后 Windows 的 `active_bounds` 与 placement 必须继续等于手势前的
  稳定值，只恢复最终倍率精确穿透；macOS/Linux 收紧到最终倍率真实包络。物理立绘锚点保持不变，
  可见顶部仍可贴近工作区上沿。连续快速点拖与一次轻量帧失败都不得显示连接错误或回放旧倍率。
- 各状态循环 20 次，支持混合 DPI、负坐标多屏且完整平台路径的物理锚点漂移为零。
- Windows 现有真实透明穿透门保持通过；macOS、X11/XWayland 分别提供实机证据，native Wayland 单列。

## 非目标

不修改角色资源、用户布局配置、聊天协议、设置业务或 Legacy Qt 实现。
