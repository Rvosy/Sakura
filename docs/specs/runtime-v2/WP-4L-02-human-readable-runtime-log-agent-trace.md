---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-12
---

# WP-4L-02 人类可读运行日志与 Prompt Trace 规范

## 1. 范围与非目标

本规范冻结两条互不混写的本地日志：`data/logs/sakura-runtime.log` 用于快速阅读运行故障，
`data/logs/sakura-agent-trace.log` 用于分析真实发送给模型的历史、记忆、动态上下文、工具定义和模型输出。
执行状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

不新增日志查看器、目录或清除按钮、远程 telemetry、Runtime 结构化 sidecar、聊天历史源或请求回放源。
Trace 不记录完整静态 system/persona 正文，也不允许因 trace 失败改变任何产品结果。

## 2. 人类可读 Runtime 日志

- Rust 继续是 `sakura-runtime.log` 唯一打开、追加、轮转和刷新者；Core/WebView 继续走 ADR-0012 的受控
  bridge。共享应用锁成功前对日志零写入。
- 每个事件占一行 UTF-8 文本：

  ```text
  [15:56:45] [API] 模型请求失败 │ status=400 elapsed_ms=2789ms
  ```

- 时间是本地时区 `HH:MM:SS`；频道和中文消息来自固定注册表。属性按注册顺序输出为 `key=value`，使用
  空格分隔；没有属性时省略 ` │ `。换行、控制字符、ANSI 和分隔符必须规范化，单行保持有界。
- polling、heartbeat、所有通用 WebView command 成功和高频进度事件为 debug/trace；失败、降级、重启、退出异常和用户需要
  关注的状态使用 info/warning/error。重要事件必须使用固定中文。失败事件必须保留稳定错误码、异常类型、
  阶段和经过凭据/控制字符清洗且限长的 `diagnostic`；不得把完整 traceback、请求/回复正文或任意异常对象落盘。
- `elapsed_ms` 等耗时最多显示两位小数并移除末尾零，不得把 JavaScript 浮点误差直接写入文本日志。
- 首次启动发现活动文件或 `.1` 至 `.5` 仍是旧 JSONL 时，把整组文件原样移动到带时间戳的
  `sakura-runtime-jsonl-archive-*` 归档名，再创建纯文本活动文件；不得解析、重写、截断或混写。
- 保留 ADR-0012 的 1024 有界队列、优先级淘汰、丢弃摘要、250 ms 刷新、warning/error 即时刷新、
  500 ms shutdown 和写入故障隔离。文本日志仍按 10 MiB、5 个备份轮转。

### 2.1 用户可观察事件目录与关联字段

默认 info 日志必须围绕用户可观察的业务链，而不是 IPC、轮询或框架调用组织。一次普通对话至少能够观察
请求接收、记忆召回、上下文构建、Provider 请求与回复、Agent 解析以及回复送达；发生工具、截图或 TTS 时
追加相应阶段。普通日志不记录对话正文、Prompt、工具参数/结果、绝对路径或二进制，只记录安全元数据。

稳定事件族如下：

- `chat.request.received/completed/cancelled/failed`：用户请求进入、最终送达或终止；
- `memory.recall.started/finished/failed`：召回状态、候选/选中数量和耗时；
- `context.prompt.prepared`：最终 payload 的历史条数、记忆数、工具数和估算 token；
- `api.request.started/finished/failed` 与 `api.response.received`：Provider、模型、HTTP 状态、耗时、usage、
  解析状态与工具调用数；失败还保留 Provider error type/code、安全 message、网络异常类型和重试状态；
- `tool.execution.started/waiting_confirmation/finished/failed`：工具名、序号、耗时与稳定错误码；
- `screen.capture.started/attached/cancelled/failed`：截图动作、数量、尺寸和耗时，不含图片/path；
- `reply.processing.finished` 与 `reply.display.completed/failed`：解析结果、segments、变更和展示终态；
- `tts.service.*`、`tts.synthesis.*` 与 `tts.playback.*`：服务、合成和播放的开始、完成或失败。

每个属于交互的事件必须尽可能携带相同 `operation_id`，文本投影为最多 8 个字符的 `op`；每次模型调用
同时携带 Agent Trace 的 `trace` 和 `model_call`，文本投影为 `trace`、`call`。事件属性按事件专属字段顺序
输出，不再用统一“最多五个字段”截断关键诊断信息。没有 Trace 或 Provider usage 时允许省略相应字段，
但关闭 Agent Trace 不得关闭普通运行日志。未知 Core 事件不得以 info 输出“Core 运行事件”；它只能是
debug/trace，直至加入固定目录。

## 3. Trace 人类可读块流与 operation 生命周期

