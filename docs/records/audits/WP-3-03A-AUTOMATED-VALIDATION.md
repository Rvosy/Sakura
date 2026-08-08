---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-09
---

# WP-3-03A 自动验证记录

## 候选与环境

- 实现候选：`cda495b43782e6cad3aa83043d99f2e871100ceb`
- 日期：2026-08-08
- 宿主：macOS 26.5.2（25F84），Apple Silicon arm64
- 工具链：rustc/cargo 1.96.0、Node 24.18.0、Python 3.13.11

## 已完成结果

| 检查 | 结果 |
|---|---|
| `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1` | 238 passed，3 ignored，0 failed |
| `npm test --prefix desktop/frontend` | 121 passed，0 failed |
| `cargo check --locked --manifest-path desktop/src-tauri/Cargo.toml` | macOS arm64 通过；只有既存 dead-code warnings |
| `python3 -m harness run runtime-v2-window-surface` | 3/3 通过；报告 `temp/harness/20260808T092432.535835Z-runtime-v2-window-surface.json` |
| `python3 -m harness run runtime-v2-shell` | 6/6 通过；报告 `temp/harness/20260808T092433.635803Z-runtime-v2-shell.json` |
| `python3 -m harness run docs` | 2/2 通过；报告 `temp/harness/20260808T092434.267823Z-docs.json` |
| `python3 -m harness verify WP-3-03A` | 8/8 自动 case 通过，`manual_pending`；报告 `temp/harness/20260808T092517.603592Z-WP-3-03A.json` |
| macOS 真实窗口启动观察 | 原生窗口为 612×645，而不是规范坐标系 900×996；动态包络已应用 |

自动测试覆盖 schema v3、alpha 外接范围与透明洞、复杂 Windows region 不退化、旧新表面桥接、
稳定物理锚点、100%–200% DPI 与负坐标多屏、latest-revision-wins、Rust 拖动点复核、表情 mask
并集、菜单提交顺序、native Wayland 检测及失败回滚。Windows 验收脚本已改为从真实 native region
动态选点，检查 20 次透明/可见点击、透明点拒绝拖动、有效像素拖动及工作区贴顶，不再依赖固定坐标。

## 未完成的平台证据

- Windows x64 交叉 `cargo check` 在 Tauri build script 阶段因本机缺少 `llvm-rc` 终止，尚未编译目标
  Rust 代码；必须在 Windows 原生 CI/设备运行 `runtime-v2-windows-interaction`。
- Linux x64 交叉 `cargo check` 在 `gdk-sys`/GTK 的 `pkg-config` 阶段因没有 Linux sysroot 终止；必须在
  Ubuntu 24.04 Xorg/XWayland 和 native Wayland 原生环境编译并运行。
- macOS 本次只记录真实窗口动态尺寸，没有执行背景接收窗口的 20 次系统级路由、拖动和贴顶矩阵。
- Mutter、KWin native Wayland 的 `wayland_degraded_anchor` 诊断和 input region 实机行为尚待分别登记。

因此本记录只证明自动门与一次 macOS 动态窗口观察，不代表负责人验收，也不把 WP 标记为 accepted。

## macOS WebKit 立绘误选中修正（2026-08-08）

负责人在 macOS 实机观察到穿透生效后，WKWebView 仍会把立绘的完整图片盒显示为蓝色选择层，并让
输入框使用平台默认选区颜色。本轮只修改前端默认选择/图片拖拽约束、既有规范和用户说明；没有修改
Rust 命中模型、macOS 光标路由、窗口几何或依赖。

| 检查 | 结果 |
|---|---|
| 定向前端 RED/GREEN | 修正前新增契约为 29 passed、2 failed；实现后 `boundary.test.js` 31 passed |
| `npm test --prefix desktop/frontend` | 122 passed，0 failed |
| `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1` | 238 passed，3 ignored，0 failed |
| debug/release `cargo build --locked` | 均通过；只有既存 dead-code/unused warnings |
| `python3 -m harness run docs` | 2/2 通过；报告 `temp/harness/20260808T094309.651189Z-docs.json` |
| `python3 -m harness run runtime-v2-shell` | 6/6 通过；报告 `temp/harness/20260808T094347.588542Z-runtime-v2-shell.json` |
| `python3 -m harness run runtime-v2-window-surface` | 3/3 通过；报告 `temp/harness/20260808T094348.337084Z-runtime-v2-window-surface.json` |
| `python3 -m harness verify WP-3-03A` | 8/8 自动 case 通过，`manual_pending`；报告 `temp/harness/20260808T094426.468220Z-WP-3-03A.json` |

