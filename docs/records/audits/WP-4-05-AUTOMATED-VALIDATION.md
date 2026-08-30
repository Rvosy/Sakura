---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-17
---

# WP-4-05 TTS、播放与音频设备门禁自动验证记录

## 候选与收口纠正

最终自动化收口候选为 `b609ab83611ea59e60522de56182787db3427c08`，分支
`refactor/tauri-runtime-v2`。本轮修复了 Linux 对 Windows verbatim path 的等价判断、WP-3V-01 运行日志
清单、TTS bundle 在完成日志落盘前过早发布终态、Windows 原子替换瞬时共享冲突，以及仅在对应平台使用
的 Rust 项未受 `cfg` 约束产生的编译警告。

CI 的 pytest 最小依赖环境改为显式加载所需插件，Harness 自测使用独立最小配置；三平台 Rust 编译统一以
`RUSTFLAGS=-D warnings` 执行。因此警告会直接使平台工作流失败，而不是只依靠人工读日志。

## 本机自动结果

2026-08-17 前的最终本机 Windows x64 运行结果如下：

- `core-host`：4/4，报告 `temp/harness/20260816T182635.039791Z-core-host.json`；
- `runtime-v2-shell`：6/6，报告 `temp/harness/20260816T182639.571619Z-runtime-v2-shell.json`；
- `journey-tts`：3/3，报告 `temp/harness/20260816T182642.528548Z-journey-tts.json`；
- `journey-observability`：3/3，报告 `temp/harness/20260816T182645.551619Z-journey-observability.json`；
- `python-full`：3/3，报告 `temp/harness/20260816T182806.598249Z-python-full.json`；其中 Python unit
  701 passed、6 skipped，integration 60 passed、2 skipped，Qt UI 24 passed；
- `docs`：2/2，报告 `temp/harness/20260816T185115.950316Z-docs.json`；
- `smoke`：2/2，报告 `temp/harness/20260816T185119.994116Z-smoke.json`；
- `RUSTFLAGS=-D warnings cargo check --all-targets` 与 `cargo test --no-run` 通过，0 warning；TTS Core
  单测与 Windows 原子替换回归分别连续运行 5 轮通过。

## CI 与警告门

同一候选的 Test 工作流 run `31965843365` 全部通过，完整日志扫描得到 0 条 Rust/Python 配置或弃用警告。
Runtime v2 Platform Foundation 工作流 run `31965843335` 在 Windows x64、macOS arm64、Linux x64 上
全部通过；三个原生构建均受 `-D warnings` 约束，完整日志扫描得到 0 条 warning。

本记录只保存自动证据。真实默认音频设备与项目负责人结论见
[`WP-4-05-OWNER-ACCEPTANCE.md`](WP-4-05-OWNER-ACCEPTANCE.md)。