- `agent_trace.enabled` 默认 `true`。关闭时不得创建新的活动文件或 staging；已有文件原样保留。
- 每次模型 request 和 reply 分别序列化为一个由 60 个 `=` 包围的人类可读文本块，块头为
  `[Agent Trace] 模型请求/模型回复`，已知字段、用途、来源、角色、状态和布尔值使用中文，内部 section
  用 60 个 `-` 分隔，块间恰好一个空行。活动文件不得显示 JSON 的对象/数组括号、带引号字段名、逗号或
  转义字符串，也不是 JSON、JSONL 或回放协议。未知 Provider 或模型字段必须保留原名，不能因中文投影丢失。
- 同一 operation 的 `trace` 为进程内单调正整数；每次真实 Provider 调用使用递增 `model_call`。格式修复、
  兼容回退后重发等真实调用不得复用编号。`purpose` 至少支持 `agent_step`、`final_reply`、
  `reply_repair`、`screen_observation`、`proactive_reply` 和 `background_agent`。
- operation 首次记录时创建仅当前用户可读的 staging 文件。每个文档先经过凭据/二进制过滤，再以内部
  可恢复格式落 staging；终态在全局提交锁内一次追加整个 operation，保证不同 operation 不交错。
- 启动时扫描遗留 staging；可恢复文档增加顶层 `status: interrupted` 后成块提交，损坏 staging 只记录
  稳定 Runtime warning 并隔离，不阻止 Core ready。提交成功后才删除 staging。
- 活动 trace 在日期变化或追加下一个 operation 会超过 32 MiB 时整块轮转，绝不拆分 operation。归档名
  含日期和序号；保留最近 30 天，同时所有活动/归档文件总计不超过 512 MiB，删除最旧归档直到满足限制。

## 4. Request 文档与真实 payload 顺序

Request 顶层字段按 `type/trace/model_call/purpose/time/model/summary/prompt/tools/parameters/dropped_context`
输出。`prompt` 每个元素严格对应最终发送给 Provider 的 `payload.messages` 一项；兼容回退删除参数、改变
runtime context role 或合并 system 后，必须记录实际重发的最终 payload，而不是初始意图。唯一压缩例外是
连续的 `history` messages 可以合并为一个范围块；块内 `items` 仍按 payload 顺序逐条保留 role 与正文，
不得跨 `user_input`、`assistant_tool_call`、`tool_result` 或 runtime context 合并。

消息使用单键来源对象：

- 首条静态消息为 `system_prompt`，只记录 `role/chars/sections`。`sections` 按 recipe 构建顺序保存
  `id/chars`，不记录正文；Provider 兼容辅助指令造成的额外字符计入 system 总 `chars`。
- 初始历史使用紧凑 `history` 范围块，块级记录 `messages/chars/estimated_tokens`，`items` 中逐条记录真实
  role 与正文。单行不超过约 100 列的正文直接使用字符串，只有原始多行或需要折行时才使用字符串数组；
  当前输入为 `user_input`，assistant 原生工具调用为 `assistant_tool_call`，tool role 回填为 `tool_result`，
  这些非历史消息仍分别占据真实位置并保留原结构化字段。
- 独立动态上下文消息为 `runtime_context`。`items` 严格按 `ContextPolicy.selected` 顺序，每项使用
  `runtime`、`session`、`memory` 或 `plugin` 单键对象，包含 id、实际发送 content 和估算 token；memory
  额外保留召回 `score/source`。
- 若 tool-message 兼容逻辑把尾部 runtime context 合并进首条 system，则不生成虚构尾消息，而在对应
  `system_prompt` 中增加 `appended_runtime_context`；回退为尾部 user 时按真实末尾位置记录。
- 待发送 message 必须携带 Python 内部 provenance；最终 payload 构建同时剥离全部内部字段并生成 trace
  part。测试必须断言 Provider 捕获的 payload 零 provenance 字段。

`tools` 记录实际 payload 的 count、schema_chars、estimated_tokens。`definitions` 按实际发送顺序只保留
工具 `name/schema_chars/estimated_tokens`，不在每次 request 中重复展开固定 description 和 parameters
正文；每份三字段工具摘要在 Tools section 中占一行，避免工具数量直接放大日志篇幅。这份 Trace 不是 schema
回放源。`parameters` 记录除 `model/messages/tools` 外实际发送参数。
`summary` 放在 `prompt` 前，分别统计 history、memory、动态 context、tool schema 和整次 request 的估算
token，使 Prompt 成本无需先滚过正文即可读取。`dropped_context` 只记录 id、source、chars、
estimated_tokens 与 reason，不得谎称为已发送内容。