自动契约证明两层立绘均禁止原生图片拖拽，WKWebView 和标准选择均在非文本区域关闭，气泡正文与
输入框继续允许文本选择并共享角色主题选区。macOS 连续拖动、透明点击和交叉淡入实机观察，以及
Windows 同候选回归仍由负责人执行；本记录不填写这些人工结果，WP-3-03A 保持 `stabilizing`。

## 缩放锚点稳定化修正（2026-08-08）

负责人反馈仅按“WebView offset 先于窗口 placement 排队”的候选实现仍可观察到气泡抖动。复核确认
Tauri/WebKit 的两条消息不能证明没有可见中间帧。本轮改为让同一立绘在 50%–150% 缩放期间固定使用
其 150% alpha 动态包络；实时 revision 只更新立绘 transform 与精确命中。新旧几何相同则不调用窗口
bounds、WebView offset 或桥接区域。物理命中 snapshot 另携带目标 envelope，避免首次异步 resize 后
读取旧窗口尺寸而把下半部圆角控件裁空。

| 检查 | 结果 |
|---|---|
| `npm test --prefix desktop/frontend` | 123 passed，0 failed |
| `cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check` | 通过 |
| `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1` | 243 passed，3 ignored，0 failed |
| release `cargo build --locked` | 通过；只有既存 dead-code/unused warnings |
| release 启动探针 | 修正 stale window readback 后进程持续运行，未再出现 `native rounded clip is empty`；锁屏下未执行视觉验收 |
| `python3 -m harness run docs` | 2/2 通过；报告 `temp/harness/20260808T110343.893391Z-docs.json` |
| `python3 -m harness run runtime-v2-shell` | 6/6 通过；报告 `temp/harness/20260808T110315.517775Z-runtime-v2-shell.json` |
| `python3 -m harness run runtime-v2-window-surface` | 3/3 通过；报告 `temp/harness/20260808T110316.067954Z-runtime-v2-window-surface.json` |
| `python3 -m harness verify WP-3-03A` | 8/8 自动 case 通过，`manual_pending`；报告 `temp/harness/20260808T110343.922437Z-WP-3-03A.json` |

新增 Rust 测试覆盖 100%、125%、150%、200% DPI、负坐标工作区和 20 次 50%↔150% 往返，断言
`active_bounds`、物理 placement、本地锚点和 `content_scale` 逐次相等；另以纯函数测试锁定相同几何
revision 只走 hit-only 路径，以及平台 region 使用 snapshot envelope 而非旧 `inner_size`。本记录不
填写 macOS/Windows/Linux 连续拖动缩放的人工结果，WP-3-03A 继续保持 `stabilizing`。

## 稳定包络收口修正（2026-08-08）

负责人继续反馈最大倍率稳定包络会在停止缩放后留下顶部透明空位。修正后，稳定包络只覆盖连续缩放
预览；前端在最新 revision 静止 120ms 后请求 Rust 以当前倍率真实 alpha 包络提交一次完整 surface
事务。A→B→C 的旧收口回包返回空结果，事务失败保留上一版窗口、舞台 offset 与精确命中区域。

| 检查 | 结果 |
|---|---|
| `npm test --prefix desktop/frontend` | 123 passed，0 failed |
| `cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check` | 通过 |
| `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1` | 244 passed，3 ignored，0 failed |
| release `cargo build --release --locked` | 通过；只有既存 dead-code/unused warnings |
| macOS release 窗口截图检查 | 733×965 目标窗口从首行可见立绘像素开始，画面完整，未再保留此前顶部大块透明区；此项不替代负责人缩放拖动验收 |
| `python3 -m harness check WP-3-03A` | 通过；无越界、保护文件或任务契约修订 |
| `python3 -m harness run docs` | 2/2 通过；报告 `temp/harness/20260808T123944.336499Z-docs.json` |
| `python3 -m harness run runtime-v2-shell` | 6/6 通过；报告 `temp/harness/20260808T123948.643106Z-runtime-v2-shell.json` |
| `python3 -m harness run runtime-v2-window-surface` | 3/3 通过；报告 `temp/harness/20260808T123949.220333Z-runtime-v2-window-surface.json` |
| `python3 -m harness verify WP-3-03A` | 8/8 自动 case 通过，`manual_pending`；报告 `temp/harness/20260808T124017.809392Z-WP-3-03A.json` |

