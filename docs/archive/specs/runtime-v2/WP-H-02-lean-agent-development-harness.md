---
kind: spec
status: superseded
audience: maintainer
source_of_truth: self
updated: 2026-08-15
---

# WP-H-02：Harness 删除型减负规范

> 当前产品契约已由 [`Product Harness`](../../../specs/product-harness.md) 替代。
> 历史状态见 [`work-packages.md`](../../../plans/runtime-v2/work-packages.md)，历史架构依据见
> [ADR-0009](../../adr/0009-lean-agent-development-harness.md)。

## 范围

WP-H-02 只简化仓库级 Harness、测试编排、CI 调用和开发文档，不修改 Sakura 产品代码、依赖、运行时
工件、角色包或用户数据。除统一隔离临时根外不新增 Harness 能力；实现必须净删除代码和治理概念。

现有 `list`、`run <profile>`、`current`、`check`、`verify`、case timeout、UTF-8 输出、原子 JSON 报告、
changed-set、依赖变化、关键测试删除和人工待办退出码继续可用。独立 `preflight` 命令和所有调用点删除。

## 任务契约 v2

新任务契约只接受以下根字段，拒绝缺失或未知字段：

```json
{
  "schema_version": 2,
  "id": "WP-H-02",
  "base_ref": "<full-40-character-sha>",
  "allowed_paths": ["harness/**"],
  "required_profiles": ["docs", "unit"]
}
```

- `id` 必须是合法且与文件名一致的 Work Package ID。
- `base_ref` 必须是完整 40 位提交 SHA 和当前 HEAD 的祖先，默认与 task 文件第一次提交中的值一致。
  暂停任务因已验收的插入依赖而恢复时，项目负责人可明确批准把它前移到初始 base 的后代提交；后退
  或跨历史移动必须失败。它只定义 changed-set，不是任务身份或批准凭证。
- 路径只接受仓库相对 POSIX 精确路径或 `directory/**`，拒绝绝对路径、反斜杠、`..`、空值、重复和
  其他 glob。`allowed_paths` 至少包含一个条目。
- profile 必须存在且不重复。最终任务不得同时选择 `core-host` 与覆盖它的 `python-full`；选择
  `python-full` 时不得同时要求 `smoke`。
- 已提交的 `base_ref`、`allowed_paths` 或 `required_profiles` 修订不会阻断运行，但 task report 必须列出
  相对首次提交变化的字段。未提交或 staged 的 task 修订返回 `owner_review_required`/exit 3，`verify`
  不运行 case。
- v1 task 与 activation 仅作为仓库历史存在。选择 v1 task 必须返回明确的 schema 错误；loader 不读取
  activation，WP-H-02 后不得新增 activation 文件。

## Work Package、Git 与安全检查

`current` 继续从唯一 Work Package 表返回当前 `active` 或 `stabilizing` 项。`check <ID>|--active` 一次
完成：

1. 目标存在且为当前 Work Package；表中的直接依赖均为 `accepted`。
2. `base_ref` 是 HEAD 祖先；相对首次提交发生修订时，它仍须是初始 base 的后代。
3. 合并 `base_ref..HEAD`、index、unstaged 与 untracked 差异；重命名同时检查旧、新路径。
4. 所有 changed path 命中 allowlist，且不命中代码内全局保护边界。
5. 继续识别依赖 manifest/lock 与 `tests/**` 删除；允许的依赖变化在报告中突出显示并继续测试，未允许
   的依赖变化按范围外失败。不得把本规则扩张成新的测试治理系统。

全局保护边界固定为 `data/**`、`characters/**`、`third_party/**`，优先于 task allowlist。Harness 是
changed-set 门，不读取或修改这些目录内容，也不把允许的 manifest/lock 变化自动视为安全。

## Profile、case 与临时根

`verify` 按 task 中 profile 顺序、每个 profile 的 case 顺序展开唯一 case ID 集合。同一 case 只启动一次；
首个失败或 timeout 后停止后续 case，并依据已执行结果反推每个 profile 的 `passed`、`failed` 或
`blocked` 状态。

`runtime-v2-shell` 只包含 Node 与 Rust Shell case；Provider/Memory Python case 归入 `core-host`。
`smoke` 保留快速本地反馈；全量任务选择 `python-full` 时不再重复选择 smoke。Journey case 不得同时被
broad Python profile 收集。

每次 `run` 或 `verify` 创建唯一的
`temp/harness/runtime-tmp/<run-id>`，将 `TMPDIR`、`TMP`、`TEMP` 默认指向该绝对路径并把路径写入报告。
每个 case 的显式 `env` 最后应用，可以覆盖默认值。Harness 不依赖平台系统临时目录的符号链接表示。

## 报告与退出码

task report 升级为 schema v2，至少包含命令、task、状态、base/head、UTC 时间、临时根、scope、依赖
变化、契约修订字段、唯一 case 结果、各 profile 派生状态、自动 case ID 与 summary。报告不得复制
Spec 验收散文、人工操作结果、credential 或完整环境。

- `0`：命令成功且没有人工待办（普通 `run`、`current`、`check`）。
- `1`：自动验证或硬范围门失败。
- `2`：调用、manifest、Work Package 或 task 契约错误。
- `3`：自动门全绿、等待人工验收，或未提交 task 修订等待负责人审查。

硬失败或 task 工作树修订时，`verify` 必须跳过所有 case。自动全绿时固定返回
`manual_pending`/exit 3；人工步骤只来自本 Spec，由负责人写入 record 并更新 Work Package 状态，Agent
不得代填。

## 验收与完成指标

单元测试必须覆盖 v2 schema、Git 四类差异、重命名、全局保护、依赖/测试删除、task 修订、base 默认
固定、负责人批准的单向前移、跨历史拒绝、命令/退出码、case 去重/顺序/失败/timeout/UTF-8、原子报告与临时根覆盖。`current/check/verify
--active` 必须可用，`preflight` 必须成为调用错误，历史 v1 task 必须给出明确错误。

完成时 Harness Python 与对应测试净减少至少 250 行；一次 verify 不重复执行 case ID；完整自动反馈保持
十分钟内。新 WP task 不超过 30 行，业务 WP 默认不修改 Harness Python。WP-H-02 自动门全绿后进入
`stabilizing`，仍由负责人单独决定是否 accepted。
