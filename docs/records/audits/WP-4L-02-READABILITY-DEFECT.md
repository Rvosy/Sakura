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

## 修复与复验结果

候选 `4d0bef57142db1742e91443330f64e30fbdfc81a` 已完成修复。Rust 持久化边界不再信任各 WebView 自报的
通用成功事件级别，统一把 `webview.command.started/completed` 归为 debug，失败仍为 warning；默认 info
因此不会再写入 `runtime_lifecycle_snapshot` 成功轮询。人类可读 `_ms` 值最多两位小数并去尾零。

Agent Trace 将 `summary` 前置，连续 history 合并为一个保留逐条 role、正文与顺序的范围块；短正文保持
单行，工具定义只保留名称、schema 字符数和 token 估算，每工具一行。23 条短历史和 18 个工具的隔离合成
request 为 169 行；缺陷现场旧 request 为 840 行，但正文长度不同，因此不把两者声明为同 payload 的精确
压缩率。当前输入、Memory、工具调用/结果、模型回复、总量统计和隐私过滤均未删除。测试只写系统临时
目录，没有修改用户 `data/logs`。

`harness verify WP-4L-02 --report temp\harness\WP-4L-02-readability.json` 对该候选得到 17/17 自动 case
通过、0 failed、0 blocked，机器状态为 `manual_pending`。Python 全量为 unit 661 passed/6 skipped、
integration 58 passed/2 skipped、Qt UI 24 passed；完整 Rust 为 299 passed/24 ignored。WP-4L-02 据此恢复
`stabilizing`，仍需负责人用真实应用重新验收这两项可读性行为，不得由 Agent 标记 `accepted`。

## 第二次复验：降噪后仍不可定位

2026-08-12，项目负责人复验真实主程序日志，确认成功轮询刷屏已经消失，但普通日志过度简化：对话只留下
“开始处理”、Core 请求终态和若干 `Core 运行事件`，看不到记忆召回、最终 Prompt 规模、具体模型调用、
回复解析、工具、截图、TTS 与 UI 送达之间的业务链，因此仍不能用于定位故障。项目负责人要求参考旧程序
中用户能感知的事件，并适配 Runtime v2 的分层所有权，而不是恢复全部旧自由文本。

静态审计发现旧代码约有 439 个字面量 `log_event` 调用，而 Core bridge/Rust 固定事件注册表只覆盖少量
事件；未注册 Core 事件最终显示为泛化的“Core 运行事件”。同时 API 发送、HTTP 成功、原始回复和工具成功
被统一降为 debug，Rust 文本编码又只投影通用优先级中的前五个属性，已有 `operation_id/trace_id` 没有显示。
这些共同造成“安静但不可定位”。

本轮修复将 WP-4L-02 再次退回 `active`。验收目标是让默认 info 日志围绕用户可观察业务里程碑组织，
普通对话保持约 7–10 行，工具/截图/TTS 只在实际发生时增加少量阶段行；正文继续只进入私密 Agent Trace，
两份日志通过 `op/trace/call` 关联。该记录是验收缺陷，不代表负责人已接受后续修复。

## 第二次修复的当前验证状态

2026-08-12 已实现有限业务事件目录、最终 payload/Provider 边界日志、Memory 召回指标、旧截图/TTS 事件
映射、Core bridge 白名单及 Rust 的 `op/trace/call` 和事件专属字段投影。新增 Rust 断言得到示例：

```text
[CONTEXT] 模型上下文已构建 │ op=chat-123 trace=17 call=2 purpose=agent_step history=8 memories=3 tools=18 estimated_tokens=11684
```

定向 Python 为 109 passed，WP-4L-02 Rust 为 8 passed，docs、`journey-observability` 和
`journey-agent-trace` 均通过；排除共享锁验收组后的 integration 为 49 passed/11 deselected。完整 Python
unit 为 664 passed/6 skipped。一次完整 `harness verify WP-4L-02` 在 integration 阶段因真实 Sakura 仍持有
全局单实例锁而得到 9 passed/1 failed/7 blocked；完整 Rust 同因该锁得到 298 passed/3 failed/24 ignored。
结构化回复用例曾因新增 stderr 事件超过未消费测试 pipe 容量而超时，随后通过去除重复 Prompt/API debug
事件和压缩 Context bridge 字段修复，隔离复跑 2 passed。

因此 WP-4L-02 继续保持 `active`。需要项目负责人正常退出正在运行的 Sakura 后重新执行完整 verify；在
17/17 自动 case 全绿之前不得恢复 `stabilizing`，本节也不构成人工验收结论。

## 退出真实应用后的完整复验

项目负责人随后明确确认 Sakura 已正常退出。单独复跑共享锁数据兼容组得到 9 passed/2 skipped；再次执行
`harness verify WP-4L-02 --report temp/harness/WP-4L-02-business-events-final.json` 得到 17/17 自动 case
通过、0 failed、0 blocked，机器状态为 `manual_pending`。其中 Python unit 为 664 passed/6 skipped、
integration 为 58 passed/2 skipped、Qt UI 为 24 passed；observability 与 Agent Trace 的 Python、Rust、
frontend journeys 全部通过。

WP-4L-02 据此恢复 `stabilizing`，只表示自动门通过。负责人仍需启动真实候选并检查一轮普通对话、工具、
截图与 TTS 的默认 info 日志是否形成可定位业务链；负责人明确验收前不得标记 `accepted`。
