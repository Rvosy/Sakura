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
runtime\python.exe -m harness preflight WP-3-04
runtime\python.exe -m harness check WP-3-04
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness verify WP-3-04
```

## 契约准备规则

任务契约必须先于实现独立审查。准备提交加入最终 allowed/forbidden/protected、依赖、文档、profiles、
验收和 rollback；激活提交只固定 base_ref。实施过程中变更契约或引用的成功标准会触发失败并要求新的
独立预检，不能与“修实现让门通过”混在同一变更。

Bootstrap 例外只属于 WP-H-01 的首次 RED/GREEN，现已关闭。后续所有非微小开发任务不得复用该例外。

`preflight` 会聚合状态、依赖、base ancestor、范围、受保护路径、依赖文件、测试删除和冻结契约的所有
可独立判断结果。`check` 输出 changed/untracked/out-of-scope/forbidden/protected/dependency/deleted-test/
contract buckets。`verify` 前置失败时写失败报告但跳过 profile；自动门全过而人工项存在时退出 3。
