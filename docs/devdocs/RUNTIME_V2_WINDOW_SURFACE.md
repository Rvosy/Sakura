---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-08-09
---

# Runtime v2 桌宠窗口表面开发指南

本文面向修改 Tauri 桌宠布局、立绘切换或平台窗口后端的开发者。规范行为以
[`WP-3-03A spec`](../specs/runtime-v2/WP-3-03A-cross-platform-pet-surface.md) 为准，架构理由见
[`ADR-0010`](../adr/0010-cross-platform-pet-surface.md)。

## 数据流与边界

前端在 900×996 规范坐标系中测量可见控件并提交 revision。Rust 从角色资源层读取可信 PNG alpha，
把控件矩形、立绘 alpha 包络和当前显示状态合成动态 `active_bounds`，再换算物理窗口与平台输入区域。
根 WebView 保持随窗口 resize；Rust 在对应窗口 bounds 前先通过受控脚本以
`-active_bounds.left/top` 预提交规范舞台偏移、指针 surface offset 和 revision，使两项修改在同一
主循环中按序处理。该顺序只服务真实几何变化，不作为缩放无中间帧的保证：同一立绘缩放时，Rust
使用当前 alpha 在 150% 下的稳定动态包络；新旧 placement、`active_bounds`、DPI 和内容缩放相同时，
完全跳过窗口、WebView offset 和桥接矩形。Windows
由于 `SetWindowRgn` 同时裁剪可见内容，在首次取得 alpha mask 后就让底层 HWND/WebView 常驻 150%
稳定包络，静止态由精确 region 裁出真实轮廓。每轮 scale preview 首次激活时只清除一次复杂 region，
不再 resize/reposition 窗口。设置页用 `settings_character_appearance_scale_frame` 将 RAF 合并后的最新
倍率直接发布为 `sakura://portrait-scale-frame`；主窗口只改 `--portrait-render-scale`，不等待完整外观
publication，也不进入窗口 bounds、surface offset 或 alpha 行段计算。快速松手只触发一次最终
`activate_portrait_hit_test`：它从放宽状态恢复当前倍率精确 region，不改变稳定 placement，也不创建
旧新 region 桥接。macOS 只在 scale preview 开始时把窗口扩到“当前控件布局 + 150% 立绘”的包络，
刻度帧不再改变 bounds 或 surface offset，只立即提交合成 transform，并通过有界 latest-wins 队列更新
同一 envelope 内的当前倍率精确光标路由。结束时以一次事务把窗口和命中收紧到最终倍率立绘 alpha 与
当前控件的真实并集；静止态不保留 150% 顶部余量。Linux 复用这套手势状态生命周期：开始时通过
GTK/GDK 一次扩到同一类临时包络，刻度只更新 transform 与当前倍率精确 input region，结束时一次收紧；
它不调用 AppKit，也不把 GTK 窗口在每个刻度 resize/reposition。
设置窗口在 range 的 pointerdown/方向键按下时先开启原生 gesture guard，所有
预览 drain 完成且 pointerup、pointercancel、失焦或按键抬起后才关闭 guard，并通知主窗口以当前倍率
真实 alpha 包络收口。Rust 在 guard 活跃期间拒绝 Windows 精确 region 提交；不再使用 120ms 计时判断
拖动结束。若新 pointerdown 在旧 pointerup 的 drain 完成前到达，设置前端保留 backend guard 并让两轮
共享同一会话；最后一轮结束后才串行发送一次 `active=false`。轻量帧 drain 是 latest-wins；单帧 bridge
失败最多内部追赶两次最新值，不调用设置页 `onError`，最终完整 preview 才是可靠状态提交。Linux
仍按刻度提交精确输入区域，但和 macOS 一样只提交 hit region；只有 Windows 在 preview 期间保持
relaxed，macOS/Linux 始终使用当前倍率精确路由。旧 revision 返回空结果。
失败时 Rust 恢复上一版有效 snapshot；首次提交
失败时窗口保持隐藏。

