---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-11
---

# WP-4-03 激活契约自动验证记录

## 候选与范围

2026-08-11，在 Windows x64、分支 `refactor/tauri-runtime-v2` 上验证 WP-4-03 激活提交
`777adc347375c94063613280d8a1c5a43aa15ab7`。固定 base 为
`a3156f3b78177816352eef82004c91b982e24513`；当前 Work Package 状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

该提交只记录 WP-4L-01 项目负责人验收，新增 WP-4-03 Spec、Plan、task v2 和 `journey-mcp` 现有 Python
baseline，并激活 WP-4-03；没有修改 MCP 或其他产品实现。本记录不把 baseline 通过解释为 WP-4-03 功能
完成，也不预填未来 Windows 实机或三平台 CI 结果。

## 已执行结果

- 提交前 `runtime\python.exe -m harness run docs`：2/2 case 通过；文档结构、元数据、链接和真相源检查
  通过，文档规范单测 4 passed。
- 提交前 `runtime\python.exe -m harness run journey-mcp`：1/1 case 通过；现有
  `tests/unit/test_mcp_runtime.py` 为 4 passed，冻结 runtime token、缺失 stdio command、超时 loop 替换和
  资源关闭 baseline。
- 提交后 `runtime\python.exe -m harness check WP-4-03`：当前 WP、依赖、固定 base、allowlist、全局保护
  路径、activation 关闭、测试删除和 task 修订全部通过；越界文件、保护文件和 owner-review 文件均为 0。
- 提交后 `runtime\python.exe -m harness verify WP-4-03`：报告
  `temp/harness/20260810T171621.011614Z-WP-4-03.json` 绑定上述提交；19/19 唯一自动 case 通过，failed、
  pending 和 blocked 均为 0，机器状态为 `manual_pending`。required profiles 为 `docs`、`smoke`、
  `core-host`、`runtime-v2-shell`、`journey-tools` 和 `journey-mcp`，重叠 case 由 Harness 去重执行。

## 结论与后续边界

WP-4-03 的激活契约和既有回归自动门通过，可以按 Spec/Plan 开始 RED 与产品实现。当前人工状态仍为
`pending`；`journey-mcp` 目前只包含既有 Python baseline，后续必须加入真实 Core、Rust 受控进程树和
frontend 设置纵向用例，并完成三平台自动门与 Windows 实机桌面 MCP 验收。项目负责人明确验收前不得把
WP-4-03 标记为 `accepted`。
