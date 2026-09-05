---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
priority: P1
resolution: unresolved
updated: 2026-09-05
---

# macOS 调整立绘和控件布局时整窗闪烁（布局方案已获用户确认）

> 当前结论：2026-09-05 固定 WKWebView 画布、动态 NSWindow 裁剪方案收到用户“这一版可以了”的实机确认，本轮控件调整体验已获认可。此前仍闪且轻微缩放的独立快照覆盖窗已经撤回。本记录还包含历史立绘切换、混合 DPI 和跨平台专项验收，因此整体 `resolution` 暂保留 `unresolved`；本轮用户验收单独记为通过。

## 优先级和影响

- 优先级：P1，由项目所有者在 2026-09-04 确认。
- 处理安排：2026-09-04 曾暂缓；2026-09-05 按项目所有者要求恢复处理。
- 直接影响：设置页调整立绘大小时，桌宠窗口可能闪一下。闪烁通常出现在第一次真实数值变化或一次缩放手势的早期。
- 暂缓时没有观察到进程崩溃、配置丢失或角色数据损坏，因此当时未作为发布阻断项；这不代表调整时的闪烁可以作为最终体验接受。
- 已知主要发生在 macOS。Windows 需要保留本文列出的输入穿透和稳定窗口约束，但尚无证据表明 Windows 存在同一种 WKWebView 闪烁。

## 2026-09-05 控件布局优化与最新反馈

本次在独立 `fix/macos-layout-preview` 工作树中处理气泡宽度、高度、上下位置和输入栏偏移。
macOS 不再为每个刻度调整原生窗口；第一次变化预留控件轨迹包络，连续变化只更新画面、材质和精确
命中模型。光标路由改为查询光标附近的 alpha 样本，消除了每帧生成整张立绘矩形集合、每 8 ms
复制整套矩形的开销。Windows 原有常驻包络与 region 路径保留。

用户随后反馈：“目前是流畅了，但是数值第一次改变的时候，整个程序会闪一下，之后正常，松手也会闪一下”。
因此连续调整改善有用户实机证据，首尾两次原生 resize 仍需处理。

后续曾把同窗 `NSImageView` 改为独立透明 `NSWindow`，保持快照屏幕坐标，覆盖扩窗和收窗，
在 WebKit 完成 `afterScreenUpdates` 回调后恢复主窗口。隔离运行的日志中捕获和移除均完成，
但用户明确反馈：“还会闪，而且这次还出现了轻微缩放的情况”。该方案及其 snapshot command、
透明度切换、前端捕获/移除流程均已删除，不能再次把回调完成当作视觉成功。

当前候选保持 WKWebView 渲染画布固定为 900×1490 逻辑像素乘 `content_scale`，其中底部 116 像素
沿用现有工具菜单预留。NSWindow 继续使用动态包络，在同一 AppKit 调用中更新外窗与 WebView 原点。
DOM stage 留在规范原点，窗口大小变化不再要求网页跟着重排或等待异步 stage offset。
固定的是渲染画布；桌宠的原生窗口仍然收紧，不恢复导致顶部拖动失败的常驻大窗口。

实机接线中查清了三个耦合点：

- 仅关闭 AppKit autoresizing 不够；Tauri 的 Resized 处理还会按比例改写子视图尺寸，必须同时停用。
- WKWebView 移到外窗顶部之外时，WebKit 自动 content inset 会改变网页 `innerHeight`，造成空白或
  二次偏移；关闭该行为后，网页 viewport 才与固定 NSView 尺寸一致。
- 当前用有 selector 检查的 `_setAutomaticallyAdjustsContentInsets:` 关闭自动 inset。这是私有 SPI，
  缺失时明确返回错误；公开的 macOS 26 inset API 不覆盖当前兼容范围，旧系统仍需验证。

