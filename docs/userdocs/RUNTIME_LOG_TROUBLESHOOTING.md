---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-13
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
默认 `info` 只保留聊天、窗口生命周期、降级、故障等有诊断意义的事件；界面命令的正常开始/完成和
`runtime_lifecycle_snapshot` 一类高频成功轮询只在 `debug` 级别出现。耗时最多显示两位小数，避免浮点
噪声。如果手动启用 `debug`，日志量会明显增加。

一次对话会按实际发生顺序显示“请求接收 → 记忆召回 → 上下文构建 → 模型请求/回复 → 回复处理/展示”；
工具、截图和 TTS 只在确实执行时插入相应行。`op` 是本轮对话的短标识，`trace` 对应 Agent Trace 文档，
`call` 对应该 trace 内的模型调用序号。排查时先按 `op` 收集普通日志，再用 `trace/call` 定位私密 Trace
中的 request/reply；普通日志里的 `history/memories/tools/estimated_tokens` 只有数量和估算大小，不含正文。

首轮对话会在真正构建 Prompt 前等待 Memory 和 MCP，合计最长约 15 秒，期间仍可取消。如果依赖及时就绪，
本轮就会使用召回记忆和完整已注册工具；如果超时或初始化失败，对话仍会继续，并出现
“Prompt 依赖未就绪，继续降级对话”。该行的 `dependency/status/reason_code/stage/category/error_type` 可
区分 Memory 子进程失败、Memory 启动超时、MCP 注册超时或没有可用服务器。“记忆未就绪，本轮未执行召回”
表示本轮确实没有执行检索，不等同于检索成功但没有相关记忆。

回复后的自动记忆整理会使用新的 `op=memory-…`，并在 Agent Trace 中显示“用途：记忆整理”。这是后台模型
请求，不属于上一轮聊天的后续 call；失败时不会撤销已经显示的回复，但运行日志会保留失败类型供下次重试
排查。

用于分析 Prompt 的私密 Agent Trace 默认开启，写入：

```text
data/logs/sakura-agent-trace.log
```

每次模型请求和回复分别是一个由 `============================================================` 包围的
人类可读中文块，块内字段对齐、不同部分用横线分隔。请求块中的 `提示词 N/M` 顺序就是实际发送给模型的
消息顺序。“上下文汇总”放在正文之前，可先查看历史消息、召回记忆、
动态上下文、工具 schema 和整次请求的估算 token 数。连续历史消息合并在一个 `history.items` 范围块中，
仍保留每条 role、正文和顺序；短正文用单行字符串。工具定义只显示名称和大小/token 成本，一项一行，
不再为每次请求重复几百行固定 schema。`user_input`、`memory`、`tool_result` 等实际动态正文仍会保留，静态
system prompt 与人格只显示区段 ID 和字符数；结构化回复会显示成“模型输出 → 回复片段 → 日文/中文/语气/
立绘”等缩进层级，列表按顺序编号，布尔值显示“是/否”，空值显示“无”。未知字段保留原名。活动文件中
不再出现 JSON 的大括号、方括号、带引号字段名或逗号，也不用于请求回放。

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
   从故障行向上寻找相同 `op`；涉及模型时再记录相同的 `trace/call`。HTTP 故障重点查看
   `status/provider_error_type/provider_error_code/error_type/diagnostic/elapsed_ms/retryable`；Core 超时重点区分
   `deadline_ms` 与 Provider 自身耗时；上下文异常重点查看 `history/memories/tools/estimated_tokens`。若 Prompt
   中没有预期记忆或 MCP 工具，先找同一 `op` 的 `context.dependencies.*`；`ready` 之后仍缺少内容才继续
   检查召回候选/阈值或 MCP allowlist，`degraded` 则先按 reason/stage 处理启动问题。
3. Agent Trace 本来就含聊天、记忆与工具正文。提供给别人前请逐份阅读并按自己的隐私要求处理；如果
   命中 API Key、Authorization、Cookie、密码、token、URL userinfo 或二进制正文，请不要上传，并报告
   隐私缺陷。
4. 不要删除 `data/`、Memory/Qdrant 文件、共享锁或日志来尝试修复。统一日志只用于诊断，不是修复入口。

日志写入失败不会阻止聊天、设置、Core 恢复或正常退出，所以文件缺失不一定表示应用没有运行。第二实例
因共享锁冲突退出时不会触碰日志；应在已经运行的 Sakura 实例中继续操作。

`SAKURA_RUNTIME_V2_LOG_LEVEL=error|warn|info|debug|trace` 可供开发者临时调整运行日志事件密度，默认是
`info`。提高到 trace 仍不会把正文写入普通运行日志，也不会改变 Agent Trace 开关。
