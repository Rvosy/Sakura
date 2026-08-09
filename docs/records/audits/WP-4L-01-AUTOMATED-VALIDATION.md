---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-10
---

# WP-4L-01 Runtime v2 迁移可观测性自动验证记录

## 候选与环境

2026-08-10，在 Windows x64、分支 `refactor/tauri-runtime-v2` 上验证实现候选
`3676d5c723b19ee2158087ad5ed383f6a5a9b07a`。固定 base 为
`6843dd40e9513d8015acde8db39fe93eedb2a134`；Work Package 当前状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。本记录只写入已经执行的自动证据，
不预填实机操作或三平台 CI 结果，也不把候选标记为 `accepted`。

候选按 [normative Spec](../../specs/runtime-v2/WP-4L-01-runtime-observability.md) 建立 Rust 唯一文件
writer，统一追加 v2 JSONL，并接入 Shell、Core lifecycle/IPC、Python stderr bridge、受控 WebView、
Memory 和 interaction latency。共享锁冲突实例不启动 writer；所有来源在持久化前经过固定字段门和
最终脱敏，日志拥塞、磁盘故障及退出超时不改变产品控制流。旧 `memory-initialization.jsonl` 保留且
Runtime v2 不再续写。

## 自动结果

- `runtime\python.exe -m harness check WP-4L-01`：依赖、固定 base、allowlist、全局保护边界、测试删除、
  task 修订和 activation 关闭检查全部通过；越界文件为 0。
- `runtime\python.exe -m harness verify WP-4L-01`：报告
  `temp/harness/20260809T184542.371411Z-WP-4L-01.json` 绑定候选 SHA，机器状态为
  `manual_pending`；14/14 唯一自动 case 通过，failed、pending 和 blocked 均为 0。Harness 按 task v2
  去重后执行 `docs` 2/2、`runtime-v2-shell` 6/6、`python-full` 3/3 和
  `journey-observability` 3/3；`python-full` 已覆盖与其重叠的 `smoke`、`core-host` case。
- Python 全量：unit 645 passed/6 skipped，integration 53 passed/2 skipped，Legacy Qt UI 24 passed。
  Observability Python journey 另以同一自动门确认 10 passed，包括真实 Core、stdout 协议纯净、stderr
  饱和退出、operation 关联和 Memory 旧文件不变。
- Runtime v2 前端全量 142 passed；其中受控诊断 journey 5 passed，确认 invoke 参数、结果和 rejection
  对象语义不变，批次不接收任意 attributes，诊断失败不影响产品 command。
- Observability Rust journey 11 passed；同一候选另运行
  `cargo test --locked --manifest-path desktop\src-tauri\Cargo.toml -- --test-threads=1`，结果为
  290 passed、24 个正式平台 fixture 测试 ignored、0 failed。
- `cargo check --locked`、`cargo fmt --check` 和 `git diff --check` 通过，Rust 编译无 warning；Windows
  实机验收脚本已通过 PowerShell 语法检查，但本记录没有把语法检查冒充实机执行。

## 最终自动门与剩余人工项

自动门通过后，WP-4L-01 进入 `stabilizing`。尚未执行的真实 Windows 验收包括：使用隔离 assistant
root 完成启动、聊天、设置、Tools、Core crash/recovery、正常退出和第二实例冲突；扫描全部轮转日志，
确认凭据、聊天正文、工具参数、绝对路径及 generation credential sentinel 为零命中；确认旧 Memory
日志逐字节不变、真实 `data/**` 清单不变、共享锁可立即重取且无进程残留。

同一候选 SHA 的 Windows x64、macOS arm64 和 Linux x64 Runtime v2 CI 也尚未在本地记录中声称完成。
上述实机与 CI 证据由项目负责人审阅前，不得把 WP-4L-01 标记为 `accepted`，也不得开始依赖它的
WP-4-03 生产实现。