## 5. Reply 文档与有效回复变化

Reply 顶层字段按 `type/trace/model_call/purpose/time` 输出，并保留 native `tool_calls`、Provider `usage` 和
`processing`。在解析、tone 清洗或 UI 投影前先记录 Provider 原始 message：

- `content` 是合法 JSON 时解析为内部 `model_output` 嵌套值，保持对象、数组、数字、布尔和 null 类型；
  活动文件按层级展示，数组使用有序编号，布尔显示“是/否”，null 显示“无”。记录 `raw_chars` 与
  SHA-256，不把整段 JSON 作为转义字符串或 JSON 语法重复保存。
- 普通非 JSON 文本使用 `raw_text` 自由文本行数组；看起来是结构化回复但 JSON 非法时同样保存
  `raw_text`，并令 `processing.parse_status` 为 `invalid_json`、记录稳定原因。
- 原生 `tool_calls` 在同一 reply 文档中独立保存实际 id、type、name 和过滤后的 arguments；只有
  pseudo-tool 解析时在 processing 标明来源。
- 格式修复必须形成下一 `model_call` 的独立 request/reply，`purpose: reply_repair`。repair requested、
  parse status 和 repair reason 不得覆盖原始调用记录。
- 只有解析修复、tone 清洗、语言修复或安全兜底实际改变展示结果时，原 reply 文档才增加
  `effective_reply` 与 `changes`；正常 `segments` 和可选 `visual_observation` 不重复保存两份。

`ChatCompletionTurn` 必须保存原始 content、原始 Provider message、usage、解析状态和实际 runtime-context
placement，使 request 在最终 payload 确定后记录，reply 在业务解析前记录。

## 6. 自由文本、隐私与二进制

- history 内单行短文本使用字符串，多行或长文本按约 100 个显示列拆成字符串数组；活动文件把这些值
  显示为连续正文行，不暴露内部数组语法。结构化模型回复在内部保持原字段类型，活动文件只做人类可读
  的递归投影，不改写字符串内容。
- 单个自由文本值 UTF-8 超过 1 MiB 时保留有界头尾，附原始字符数、字节数、SHA-256 与
  `truncated: true`。不得先把超大值完整复制进多个中间结构。
- 普通用户文本、历史、实际选中记忆、动态上下文、普通工具参数/结果和模型输出不脱敏。
- 任意层级字段名匹配 API key、Authorization、Cookie、password、secret、credential、access/refresh
  token 等凭据时删除值；URL userinfo 永久移除。即使用户正文里出现 secret-shaped 普通自然语言也不做
  泛化遮盖，只有明确的凭据键值模式和已知当前 Provider secret 才删除，避免破坏 Prompt 取证。
- bytes、data URL、base64 图片/音频和工具二进制块只记录 mime/type、尺寸、字节数和 SHA-256；正文在
  活动 trace、staging 和 Runtime 日志均必须零命中。

## 7. 设置与故障隔离

Runtime v2 设置页新增 `agent_trace` feature，只提供“记录 Agent Prompt Trace”开关和本地明文隐私说明。
保存写入 `data/config/system_config.yaml` 的 `agent_trace.enabled`，默认 true；保存采用现有原子 YAML 路径，
Core generation 重启后生效。WebView/Rust DTO 不包含 trace 正文、路径内容或凭据。

Trace 与 Runtime 日志的 mkdir/open/write/flush/fsync/rename/chmod/recovery/rotation/retention 错误都只允许
best-effort 稳定诊断，不得改变聊天终态、工具确认、取消、Core readiness/health、设置关闭或应用退出。

## 8. 验收条件

自动测试必须捕获 mock Provider 的最终 payload，逐项比对 trace 顺序、role、来源、正文和统计；覆盖尾部
system、尾部 user、合并首 system、初始对话、多步 tool loop、tool result、确认续接、文本工具摘要、
reply repair、合法 segments/visual_observation、普通文本、非法 JSON、tone 清洗和安全兜底。

文件测试必须证明每个 request/reply 是独立完整文本块、块间一个空行、调用顺序、连续 history 分组不改变
角色/正文顺序、工具摘要顺序和总量准确、summary 在正文前、中文不转义、长文本分行、结构化值以中文
层级展开且活动文件没有 JSON 语法、未知字段不丢失、布尔与空值可辨认、并发 operation
成块、崩溃恢复、日期/32 MiB 轮转、30 天/512 MiB 保留和开关。隐私测试同时断言
普通正文原样存在、凭据与二进制正文零命中。Runtime 测试覆盖旧 JSONL 整组归档、纯文本格式、等级降噪、
Provider/Core/WebView 安全错误详情和 writer 故障隔离。