启用固定画布后，当前开发进程已恢复可见立绘、气泡和输入框。一次宽度修改的首尾日志均为
`resized=false`，网页 viewport 保持不变；右键菜单可以打开设置。这些日志只确认接线和几何；
用户随后针对新方案明确反馈“这一版可以了”，补充了本轮调整体验的实机证据。
临时 DOM 几何诊断已删除，保留可开关的 native canvas/crop 日志。收尾还清理了死快照流程、
补齐底部工具菜单画布预留和裁剪输入栏材质；这些改动通过构建和相应自动回归。

## 2026-09-04 用户反馈

最新开发版包含异步 `WKWebView` 快照遮挡，并把快照捕获推迟到第一次真实数值变化。原生日志能完整走完捕获、扩窗、重定位和移除，用户连续实机拖动后仍确认“还是会闪”。

因此，下面两条都不成立：

1. 日志完整不代表画面没有中间帧。
2. 前端和 Rust 测试通过不代表透明原生窗口没有闪烁。

此前同一问题还出现过更明显的表现：

- 仅按下立绘大小滑条，桌宠整体向下移动；松手后恢复。
- 第一次改变数值时，整个桌宠向左上方闪现一下。
- 位置问题修正后，窗口仍会在原地闪一下。
- 一版按下即预扩 WebView 的实现会立刻闪，输入栏还会瞬间出现在立绘顶部；开始拖动时再次闪烁。该方案已撤回。
- N.A.V.I. 能复现位移和闪烁；全透明的 N.A.V.I.-Test 不容易表现同一视觉问题。N.A.V.I.-Test 只能检查气泡、输入栏和命中区域，不能作为立绘无闪烁的反证。

## 与早期立绘切换双闪的关系

早期问题发生在切换立绘或表情时，与本次缩放滑条闪烁共享原生窗口、WebView 和透明表面链路，但触发动作不同。以下历史直接保存在仓库中，不依赖某台电脑上的 Codex 对话：

- 版本核对时，`v1.0.3` 与当时的 `origin/main` 都指向 `d5ce4253c1129e329debd46ef61ee6d5b2fd833c`，该版本已经存在立绘切换双闪。
- 当时的前端先调用 `prepare_portrait_transition`。Rust 计算新旧立绘 alpha bounds 的并集，并提交一次原生窗口 frame；前端执行交叉淡入淡出、等待两个绘制帧后，又调用 `commit_portrait_transition` 提交最终 frame。两次 AppKit 窗口事务与“整窗闪两次”的现象吻合。
- 那次排查曾建立独立修复工作树，但实际改动偏到了缩放结算和物理立绘锚点保持。`portrait_scale` Rust 测试虽为 3/3 通过，实机也只操作了缩放滑条，没有完成真实立绘切换的无闪验收；当时没有提交、推送或创建 PR。
- 后续处理同类问题时，必须沿实际 `portrait-key` 切换链路记录原生 frame 提交次数，并在 macOS 实机观察淡入和整窗表面。缩放测试、日志完整或几何单测都不能替代这项证据。

这段历史用于提醒后续维护者区分“立绘切换”和“立绘缩放”两条事务。不能因为其中一条路径恢复正常，就推断另一条也不会闪。

## 复现步骤

### 2026-09-04 历史环境

- 平台：macOS 实机。
- 当时代码位置：仓库主工作区。
- 记录时分支：`fix/telemetry-diagnostics-quality`。
- 记录时 HEAD：`e654348c41a7`。
- 当时修复尝试位于未提交工作树；2026-09-05 的后续工作已隔离到 `fix/macos-layout-preview`，基线为 `57aab298`。

开启原生表面诊断：

```bash
SAKURA_TRACE_MACOS_SURFACE=1 ./scripts/start.sh
```

不要读取、修改或清理仓库 `data/` 及外部用户数据。复现不需要碰这些目录。

### 操作

