---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-03
---

# WP-3V-01 本地自动验证记录

## 候选与范围

2026-08-03，在分支 `refactor/tauri-runtime-v2` 对恢复后的实现候选 `43b9b731` 完成本地自动验证。
WP-2-01 Router 稳定化 `fab46beb` 已由项目负责人重新验收；接受治理提交为 `60fcc79d`，WP-3V-01
第二次 activation 为 `6d91c283`。当前状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

本轮没有修改产品协议、Gateway、Provider 时序、用户数据或 Legacy Qt 产品方向。`43b9b731` 只对
WP-3V-01 验收模块应用 rustfmt；真实行为候选包含此前的组合门、诊断修正和已 accepted Router 修复。

## 真实 Windows 组合验收

命令：

```powershell
.\desktop\tests\windows_wp_3v_01_assistant_architecture_acceptance.ps1
```

结果：

```json
{"acceptance_root_removed":true,"cancel_terminals":1,"changed_paths":["data/chat_history/fixture.jsonl"],"core_kills":1,"fixture_files":28,"generation_rehydrated":true,"legacy_oracle":"read-compatible","process_residue":0,"provider_requests":4,"sensitive_evidence":0,"status":"passed"}
```

该结果证明真实 bundled Python Core、无 Qt Assistant Adapter、Chat Pipeline、Rust Gateway 和 Tauri
验收表面完成回复、取消、并发 health、Core 强杀、新 generation 水合、shutdown、共享锁重获及 Legacy
oracle 回读。临时根已删除，只有预声明的隔离 fixture history 发生变化；没有修改仓库真实 `data/**`、
`characters/**` 或 `third_party/**`。

## 本地门禁

- `cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check`：通过。
- `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked -- --test-threads=1`：239 passed、
  24 ignored、0 failed。
- `cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked --release`：通过；只有既有
  unused/dead-code warning。
- `runtime\python.exe -m harness verify WP-3V-01`：报告
  `temp/harness/20260802T165927Z-WP-3V-01.json`，preflight/scope/dependencies 全部通过；docs、smoke、
  core-host、runtime-v2-shell、python-full 五个 profiles 全绿；24 项自动检查通过、0 失败、3 项人工
  验收 pending。
- Python 汇总：unit 581 passed/6 skipped；integration 40 passed/2 skipped；Legacy reference Qt UI
  24 passed。Core Host 定向为 unit 109 passed、integration 31 passed。

## 尚未满足的证据

本地分支在 `43b9b731` 时比 `origin/refactor/tauri-runtime-v2` ahead 24；GitHub 上没有该 SHA 的
Windows x64、macOS arm64、Linux x64 workflow 运行。Harness 报告中的 automated 状态表示冻结的本地
profiles 与范围检查通过，不得解释为远端同 SHA 三平台证据已经存在。

任务契约中的三项负责人验收也尚未完成：直接启动当前 Runtime v2 EXE 使用已有开发 Provider 完成
真实回复和取消；复核 Core 强杀恢复、兼容 history append、正常退出、锁重获与零相关残留；审查同 SHA
三平台结果、脱敏 manifest/log、CAP-004 边界和 validation-only 回退。

因此本记录只支持进入 `stabilizing`，不支持 Agent 标记 `accepted` 或更新 CAP-004 为
`architecture-validated`。
