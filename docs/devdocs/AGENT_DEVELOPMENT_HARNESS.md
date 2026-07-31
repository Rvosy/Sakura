---
kind: devdoc
status: current
audience: developer
source_of_truth: ../specs/runtime-v2/WP-H-01-agent-development-harness.md
updated: 2026-07-31
---

# Agent Development Harness 开发说明

## 当前可用与计划能力

当前 Harness 仍只提供 `list` 和 `run`，负责运行既有验证 profile 并生成 JSON 报告。`WP-H-01` 已激活，
正处于一次性 bootstrap 实现阶段；`current/preflight/check/verify` 在转绿前仍不能当作已实现命令。

概念边界：Test 是单个行为断言；Test Harness 选择、执行并汇总测试；Agent Development Harness 在
测试外增加 Work Package、任务契约、Git 范围和依赖门；Task Contract 是单个 WP 的机器可读边界；
Work Package 是唯一状态真相源中的可回退交付单元；自动验收可由进程确定结果，人工验收只能由负责人
记录，Agent 只汇总 pending/结果。

当前可执行：

```powershell
runtime\python.exe -m harness list
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness run docs
```

`WP-H-01` accepted 后的标准流程将是：

```powershell
runtime\python.exe -m harness current
runtime\python.exe -m harness preflight WP-3-04
runtime\python.exe -m harness check WP-3-04
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness verify WP-3-04
```

## 契约准备规则

任务契约必须先于实现独立审查。准备提交加入最终 allowed/forbidden/protected、依赖、文档、profiles、
验收和 rollback；激活提交只固定 base_ref。实施过程中变更契约或引用的成功标准会触发失败并要求新的
独立预检，不能与“修实现让门通过”混在同一变更。

Bootstrap 例外只属于 `WP-H-01`：命令尚未存在时使用当前 `run docs`、`run smoke` 和定向 pytest。
后续所有非微小开发任务不得复用该例外。