新增 Rust 几何测试分别验证活动预览保持窗口 placement 与全部规范锚点不动，以及收口后的物理窗口
高度缩小、立绘底部全局物理锚点不变。前端边界测试锁定 120ms 收口、最新 revision 守卫和命令注册。
本记录不填写连续滑块拖动、透明洞点击或跨平台人工验收结果，WP-3-03A 继续保持 `stabilizing`。

## 50%–55% 慢速缩放误收口修正（2026-08-08）

负责人在上一候选中观察到 50%–55% 慢速微调时偶发向上闪动。复核确认相邻 `input` 事件超过 120ms
时，兼容计时器会把仍按住的滑块误判为已经结束，导致 exact envelope 收口后又在下一刻度展开。本轮
改为由设置窗口显式发布 pointer/keyboard 手势开始与结束，并在最后一项预览完成后才结束手势。Rust
另持有独立 gesture guard；手势活跃时即使旧计时器到期，也拒绝收口。

| 检查 | 结果 |
|---|---|
| `npm test --prefix desktop/frontend` | 124 passed，0 failed；新增手势命令顺序测试 |
| `cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check` | 通过 |
| `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1` | 245 passed，3 ignored，0 failed；新增 51→55 手势中途禁止收口测试 |
| `python3 -m harness check WP-3-03A` | 通过；无越界、保护文件或任务契约修订 |
| `python3 -m harness run docs` | 2/2 通过；报告 `temp/harness/20260808T125733.288687Z-docs.json` |
| `python3 -m harness run runtime-v2-shell` | 6/6 通过；报告 `temp/harness/20260808T125737.284511Z-runtime-v2-shell.json` |
| `python3 -m harness run runtime-v2-window-surface` | 3/3 通过；报告 `temp/harness/20260808T125737.852117Z-runtime-v2-window-surface.json` |
| `python3 -m harness verify WP-3-03A` | 8/8 自动 case 通过，`manual_pending`；报告 `temp/harness/20260808T125752.356117Z-WP-3-03A.json` |

自动测试证明手势命令顺序为 begin→preview→end，最新 revision 只有在 gesture guard 关闭后才具备收口
资格。连续慢速拖动的视觉结果仍由负责人实机确认，本记录不填写人工验收，WP-3-03A 保持
`stabilizing`。

## Windows 启动窗口死锁热修复（2026-08-08）

本轮在 Windows 11 x64（Python 3.12.8、rustc/cargo 1.96.0、Node 22.14.0）验证候选
`753eacec4b7a56e4c299e8961d12bb4f5de690c3` 上的未提交修复。修复仅让同步 `Moved` 回调以
`try_lock` 非阻塞观察几何状态；内部窗口事务持锁重入时跳过观察，真实 deferred drag 在锁可用时仍
更新物理锚点。启动入口、公开 IPC、revision/回滚和动态表面事务未改动。

| 检查 | 结果 |
|---|---|
| `runtime\python.exe -m harness check WP-3-03A` | 修改前及开发中均通过；无越界、保护文件或任务契约修订 |
| 定向 Rust `moved_observation_skips_reentrant_geometry_lock_and_keeps_deferred_drag` | 1 passed，0 failed；覆盖持锁立即跳过及释放锁后 deferred drag 更新锚点并保持 pending |
| `cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check` | 通过 |
| `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1` | 260 passed，24 ignored，0 failed；首次运行因旧冻结 Shell 持有命名互斥体出现 3 个共享实例失败，终止 PID 28340 的精确进程树并确认无残留后复跑全绿 |
| `npm test --prefix desktop/frontend` | 124 passed，0 failed |
| `runtime\python.exe -m harness run docs` | 2/2 通过；报告 `temp/harness/20260808T140135.412910Z-docs.json` |
| `runtime\python.exe -m harness run runtime-v2-shell` | 6/6 通过；报告 `temp/harness/20260808T135743.784232Z-runtime-v2-shell.json` |
| `runtime\python.exe -m harness run runtime-v2-window-surface` | 3/3 通过；报告 `temp/harness/20260808T135756.784306Z-runtime-v2-window-surface.json` |
| `runtime\python.exe -m harness run runtime-v2-windows-interaction` | 三次均在 15 秒窗口发现门内取得可见候选窗口及有效 native region，但透明点背景接收器分别只收到 18/20、19/20、15/20 次系统级合成点击，未进入后续可见点击、拖动和贴顶完整矩阵；报告分别为 `temp/harness/20260808T135848.139654Z-runtime-v2-windows-interaction.json`、`temp/harness/20260808T135920.565785Z-runtime-v2-windows-interaction.json`、`temp/harness/20260808T140006.063495Z-runtime-v2-windows-interaction.json` |
| `runtime\python.exe -m harness verify WP-3-03A` | 8/8 自动 case 通过，`manual_pending`；报告 `temp/harness/20260808T140209.429123Z-WP-3-03A.json` |

