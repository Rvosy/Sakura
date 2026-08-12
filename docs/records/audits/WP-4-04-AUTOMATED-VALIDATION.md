---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-12
---

# WP-4-04 Python 插件能力等价自动验证记录

## 候选与环境

2026-08-12，在 macOS arm64、分支 `refactor/tauri-runtime-v2` 上验证产品候选
`73c501e808ab9c493d40264e3a916db20d2d0a66`。固定 base 为
`80764fa55d9dbb69e44f4bd5f634093f44d79010`；Work Package 当前状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。本记录只写入本机已经执行的自动证据，
不预填 Windows 实机操作、Linux x64、Windows x64 或同一 SHA 三平台 CI 结果，也不把 WP 标记为
`accepted`。

候选按照 [normative Spec](../../specs/runtime-v2/WP-4-04-python-plugin-capability-parity.md) 和
[ADR-0013](../../adr/0013-runtime-v2-generation-private-plugin-worker.md) 增加 generation 私有插件 worker。
Core 主解释器不导入插件实现；worker 通过有界 JSON RPC 提供 tool、prompt patch、context provider、
`app/message/tool` 摘要事件和声明式设置。Rust gateway 与设置 WebView 严格校验脱敏 DTO，插件启停保存
后受控重启 Core 并原位重绑 generation。

## 已执行自动结果

- `runtime/bin/python3.12 -m harness check WP-4-04`：当前任务、accepted 依赖、固定 base、allowlist、全局
  保护边界、测试删除、task 修订和 activation 关闭检查全部通过；越界、保护和 owner-review 文件均为
  0。
- required profiles 的逐项预跑全部通过：`docs` 2/2、`smoke` 3/3、`core-host` 4/4、
  `runtime-v2-shell` 6/6、`journey-tools` 3/3、`journey-plugins` 3/3。
- 最新插件 journey：Python 58 passed，覆盖健康/损坏插件、Qt-free 导入、worker 加载/关闭、工具、
  prompt/context/event、设置/action、超时终止和 Core 纵向链；Rust 2 passed，覆盖 capability 协商与严格
  设置 DTO；前端 4 passed，覆盖私有字段拒绝、保存重绑、失败草稿和 action 边界。
- 额外宽回归：插件相关 Python 86 passed；完整 Runtime v2 前端 149 passed；完整 Rust 串行测试
  282 passed、3 ignored。`cargo fmt`、Python `py_compile` 和 `git diff --check` 通过。
- Windows 验收脚本 `desktop/tests/windows_wp_4_04_plugin_acceptance.ps1` 已加入候选，覆盖隔离 root、健康/
  损坏插件、prompt/context/event、原生工具允许/拒绝、超时失效、设置 action、禁用/启用 generation
  重建、Core 强杀恢复、日志 sentinel、真实 `data/**`/`plugins/**` 零变化和退出零残留；本机没有
  PowerShell/Windows 环境，因此未把脚本存在或静态检查冒充实机执行。
- 首次干净候选 verify 报告 `temp/harness/20260812T125617.938764Z-WP-4-04.json` 在 Core Host 单测中
  暴露了测试同步竞态：100ms 的通用 callback deadline 可能先让并行 `app.start` 结束 worker，测试随后
  收到 `PLUGIN_WORKER_EOF` 而不是其硬编码的工具 timeout。产品仍按规范 fail closed；后续修订增加异步
  contribution 绑定完成同步点，并让故障测试在绑定完成后以 1 秒 deadline 单独触发 30 秒工具 hang。
  本记录保留该失败事实，最终结论只以后续干净候选 verify 报告为准。

## 未执行证据与人工边界

Windows x64 实机清单、Linux x64 自动门、Windows x64 自动门以及同一候选 SHA 的三平台 Runtime v2 CI
尚未在本记录中声称完成。Windows 实机需运行
`desktop/tests/windows_wp_4_04_plugin_acceptance.ps1` 并把操作结果交给项目负责人；脚本本身不会自验收
Work Package。

本记录提交后还必须对干净候选运行 `runtime/bin/python3.12 -m harness verify WP-4-04`。自动门全绿只表示
进入 `manual_pending`，仍需上述平台证据和项目负责人明确验收；Agent 不填写人工结果，也不得把
WP-4-04 标记为 `accepted`。
