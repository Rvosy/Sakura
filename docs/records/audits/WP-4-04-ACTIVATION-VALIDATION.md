---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-4-04 激活契约自动验证记录

## 候选与范围

2026-08-12，在 macOS arm64、分支 `refactor/tauri-runtime-v2` 上验证 WP-4-04 激活提交
`2a1bf3925d9688d1da1f7cb302fd9045adab2ea2`。该分叉激活时固定 base 为
`80764fa55d9dbb69e44f4bd5f634093f44d79010`；整合已验收的 WP-4L-02、WP-4-01B 与 WP-3-03B 后，当前
审计 base 前移为 `7884e6d41bf2c29ce1ce7472f6936fa3ed29763c`。当前 Work Package 状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

该提交只记录 WP-4-03 项目负责人验收，新增 WP-4-04 ADR、Spec、Plan、task v2 和
`journey-plugins` 既有 Python baseline，并激活 WP-4-04；没有修改插件或其他产品实现。本记录不把
baseline 通过解释为 WP-4-04 功能完成，也不预填未来三平台 CI 或 Windows 实机结果。

## 已执行结果

- 修改前 `runtime/bin/python3.12 -m harness check WP-4-03`：当前任务、依赖、固定 base、allowlist、保护
  路径、测试删除和 task 修订全部通过。
- 提交前 `runtime/bin/python3.12 -m harness run docs`：2/2 case 通过；文档结构、元数据、链接和真相源
  检查通过，文档规范单测 4 passed。
- 提交前 `runtime/bin/python3.12 -m harness run journey-plugins`：1/1 case 通过；既有
  `tests/unit/test_plugin_system.py` 为 52 passed，冻结 manifest、权限、贡献、失败隔离和清理基线。
- 提交后 `runtime/bin/python3.12 -m harness check WP-4-04`：当前 WP、当时 WP-4-03 accepted 依赖、固定 base、
  allowlist、保护路径、activation 关闭、测试删除和 task 历史全部通过；越界、保护和 owner-review 文件
  均为 0。
- 提交后 `runtime/bin/python3.12 -m harness verify WP-4-04`：报告
  `temp/harness/20260812T120119.406270Z-WP-4-04.json` 绑定上述提交；19/19 唯一自动 case 通过，failed、
  pending 和 blocked 均为 0，机器状态为 `manual_pending`。required profiles 为 `docs`、`smoke`、
  `core-host`、`runtime-v2-shell`、`journey-tools` 和 `journey-plugins`，重叠 case 由 Harness 去重执行。

## 结论与后续边界

WP-4-04 的激活契约和既有回归自动门通过，可以按 Spec/Plan 开始 RED 与产品实现。当前
`journey-plugins` 只有 legacy 插件领域 baseline，尚未覆盖 generation 私有 worker、Core/Rust 受控进程
树、ToolRegistry/Action ID、prompt/context/event 或设置 WebView 纵向链；这些必须由后续实现和测试补齐。

当前人工状态为 `pending`。完成生产实现、三平台自动门和 Windows 隔离实机验收并由项目负责人明确验收
前，不得把 WP-4-04 标记为 `accepted` 或开始 WP-4-05。