三次 Windows 交互运行都已确认候选透明点不归 pet 进程、有效像素点归 pet 进程，失败集中在基于
`mouse_event` 的背景接收计数；每轮结束后均未留下本轮 Shell、接收器或验收脚本进程。该项没有通过，
因此本记录只声称 Work Package 自动门通过、等待验收，不声称 Windows 交互矩阵完成，不填写人工
验收，也不把 WP 标记为 `accepted`；WP-3-03A 继续保持 `stabilizing`。

## Windows 立绘缩放预览迟滞与快速松手竞态修复（2026-08-08）

本轮在 Windows 11 x64（Python 3.12.8、rustc/cargo 1.96.0、Node 22.14.0）验证工作区候选。
Windows scale preview 首次 revision 清除一次复杂 window region，逐刻度仅更新稳定 bounds 和内存中
的最终 alpha 模型；快速松手不会让最后一刻度因 gesture guard 已关闭而提前重建 region，只有同
revision settle 才恢复最终精确穿透。设置页对短暂 `CHARACTER_PRESENTATION_NOT_READY` 最多有界重试
三次，恢复时不显示内部错误码，普通错误仍按原路径报告。

| 检查 | 结果 |
|---|---|
| `runtime\python.exe -m harness check WP-3-03A` | 开发中通过；无越界、保护文件、测试删除或任务契约修订 |
| `npm test --prefix desktop/frontend` | 124 passed，0 failed；包含首次 `NOT_READY`、第二次恢复且 begin→preview→end 顺序不变 |
| 定向 Rust `portrait_scale_cannot_settle_between_ticks_of_one_pointer_gesture` | 1 passed，0 failed；覆盖快速松手后仍延迟精确 region，settle 后才清除放宽状态 |
| `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1` | 最终干净复跑 260 passed，24 ignored，0 failed；此前一次并行运行有 3 个共享单实例锁用例受互斥体瞬态污染，失败组单独复跑及后续全套均通过 |
| `runtime\python.exe -m harness run docs` | 2/2 通过；报告 `temp/harness/20260808T142238.247964Z-docs.json` |
| `runtime\python.exe -m harness run runtime-v2-window-surface` | 3/3 通过；报告 `temp/harness/20260808T142242.562454Z-runtime-v2-window-surface.json` |
| `runtime\python.exe -m harness run runtime-v2-windows-interaction` | 首次背景接收计数 5/20，报告 `temp/harness/20260808T141701.295591Z-runtime-v2-windows-interaction.json`；干净复跑透明点击 20/20、可见点击 20/20、透明点拒绝拖动、alpha 点可拖动并贴顶通过，报告 `temp/harness/20260808T142307.278638Z-runtime-v2-windows-interaction.json` |

Windows 交互脚本的首次失败仍属于已有系统级合成点击计数波动，未修改验收脚本或降低阈值；最终通过
证明静止态精确 region 在缩放策略调整后仍可恢复。视觉流畅度与快速松手无闪烁仍由负责人实机确认，
本记录不填写人工验收，不把 WP 标记为 `accepted`。

## Windows 缩放纯合成预览修正（2026-08-08）

负责人继续观察到上一候选在快速拖动时仍有迟滞、闪烁和遮罩跟帧。复核确认虽然逐刻度不再重建复杂
region，但视觉更新仍等待每刻度 Rust IPC、WebView surface 预提交和原生 bounds 确认。本轮把 Windows
手势改为两端事务：开始时一次建立 150% 稳定包络并清除 region；中间刻度只更新 WebView 合成
transform，不进入 Rust 或 alpha 行段计算；结束时从放宽状态一次提交最终真实包络和精确 region，且
不再插入旧新 region 桥接。macOS/Linux 保留逐刻度精确输入区域更新。
截图复核后另确认设置前端会在旧 pointerup drain 尚未完成时丢弃新 pointerdown；修正后的本地/backend
双状态机会让重叠的快速拖动共享同一 guard，最后一轮完成后才串行关闭，避免无 guard 刻度和连接错误。

