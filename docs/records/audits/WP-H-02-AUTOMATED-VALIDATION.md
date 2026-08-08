---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-08
---

# WP-H-02 自动验证记录

## 候选与范围

WP-H-02 最终实现候选为 `eb36dc2262a5159c59a1af120cbe9cde74f2c237`，changed-set 基线固定为
`cd9faf8f828d97b076cca404abfe48119b876ee8`。旧 Harness 在纯治理提交
`4400a262f7d64e3fde5f1c0f570996ac4923f66f` 接受 `WP-H-02/0001`；该文件是最后一个 activation，
后续 v2 loader 不读取 activation 历史。

候选只修改 Harness、对应测试、CI 调用、AGENTS 和开发/治理文档。`app/`、`desktop/`、`plugins/`、
依赖、`data/`、`characters/`、`third_party/` 与 WP-4-01 Memory 实现均未修改。本记录只保存自动事实；
Work Package 当前状态仍只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

## 删除结果与接口检查

- Harness Python 与对应两份单元测试相对激活提交为 849 行新增、1221 行删除，净删除 372 行，超过
  最低 250 行目标。
- 最终 `harness/tasks/WP-H-02.json` 为 21 行、五字段 v2；报告明确列出已提交的
  `allowed_paths`/`required_profiles` 修订，不建立批准账本，也没有未提交 task 修订。
- activation 历史校验、治理文件冻结、per-WP forbidden/protected/dependency policy、验收散文映射和
  独立 `preflight` 已删除；CI、AGENTS、Harness README 与开发文档均已迁移到 `current/check/verify`。
- `data/**`、`characters/**`、`third_party/**` 继续作为不可被 allowlist 覆盖的代码内全局保护边界；
  changed-set、重命名、依赖变化、测试删除与原子 JSON report 保持有效。
- ADR-0008、WP-H-01 Spec 和 v1 Plan 已保留原历史内容并移入 archive；既有 v1 task/activation 文件仍在
  HEAD，可用于整体回退，但当前 loader 对 v1 task 返回明确 schema 错误。

## 本地自动结果

- 定向 Harness pytest：38 passed；覆盖 v2 unknown/非法路径/profile/base、Git 四类差异与重命名、全局
  保护、依赖/测试删除、已提交/未提交 task 修订、移除的 preflight、历史 v1 错误、case 去重/顺序/
  首失败/timeout/UTF-8、原子报告和三个临时目录变量。
- `runtime/bin/python3 -m harness run docs`：2/2 case 通过，文档结构测试 4 passed。
- `runtime/bin/python3 -m harness run unit`：593 passed、1 skipped。
- `runtime/bin/python3 -m harness run core-host`：4/4 case 通过；Core unit 117 passed、integration 34
  passed、Provider/模型 25 passed、Memory 13 passed。
- `runtime/bin/python3 -m harness run runtime-v2-shell`：6/6 case 通过；Node 120 passed，Rust
  角色外观/表现、产品 Shell、窗口几何/交互共 54 passed。Provider/Memory Python case 未在 Shell 重跑。
- `runtime/bin/python3 -m harness check WP-H-02`：当前 WP、WP-4-01 accepted 依赖、base ancestor、allowlist、
  全局保护、activation 关闭、测试删除和 task 工作树修订检查全部通过；无越界、保护、依赖或测试删除。
- `git diff --check` 通过。

最终 `verify` 报告为
`temp/harness/20260808T073835.091965Z-WP-H-02.json`，HEAD 为上述实现候选。它在唯一仓库内临时根
`temp/harness/runtime-tmp/20260808T073835.277119Z-91492-7be3dac4` 运行 docs-check、
docs-structure-tests、python-unit-tests 三个唯一 case，各执行一次且全部通过；总耗时 13.769 秒。结果按
规范为 exit 3 / `manual_pending`，人工结果未填写。

## 远端 Test

[GitHub Test run 31246665937](https://github.com/Rvosy/Sakura/actions/runs/31246665937) 在同一实现候选上
全部成功：Agent Development Harness、Documentation checks、Unit tests (3.12) 和 UI tests (3.12)
四个 job 均通过。现有 PR 同步还自动触发了平台 workflow，但 WP-H-02 没有产品代码变化，不把该运行
升级为新的产品三平台验收要求，也不以它替代本 WP 的负责人审查。

## 尚待负责人完成

负责人仍须审查删除的治理概念、最终 task/report、case 去重与临时根实现，以及保留的全局保护、
changed-set、依赖和测试删除边界。确认后可将 WP-H-02 标记 `accepted` 并激活 WP-4-02；在明确声明前，
只能表述为“自动门通过，等待验收”。
