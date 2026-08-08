---
kind: plan
status: active
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-08
---

# WP-H-02：Harness 删除型减负实施计划

## 前置与提交边界

执行状态只见 [`Runtime v2 Work Package 总表`](runtime-v2/work-packages.md)。WP-4-01 的最终候选
`bfa5edc6fdd1b921fce6d366096fa95192f9d878` 已取得本地自动门、Test、Windows/macOS/Linux 平台门和
项目负责人人工验收；验收事实独立记录后，WP-H-02 才能激活。

WP-H-02 最多三个候选提交：激活与契约、实现与测试、自动验证记录；负责人 acceptance 提交不计入。
WP-H-02 不修改产品代码，也不触发新的产品三平台验收要求。

## 实施步骤

1. 使用旧 Harness 创建 v1 task 和最后一个 `harness/activations/WP-H-02/0001.json`，在同一纯治理
   提交中把 WP-4-01 标记 accepted、WP-H-02 标记 active，并运行旧 `preflight/check`。
2. 用失败测试锁定 task v2、首次提交 base、Git 四类差异、全局保护、task 修订、命令退出码、case
   去重、稳定顺序和隔离临时根。
3. 删除 activation 历史读取、治理文档冻结、任务依赖副本、per-WP forbidden/protected/dependency
   policy、验收散文映射和独立 `preflight`。
4. 把依赖读取并入 Work Package 表，把 scope/依赖/测试删除检查并入 `check`；实现 v2 报告与 task
   修订字段。
5. 让 `verify` 展开有序唯一 case 集，一次执行后派生 profile 状态；调整 `runtime-v2-shell` 与
   `core-host` 的 Python case 归属。
6. 为每次 Harness invocation 创建仓库内唯一临时根并注入三个标准环境变量，保留 case 显式覆盖。
7. 把 WP-H-02 task 转为 v2；迁移 CI、AGENTS、Harness README 和开发文档；归档 ADR-0008、WP-H-01
   Spec 与 v1 Plan，清理旧链接。
8. 执行完整验证、统计净删除行数并写自动 record；自动全绿后把 WP-H-02 转入 `stabilizing`，不得填写
   人工验收。

Journey 不在本 WP 批量重组。从 WP-4-02 起增加 `journey-tools`，后续随 MCP、Plugins、TTS 的真实用户
旅程渐进扩展；Journey case 不得与 broad Python profile 重复收集。

## 验证矩阵

必须执行：

```text
runtime/bin/python3 -m pytest tests/unit/test_harness_runner.py tests/unit/test_harness_agent_development.py -q
runtime/bin/python3 -m harness run docs
runtime/bin/python3 -m harness run unit
runtime/bin/python3 -m harness run core-host
runtime/bin/python3 -m harness run runtime-v2-shell
runtime/bin/python3 -m harness check WP-H-02
runtime/bin/python3 -m harness verify WP-H-02
git diff --check
```

最终 `verify` 预期自动门全绿、exit 3、`manual_pending`；GitHub Test job 必须通过。完整自动反馈不得超过
十分钟，同一 case ID 不得重复执行。

## 回退

整体 revert 实现与调用文档提交，再恢复激活提交中的 v1 task 使用；历史 v1 task/activation 保留在
HEAD，可立即恢复旧 loader。回退不修改产品代码、依赖、用户数据、角色包或 WP-4-01 Memory 实现；
不得通过删除测试、放宽保护路径或伪造人工验收完成回退。
