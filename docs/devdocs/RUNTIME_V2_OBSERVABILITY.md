---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-08-12
---

# Runtime v2 运行日志开发指南

Runtime v2 的普通运行日志为 `data/logs/sakura-runtime.log`。Rust 日志服务是唯一文件 writer；Python
Core 和 WebView 不得自行打开该文件。它输出 `[HH:MM:SS] [CHANNEL] 中文消息 │ key=value`，不再输出
JSONL。旧 JSONL 组在首次写入前原样归档，不能与纯文本混写。运行日志边界见
[`WP-4L-01 Spec`](../specs/runtime-v2/WP-4L-01-runtime-observability.md)，双日志与 Prompt Trace 契约见
[`WP-4L-02 Spec`](../specs/runtime-v2/WP-4L-02-human-readable-runtime-log-agent-trace.md)。

## Rust 事件

Rust 调用统一服务提交固定 `channel/event/message` 和批准的关联 ID/attributes。调用必须是非阻塞的；
调用方不能因日志返回失败改变 command、health 或 shutdown 结果。新增事件时同时增加注册表/字段测试，
不要把 `Debug` 格式、原始 `Error`、路径、请求/响应对象或环境变量塞进 attributes。

等级由 `SAKURA_RUNTIME_V2_LOG_LEVEL` 控制，默认 `info`。`debug`/`trace` 只增加事件密度，不允许记录正文
或凭据。warning/error 应描述稳定失败类别；具体异常正文只留在内存中的有界故障诊断，不持久化。

Rust 编码器只投影固定中文消息和有界标量摘要。轮询、请求准备、普通成功和正文丰富事件应保持
debug/trace；info 只保留用户值得关注的生命周期与结果。不要依赖 JSON 字段、sequence 或 correlation ID
仍会落盘；需要 Prompt 级诊断时使用 Agent Trace。

## Python Core 事件

Core Host 启动时安装 stderr bridge。现有 `app.core.runtime_log.log_event` 和 `app.*` 标准 logger 会被转换为：

```text
SAKURA_RUNTIME_LOG_V1\t{"severity":"info","channel":"core.chat",...}
```

只允许 bridge 模块生成该行；业务代码仍使用 `log_event` 或标准 logger。不要 `print` 到 stdout，因为 stdout
只承载 Core framed protocol。聊天 worker 必须进入 operation interaction context，终态后清理。
Memory 启动诊断在 bridge 激活时也使用 `log_event`；已有 `memory-initialization.jsonl` 仅作为历史文件
保留，Runtime v2 不得再打开或续写它。

## Prompt/Agent Trace

`app.agent.trace.AgentTraceRecorder` 是 Python Core/Legacy 内的独立私密 writer，只写
`data/logs/sakura-agent-trace.log`，不经过 stderr bridge。`AgentTraceSettings(enabled=True)` 从
`system_config.yaml` 的 `agent_trace.enabled` 加载。Runtime v2 设置保存必须原子更新该窄字段并重启 Core
generation；WebView/Rust DTO 只能包含布尔值和 generation 身份，不能包含正文、文件内容或凭据。

消息构造阶段用 `_sakura_trace_provenance` 的 `MessageProvenance` 标记 `history`、`user_input`、
`assistant_tool_call`、`tool_result` 和 runtime context。`OpenAICompatibleClient` 在构造最终 payload 时同时
生成平行 provenance，并在网络请求前从每条 Provider message 移除内部字段。测试必须捕获真实最终 payload，
逐项比较 trace 顺序，并对 payload 深度扫描 provenance 零命中。

每个模型尝试先写 request staging，原始 Provider message 在业务解析前写 reply。一次用户/主动 operation
中的兼容回退、工具循环、确认续接、屏幕观察 follow-up 和 reply repair 共用同一 trace 编号，`model_call`
单调增加；终态后才在 commit lock 下把整个 operation 追加到活动文件。崩溃残留 staging 在下次启动恢复为
`status: interrupted`。写入、轮转、恢复、retention 或清理失败都必须 best-effort 隔离。

自由文本按约 100 个显示列输出为字符串数组；合法结构化回复维持原字段类型并缩进展开。超过 1 MiB 的
单值保存头尾、大小和 SHA-256。普通正文不脱敏；凭据键、内联凭据、已知 API secret、URL userinfo 和
data URL/bytes 正文必须零命中。新增 trace 字段时需要同时覆盖普通正文存在与敏感 sentinel 不存在两类
断言。

## WebView 事件

前端通过 runtime diagnostics 模块批量提交固定事件。invoke 包装器只记录 command 名、稳定 outcome/code、
耗时、operation ID 和 revision；它不检查或复制 args/result/error message。业务 Promise 的返回值和拒绝
对象必须原样传回，诊断 command 失败必须被吞掉。

## 本地验证

```powershell
runtime\python.exe -m harness run journey-observability
runtime\python.exe -m harness run journey-agent-trace
cargo test --manifest-path desktop\src-tauri\Cargo.toml --locked wp_4l_02 -- --test-threads=1
node --test desktop/frontend/tests/runtime-diagnostics.test.js
node --test desktop/frontend/tests/agent-trace-runtime.test.js
```

排查隐私回归时，用专门 sentinel 作为 API key、聊天正文、工具参数、绝对路径和 generation credential，
退出后分别扫描 `sakura-runtime.log*` 与 `sakura-agent-trace.log*`。测试只能使用隔离 assistant root，
不得用真实用户 `data/` 注入 sentinel。
