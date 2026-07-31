---
kind: plan
status: planned
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-07-31
---

# Agent Development Harness v1 实施计划

## 前置与治理门

执行状态只见 [`Runtime v2 Work Package 总表`](runtime-v2/work-packages.md)。当前 `WP-3S-01` 仍在
stabilizing，因此本文只冻结设计和任务契约草案；不得激活 `WP-H-01`、提交 Harness Python/测试/CI
生产实现，或开始 `WP-3-04`。项目负责人确认 `WP-3S-01` accepted 后，才能把 `WP-H-01` 置为 active。

激活使用两步：先以独立可审查提交加入 `WP-H-01.json` 的最终边界，再在后续激活提交把 `base_ref`
固定为该准备提交的完整 SHA。一次性 bootstrap 只使用现有 docs/smoke/定向 pytest；新命令可用后立即
关闭例外。

## 实施步骤

1. 先写任务契约失败测试：schema/未知字段/路径冲突/引用/base ref/空验收。
2. 实现严格 Work Package 表解析与 `current`，保持 runner 导入和 `list/run` 行为。
3. 实现 `preflight`，聚合所有阻断原因且不启动产品验证。
4. 用临时 Git 仓库实现并验证 committed/index/unstaged/untracked、删除、重命名和冻结契约检查。
5. 实现依赖策略、protected path 和关键测试删除检查。
6. 实现 `verify` 编排、人工 pending 退出码与失败时原子报告。
7. 增量增加当前分支 CI 自测；不重写平台矩阵或 Qt cleanup。
8. 更新 AGENTS.md/README 为已启用状态，执行定向 pytest、docs、smoke、unit 和可用的 runtime shell。
9. 审计 diff、写验证 record，进入 stabilizing；由项目负责人决定人工验收和 accepted。

## 退出条件与故障测试

退出条件以 [`WP-H-01 spec`](../specs/runtime-v2/WP-H-01-agent-development-harness.md) 为准。除功能测试
外，必须注入 Git timeout/坏引用、畸形 Markdown 表、窄控制台编码、报告 replace 失败、profile 超时、
契约自我放宽和路径穿越。任何失败不得泄露环境或继续昂贵测试。

## 回退

按 CI → CLI 编排 → checks → parser/contract → tests/docs 的逆序 revert；确认现有 `list/run` 与全部 profile
仍可运行。回退不修改产品代码、真实 data、角色包、依赖或既有三平台 workflow 语义。
