---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-12
---

# Runtime v2 运行日志与故障排查

Runtime v2 现在维护两个用途不同的本地日志。

普通运行日志追加到：

```text
data/logs/sakura-runtime.log
```

它使用接近旧版控制台的单行格式，例如：

```text
[15:56:45] [API] 模型请求失败 │ status=400 elapsed_ms=2789ms
```

文件达到约 10 MiB 后会轮转，最多保留 `sakura-runtime.log.1` 到 `.5`。升级时如果同名文件还是旧版
JSONL，Sakura 会先把整组旧文件原样改名为 `sakura-runtime-jsonl-archive-*`，再创建纯文本日志，二者不会
混写。运行日志不会记录 API Key、对话正文、Prompt、工具参数/结果或绝对路径。

用于分析 Prompt 的私密 Agent Trace 默认开启，写入：

```text
data/logs/sakura-agent-trace.log
```

它没有标题行；每次模型请求和回复分别是一个缩进 JSON 文档，文档之间留两个空行。请求文档中的
`prompt` 顺序就是实际发送给模型的消息顺序；`history`、`user_input`、`memory`、`tool_result` 和工具定义
会保留真实正文，静态 system prompt 与人格只显示 section ID 和字符数。`summary` 可直接查看历史消息、
召回记忆、动态上下文、工具 schema 和整次请求的估算 token 数；回复中的合法 JSON 会直接展开，不会再
显示成带大量反斜杠的字符串。

Agent Trace 仅保存在本机，不会自动上传，但它是高敏感明文文件。API Key、Authorization、Cookie、密码、
token、URL userinfo 和二进制正文会强制移除；普通对话、历史、记忆和工具内容不会脱敏。在设置的“系统”
页可以关闭“记录 Agent Prompt Trace”；关闭后不会创建新的活动文件或 staging，已有文件不会自动删除。
Trace 按日期或约 32 MiB 整块轮转，保留 30 天且总计不超过约 512 MiB。

当前版本没有日志查看器、导出、打开目录或清除按钮，也不会把 Trace 用作聊天历史或请求回放源。

升级前已经存在的 `memory-initialization.jsonl` 会原样保留作为历史记录，但 Runtime v2 不再追加它；新的
Memory 启动诊断请以统一日志为准。

## 遇到问题时

1. 记下问题发生的大致时间，并尽量正常退出 Sakura，让最后的 warning/error 完成刷新。
2. 复制 `data/logs/sakura-runtime.log` 和相邻备份到单独目录；若问题与 Prompt、记忆召回或工具循环有关，
   同时复制 `sakura-agent-trace.log*`，再重新启动，避免后续轮转覆盖现场。
3. Agent Trace 本来就含聊天、记忆与工具正文。提供给别人前请逐份阅读并按自己的隐私要求处理；如果
   命中 API Key、Authorization、Cookie、密码、token、URL userinfo 或二进制正文，请不要上传，并报告
   隐私缺陷。
4. 不要删除 `data/`、Memory/Qdrant 文件、共享锁或日志来尝试修复。统一日志只用于诊断，不是修复入口。

日志写入失败不会阻止聊天、设置、Core 恢复或正常退出，所以文件缺失不一定表示应用没有运行。第二实例
因共享锁冲突退出时不会触碰日志；应在已经运行的 Sakura 实例中继续操作。

`SAKURA_RUNTIME_V2_LOG_LEVEL=error|warn|info|debug|trace` 可供开发者临时调整运行日志事件密度，默认是
`info`。提高到 trace 仍不会把正文写入普通运行日志，也不会改变 Agent Trace 开关。
