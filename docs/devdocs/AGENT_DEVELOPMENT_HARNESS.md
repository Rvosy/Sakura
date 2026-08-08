---
kind: devdoc
status: current
audience: developer
source_of_truth: ../specs/runtime-v2/WP-H-02-lean-agent-development-harness.md
updated: 2026-08-08
---

# Agent Development Harness 开发说明

## 职责边界

Harness 负责两件事：稳定执行仓库测试，以及在 Work Package 开发中检查 Git 范围和全局安全边界。它
不审批项目计划，不维护 activation 账本，也不复制 Spec 的人工验收散文。

Test 仍是 `tests/` 或各语言测试目录中的单个行为断言；`suites.json` 只把可执行 case 组织成 profile；
`harness/tasks/<WP-ID>.json` 只描述当前任务的 changed-set 起点、允许路径和必需 profile。Work Package
状态与依赖唯一来自 `docs/plans/runtime-v2/work-packages.md`。

## 标准流程

```powershell
runtime\python.exe -m harness current
runtime\python.exe -m harness check --active
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness verify --active
```

- `current [--json]`：查询唯一 `active` 或 `stabilizing` Work Package。
- `check <ID>|--active`：一次完成 task、当前 WP、表中依赖、base ancestor、四类 Git 差异、重命名、
  allowlist、依赖变化、全局保护路径和测试删除检查。
- `verify <ID>|--active`：前置检查通过后，把 required profiles 展开为稳定的唯一 case 集并执行；同一
  case ID 只运行一次。
- `run <profile>`：独立运行一个 profile，用于开发中的快速反馈。

独立 `preflight` 已删除；旧命令属于调用错误并返回 2。

## Task contract v2

新任务只需五个字段：

```json
{
  "schema_version": 2,
  "id": "WP-X-01",
  "base_ref": "<full-40-character-sha>",
  "allowed_paths": ["app/example/**", "tests/**"],
  "required_profiles": ["docs", "unit"]
}
```

路径只接受仓库相对 POSIX 精确路径或 `directory/**`。`base_ref` 必须等于 task 文件第一次提交时的值，
后续不得移动；它只用于计算 changed-set。依赖不写入 task，由 Work Package 表提供。

已提交的 `allowed_paths`/`required_profiles` 变化会列入报告的 `contract.revision_fields`，不再产生批准
锚点。task 在 index 或工作树中有修订时，`check/verify` 返回 3/`owner_review_required`，且 `verify`
不执行 case。历史 v1 task 与 activation 保留在 Git 中，但 loader 不再读取；WP-H-02 的 `0001` 是最后
一个允许出现的 activation。

最终 task 不得同时选择 `core-host` 与 `python-full`，也不得同时选择 `smoke` 与 `python-full`。
`python-full` 已覆盖完整 unit，`core-host` 负责 Provider/Memory Python 边界，`runtime-v2-shell` 只负责
Node/Rust 桌面壳检查。

## Git 与安全边界

changed-set 是 `base_ref..HEAD`、staged、unstaged、untracked 的并集；重命名同时检查旧路径和新路径。
任何未命中 allowlist 的路径直接失败。

`data/**`、`characters/**`、`third_party/**` 是代码内全局保护路径，即使 task 显式允许也会失败。
`tests/**` 删除继续失败。允许列表中的 manifest/lock 变化会在 `dependencies.changes` 中标为
`allowed` 并继续运行测试；未允许时按范围外失败。

Harness 是确定性的 changed-set 门和安全告警，不是文件系统权限系统。负责人仍须审查具体 diff。

## 报告、临时根与退出码

每次 `run` 或 `verify` 创建唯一的
`temp/harness/runtime-tmp/<run-id>`，默认覆盖 `TMPDIR`、`TMP`、`TEMP`。case 的显式 `env` 最后应用，
可以覆盖单个变量。临时根绝对路径写入 JSON 报告，避免不同平台的系统临时目录别名导致误判。

task report schema v2 保存 scope、依赖变化、契约修订字段、唯一 case 结果、派生 profile 状态和自动
case ID，不复制验收散文或环境密钥。写入仍使用 UTF-8、同目录临时文件、fsync 和原子 replace。

- `0`：普通命令成功；
- `1`：范围门或自动 case 失败；
- `2`：调用、manifest、Work Package 或 task 错误；
- `3`：`manual_pending`，或 task 工作树修订导致 `owner_review_required`。

只有报告状态为 `manual_pending` 才能声称自动门已经通过。人工步骤来自对应 normative Spec，由负责人
执行并写入 record/Work Package 状态；Agent 不得代填或擅自标记 `accepted`。

## 扩展规则

新增检查时先把行为断言放在所属测试目录，再在 `suites.json` 注册一个窄 case。业务 WP 优先复用现有
profile；若新增真实用户旅程，建立独立 Journey case，且不得同时被 broad Python profile 收集。新增
业务 WP 默认不修改 Harness Python，也不得恢复 activation、治理文件冻结或验收散文映射。