1. 打开“设置 → 角色与布局”。
2. 选择有可见立绘的 N.A.V.I.。
3. 先单击或按住“立绘大小”滑条，不改变数值，然后松手。
4. 再进行一次只跨越 1～2 个刻度的拖动，观察第一次数值变化。
5. 连续执行 `50% → 150% → 50%`，分别观察开始拖动、连续变化和松手。
6. 重复短拖动至少 20 次。该问题有概率性，一次没有出现不能判定通过。
7. 将桌宠拖到屏幕顶部附近后重复上述步骤，并确认松手后仍能继续向上拖动。
8. 分别拖动气泡宽度、高度、上下位置和输入栏偏移，检查首帧、连续变化与松手。
9. 切换一次立绘或角色，检查原先修好的立绘淡入和双闪问题是否回归。

最好录制 60 fps 以上的视频，并同时保存带 revision 和单调时钟的原生日志。静态截图很难捕获一帧闪烁。

## 目标行为

macOS 实机需要同时满足以下条件：

- 按下滑条但不改变数值时，原生窗口、WebView 和 DOM 均不发生视觉变化。
- 第一次数值变化不闪、不跳，也不能短暂显示错误位置的气泡或输入栏。
- 连续拖动期间立绘平滑缩放，气泡和输入栏的全局位置保持不变。
- 松手不闪、不跳，最终精确命中区域与显示倍率一致。
- 桌宠仍可拖到工作区顶部，不能因临时最大包络被 AppKit 向下钳回。
- 立绘/角色切换保留淡入效果，不能恢复整窗双闪。

跨平台还要守住两项约束：

- Windows 可以常驻稳定 HWND/WebView 包络，但设置页打开和滑条操作期间只能使用覆盖可见组件的粗 region。巨大透明区域必须穿透，不能吞掉半个屏幕的点击。
- 调整气泡宽度、高度、位置和输入栏位置时，不能因为原生包络或精确 region 重建而抖动。

## 当前调用链

### 设置页

`desktop/frontend/settings/appearance-runtime.js` 在真实 pointer/key 手势开始和结束时发布：

- `settings_character_appearance_scale_gesture(active=true/false)`
- `settings_character_appearance_scale_frame(portrait_scale_percent)`

数值帧采用 RAF 和 latest-wins 队列。单纯按下而没有 `input` 事件时，不应发布 scale frame。

### 主窗口前端

控件布局由 `begin_control_surface_preview` 登记，第一次数值帧调用 `prepare_control_surface_preview`。
准备完成后复用临时原生表面，`preview_pet_control_surface` 只追随最新材质和命中模型；
`end_control_surface_preview` 返回最终动态表面。`control-surface-transactions.js` 串行提交原生结果
及相应前端状态，防止快速重拖时新任务越过已经开始的旧任务。

立绘缩放继续由 `begin_portrait_scale_preview` 取得临时包络，第一次数值帧才通过
`activate_portrait_hit_test` 准备原生表面，随后更新倍率；松手提交最终动态包络。截图准备与移除均已删除。
`samePetSurfaceGeometry()` 跳过重复几何，macOS 的 `nativeViewport` 标志让 stage 保持规范原点。

### Rust 与 AppKit

- `desktop/src-tauri/src/main.rs` 负责手势生命周期、临时包络、工作区裁剪和最终动态表面。
  `precommit_webview_surface()` 在 macOS 停用 Tauri 自动 resize，并准备固定画布与裁剪参数。
- `desktop/src-tauri/src/macos_surface_viewport.rs` 关闭 AppKit autoresizing 和 WebKit 自动 content inset，
  然后在同一主线程调用中设置 NSWindow frame 与 WKWebView frame origin；只在内容缩放因子改变时
  调整画布尺寸。设置等其他窗口不启用此机制。
- `desktop/src-tauri/src/platform/window_backend.rs` 沿用物理 placement 到 Cocoa frame 的换算，
  通过上述模块同步窗口与子视图裁剪。
- `desktop/src-tauri/src/window_interaction.rs` 保存精确命中模型；macOS 只检查当前光标的 alpha footprint，
  每次最多排队一个原生路由回调，不再为整个立绘逐帧构造矩形集合。