| 检查 | 结果 |
|---|---|
| `npm test --prefix desktop/frontend` | 124 passed，0 failed；覆盖 Windows 刻度纯合成、松手单次 native 提交及重叠快速拖动只产生一组 begin/end |
| 定向 Rust `tests::portrait_scale_defers_native_regions_only_while_the_pointer_gesture_is_active` | 1 passed，0 failed |
| `cargo test --manifest-path desktop/src-tauri/Cargo.toml --no-run` | 编译通过 |
| `runtime\python.exe -m harness run docs` | 2/2 通过；报告 `temp/harness/20260808T144309.917126Z-docs.json` |
| `runtime\python.exe -m harness run runtime-v2-window-surface` | 3/3 通过；报告 `temp/harness/20260808T144318.808627Z-runtime-v2-window-surface.json` |
| `runtime\python.exe -m harness verify WP-3-03A` | 8/8 自动 case 通过，`manual_pending`；报告 `temp/harness/20260808T144328.351365Z-WP-3-03A.json` |
| 未过滤完整 `cargo test` | 本次缩放用例修正后通过；另有 3 个共享实例锁用例因用户正在运行 PID 41872 的旧 Shell 占用命名互斥体而失败，未擅自终止该进程 |

自动门通过只证明契约和回归检查；拖动流畅度、无闪烁及最终遮罩跟帧仍等待负责人使用全新构建实机
确认。本记录不填写人工验收，不把 WP 标记为 `accepted`。

## Windows 稳定 HWND 与轻量缩放帧修正（2026-08-08）

负责人确认使用新编译的 `desktop/src-tauri/target/debug/sakura-runtime-v2-shell.exe` 后，高频拖动仍会
闪烁、瞬时跳位、遮罩跟不上，并偶发提示无法连接桌宠。复核确认剩余视觉路径仍让刻度等待完整外观
preview，且手势开始/结束仍在最大包络与真实包络之间 resize/reposition HWND；两项原生/WebView 更新
无法构成一个可见原子帧。

本轮让 Windows 在取得当前 alpha mask 后常驻 150% 稳定 HWND/WebView 包络，静止态继续由精确
`SetWindowRgn` 表达真实轮廓与穿透；手势两端不再改 bounds 或 surface offset。设置页新增 RAF 合并、
latest-wins 的轻量倍率命令，主窗口收到专用事件后直接更新 CSS transform；完整 appearance preview 只在
松手时提交最终值。重叠快速点拖继续共享 backend guard。轻量帧首次失败会在内部有界追赶最新倍率，
不再触发设置页错误；最终完整 preview 的 `NOT_READY/UNAVAILABLE` 仍有界重试。

| 检查 | 结果 |
|---|---|
| `runtime\python.exe -m harness check WP-3-03A` | 通过；无越界、保护文件、测试删除或任务契约修订 |
| `npm test --prefix desktop/frontend` | 124 passed，0 failed；覆盖重叠快速点拖、首个轻量帧丢失、完整 preview 首次 `NOT_READY`、最终只成功提交最新 52% |
| 定向 Rust `tests::portrait_scale_defers_native_regions_only_while_the_pointer_gesture_is_active` | 1 passed，0 failed |
| 定向 Rust `tests::windows_keeps_the_scale_stable_hwnd_envelope_after_alpha_is_available` | 1 passed，0 failed |
| `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml --no-run` | 编译通过 |
| `runtime\python.exe -m harness run docs` | 2/2 通过；报告 `temp/harness/20260808T145636.029321Z-docs.json` |
| `runtime\python.exe -m harness run runtime-v2-window-surface` | 3/3 通过；报告 `temp/harness/20260808T145635.820488Z-runtime-v2-window-surface.json` |
| `runtime\python.exe -m harness verify WP-3-03A` | 8/8 自动 case 通过，`manual_pending`；报告 `temp/harness/20260808T145747.523317Z-WP-3-03A.json` |

