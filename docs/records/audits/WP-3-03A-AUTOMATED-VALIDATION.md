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
