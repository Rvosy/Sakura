---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0001-automatic-recovery-parts
updated: 2026-08-23
---

# ADR-0030：Core 明确失败并由用户手动重试

## 背景

旧 Supervisor 用 restarting、timer token、backoff、三次 budget 和 retryable 分类自动重建 Core。小规模
桌面应用中的真实失败通常需要看见原因、修复配置或直接重试；自动循环反而覆盖首因并增加清理竞态。

## 决策

- Supervisor 只保留 `stopped/spawning/running/stopping/failed`。
- 任意启动失败或崩溃先保留首因、失效 generation 并完整回收进程树，然后停在 `failed`。
- 只有用户 Retry 可从 `failed` 创建新 generation；配置应用使用显式 Restart。
- 旧进程树未确认清理时不得 spawn。后续清理错误只作附属诊断，不替换首因。
- UI 保留窗口、草稿和已完成回复，显示固定安全错误与“重试连接”。

## 后果

暂时性错误不会自动恢复，用户需要点一次重试；这是可接受的产品取舍。失败路径只剩一条，日志中的首因与
UI 一致，进程泄漏风险也不再与定时重启竞态交叉。

本 ADR supersede ADR-0001 的自动恢复、restart budget/backoff 和 timer 决策；ADR-0001 的串行所有权、
generation 隔离与进程树回收继续有效。
