---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-02
---

# WP-3-05 项目负责人验收声明

## 日期与结论

2026-08-02，项目负责人在当前开发会话中明确声明 WP-3-05 验收通过，并确认可以进入下一步。声明覆盖
任务契约中的三项人工门：真实 Windows Tauri/WebView2 下 idle、活动回复和已完成回复后的 Core 强杀；
恢复期中文/日文 IME、连续崩溃预算耗尽、手动重试和退出后的相关进程归零；以及同一候选证据与独立
回退边界审查。

负责人验收的工作树随后原样固化为提交
`f53a42d3885b3d98d9ace37ce164a49d45655635`。本记录只保存负责人实际给出的人工结论，不替代
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 中的状态真相源，也不预先通过 WP-3-06。

## 实机结果

- Core 空闲时强杀不关闭或重建窗口/WebView，不重播 greeting；恢复后下一轮聊天成功。
- 回复生成或逐字显示时强杀只产生一次“连接中断，本次回复已停止”；取消能力消失；完成回复、回复
  翻阅和草稿保留，且不自动重发。
- 恢复期间继续编辑草稿并使用中文/日文 IME 时，组合文本不被提交或清空；恢复后保留最新输入。
- 已完成回复后强杀不重复插入内容，完成内容和当前回复导航保持不变。
- 连续强杀耗尽自动重启预算后窗口仍存在，并显示失败状态和“重试连接”；手动重试后恢复并可发送。
- 应用退出后没有相关 Core、子进程或 Runtime v2 残留；未误杀其他 Python 进程或 Tauri Shell。

## 证据边界与后续处理

自动门与本地环境记录见
[`WP-3-05-AUTOMATED-VALIDATION.md`](WP-3-05-AUTOMATED-VALIDATION.md)：最终 Harness 报告为
`23 passed / 0 failed / 3 manual pending`，其中 pending 是负责人声明前的状态。该记录不因本次人工结论
而倒改。

本次声明没有提供远端 workflow run ID、日志 URL 或新的自动测试计数，因此不补造这些字段；负责人对
同一候选证据和回退边界的明确确认作为人工门关闭依据。若后续发现可复现且可归因于 WP-3-05 的退出
条件缺陷，应按交付治理重新打开该责任 WP，不得通过放宽 WP-3-06 的数据兼容门规避。
