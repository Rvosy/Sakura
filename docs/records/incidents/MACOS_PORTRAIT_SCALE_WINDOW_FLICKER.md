---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
priority: P1
resolution: unresolved
updated: 2026-09-04
---

# macOS 调整立绘大小时整窗概率闪烁（未解决）

> 当前结论：问题仍可在 macOS 实机复现。2026-09-04 的最新实现已经消除了窗口下跳、左上角闪现和同步快照死锁，但调整立绘大小时仍有概率发生整窗闪烁。本记录不能作为修复或验收证据。

## 优先级和影响

- 优先级：P1，由项目所有者在 2026-09-04 确认。
- 处理安排：暂缓，之后单独继续。
- 直接影响：设置页调整立绘大小时，桌宠窗口可能闪一下。闪烁通常出现在第一次真实数值变化或一次缩放手势的早期。
- 当前没有观察到进程崩溃、配置丢失或角色数据损坏。立绘缩放仍能完成，因此它不是当前发布工作的阻断项。
- 已知主要发生在 macOS。Windows 需要保留本文列出的输入穿透和稳定窗口约束，但尚无证据表明 Windows 存在同一种 WKWebView 闪烁。

## 最终用户反馈

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

### 环境

- 平台：macOS 实机。
- 当前代码位置：仓库主工作区。
- 记录时分支：`fix/telemetry-diagnostics-quality`。
- 记录时 HEAD：`e654348c41a7`。
- 修复尝试全部位于未提交工作树。当前分支名称与本缺陷无关，后续不得把整份工作树直接提交到该分支。

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
8. 切换一次立绘或角色，检查原先修好的立绘淡入和双闪问题是否回归。

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

`desktop/frontend/app.js` 负责：

1. `begin_portrait_scale_preview` 登记预览并取得最大倍率临时包络。
2. 第一个真实数值帧调用 `prepare_portrait_scale_preview_snapshot`。
3. 快照安装成功后，先提交新的 WebView stage offset，再调用 `activate_portrait_hit_test` 扩大原生窗口。
4. 新倍率画面经过两个 `requestAnimationFrame` 后调用 `finish_portrait_scale_preview_snapshot`。
5. 手势结束后提交最终精确表面。

`samePetSurfaceGeometry()` 会跳过相同 `contentScale + activeBounds` 的重复 CSS 写入。这个优化能减少无效重绘，但没有消除本次实机闪烁。

### Rust 与 AppKit

- `desktop/src-tauri/src/main.rs`
  - `begin_portrait_scale_preview()` 计算临时 50%～150% 包络。
  - `prepare_portrait_scale_preview_snapshot()` 在不持有 `WindowGeometrySession` 锁时异步等待快照。
  - `activate_portrait_hit_test()` 提交本轮原生表面。
  - `finish_portrait_scale_preview_snapshot()` 按 revision 移除快照。
  - `settle_portrait_scale_surface()` 恢复静止态动态包络。
- `desktop/src-tauri/src/macos_surface_snapshot.rs`
  - 使用 `WKWebView.takeSnapshotWithConfiguration` 捕获当前 WebView。
  - 以 `NSImageView` 把快照放在 WKWebView 上方。
  - 使用 `tokio::sync::oneshot` 等待回调，避免堵塞 Tauri/AppKit 主线程。
  - 快照和清理均带 revision；旧回调不能移除新手势的快照。
- `desktop/src-tauri/src/platform/window_backend.rs`
  - `macos_atomic_frame()` 使用 `setFrame_display(frame, false)`。
  - 原生 frame 改变后重定位快照，使它在屏幕上的全局位置保持不变。
- `desktop/src-tauri/src/window_geometry.rs`
  - `clip_expanded_surface_bounds_to_work_area()` 把临时包络裁到工作区，避免越过屏幕顶部时被 AppKit 整窗向下钳制。

### Windows 保护措施

Windows 路径保留稳定最大包络，并用 `coarse_preview_hit_regions()` 生成“立绘外接矩形 + 可见控件”的临时 region。不能重新采用清空 `SetWindowRgn` 的做法，否则稳定 HWND 的巨大透明余量会拦截其他窗口。

## 已确认和仍未确认的归因

### 已确认

- 窗口下跳来自临时大窗口越过工作区顶部后被 AppKit 调整；工作区裁剪已经消除该位移。
- 左上角闪现来自原生 frame 与前端 stage offset 在不同时间生效；按相同几何提交后，该错误位置不再是当前主要表现。
- 早期同步快照实现会在 Tauri command 内等待 WKWebView 异步回调，同时占有几何锁。日志只有 `installed`，没有 command return；后续 frame 和 gesture-end 全部等待该锁。该死锁已改为异步等待。
- 按下即预扩 WKWebView 会暴露错误切片，输入栏曾瞬间出现在立绘顶部。当前实现不在 pointerdown 时捕获快照或改变原生 frame。
- 最新实机日志中，快照事务能正常完成；用户仍看到概率闪烁。

### 高概率原因，但尚未证明

第一次扩展 `NSWindow/WKWebView` 仍会重建 backing/compositor surface。现有 `NSImageView` 是同一个窗口内容树中的兄弟/覆盖视图，它可能与 WKWebView 一起参与窗口表面重建，所以不能保证每一帧都遮住底层变化。

另一个可疑点是快照移除时机。两个 RAF 只能证明 JavaScript 获得了两次绘制机会，不能证明 WebKit 远程图层已经由 WindowServer 合成到屏幕。日志中的 `finished` 也只代表 `NSImageView` 已移除，不代表替换后的 WKWebView 帧已显示。

