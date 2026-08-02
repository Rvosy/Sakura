---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-03
---

# WP-2-01 Router 稳定化项目负责人验收声明

## 日期与结论

2026-08-03，项目负责人在当前开发会话中明确声明：

> 我确认 WP-2-01 Router 顺序稳定化验收通过，批准重新标记 accepted 并恢复 WP-3V-01。

该声明关闭重新打开的 WP-2-01 负责人复核门，并授权恢复 WP-3V-01。当前状态只维护在
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)，本记录不形成第二个状态真相源。

## 接受范围

- 接受 `fab46beb` 对普通/关键事件队列的 wire-order 合并：快速 `chat.completed` 不再超越同一
  operation 的 `chat.started`。
- 接受缓存队头继续计入原命名容量；终态预留没有从 8 个槽位隐式扩容。
- 不接受协议、Gateway 校验、Provider 时序、队列上限、Core、UI 或用户数据边界的扩大；这些均未由
  稳定化实现修改。
- 接受首次默认并行完整 Rust 的 3 个共享 Windows mutex 干扰不归因于 Router；精确回收该轮测试树后，
  稳定的单线程完整运行 239 passed、24 ignored、0 failed。

## 证据与后续

根因、TDD、容量边界和真实 Windows 证据见
[`WP-3V-01-ROUTER-ORDERING-DEFECT.md`](WP-3V-01-ROUTER-ORDERING-DEFECT.md)。真实组合验收完成
4 次 Provider 请求、1 次 Core 强杀、新 generation 水合、唯一取消终态和 Legacy oracle 回读；敏感
证据与进程残留均为 0。WP-2-01 的接受治理提交为 `60fcc79d`，WP-3V-01 reactivation 为
`6d91c283`。

恢复 WP-3V-01 不等于其自动或人工验收已经通过。它仍须在恢复后的候选重新完成 required profiles、
真实 Windows 组合验收、同 SHA 三平台证据和真实开发 Provider 人工验收。若再次复现终态重排、容量
突破或 Router 资源残留，应重新打开 WP-2-01，不得在 WP-3V-01 内规避。
