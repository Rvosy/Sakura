---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-03
---

# WP-3V-01 CI 稳定化本地验证记录

## 候选与范围

2026-08-03，在分支 `refactor/tauri-runtime-v2` 对候选 `2a0eb94b` 完成本地验证。负责人治理锚点为
`82732ec1`，批准范围见
[`WP-3V-01-CI-STABILIZATION-OWNER-APPROVAL.md`](WP-3V-01-CI-STABILIZATION-OWNER-APPROVAL.md)。
当前 Work Package 状态仍只以 [`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

候选只修改 WP-3V-01 验收设施、三平台 workflow、回归测试和开发文档：POSIX 进程表把数值列置于
命令名前并保留含空格的完整命令名；Legacy 参考验证改由拒绝 PySide6 的独立 headless 数据/锁 oracle
执行。未修改 Legacy Qt 入口、生产 Supervisor/Router/Gateway/Core、共享锁协议、用户数据或依赖锁文件。

## 本地结果

- `runtime\python.exe -m harness preflight WP-3V-01`：通过；依赖、范围、冻结治理文件均通过，
  `owner-review files: none`。
- `runtime\python.exe -m pytest tests\integration\test_wp_3v_01_assistant_architecture.py -q`：
  7 passed；覆盖含空格 POSIX 命令名、oracle 环境隔离和冻结历史只读 manifest。
- `desktop\tests\windows_wp_3v_01_assistant_architecture_acceptance.ps1`：`status=passed`；
  `provider_requests=4`、`core_kills=1`、`cancel_terminals=1`、新 generation 水合成功；headless oracle
  立即重获生产共享锁并兼容回读；仅 `data/chat_history/fixture.jsonl` 变化，敏感证据和进程残留均为 0，
  验收临时根已删除。
- `runtime\python.exe -m harness verify WP-3V-01`：报告
  `temp/harness/20260802T172814Z-WP-3V-01.json`；5/5 required profiles 通过，24 项自动检查 passed、
  0 failed，3 项负责人验收 pending，最终状态为 `manual_pending`。
- `cargo test --manifest-path src-tauri/Cargo.toml --locked -- --test-threads=1`：239 passed、
  24 ignored、0 failed。
- `cargo fmt --manifest-path src-tauri/Cargo.toml -- --check`、release build、workflow YAML 解析、
  docs profile、Python `py_compile` 和 `git diff --check`：通过。release build 仅有既有
  `unused_mut`/`dead_code` warning。

## 尚待远端证据

本地 Windows 结果和合成 macOS 进程表回归不能替代同一候选 SHA 的原生三平台 workflow。候选推送后
仍须复核 Windows x64、macOS arm64、Linux x64 的 RuntimeLocator/纵向场景和 Harness job；通过前不把
WP-3V-01 标记 accepted，不更新 CAP-004，也不代填三项人工验收。
