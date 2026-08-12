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

## 分叉整合前的干净候选复验

2026-08-12，日志业务事件链收口为干净候选
`bc643954615304aefdcb9e78b78ebadbbb5e03d2`。执行前已确认远端并行 WP-4-04 停留在
`c6a3fa6b5c73825af387fbe809b742832d4f0b8f`，本轮没有合并、改写或丢弃任一方提交。

该候选的 `harness check WP-4L-02` 通过；定向 Python 回归为 153 passed。随后在干净 HEAD 上执行
`harness verify WP-4L-02`，机器报告
`temp/harness/20260812T133510.969420Z-WP-4L-02.json` 为 17/17 自动 case 通过、0 failed、0 blocked，
状态为 `manual_pending`。其中 Python unit 为 665 passed/6 skipped，integration、Qt UI、observability
和 Agent Trace 门禁均通过。

WP-4L-02 继续保持 `stabilizing`。本次复验只固定分叉整合前的候选与自动证据；在负责人明确验收真实
普通对话、工具、截图、TTS 和 Agent Trace 前，不得标记 `accepted`，也不得开始合并 WP-4-04。

## 第三次实机验收：失败原因不足与 JSON Trace 不可读

2026-08-12，项目负责人直接检查 `data/logs/sakura-runtime.log` 与
`data/logs/sakura-agent-trace.log`，明确指出当前候选仍不能验收：普通日志用于用户提交现场后排查问题，
不能只提示“出现错误”；Prompt Trace 应采用类似 TTS 请求报告的 `====` 包围、字段对齐、section 分隔形式。

只读审计最近一次实机现场确认：`21:40:43` 的两条“模型请求失败”只有 `elapsed_ms/attempt/retryable`，
Provider/网络原因已在 Python bridge 与 Rust writer 的错误字段过滤中丢失；`REQUEST_DEADLINE_EXCEEDED`、
`INVOKE_FAILED` 和 `CORE_HOST_TRANSPORT_ERROR` 也只剩稳定码，没有 deadline、阶段或安全因果摘要。相同
`op=chat-000 trace=1 call=1` 在 Shell 等待 30 秒超时后，底层 Provider 请求仍继续到 60 秒失败并重试成功，
现有文本没有解释两个 deadline，容易误判。Trace 则仍是跨数百行的缩进 JSON 文档，需要穿过多层括号阅读。

WP-4L-02 据此退回 `active`。修复契约为：普通日志保留单行业务链，但失败事件增加经过凭据、URL userinfo、
控制字符和长度过滤的 `diagnostic`，并保留 Provider error type/code、异常类型、deadline 与 retryable；
请求/回复正文、traceback、任意异常对象仍不得进入普通日志。活动 Agent Trace 改为每次 request/reply 一个
人类可读文本块，Summary 在正文前，Prompt section 仍严格遵循最终 payload 顺序，结构化模型输出继续以
未转义缩进 JSON 展开。内部 staging 保持 JSON，仅用于崩溃恢复，不新增 sidecar。

为修复实机出现的 `INVOKE_FAILED` 详情丢失，任务 allowlist 只增补既有
`desktop/frontend/core/runtime-diagnostics.js` 及其定向测试；不增加新的前端数据通道，不改变固定 base、
依赖或 required profiles。WebView 只允许预注册基础设施错误码携带限长诊断，未知错误码继续投影为
`INVOKE_FAILED` 且不携带任意异常文本。

## 第三次缺陷修复与自动复验

2026-08-12，第三次缺陷修复收口为干净候选
`e8abcca20bb5262e96bd6b9e322b9cb3bc75aaa6`。运行日志继续保持面向用户可观察业务事件的单行格式，
但 Provider 失败现在保留经过凭据、URL userinfo、控制字符和长度过滤的 error type、error code、异常类型
与诊断摘要；Core IPC 失败同时记录稳定错误码、请求期限和安全因果摘要。这样能够从同一 `op/trace/call`
看出 Shell 的 30 秒等待期限与底层 Provider 的 60 秒请求及后续重试是两个不同阶段，而不会把前者误解为
底层任务已经停止。WebView 只允许固定基础设施错误码携带清洗后的诊断，未知错误仍拒绝任意异常文本。

活动 `sakura-agent-trace.log` 已改为 60 字符 `=` 边界、`[Agent Trace] Model Request/Reply` 标题、
对齐字段和分节的文本报告。请求中的 Prompt section 仍严格遵循最终 Provider payload 顺序；静态 system
与 persona 只显示 section ID 和字符数，历史、当前输入、动态上下文、Memory、工具及结果保留实际内容，
结构化模型回复继续以未转义、缩进 JSON 展示。私密 staging 继续使用紧凑 JSON，以保持 operation 原子
追加、崩溃恢复、轮转和保留能力；没有新增 sidecar，也没有改写用户已有日志。

在该提交上执行 `harness check WP-4L-02` 通过。随后执行 `harness verify WP-4L-02`，机器报告
`temp/harness/20260812T141248.141353Z-WP-4L-02.json` 为 17/17 自动 case 通过、0 failed、0 blocked，
状态为 `manual_pending`；Python unit 为 666 passed/6 skipped，全部 required profile 均通过。
WP-4L-02 据此恢复 `stabilizing`，仍需负责人启动真实候选并检查一次失败链和一次正常对话生成的两份
日志。该自动证据不构成人工验收，不得标记 `accepted`，也不得开始合并 WP-4-04。

## 第三次候选的真实启动复核

2026-08-12，Agent 按负责人“实机看两份日志”的要求重新构建 debug EXE，并在真实用户数据根连续执行
启动、等待 Core/MCP 就绪、正常关闭。复核没有删除或改写既有 `data/logs`，只读取每次启动新增的尾部。
第一次启动确认候选仍写入重复 `Runtime diagnostic event`、无因果摘要的普通 stderr 告警，以及丢失
`server_id/reason_code` 的 MCP 泛化事件，因此 WP-4L-02 再次退回 `active`。

后续修复删除了与既有 Core lifecycle 重复的 Shell 诊断和重复“Core 已停止”，将所有非失败型内部窗口/
Memory gateway 诊断降为 debug；MCP 连接、失败和注册完成进入固定事件目录并保留服务器 ID、稳定原因码及
listed/filtered/registered 数量。普通 stderr 首条警告保留脱敏、限长诊断，真实路径和 URL 分别替换为
`[PATH]`、`[URL]`。实际复核因此能够直接读出 `server_id=web reason_code=TRANSPORT_FAILED` 以及
`[Errno 22] Invalid argument`，同时没有泄露本机路径或 URL userinfo。角色 presentation 启动期间的预期
NOT_READY/UNAVAILABLE 重试已降为 debug，不再伪装成 info 级故障。

正常关闭现场仍出现 `CORE_HOST_TRANSPORT_ERROR category=WriterError`。进一步静态与动态审计确认这不是可忽略
的普通 pipe close，而是 Assistant 初始化资源未在既定退出期限内结束；日志边界现从稳定异常 code 或安全
code 前缀恢复 `SHUTDOWN_DURING_INITIALIZE`，显示“Assistant 后台初始化未在退出期限内结束”，同时拒绝
原始异常正文。这一行因此保留为真实回收问题的可定位证据，而不是隐藏失败。

定向验证为 Core 协议/日志 34 passed、前端诊断 8/8、Rust WP-4L-02 10/10；`journey-observability`、
`journey-agent-trace` 与 `docs` 均通过。当前状态继续为 `active`，需提交后重新运行完整 verify；本节不构成
人工验收结论。
