---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-11
---

# WP-4-03 MCP 生命周期与工具调用等价自动验证记录

## 候选与环境

2026-08-11，在 Windows x64、分支 `refactor/tauri-runtime-v2` 上验证产品候选
`f06392b8e00eb976555a8e455059b8e7312bde34`。固定 base 为
`a3156f3b78177816352eef82004c91b982e24513`；Work Package 当前状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。本记录只写入已经执行的自动证据，
不预填 Windows 实机操作或三平台 CI 结果，也不把候选标记为 `accepted`。

候选按 [normative Spec](../../specs/runtime-v2/WP-4-03-mcp-lifecycle-tool-parity.md) 把 MCP provider、
stdio/SSE session、工具映射和调用绑定到当前 Core generation。Server initialize 与 `tools/list` 在后台
完成，不阻塞 Core readiness；MCP 工具复用 WP-4-02 ToolRegistry、聊天取消和 Action ID 原生确认。
设置页只显示桌面偏好和脱敏状态。正常关闭由 provider 清理，Core 崩溃或超时由 Rust 受控进程树兜底
回收 stdio 后代。

## 自动结果

- `runtime\python.exe -m harness check WP-4-03`：当前任务、accepted 依赖、固定 base、allowlist、全局保护
  边界、测试删除、task 修订和 activation 关闭检查全部通过；越界与保护文件均为 0。
- 提交后运行 `runtime\python.exe -m harness verify WP-4-03`：报告
  `temp/harness/20260810T175735.921109Z-WP-4-03.json` 绑定上述候选 SHA，退出码为 3，机器状态为
  `manual_pending`；21/21 唯一自动 case 通过，failed、pending 和 blocked 均为 0。required profiles
  分别覆盖 docs 2 项、smoke 3 项、core-host 4 项、runtime-v2-shell 6 项、journey-tools 3 项和
  journey-mcp 3 项。
- MCP Python journey 9 passed：真实 FastMCP stdio 子进程证明慢启动期间 Core 先就绪、释放后工具注册，
  正常 shutdown 后子进程消失；同时覆盖配置损坏、命令缺失、状态脱敏、结果上限和注册/关闭竞态。
- MCP Rust journey 2 passed，确认 `assistant.mcp-v1` 进入产品 hello，Core 返回的设置 DTO 在加入窗口与
  generation 身份前通过严格字段、枚举、数量和文本边界。
- MCP/frontend journey 7 passed，确认设置读取、仅保存桌面布尔偏好、Core 受控重启、原位重绑、旧
  generation 丢弃和 WebView 无 command/args/env/headers 字段。
- 额外宽回归：Python unit 648 passed/6 skipped；Rust 完整测试 295 passed/24 ignored；完整 Runtime v2
  前端 145 passed。`cargo fmt --check`、`git diff --check` 与文档门通过。Windows 实机脚本通过 PowerShell
  语法检查，但本记录没有把语法检查冒充实机执行。

## 最终自动门与剩余人工项

自动门通过后，WP-4-03 进入 `stabilizing`。尚未执行的 Windows 实机验收使用
`desktop/tests/windows_wp_4_03_mcp_acceptance.ps1`：在隔离 assistant root 检查真实 Windows MCP 与
受控 fixture ready，完成原生确认允许、拒绝、工具超时、Core 强杀恢复、设置状态重绑和正常退出；脚本
随后扫描轮转日志中的配置/工具 sentinel 与绝对路径，确认真实 `data/**` 不变且 Shell/Core/MCP 子进程
零残留。

同一候选 SHA 的 macOS arm64 与 Linux x64 Runtime v2 CI 尚未在这份本地记录中声称完成。上述人工与
平台证据由项目负责人审阅前，不得把 WP-4-03 标记为 `accepted`，也不得激活依赖它的 WP-4-04。
