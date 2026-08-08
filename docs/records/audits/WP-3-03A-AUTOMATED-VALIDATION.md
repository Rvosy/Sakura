---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-08
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