控制面板的 `controlPanelWidth`、`bubbleMaxHeight`、`controlPanelVerticalOffset`、`inputBarOffset` 使用
独立的 `settings_character_appearance_layout_frame` / `sakura://control-surface-frame` 通道。Windows
启动时的稳定包络枚举上述四项的最小/最大值、输入框最小/最大高度与立绘最大倍率，因而刻度帧可在
WebView 中先绘制并设置 `deferNative`，不等待 `begin_control_surface_preview` 的 region 放宽；松手时
`adaptiveSurface.flush()` 强制一次非 deferred layout，再恢复精确 region。事件 payload 的
布局事件的 `deferNative` 仍只在 Windows 为真，macOS/Linux 逐帧进入原生布局；这与缩放 preview 中
Windows/macOS/Linux 都返回 deferred 是两个独立策略。手势开始要先设置
`data-layout-preview=active`，在原生预览准备前关闭布局过渡；手势结束或失败必须清除此状态。立绘图片
常驻 `will-change: transform` 合成层，避免第一次倍率变化才触发图层提升。

`bubbleMaxHeight` 名称仅为数据兼容；布局计算必须直接把它作为固定 `bubbleHeight`。回复文字、逐字动画、
历史和字幕语言变化只改变 `.bubble-copy` 的内部滚动，不得重新测量并覆盖外框高度。输入框高度仍使用
行高、padding 与 `inputMaxRows` 的自适应测量。

原生 bounds 更新不得通过同步窗口事件重入 `WindowGeometrySession`。Windows `SetWindowPos` 可在调用
栈内触发 `WindowEvent::Moved`，而提交命令此时仍持有几何锁；移动回调必须使用非阻塞 `try_lock`。
锁被事务占用时跳过该程序化移动，因为提交方会在 native 调用返回后写入同一版 session；锁空闲且
deferred drag 活跃时才观察用户拖动位置。窗口事件回调不得改回阻塞 `lock`。

`visual bounds` 与 `hit regions` 是两个独立结果：前者缩小原生矩形窗口，后者决定窗口内部哪些像素
接收事件。不要用控件外接矩形代替立绘 alpha，也不要仅依赖 DOM `pointer-events` 实现跨进程穿透。

拖动命令必须携带当前 revision 和规范坐标点。前端逻辑模型按 `interactive > drag > neutral >
transparent` 分类：`drag[0]` 固定为立绘，后续项包含可见气泡，输入框和导航控件保持 `interactive`，
`neutral` 当前为空。气泡 DOM 本身是 drag region，但实际回复 span 使用 `data-selectable-text`，正文
滚动条另做精确边缘检测；这些目标必须在调用 Rust 前覆盖为 `interactive`。Rust 不接收 DOM 内容，
只按控件优先级、气泡矩形及当前 alpha mask 二次分类，只有 `drag` 才能进入平台拖动；`interactive`、
`neutral`、`transparent` 都拒绝。`drag[0]` 的顺序不得改变，因为 alpha 分类只应用于立绘；后续气泡
矩形不受立绘透明洞影响。表情交叉淡入先提交旧、新 mask 的精确并集，动画结束后再收窄为新 mask。

## 锚点与坐标

规范锚点为立绘底部中心 `A`，动态包络为 `[L,T,R,B]`，物理比例为 `S`。相对边界按左上向下取整、
右下向上取整，窗口原点为全局物理锚点加相对左上，窗口本地锚点为相对左上的相反数。因此窗口原点
与本地锚点之和始终等于同一个物理锚点，不会在反复 DPI 换算中积累误差。首次启动才按工作区钳制；
已有锚点在表情、缩放和控件变化时不重新钳制。所有全局坐标计算必须保留有符号值以支持负坐标屏幕。
`content_scale` 从完整规范视口决定，不能随当前 alpha 包络改变，否则气泡与输入框会被隐式重缩放。
缩放 envelope 与 hit region 不得混用：Windows 取得 alpha mask 后的 envelope 固定按允许最大倍率生成，
用于彻底消除手势开始、中间和结束的窗口重排；静止态精确 region 仍按当前倍率生成。手势期不生成逐
刻度 native 模型且原生 region 保持放宽，结束后只应用一次最终精确模型。最大倍率 HWND/WebView 包络
在 Windows 是常驻的实现细节，放宽 region 不是。macOS/Linux 的最大倍率包络只存在于活跃缩放手势，
静止态收紧到当前倍率与控件并集；手势内 hit router/input region 仍按当前倍率 alpha 精确穿透。
`PhysicalHitRegions.envelope` 是同一 surface snapshot 的目标物理尺寸。平台 backend 必须使用它生成
region 和 macOS 光标路由坐标，不要在 resize 后立刻调用 `inner_size`。macOS 的手势首尾 bounds 通过
AppKit 主线程单次 `setFrame:display:NO` 同时提交位置和尺寸；`display` 不得设为 `YES`，否则 AppKit 会在
WebKit 消费已预提交的舞台 offset 前强制显示旧内容，形成偶发闪帧。不得退回 Tauri 分离的异步
`set_size`、`set_position`，否则两步之间会暴露气泡跳帧。目标 frame 使用提交前原生 frame、物理左上
角与 backing scale 换算，继续支持 Retina 和有符号屏幕坐标。
macOS 根 `WebviewWindow` 的独立 bounds 会被 WRY 忽略，远程 WebKit 图层也不能靠父 `NSView` 平移；
不得关闭 auto resize 后继续假定负偏移已经生效，否则页面会被裁断且 DOM 命中与原生命中不一致。