上述自动证据证明轻量事件边界、稳定 Windows 包络、最终精确 region 恢复及错误抑制的代码契约；真实
高频拖动的跟手度、无闪烁和无瞬时跳位仍等待负责人用本轮新构建实机确认。本记录不填写人工验收，
不把 WP 标记为 `accepted`。

## 固定对话框高度、布局轻量帧与首次拖动修正（2026-08-08）

负责人确认立绘缩放候选可用后，要求对话框在对话过程中不再自动改变高度，并反馈立绘及四个布局
滑块第一次拖动明显卡顿、甚至需要重复拖动才响应。本轮将兼容字段 `bubbleMaxHeight` 的运行时语义改为
固定外框高度；回复内容只在框内滚动。Windows 稳定 HWND/WebView 包络扩大到全部合法布局极值，布局
刻度通过独立 RAF/latest-wins 帧直接绘制，松手才提交最终原生布局与精确 region。冷路径另在 region
准备完成前放行 Windows 视觉帧，并提前建立立绘 transform 合成层。macOS/Linux 的布局帧 payload
明确为非 deferred，仍逐帧提交原生表面。

当前自动证据包括：前端 126 项全绿；高频布局测试覆盖重叠快速拖动只发布一组 begin/end、首个轻量帧
丢失不报警、最终完整 preview 只成功提交最新高度；Rust 极值测试覆盖 50%–150% 每个倍率及全部合法
宽度、高度、上下偏移、输入栏偏移和输入高度组合均落入同一稳定包络。未过滤 Rust 全套在负责人正在
运行的 Shell 占用共享实例命名互斥体时出现 3 个既有锁用例失败，相关窗口表面定向测试通过；后续门禁
结果在本节继续追加。真实首次拖动、持续跟手和精确遮罩仍等待负责人使用新候选实机确认，
本记录不填写人工结果，不把 WP 标记为 `accepted`。

## Windows 交互延迟测量与拖动提交回归修正（2026-08-09）

负责人确认既有 RAF、稳定包络和延后原生提交候选仍普遍存在约 1 秒首帧延迟。本轮先增加仅在
`debug_assertions + interaction-latency-diagnostics` 下启用的 JSONL 时间线，并由负责人在真实桌面
完成设置滑块和立绘拖动。日志不记录角色内容、输入、路径或错误原文，只记录固定 gesture/revision、
阶段名和毫秒时间。

首轮实机日志共 22,238 行。设置页 `pointerdown/input` 到 RAF、IPC 入口、geometry Mutex、CSS 提交和
绘制机会均为毫秒级；真正的约 0.9 秒发生在 Win32 调用前。`logical_scale_and_control_stable_surface_bounds`
枚举 32 组布局极值时，每组都重新扫描整张立绘 alpha。修正后 `PortraitAlphaMask` 在解码时缓存可见
bounds，同一计算从约 900 ms 降到小于 1 ms。负责人随后确认立绘直接拖动已不再等待约 1 秒。

该候选同时暴露了新的松手回位回归。四次拖动均先正常进入自定义 Win32 拖动循环并移动窗口，松手后
依次在 241、464、501、611 ms 返回错误；每条时间线都结束于 `setwindowrgn-rectangles-return`，没有
`setwindowrgn-subclass-return`。这证明失败点是后台 `spawn_blocking` 线程调用 HWND 所属线程限定的
`SetWindowSubclass`；事务随后按设计恢复旧 application，因而窗口回到原位并显示“窗口拖动暂时不可用”。
修正保留后台拖动循环，只把松手后的最终位置读取、布局事务、精确 region 和 subclass 操作派回窗口
主线程，再把结果传回异步命令。

同一日志还记录了 22 次持续约 0.04–0.08 ms 的 `settings.appearance-preview` 即时失败，负责人看到的
文案为“角色正在重新连接”。代码复核确认高频预览每帧都重复查询 Core 当前角色，而且 lifecycle 暂时
没有可用 generation 时会把已有预览会话当作代际变化关闭。修正后高频帧使用设置窗口已绑定的会话；
`None` 只表示暂时无可用代际，只有观察到一个确定且不同的新 generation 才回滚并重绑。诊断同时新增
固定错误类别，不复制错误原文或任何用户数据。