当前没有高帧率视频与原生时间戳的逐帧对应关系，无法确认闪烁发生在：

- 快照安装时；
- `setFrame_display(false)` 扩窗时；
- 快照移除时；
- 手势结束后从最大包络收回静止态包络时。

下次继续前先定位到其中一个阶段，不要再同时调整多个时序参数。

## 已尝试方案

### 保留的改动

- macOS 静止态使用动态包络，设置缩放时才计算临时最大包络。
- 临时包络按工作区裁剪，防止顶部钳制造成窗口下跳。
- 缩放结算保持全局物理立绘锚点。
- 相同原生表面和相同前端几何不重复提交。
- Windows 设置预览使用 coarse region，透明余量不接收点击。
- 快照捕获和移除不再同步阻塞 AppKit 主线程，并加入 revision 隔离和设置窗口关闭清理。

这些改动分别解决了真实回归。后续若撤回当前快照实验，应按 hunk 区分，不能整体回退工作树。

### 已撤回或判定不足的方案

- macOS 常驻 150% 最大窗口：缩放稳定一些，但窗口过高，桌宠无法继续向上拖动；静止态也不应长期保留大包络。
- 设置页打开时直接关闭 Windows 遮罩/region：大半屏幕变成不可点击区域，不可接受。
- pointerdown 时直接预扩 WKWebView、保持旧 `NSWindow`：按下即闪，并显示错误 WebView 切片，已撤回。
- AppKit 原生窗口 class swapping：启动阶段触发 KVO crash，已撤回，禁止恢复。
- 仅使用 `setFrame_display(false)`、去重 frame 或提前提交 stage offset：能消除部分错位，不能遮住 backing surface 首次重建。
- 当前同窗 `NSImageView` 快照：解决了同步死锁，日志顺序正确，但未通过用户实机视觉验收。

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

这些测试覆盖几何、revision 和前端状态流，不覆盖 macOS WindowServer 的实际合成结果。实机视觉验收失败，所以 `resolution` 仍为 `unresolved`。

## 当前工作树边界

记录时共有 13 个相关修改文件和 1 个新增文件，约 `1062 insertions / 262 deletions`。主要路径：

```text
desktop/frontend/app.js
desktop/frontend/pet/layout-controller.js
desktop/frontend/pet/layout.js
desktop/frontend/tests/layout-bootstrap.test.js
desktop/src-tauri/Cargo.toml
desktop/src-tauri/Cargo.lock
desktop/src-tauri/src/main.rs
desktop/src-tauri/src/macos_surface_snapshot.rs
desktop/src-tauri/src/platform/window_backend.rs
desktop/src-tauri/src/window_geometry.rs
desktop/src-tauri/src/window_interaction.rs
docs/adr/0010-cross-platform-pet-surface.md
docs/adr/0025-macos-dynamic-surface-envelope.md
docs/specs/runtime-v2/WP-3-03A-cross-platform-pet-surface.md
```

其中 `macos_surface_snapshot.rs`、`objc2-web-kit` 依赖和三个相关 command 属于仍未通过验收的实验。几何裁剪、锚点保持、相同表面去重和 Windows coarse region 是此前回归的有效修复。下次接手应先保存当前 diff，再按这条边界拆分。

带 `SAKURA_TRACE_MACOS_SURFACE=1` 的开发进程已在本记录完成前停止。下次复现需要重新运行 `scripts/start.sh`，不能假定屏幕上残留的 Sakura 就是这份工作树构建出的版本。

## 下次调查建议

1. 先录制高帧率视频，在 `installed`、`repositioned`、`finished` 和最终收缩各写一个单调时钟时间戳，确认闪烁属于哪一阶段。
2. 临时延长快照保留时间，只用于诊断。如果闪烁随移除时间后移，问题在 reveal gate；如果仍发生在扩窗瞬间，同窗快照没有隔离 backing 重建。
3. 若确认同窗快照无效，优先做一个独立无交互透明覆盖窗口的最小 PoC。覆盖窗口必须固定在旧画面的屏幕坐标，并在主窗口扩容完成后移除。不要先引入双窗口常驻架构。
4. 若闪烁发生在最终收缩，保持手势期间最大包络不是问题根源；应单独处理 settle transaction，避免把首帧和松手问题混在一起。
5. 每次实验只修改一个原生阶段，并用 N.A.V.I. 重复至少 20 次。N.A.V.I.-Test 只用于穿透和控件位置检查。
6. macOS 通过后再到 Windows 实机检查透明区域点击穿透、四个布局滑条和立绘缩放。不要用 macOS 结果代替 Windows 验收。

## 关闭条件

只有满足以下条件后才能把本记录改为 resolved：

- N.A.V.I. 在 macOS 实机连续完成 20 轮短拖动和 10 轮 `50% → 150% → 50%`，没有整窗闪烁、错位或下跳。
- 单击/按住滑条但不改值无闪烁，第一次真实数值变化也无闪烁。
- 屏幕顶部拖动、立绘切换淡入、气泡和输入栏位置全部通过回归检查。
- Windows 实机确认稳定 HWND 的透明余量仍可点击穿透，立绘与四个布局滑条无抖动。
- 自动测试继续通过，并保留至少一个覆盖过期 revision 不能清理新快照的回归测试。

在达到这些条件前，不要在提交、PR 或发布说明中写“macOS 立绘缩放闪烁已修复”。
