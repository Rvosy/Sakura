---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0020
updated: 2026-08-23
---

# ADR-0031：Runtime v2 删除工具确认协议

## 背景

ADR-0020 已决定当前响应式助手的工具直接执行，却保留 Action ID coordinator、确认请求、原生对话框和
设置字段作为“未来基础设施”。这些无消费者代码仍穿过 Python、Rust、前端和测试，使普通工具故障难以
定位，并让未来设计被迁移脚手架绑住。

## 决策

- Runtime v2 删除工具 Action ID 状态、确认/拒绝请求、确认事件、原生确认框与 confirmation policy。
- `assistant.tools-v1` 只表示经过 schema、generation 和 contribution identity 校验后的直接工具执行。
- Legacy Qt 的 `PendingToolAction` 与 `PermissionPolicy` 隔离保留，不进入 Runtime v2。
- 将来自用户、MCP、插件或模型的工具错误按现有调用边界明确返回，不用确认状态包装。
- 日后若出现自主 Agent 的真实权限消费者，必须重新设计能力声明、授权与撤销；不得恢复本次删除的协议。

## 后果

当前工具链少一套跨进程状态机和失败终态。Runtime v2 不再提供工具执行前二次确认；这是对当前产品角色
的明确选择，而不是临时隐藏设置。
