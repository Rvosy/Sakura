---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-10
---

# WP-4-02 内置 Tools 与 Action ID 确认自动验证记录

## 候选与环境

2026-08-10，在 Windows x64、分支 `refactor/tauri-runtime-v2` 上验证实现候选
`0ea4e0baac9eb0c2fbd661485063fdd9a0e1f48b`。固定 base 为
`e8de48e8ec4ae058216a6d289134256b51494cf3`；Work Package 当前状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。本记录只写入已经执行的自动证据，
不预填原生界面人工验收，也不把候选标记为 `accepted`。

候选按[当时的规范](../../archive/specs/runtime-v2/pre-simplification-2026-08-23/WP-4-02-tools-operation-action-confirmation.md)只开放
`get_current_time`、`memory_search`、`memory_remember`、`memory_update` 和 `memory_forget`。Core 保存
不可变参数并签发 128-bit 一次性 Action ID；Rust 只提交 `{actionId}`，WebView 没有确认 command。
Windows 原生 Task Dialog 由应用状态拥有，可被聊天取消、Core generation 变化和 Core 到期时间关闭；
晚结果仍由 Gateway/Core fail closed。Tools 设置只开放循环上限与确认策略，原子写入既有兼容字段后
受控重启 Core，并让设置窗口原位重绑定。本段只记录历史候选；当前确认协议已由
[ADR-0031](../../adr/0031-retire-runtime-v2-tool-confirmation.md) 删除。

## 自动结果

- `runtime\python.exe -m harness check WP-4-02`：当前任务、三项 accepted 依赖、固定 base、allowlist、
  全局保护、activation 关闭、测试删除、task 修订和依赖变化检查全部通过；越界文件为 0。
- `runtime\python.exe -m harness run docs`：2/2 passed，报告
  `temp/harness/20260809T162859.766009Z-docs.json`。
- `runtime\python.exe -m harness run smoke`：3/3 passed，报告
  `temp/harness/20260809T162302.757506Z-smoke.json`。
- `runtime\python.exe -m harness run core-host`：4/4 passed；Core Host unit 139 passed，真实进程
  integration 34 passed，Provider/模型 25 passed，Memory 17 passed；报告
  `temp/harness/20260809T162347.720690Z-core-host.json`。
- `runtime\python.exe -m harness run runtime-v2-shell`：6/6 passed；前端 137 passed，Rust 角色外观、
  角色表现、产品 Shell、窗口几何和窗口交互定向组全部通过；报告
  `temp/harness/20260809T162314.156708Z-runtime-v2-shell.json`。
- `runtime\python.exe -m harness run journey-tools`：3/3 passed；Python 22 passed、Rust 7 passed、
  frontend 4 passed；报告 `temp/harness/20260809T162228.985120Z-journey-tools.json`。
- `cargo test --manifest-path desktop\src-tauri\Cargo.toml --locked -- --test-threads=1`：
  280 passed、24 个需正式平台 fixture 的测试 ignored、0 failed；`cargo check --locked`、
  `cargo fmt --check` 与 `git diff --check` 通过。
- 额外合并回归：完整前端 137 passed；WP-4-02、真实聊天与 Core 聊天组合 35 passed。

## 最终自动门与剩余人工项

提交后执行 `runtime\python.exe -m harness verify WP-4-02`，报告
`temp/harness/20260809T162604.996257Z-WP-4-02.json` 为 `manual_pending`：18/18 唯一自动 case 通过，
failed、pending 和 blocked 均为 0，人工状态为 `pending`。因此自动门已通过，WP-4-02 进入
`stabilizing`，等待项目负责人验收。

尚未在本记录中声称完成的人工项包括：真实 Windows 候选上的原生提示焦点、执行、取消、关闭和超时；
Tools 设置保存、重开、Core restart 后继续聊天；应用正常退出后的提示、线程、请求和进程零残留。
macOS arm64 与 Linux x64 同 SHA 平台候选也未在本地 Windows 记录中冒充已运行。项目负责人明确验收前，
不得把 WP-4-02 标记为 `accepted`，也不得激活依赖它的 WP-4-03。
