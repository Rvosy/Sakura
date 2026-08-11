---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-12
---

# WP-4L-02 真实日志可读性验收缺陷记录

## 发现与影响

2026-08-12，项目负责人检查真实运行日志后指出两项阻塞验收的可读性缺陷：普通日志持续写入
`runtime_lifecycle_snapshot` 成功事件而刷屏；Agent Trace 中一次对话产生上千行，排版改善仍不足以支持
Prompt 优化阅读。该反馈不是验收通过声明，WP-4L-02 从 `stabilizing` 退回 `active`。

只读统计确认当时活动 `sakura-runtime.log` 有 6343 行，其中 6205 行为
`runtime_lifecycle_snapshot` 成功事件，约占 98%。活动 Agent Trace 的一个 request 为 840 行，其中完整
工具定义占 460 行，Prompt 占 358 行；该 request 实际包含 23 条历史消息。统计过程没有输出或修改用户
对话、记忆、工具参数或回复正文。

## 修复契约

- 通用 WebView command 的 started/completed 均为 debug；失败保持 warning。聊天终态、窗口生命周期、
  降级和故障继续使用其专用 info/warning/error 事件。
- 人类可读耗时最多显示两位小数，消除 `1.799999998882413ms` 一类浮点噪声。
- Request 的 `summary` 提前到正文之前；连续 history 合并为一个范围块，正文顺序和 role 不变，逐消息
  chars/token 改为块级总量。
- 每次 request 不再重复完整固定工具 schema；保留实际工具顺序、名称、逐工具 schema 大小/token 估算及
  整体 schema 总量。Trace 继续保留动态 Memory、当前输入、工具调用/结果和模型回复正文。

修复后必须用与本次同等规模的 23 条历史和实际工具集合证明单个 request 显著低于原 840 行，同时逐项
核对 Prompt 顺序、角色、正文、history/tool 总量和隐私过滤。不得通过关闭 Agent Trace 或删除正文规避。
