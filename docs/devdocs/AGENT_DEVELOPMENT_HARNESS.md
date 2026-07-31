---
kind: devdoc
status: current
audience: developer
source_of_truth: ../specs/runtime-v2/WP-H-01-agent-development-harness.md
updated: 2026-07-31
---

# Agent Development Harness 开发说明

## 当前能力

Harness 同时提供 profile 级 `list/run` 和任务级 `current/preflight/check/verify`。WP-H-01 的一次性
bootstrap 例外已关闭，当前实现也必须接受自身 `check/verify` 约束。

概念边界：Test 是单个行为断言；Test Harness 选择、执行并汇总测试；Agent Development Harness 在
测试外增加 Work Package、任务契约、Git 范围和依赖门；Task Contract 是单个 WP 的机器可读边界；
Work Package 是唯一状态真相源中的可回退交付单元；自动验收可由进程确定结果，人工验收只能由负责人
记录，Agent 只汇总 pending/结果。

profile 级命令：

```powershell
runtime\python.exe -m harness list
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness run docs
```

任务级标准流程：

```powershell
runtime\python.exe -m harness current
runtime\python.exe -m harness preflight --active
runtime\python.exe -m harness check --active
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness verify --active
```

## 契约准备规则

任务契约必须先于实现独立审查。治理提交加入最终 allowed/forbidden/protected、依赖、文档、profiles、
验收、rollback 和 `harness/activations/<WP-ID>/<sequence>.json` 锚点；`base_ref` 必须是完整 40 位 SHA。
锚点首次加入的提交只能包含契约、状态源、引用文档和锚点，不能夹带实现。后续契约修订新增递增锚点，
不能覆写旧记录或与“修实现让门通过”混在同一提交。

Bootstrap 例外只属于 WP-H-01 的首次 RED/GREEN，现已关闭。后续所有非微小开发任务不得复用该例外。

`preflight` 会聚合状态、依赖、base ancestor、范围、受保护路径、依赖文件、测试删除和冻结治理边界的所有
可独立判断结果。`check` 输出 changed/untracked/out-of-scope/forbidden/protected/dependency/deleted-test/
contract/owner-review buckets。实现阶段若状态源、契约或引用文档偏离最新锚点，命令退出 3 并报告
`owner_review_required`，不运行昂贵 profile。退出码 3 表示没有自动失败但仍需负责人处理，可能是自动门
已经通过等待验收，也可能是治理变化等待审查；只有报告为 `manual_pending` 时才能声称自动验证已完成。
Agent 始终不得把 Work Package 声称为 `accepted` 或代填人工验收结果。

`documents.specs`、`documents.adrs` 和 `documents.plans` 三类字段都必须存在，但每类可以为空；三类合计
至少引用一份与任务相关的权威文档。普通修复不应为了满足契约机械创建 Spec、ADR、Plan 三件套。

路径规则 v1 只接受精确文件和 `directory/**`；`app/*.py` 一类模式会被拒绝，避免跨目录误匹配。
