---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-08-13
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
或凭据。warning/error 应描述稳定失败类别，并持久化经过严格清洗和限长的 `diagnostic`；完整异常对象、
traceback、请求/回复正文仍不得落盘。HTTP 错误只提取 Provider error type/code/message，网络和 IPC 错误保留
异常类型、deadline 与稳定因果摘要。

Rust 编码器只投影固定中文消息和有界标量摘要。轮询、IPC 握手和框架内部成功事件应保持 debug/trace；
info 以 Chat、Memory、Context、API、Tool、Screen、Reply、TTS 的用户可观察里程碑为主。属于交互的事件
复用 operation/Agent Trace 身份，文本中显示短 `op`、`trace` 和 `call`；每个事件必须注册专属字段顺序，
不能依赖通用前五字段摘要。需要 Prompt 正文级诊断时再按 `trace/call` 查看 Agent Trace。

## Python Core 事件

Core Host 启动时安装 stderr bridge。现有 `app.core.runtime_log.log_event` 和 `app.*` 标准 logger 会被转换为：

```text
SAKURA_RUNTIME_LOG_V1\t{"severity":"info","channel":"core.chat",...}
```

只允许 bridge 模块生成该行；业务代码仍使用 `log_event` 或标准 logger。不要 `print` 到 stdout，因为 stdout
只承载 Core framed protocol。聊天 worker 必须进入 operation interaction context，终态后清理。
Memory 启动诊断在 bridge 激活时也使用 `log_event`；已有 `memory-initialization.jsonl` 仅作为历史文件
保留，Runtime v2 不得再打开或续写它。

Assistant Session 发布 ready 后，Memory preload 和 MCP discovery 可能仍在后台进行。`RealChatBoundary`
必须在读取历史、召回 Memory 和构建 Provider payload 前调用 session 的 Prompt dependency gate；Memory
与 MCP 共用 15 秒总预算并传播聊天 cancel checker。不要在 AgentRuntime 内再做第二次等待，也不要快照
一个随后才注册工具的 ToolRegistry。降级事件只能携带稳定 status/reason/stage/category/error type，不能
携带 Memory message、MCP command 或配置正文。

新增 Core 业务事件必须同时加入 Python `_FIXED_MESSAGES`、安全 attribute 白名单和 Rust `core_message`；
未注册的 info 事件会被降为 debug/trace，并以内部诊断处理，防止自由文本重新污染默认日志。正文、完整
异常对象、工具 arguments、路径和二进制不能为了“方便定位”加入白名单；失败事件使用稳定
`stage/code/status/error_type/diagnostic` 与计数、耗时、模型 usage 描述问题。`diagnostic` 必须在业务边界
生成、经过凭据和控制字符清洗、最长 320 字符，并在 Rust writer 再次验证。

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
单调增加；终态后才在 commit lock 下把整个 operation 以 `====` 包围的 Request/Reply 文本块追加到活动文件。
staging 继续使用紧凑 JSON 作为内部崩溃恢复格式，不新增结构化 sidecar。崩溃残留 staging 在下次启动恢复为
`status: interrupted`。写入、轮转、恢复、retention 或清理失败都必须 best-effort 隔离。

自动记忆整理不能复用已结束的 chat trace。`MemoryBoundary` 为每个整理任务生成
`memory-curation-*` operation，同时进入 `interaction_context` 和 `AgentTraceRecorder.operation`；创建的
Provider client 必须注入同一个 recorder，`MemoryCurator.complete_raw` 必须设置
`PromptTraceMetadata(purpose="memory_curation")`。成功、失败和取消都不得改变已完成聊天终态。

请求块先输出“上下文汇总”，再按最终 payload 顺序输出 `提示词 N/M［来源］` 分节。连续 history 会
合并成一个范围块，块内 `items` 保持原 role、
正文和消息顺序；短单行正文直接使用字符串。固定工具 schema 不重复展开，`tools.definitions` 只保留每个
工具的名称、schema 字符数和 token 估算，并按一工具一行输出。`user_input`、动态 context、memory、工具
调用/回填和回复正文不因紧凑显示而省略。

自由文本按约 100 个显示列折行；合法结构化回复在内部维持原字段类型，活动文件通过通用层级渲染器输出
中文字段、编号数组、“是/否”和“无”，不显示 JSON 标点。已知字段使用固定中文映射，未知字段保留原名；
工具 arguments 若是合法结构化文本，也按相同层级展开。超过 1 MiB 的
单值保存头尾、大小和 SHA-256。普通正文不脱敏；凭据键、内联凭据、已知 API secret、URL userinfo 和
data URL/bytes 正文必须零命中。新增 trace 字段时需要同时覆盖普通正文存在与敏感 sentinel 不存在两类
断言。

## WebView 事件

前端通过 runtime diagnostics 模块批量提交固定事件。invoke 包装器只记录 command 名、稳定 outcome/code、
耗时、operation ID 和 revision；通用 command 的 started/completed 属于 debug，只有失败进入 warning，
避免默认 info 被生命周期快照等成功轮询淹没。专用聊天、窗口生命周期、降级和故障事件仍按各自固定级别
记录。包装器不检查或复制 args/result/error message，业务 Promise 的返回值和拒绝对象必须原样传回，
诊断 command 失败必须被吞掉。人类可读 `_ms` 字段最多保留两位小数并去掉尾零。

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
