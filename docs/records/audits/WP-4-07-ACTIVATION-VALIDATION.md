---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-25
---

# WP-4-07 激活验证

WP-4-06 已通过自动门、三平台 CI 与项目负责人验收；验收声明见
[`WP-4-06-OWNER-ACCEPTANCE.md`](WP-4-06-OWNER-ACCEPTANCE.md)。WP-4-07 现在成为唯一 active Work
Package，范围只对应 CAP-016。CAP-017 提醒与待办保持未排期。

激活准备只完成以下工作：

- 从当前分支真实 legacy 实现确认自动观察与主动回复的数据和失败语义；没有读取或
  采用路线图明确排除的 WP-4-07 stash。
- 冻结 [`WP-4-07 规范`](../../specs/runtime-v2/WP-4-07-proactive-reminders-todos.md)，明确 WebView 自动截图
  timer、Rust 有界内存批次、普通聊天复用和忙时跳过。
- 更新唯一状态源、CAP-015 验收状态和文档索引。

激活准备本身没有修改生产代码、配置 schema、用户 `data/`、IPC allowlist 或平台捕获实现，也不声称
CAP-016 已经 accepted。后续生产实现必须保持一个最小纵向消费者；不得恢复通用 Scheduler、主动事件系统、
trigger queue、lease、occurrence ledger、claim、outbox、ack、自动退避或 crash-recovery 协议。