## 平台后端

- Windows 使用 `SetWindowRgn`；alpha 行段经 `ExtCreateRegion` 建区，重叠过渡矩形先规范化为不重叠
  带区。圆角控件再以 `CombineRgn` 合并。普通事务失败都保留或回滚上一版精确区域；仅连续缩放手势
  允许 `relax_hit_regions` 清除一次 region，预览期禁止逐刻度调用，结束后必须以最新 revision 恢复。
- macOS 在 AppKit 主线程维护当前精确矩形快照，根据鼠标位置切换
  `NSWindow.ignoresMouseEvents`。local/global event monitor 加 8ms 采样兜底；按键拖动期间锁定路由，
  防止拖动途中过早穿透。缩放期间每个已处理倍率整体替换矩形与目标 envelope，不能调用通用
  cursor-ignore API 把稳定包络暂时变成整窗命中；前端视觉允许领先路由一个有界 latest-wins 帧。
- Linux 以 `cairo::Region` 合并 alpha 行段和控件，通过 GTK `input_shape_combine_region` 应用。
  `PhysicalHitRegions` 是物理像素；写入 cairo 前必须按当前 GTK scale 向外取整为 surface-local 坐标，
  否则 HiDPI 下 alpha 洞和控件命中会偏移。X11/XWayland 在手势首尾用 GDK `Window::move_resize` 一次
  提交逻辑位置和尺寸；native Wayland 只调用 `GtkWindow::resize`，保留 input region 和交互式拖动，
  报告 `wayland_degraded_anchor`，且不得宣称绝对定位能力。两条路径都禁止用 `inner_size` readback
  重建 region，也不等待 configure 阻塞 GTK 主线程。

### Linux 缩放状态机

1. `idle`：`active_bounds` 是当前倍率 alpha 与全部可见控件的真实并集，input region 精确。
2. `begin(revision)`：确认 gesture guard 后，从当前 snapshot 计算控件与 150% alpha 并集；先 eval
   stage offset/revision，再执行一次 X11 move+resize 或 Wayland resize，并立即应用当前倍率精确 region。
3. `frame(revision, scale)`：窗口 placement 与 `active_bounds` 不变；前端先提交 CSS transform，再把倍率
   写入单槽队列。drain 只处理最新值，失败不弹窗；Rust 在同一 envelope 内只替换精确 region。
4. `end(revision)`：先停用 guard 并清空槽；最终 `activate_portrait_hit_test` 以新 revision 一次提交真实
   并集和精确 region。旧 drain 或上一轮结束回调的 revision 只能得到空结果，不能回写前端。

目标依赖必须放在 `Cargo.toml` 对应 target 区域，避免把 AppKit 或 GTK 依赖带入其他平台。

## 验证入口

```bash
npm test --prefix desktop/frontend
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1
python3 -m harness run runtime-v2-window-surface
python3 -m harness check WP-3-03A
python3 -m harness verify WP-3-03A
```

Windows 还必须运行 `runtime-v2-windows-interaction`。macOS 和 Linux 的无窗口 CI 只能证明模型、
生命周期和宿主平台可编译部分；X11/XWayland 与 native Wayland 的系统级配置、合成和路由必须使用
真实 Linux 桌面完成。实机证据需要记录同一候选 SHA、系统与桌面会话、DPI/显示器排列，以及 surface
诊断快照。