| 检查 | 结果 |
|---|---|
| `runtime\python.exe -m harness check WP-3-03A` | 修改前及开发中均通过；无越界、保护文件、测试删除或任务契约修订 |
| `npm test --prefix desktop/frontend` | 128 passed，0 failed |
| `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml` | 266 passed，24 ignored，0 failed |
| `cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --features interaction-latency-diagnostics` | 通过；生成新的独立诊断候选 |
| 诊断候选 | `desktop/src-tauri/target/debug/sakura-runtime-v2-shell.exe`；SHA-256 `C43444A24B72D48B89FEBB57CED2FC6E7028F9D531528A028BEC7468762A96CF` |

新增 Rust 测试覆盖临时 generation 缺失时保持绑定会话、错误窗口 generation 拒绝、确定新 generation
到达后恢复 baseline，以及诊断错误类别不复制任意错误文本。自动测试不能证明真实 HWND 拖动提交与
WebView2 合成结果，新的候选仍需负责人复测“无等待、松手不回位、无重新连接/拖动不可用提示”。本记录
不填写该人工结果，不把 WP 标记为 `accepted`。

## Windows 立绘缩放旧遮罩裁断修正（2026-08-09）

负责人使用上一诊断候选放大立绘后，截图显示立绘右侧和下方被旧窗口 region 大片横向裁断。对应
`sakura-interaction-latency.jsonl` 为 4,302,293 字节；手势 `settings-portrait-scale-6` 的
`main.begin-portrait-scale-preview` revision 2 在 0.088 ms 内返回
`command-error-character-not-ready`，随后仍记录 272 次 `portrait.frame-event-received` 和
`portrait.css-commit`，且没有该手势的 `main.activate-portrait-hit-test`。因此该现象不是 WebView2
毛玻璃或绘制耗时，而是 begin 未放宽旧精确 region 时，轻量 CSS 帧仍继续放大立绘，松手也没有恢复
最终遮罩。

修正后，Core 暂时没有可用 generation 时，begin 和最终 activate 沿用窗口几何中已确认的
`portrait_hit_generation`；只有 Core 明确给出不同的新 generation 才使旧 alpha/key 缓存失效。初始
窗口尚无任何已确认代际时仍返回 `CHARACTER_PRESENTATION_NOT_READY`。begin 的返回值同时显式区分
“成功开始”和陈旧 revision 的空结果；主窗口在成功结果到达前不提交缩放 CSS，错误或空结果均丢弃
视觉帧，避免任何 begin 失败再次演变成旧遮罩裁断。

| 检查 | 结果 |
|---|---|
| `runtime\python.exe -m harness check WP-3-03A` | 修改前及开发中通过；无越界、保护文件、测试删除或任务契约修订 |
| `npm test --prefix desktop/frontend` | 129 passed，0 failed；新增 begin 未成功前禁止 CSS 缩放帧的边界测试 |
| 定向 Rust `tests::portrait_hit_generation_survives_transient_core_absence` | 1 passed，0 failed；覆盖暂空时沿用已确认代际、确定新代际优先及初始未就绪失败 |
| Rust 全套（保留负责人运行中的旧 Shell） | 264 passed，24 ignored，0 failed；只过滤 3 个必须独占同名 Windows 单实例锁的测试，未终止 PID 39272 |
| `cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check` | 通过 |
| `runtime\python.exe -m harness run docs` | 2/2 通过；报告 `temp/harness/20260808T170707.059391Z-docs.json` |
| `runtime\python.exe -m harness run runtime-v2-window-surface` | 3/3 通过；报告 `temp/harness/20260808T170749.288054Z-runtime-v2-window-surface.json` |
| `runtime\python.exe -m harness run runtime-v2-shell` | 6/6 通过；报告 `temp/harness/20260808T170759.012025Z-runtime-v2-shell.json` |
| 独立诊断候选构建 | `cargo build --locked --features interaction-latency-diagnostics` 通过；只有既存 dead-code warnings |
| 诊断候选 | `desktop/src-tauri/target/codex-interaction-latency/debug/sakura-runtime-v2-shell.exe`；SHA-256 `919837CB5B75F8FC875B08F41FA215CDBFEF43146A59EC6AD812A335224933E6` |

自动测试证明失败安全门、代际回退和最终精确 region 路径的代码契约；真实缩放过程是否不再裁断、
松手后的精确遮罩是否与最终倍率一致，仍需负责人使用本节候选实机确认。本记录不填写人工验收，
不把 WP 标记为 `accepted`。