- `desktop/src-tauri/src/macos_input_glass.rs` 允许工作区裁剪产生负坐标，保留完整输入栏材质形状，
  由外窗裁剪；不会因部分输入栏在屏幕外而把材质永久降级。
- `desktop/src-tauri/src/window_geometry.rs` 保留 `clip_expanded_surface_bounds_to_work_area()`，
  避免临时包络越过工作区顶部时被 AppKit 整窗向下钳制。

### Windows 保护措施

Windows 路径保留稳定最大包络，并用 `coarse_preview_hit_regions()` 生成“立绘外接矩形 + 可见控件”的临时 region。不能重新采用清空 `SetWindowRgn` 的做法，否则稳定 HWND 的巨大透明余量会拦截其他窗口。

## 已确认和仍未确认的归因

### 已确认

- 窗口下跳来自临时大窗口越过工作区顶部后被 AppKit 调整；工作区裁剪已经消除该位移。
- 左上角闪现来自原生 frame 与前端 stage offset 在不同时间生效；按相同几何提交后，该错误位置不再是当前主要表现。
- 早期同步快照实现会在 Tauri command 内等待 WKWebView 异步回调，同时占有几何锁。日志只有 `installed`，没有 command return；后续 frame 和 gesture-end 全部等待该锁。该死锁已改为异步等待。
- 按下即预扩 WKWebView 会暴露错误切片，输入栏曾瞬间出现在立绘顶部。当前实现不在 pointerdown 时捕获快照或改变原生 frame。
- 2026-09-04 同窗快照和 2026-09-05 独立快照的控制流均能完成；用户仍看到闪烁，后者还出现轻微缩放。
- 固定画布的初次接线发现 Tauri 自动 resize 和 WebKit 自动 inset 均会改变网页 viewport；关闭后几何恢复一致。

### 尚未证明

NSWindow/WKWebView 一起变尺寸会触发 WebKit backing 更新，与用户观察到的首尾闪动吻合。
固定画布已消除日志中的网页尺寸变化，用户也认可了本轮体验。但没有高帧率视频与原生时间戳的逐帧
对应关系，尚不能把这轮确认扩大到所有系统版本与显示器组合。独立覆盖窗的轻微缩放也只有用户视觉反馈，
不能把它归结为某个已确认的像素换算错误。

## 已尝试方案

### 保留的改动

- macOS 静止态使用动态包络，设置缩放时才计算临时最大包络。
- 临时包络按工作区裁剪，防止顶部钳制造成窗口下跳。
- 缩放结算保持全局物理立绘锚点。
- 相同原生表面和相同前端几何不重复提交。
- Windows 设置预览使用 coarse region，透明余量不接收点击。
- 控件手势复用原生表面，macOS 光标路由取消整张立绘扫描与频繁矩形集合复制。
- 固定 WKWebView 画布与动态外窗裁剪的当前候选，以及快速重拖时原生结果到前端状态的顺序保护。

几何裁剪、锚点保持和 Windows coarse region 是已有有效修复；固定画布已有本轮用户验收。回退时应保留这些有效行为。

### 已撤回或判定不足的方案

- macOS 常驻 150% 最大窗口：缩放稳定一些，但窗口过高，桌宠无法继续向上拖动；静止态也不应长期保留大包络。
- 设置页打开时直接关闭 Windows 遮罩/region：大半屏幕变成不可点击区域，不可接受。
- pointerdown 时直接预扩 WKWebView、保持旧 `NSWindow`：按下即闪，并显示错误 WebView 切片，已撤回。
- AppKit 原生窗口 class swapping：启动阶段触发 KVO crash，已撤回，禁止恢复。
- 仅使用 `setFrame_display(false)`、去重 frame 或提前提交 stage offset：能消除部分错位，不能遮住 backing surface 首次重建。
- 同窗 `NSImageView` 快照：解决了同步死锁，日志顺序正确，但未通过用户实机视觉验收，已删除。
- 独立透明覆盖窗：用户仍看到首尾闪动，并新增轻微缩放，已删除；不继续调整快照保留时长。

