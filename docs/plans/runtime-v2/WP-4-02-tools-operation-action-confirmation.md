---
kind: plan
status: accepted
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-10
---

# WP-4-02 Tools、Operation 与 Action ID 确认实施计划

## 1. 目标与边界

按 [`WP-4-02 normative Spec`](../../specs/runtime-v2/WP-4-02-tools-operation-action-confirmation.md)
交付 CAP-009/010 的最小真实纵向链。当前状态、依赖与启动点只以
[`work-packages.md`](work-packages.md) 为准。

本计划不迁移 Todo/提醒、截图、MCP、插件、TTS、浏览器/桌面控制、通用任务图、resource token 或
worker process 平台；不修改 `data/**`、`characters/**`、`third_party/**` 或 `tools/mcp/**`。

## 2. 实施顺序与提交边界

### A. 冻结契约与测试入口

- 提交 normative Spec、本文、索引和 Work Package 范围说明。
- 以已提交 task v2 修订加入逐路径产品 allowlist，以及 `docs`、`smoke`、`core-host`、
  `runtime-v2-shell`、`journey-tools` profiles。
- 在任何产品代码修改前再次执行 `harness check WP-4-02`。

退出条件：task 修订已提交、check 通过、没有受保护或范围外文件。

### B. Core ToolRegistry 与 Action store

- 从 WP-4-01 Memory boundary 显式组装五个允许工具；不加载 Legacy 全量 built-ins。
- 冻结参数/结果 DTO、确认策略、至少 128-bit Action ID、generation/operation 绑定、60 秒单调 TTL、
  compare-and-consume 和关闭清理。
- 让 RealChatBoundary 接受工具 action，不再以 `UNEXPECTED_CHAT_ACTION` 失败；工具拒绝/失败结果回填
  Agent 循环，聊天仍保持唯一终态。

退出条件：Python unit/integration 覆盖未知/重复/过期/旧 generation、取消竞态、Memory 降级和零残留。

### C. IPC、Gateway 与原生确认

- 协商 `tools_v1`，增加 `tool.confirmation.requested`、`tool.confirm`、`tool.reject` 的严格 envelope。
- Rust Gateway 关联当前 chat operation，只把脱敏 DTO交给应用拥有的跨平台原生提示实现；不向 WebView
  注册确认工具参数的 command。
- 覆盖对话框关闭、焦点、超时、并发事件、Core crash/restart、主窗口退出和晚结果。

退出条件：Rust/协议/前端定向测试通过；WebView 无法提交或替换参数；三平台代码可编译。

### D. Tools 设置纵向链

- Core 读取/校验/原子保存 `tool_loop.*` 与 `ui.free_access_enabled`，返回公开 DTO 和
  `core_restart_required` change plan。
- Rust 设置 Gateway 注入窗口/generation identity；manifest 开放 `tools.runtime_limits` 与
  `tools.confirmation_policy`，保持 `windowsMcp` unavailable。
- 复用既有 Tools 页面布局，增加确认策略控件和 feature 标记；保存后走受控 Core restart 与原位重绑定。

退出条件：兼容 fixture、unknown-field preservation、未来/损坏 schema、写故障、重开一致性和旧
generation 丢弃通过。

### E. Journey、自动门与候选记录

- 增加不与 broad Python profile 重复的 `journey-tools` 定向 cases。
- 运行 task `check`、required profiles、相关定向测试与 `harness verify WP-4-02`。
- 把实际日期、环境、命令、结果、报告路径、候选 SHA 和剩余人工项写入 audit record。

退出条件：自动门全绿时只声明 `manual_pending`/等待验收；项目负责人完成真实 Windows 确认与设置
Journey 前不得写 `accepted`。

## 3. 验收环境

- 自动参考环境：Windows 2025 x64，仓库 `runtime/python.exe`、锁定 Node/Cargo，deterministic/local
  Provider 与隔离临时数据目录。
- 平台门：同一候选 SHA 的 Windows x64、macOS arm64、Linux x64 公共 Core/Rust/frontend checks。
- 人工门：真实 Windows Runtime v2 debug/release 候选，原生确认焦点、执行/取消/关闭/超时、Tools
  设置保存/重开、Core restart 后继续聊天和正常退出零残留。

## 4. 故障注入

实现和 Journey 必须逐项覆盖：非法参数、伪造 Action ID payload、重复/竞态决定、TTL、旧 generation、
chat cancel、Core crash、dialog 创建失败/关闭、Memory loading/degraded/只读/不存在 ID、设置损坏/未来
schema/权限/temp/replace 失败、shutdown/EOF 和 late callback。

## 5. 回退

1. 禁用 `tools_v1` 和两个 Tools settings feature，停止创建新 Action ID。
2. reject pending actions，取消并排水活动 chat operation，关闭 native dialog。
3. 确认 Router/writer/request/thread/timer/store/Memory worker/Core 进程树归零且共享锁可立即重获。
4. 按本 WP 的实现提交逆序 revert；不回退 task 固定 base，不删除或恢复 Memory、history、
   `system_config.yaml` 或任何用户数据。

若缺陷只涉及设置写入，优先把 Tools 设置退回只读并保留已提交兼容值；若涉及确认绕过或重复执行，
立即禁用整个 `tools_v1` capability。
