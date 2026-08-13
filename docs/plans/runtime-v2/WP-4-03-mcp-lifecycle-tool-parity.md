---
kind: plan
status: accepted
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-12
---

# WP-4-03 MCP 生命周期与工具调用等价实施计划

## 1. 目标、基线与边界

以项目负责人接受的 WP-4L-01 最终候选
`a3156f3b78177816352eef82004c91b982e24513` 为固定 base，实现
[`normative Spec`](../../specs/runtime-v2/WP-4-03-mcp-lifecycle-tool-parity.md)。任务契约为
`harness/tasks/WP-4-03.json`，不创建 activation。

本 WP 只迁移配置、stdio/SSE session、generation 私有生命周期、ToolRegistry/Action ID 调用链和现有
平台桌面 MCP 设置语义。不修改 `data/**`、`characters/**`、`third_party/**` 或 `tools/mcp/**`，不实现
插件、TTS、截图 resource token、浏览器、调度或通用 worker 平台。架构预检确认复用 ADR-0001/0002/
0004/0005/0007，不新增 ADR。

## 2. 分阶段实施

### A. 治理与 RED

- 提交负责人验收 record、Spec、本文、task v2 和 `journey-mcp` baseline，并更新 Runtime v2 索引和状态
  真相源。
- 在任何生产修改前运行 `runtime\python.exe -m harness check WP-4-03`；随后增加 Python、Rust 和 frontend
  RED，分别冻结 Core MCP journey、受控后代清理/gateway 和设置状态/generation 重绑定。

退出条件：固定 base、依赖、allowlist、required profiles、保护路径和文档门通过；RED 能证明当前 Runtime
v2 尚未建立 MCP 纵向链。

### B. Qt-free Core 生命周期

- 把现有 MCP 资源所有权从 Legacy `app.core.resource_manager` 切换到 Qt-free
  `app.core.runtime_resources`，由 Assistant Adapter 为每个 generation 创建和关闭 provider。
- 收紧 `mcp.yaml` parser、runtime token、stdio/SSE 初始化、list/call/close deadline、schema/result 大小和
  凭据边界；单 server 配置或连接失败只降级 MCP 域。
- 把 stdio 后代纳入 Core/Rust 受控进程树兜底，SSE socket/event loop/timer 在 generation 关闭后归零。

退出条件：真实 Core 可在不影响 readiness 的情况下暴露成功 server，故障 server 有稳定状态；关闭、崩溃、
超时和重建均无旧 handler、任务、线程、socket 或进程残留。

### C. ToolRegistry 与 Action ID 纵向链

- 将经校验和过滤的远端 tools 注册到 WP-4-02 ToolRegistry，保持稳定命名、风险与确认策略，并在执行前
  重验 generation 和参数。
- 复用聊天 Operation、取消、Action ID 原生确认和唯一终态；拒绝伪造、重复、过期与旧 generation 决定。
- 对文本、structured content、图像和错误结果实施数量/深度/大小上限与脱敏；不建立持久资源或截图能力。

退出条件：成功、失败、拒绝、超时、取消、server crash 和 Core restart 都产生一个有界 ToolResult/聊天
终态，且内置 Tools 与 Memory 行为不回归。

### D. 设置与可观测性

- 迁移现有平台桌面 MCP 开关，保持高级配置只来自 `mcp.yaml`；设置 DTO 只公布支持性、偏好、当前
  generation、server 状态和稳定 reason code。
- 保存后按既有配置 owner 受控重建 Core，并原位重绑设置窗口；不支持平台不得误启其他平台 server。
- 接入 WP-4L-01 固定事件和脱敏注册表；stderr flood、配置/网络异常与工具 payload 不得进入日志。

退出条件：设置刷新、保存失败、重启、旧 generation 晚到和窗口关闭通过 frontend/Rust/Python journey，
日志 sentinel 扫描为零。

### E. 候选与验收

- 运行 task required profiles：`docs`、`smoke`、`core-host`、`runtime-v2-shell`、`journey-tools` 和
  `journey-mcp`，并运行完整 Rust/frontend 相关回归与三平台 Runtime v2 CI。
- 在 Windows 隔离 assistant root 完成桌面 MCP 启动、工具列举、确认/拒绝、调用、超时、Core
  crash/recovery、设置重绑和正常退出，确认日志/DTO 无敏感值且进程零残留。
- 写入已经发生的自动验证 record，再运行 `harness verify WP-4-03`。自动门全绿只进入
  `manual_pending`/`stabilizing`；不得代填负责人验收。

## 3. 故障矩阵

覆盖缺失/损坏/未来配置、非法 transport、runtime token 不存在、command 缺失/无权限、stdio 启动失败/
提前退出/stderr flood、SSE 连接拒绝/断流、initialize/list/call/close 超时、重复名、恶意 schema、巨大或
非法 text/structured/image 结果、Action ID 拒绝/重复/过期、Operation 取消、Core crash/restart、旧
generation 迟到、设置保存冲突和 shutdown deadline。所有失败必须有界、脱敏并保持其他产品域可用。

## 4. 回退

先关闭 MCP 设置 feature 和新注册入口，正常关闭当前 generation，再按 WP-4-03 产品提交逆序 revert。
回退不得删除、改写或迁移 `mcp.yaml`、system config、日志或其他用户数据；清理超时由 Rust 受控进程树
兜底回收 stdio 后代。每个阶段保持独立可回退，不要求回退 WP-4L-01 或 WP-4-02。