## 2026-09-04 日志证据

一次典型首帧事务：

```text
phase=settings-gesture active=true
phase=settings-frame scale=62
phase=installed revision=2 window=(2022,108,428,605) snapshot=(0,0,428,605)
phase=prepare-return revision=2 snapshot=true
phase=repositioned revision=2 window=(1823,108,707,1186) snapshot=(199,0,428,605)
phase=finish-command
phase=finished revision=2
phase=settings-gesture active=false
```

连续多轮拖动中，每个 revision 都能看到 `installed`、`prepare-return`、`repositioned` 和 `finished`。没有再次出现早期实现缺少 `begin-return` 的阻塞表现，也没有捕获到 Rust panic。用户在这组日志对应的实机运行中仍看到了闪烁。

这段日志只能证明控制流完成，不能证明 WindowServer 没有显示过空白帧或旧帧。

## 自动验证状态

2026-09-04 当前未提交代码完成：

- `cargo check --manifest-path desktop/src-tauri/Cargo.toml -j 2`：通过。
- `cargo test --manifest-path desktop/src-tauri/Cargo.toml -j 2 portrait_scale -- --nocapture`：3 passed。
- 快照 revision 竞态测试：1 passed。
- `desktop/frontend`：249 passed。

2026-09-05 固定画布候选完成 `runtime-v2-window-surface` Harness：4/4 通过，包含前端 274 项、
表面回归 20 项、几何 29 项和交互 39 项。后续补充的输入栏裁剪回归与材质测试共 7 项通过；
文档 Harness 1/1 通过。

这些测试覆盖几何、revision 和前端状态流，不覆盖 macOS WindowServer 的实际合成结果。
固定画布方案已有本轮用户视觉确认；下文列出的历史回归和跨平台专项尚未全部执行，所以整体 `resolution` 仍为 `unresolved`。

## 当前工作树边界与后续验收

2026-09-05 的修改在独立 `fix/macos-layout-preview` 工作树中完成，代码基线为 `57aab298`。
原 `macos_surface_snapshot.rs` 和相关 commands 已删除，`objc2-web-kit` 只保留固定视图需要的能力。
前端调整、光标路由、固定原生画布和相应测试、Spec、ADR 是本轮范围；角色包与用户数据未纳入改动。

复现前须核对运行进程的可执行文件与对应构建；不要假定屏幕上的开发 app 自动换成刚编译的代码。
用户已确认当前固定画布方案可用。下面是后续平台回归范围，不作为重复索取本轮用户确认的要求：

1. 可见 N.A.V.I. 的四个布局滑条、立绘缩放及快速重复短拖动。
2. 顶部拖动、立绘切换淡入、菜单与输入栏材质位置，尤其是工作区裁剪后的控件。
3. macOS 不同内容缩放和系统版本的固定画布兼容性。
4. Windows 实机透明点击穿透和现有滑条流畅性。

## 关闭条件

只有满足以下条件后才能把本记录改为 resolved：

- N.A.V.I. 在 macOS 实机连续完成 20 轮短拖动和 10 轮 `50% → 150% → 50%`，没有整窗闪烁、错位或下跳。
- 单击/按住滑条但不改值无闪烁，第一次真实数值变化也无闪烁。
- 屏幕顶部拖动、立绘切换淡入、气泡和输入栏位置全部通过回归检查。
- Windows 实机确认稳定 HWND 的透明余量仍可点击穿透，立绘与四个布局滑条无抖动。
- 自动测试继续通过，固定画布的屏幕锚点、顶部裁剪、快速重拖事务顺序和精确 alpha 路由均有回归覆盖。

在达到这些条件前，不要在提交、PR 或发布说明中写“macOS 立绘缩放闪烁已修复”。
