---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-18
---

# Assistant 插件与 Core 对话边界

## 职责与可替换性

`sakura.assistant` 是唯一服务，使用普通 Plugin Runtime v4 的声明、进程、依赖、配置和回收机制。
预装实现 `sakura.assistant.default` 位于 `plugins/builtin/sakura_assistant/`，可以停用并由提供同一服务的
其他插件替换。多个启用实现同时声明该服务时按普通服务冲突处理；Core 不按实现 ID 选择隐藏兜底。

默认插件拥有模型客户端、Prompt、上下文预算、历史选择、工具循环、模型响应解析与修复，以及 Agent Trace。
Core 拥有角色和设置、受理与取消、Timeline 写入、附件授权、表现控制派发、TTS 消费与终态发布。
Core 的 `AssistantSession` 保存角色、固定实例的 `BoundAssistant`、已发布的模型快照和轮次配置，不创建本地 Agent 或 ChatPipeline。
插件停用、退出或重载后，旧会话失效；恢复必须来自显式插件操作或重新初始化，不重放已受理操作。

插件只导入公开 SDK 和自身包。公开 DTO 位于 `sakura_assistant_contract`、`sakura_context`、
`sakura_tools`、`sakura_model`、`sakura_cancellation` 等模块；Prompt 与模型协议解析仍属于插件实现。
SDK 不暴露 `app.*`、Core Session、TimelineStore、callable 或宿主 Python 对象。

## 服务合同

Provider 在 setup 中公开以下六个方法。所有参数与返回值走有界 JSON；输入、完整结果和大历史页用 artifact
描述符传递，不把文件正文塞入单帧 RPC。

| 方法 | 参数与结果 | 含义 |
|---|---|---|
| `prepare(session)` | 返回 `state/code/message/retryable` | 只检查本地可用性，不请求模型；`ready/degraded` 才能建立可用会话 |
| `begin({operationId, input})` | 返回 `{operationId}` | 接受一个输入 artifact，启动后台执行；重复提交不具有重试语义 |
| `poll(operationId, afterSequence=0, waitMs=500)` | 返回 `state/sequence/progress`，失败时附 `failure` | 最多等待 500 ms；sequence 为已产生进度项总数，只返回 afterSequence 之后的项目 |
| `result(operationId)` | 返回已提交的 JSON artifact 描述符 | 运行中报 `ASSISTANT_RESULT_PENDING`；失败时抛出原错误并保留诊断链 |
| `cancel(operationId)` | 返回 `{cancelled: true}` | 请求取消并唤醒等待者；不等同于 worker 已停止 |
| `release(operationId, terminalStatus)` | 返回 `{released: bool}` | terminalStatus 为 `completed/cancelled/failed`；释放 Trace、输出和执行槽 |

`prepare.state` 只接受 `ready/degraded/setup_required/failed`；`code` 是 1–80 字符的公开标识符，
字符集为 `[A-Za-z0-9_.:-]`，允许第三方使用 `vendor.account_required` 等命名，不限于内置错误枚举。
`message` 必须是至多 2000 字符的脱敏字符串，`retryable` 必须是 JSON boolean；它只描述是否可由用户重试，
不触发 Core 重启、重新 prepare 或自动恢复。Core 投影这四项，忽略未知附加字段；形状无效时报
`ASSISTANT_PREPARE_INVALID` 并保留初始化诊断。`ready/degraded` 才建立可用 Session，其他状态不阻止已加载的角色显示。

`poll.state` 为 `running/completed/failed/cancelled`。其中 completed 只表示插件结果可读；是否写入历史并
发布 `chat.completed` 由 Core 最终裁决。`failure` 包含稳定 `code/message/retryable` 和可选诊断 `attributes`，
不包含消息正文或凭据。找不到操作报 `ASSISTANT_OPERATION_NOT_FOUND`。

默认插件一次只持有一个操作，包括尚未 release 的终态操作；新 begin 报 `ASSISTANT_BUSY`。
关闭后 begin 报 `ASSISTANT_CLOSED`。worker 未退出时 release 设置取消并返回 false，保留原 owner；
worker 退出后完成回收，不开第二个 worker 绕过占用。Trace 失败不得阻止输出回收或执行槽释放。

### 输入与结果

Core 构造输入 JSON，至少包含 `operationId/session/turnId/message/historyCursor/historyNow/entryIds`，
并按本轮输入提供 `event/attachment/humanEntryId/observationEntryIds`。`session` 包含角色 ID、显示名、
回复语气、系统说明、loopSettings、appVersion、modelSlots 与可空 replyVisual；不包含 Core 对象或 generation credential。
公开聊天入口不接受调用方指定的系统说明、历史数组或这些内部字段。

Host 在创建输入 artifact 时增加 `historyToken`，将读取许可固定到接收插件、角色和历史快照。
图片及长文本随 JSON 文件传递；插件通过 artifacts.resolve 取得允许读取的本地路径，检查实际长度后解析。
输入文件读完可以 release_received；历史 token 必须保留到 worker 停止。

`modelSlots` 为 Core 成功准备会话时发布的 chat/vision_chat 配置快照，可能包含调用模型所需的凭据，只允许
经受控 prepare 或输入 artifact 交给绑定插件。默认 Assistant 不在每轮重新读取磁盘或 Host active；
设置保存但应用失败时，下一轮继续使用已发布快照，不隐式应用失败的变更。

角色说明也在创建 Session 时读取并冻结。角色卡保存后，Core 先准备新的角色、提示词、视觉绑定和公开投影，
校验通过才一起发布；准备失败时，旧提示词、视觉和 revision 保持不变。模型热应用返回 `failed` 或绑定失败时，
设置回执必须为 `CONFIG_APPLY_FAILED`；仍有效的旧 Assistant scope 继续服务，已撤销的 scope 不得保留。
明确清空模型配置或停用 Assistant 则发布 `setup_required` 并退休旧 Session，角色仍可显示。

输出 JSON 为 `{reply: {segments: [...]}, actions: [...], visual_observation: object|null}`。
segment 使用公开 DTO 的 `text/translation/tone/portrait/suppress_tts/control`。Core 验证结果形状，按表现
插件合同封装待解析 control，再投影为桌面协议中的 `suppressTts` 等公开字段。正文提交与 Assistant 释放
不等待表现 Provider；桌面准备播放时另行请求解析控制。actions、模型 continuation、
内部错误和工具参数不进入聊天终态。插件无权直接写入用户或助手 Timeline 条目。

## 固定实例与未知结果

Core 在会话创建时取得 `{providerId, scopeId}`，后续 prepare、begin、poll、result、cancel、release 都绑定
同一进程生命周期。scope 使用运行时生成的随机身份，不靠 PID 判断；同 ID 插件重载也会产生新 scope。
RPC 路由在调用前和返回后验证身份，旧操作不能查询、取消或 release 新进程中的同名 operationId。

最终结果提交还要与服务失效进行原子仲裁：Core 通过 `commit_bound_service`，在运行时身份仍有效的临界区
检查取消并提交 Timeline。result 已返回但此后重载的旧结果不得写入历史、播放或发布成功，报告
`ASSISTANT_BINDING_EXPIRED`。取消、角色切换和 generation 失效继续服从 Core 的唯一终态规则。

begin 发生传输错误时可能已启动 worker，不能仅凭 ACK 丢失重试，也不能立即撤销它仍在读取的文件和历史。
Core 在调用 Assistant 前的历史读取、输入保存或 Session 描述失败，不调用 release。适配器仅为 begin
已确认受理的 operationId 承担 release；输入 artifact 创建失败、明确拒绝和已交给 abort 的操作不再重复释放。
处理顺序如下：

1. 已确认 begin 接收的操作先 cancel，再 poll 等待 worker 终态。cancel 和后续 release 共用 2 秒协作清理预算，
   每次 RPC 使用剩余期限；响应持续 running 也不能延长预算。超时或 cancel/poll 失败后进入精确实例回收，
   复用进程所有者的 0.8 秒关闭预算。正常生成没有这项总时长限制。
2. begin 接收状态不明时，直接调用 Host 内部 `abort_bound_service(serviceKey, identity, reason)`。
   只有明确拒绝受理的 BUSY、CLOSED 或 INPUT_INVALID 可以直接释放尚未使用的输入。
3. abort 重新检查 scope，停止该实例及其硬依赖；已有 cleanup owner 时等待其完成。旧 scope 已结束或已被
   替换则不触碰新实例。复用运行时进程树回收，不自动启动替代实例。
4. 确认旧实例结束后才撤销该 identity 的输入与历史许可。清理失败保留 owner、artifact 和诊断，禁止把
   “服务已撤销”当作“进程已退出”。release ACK 丢失同样回收精确实例，避免永久占用执行槽。

清理错误记录为附属诊断，不覆盖最初的模型、传输或取消异常。已经开始执行的工具副作用不会自动重放；
取消只能阻止后续步骤，不能承诺撤销已经发生的外部操作。

## 公开 Host 能力

这些方法对 bundled 与第三方插件使用同一合同，实际路由继续验证声明依赖和调用进程身份。

| Host 服务 | 消费入口 | 边界 |
|---|---|---|
| `sakura.host.artifacts` | `allocate/commit/release/resolve/release_received` | 只解析已提交且由调用方拥有或明确交付给它的资源；描述符为 artifactId、mediaType、byteLength |
| `sakura.host.timeline` | `read_turn_page(request)` | historyToken、characterId、snapshotCursor 必须匹配；按完整轮分页，只读 |
| `sakura.host.tools` | `catalog()`、`execute(registrationId, name, arguments)` | 目录带真实登记身份；执行前复核并固定工具对象，同名新登记不能接管旧调用 |
| `sakura.host.context` | `catalog()`、`collect(registrationId, request)` | 目录带真实插件和 Provider；回调失效明确失败，Host 不决定 Prompt 优先级 |
| `sakura.host.model_slots` | `active()`、既有 `catalog/resolve` | active 提供当前 chat 与可空 vision_chat 配置；默认 Assistant 使用 Core 传入的已发布会话快照；凭据不进入 UI 或日志 |
| `sakura.host.storage` | `resolve("data", "logs")` | 返回插件可使用的日志目录，由插件写自己的 Trace |
| `sakura.host.logging` | 既有日志 SDK | 统一 Host 脱敏、身份绑定与落盘；模型调用统计沿既有遥测链路，不传播 Prompt 正文 |

历史页请求包含 `category/limit/beforeCursor/observationSince/proactiveSince`，以及上述角色与快照凭据。
结果为 `{turns, nextCursor, snapshotCursor}`；页过大时返回 `{artifact}`。Host 不拆开一轮，也不截断超大单轮；
插件读完大页后释放 artifact。轮次取消或插件 scope 结束会回收仍登记的历史页资源。
排序、快照与 TTL 详见[类型化时间线](WP-4-07R-typed-timeline-adaptive-context.md)。

工具目录的 timeoutSeconds 默认 15 秒、最大 120 秒。超时不表示外部副作用已停止，消费者不得自动重试。
工具图片仍以 `{content, artifact}` 交付，Host 验证提交状态和格式并授权接收方；默认 Assistant 在自身进程中
读取、编码和组装模型图像，避免把 base64 再次塞过 RPC 尺寸上限。
交付时在接收者 scope 的短临界区内转交资源所有权，使用接收者的 artifact 额度，不复制或移动文件正文。
授权绑定 providerId 与 scopeId；源插件关闭不能删除已交付的文件，接收者关闭则回收文件和授权。
工具回调在原接收者失效后才返回时，Host 拒绝交付并释放源文件，不授权给同 ID 的新实例。
当前工具图片合同每次只接受一个明确的 artifact 描述符；格式错误或额度不足也释放未能交付的源文件，
但不得删除已经转交给其他消费者的资源。

Context 的 step/turn 采集、required、failurePolicy、预算与模型 role 选择由默认插件消费实现负责，
遵循 [Runtime Context 合同](sakura-plugin-runtime-v4.md#64-context-行为贡献)。贡献请求中的 recent_messages
来自同一轮历史快照，最多保留最近八条消息；本轮缓存和模型窗口扩容可以复用已读取页，不扫描全部历史。

## 验证边界

真实进程用例覆盖默认插件与替代 Provider 的准备、接收、取消和结果读取；服务停用后不得回落到 Core Agent。
生命周期回归覆盖 begin ACK 丢失、cancel/poll/release 错误、进程清理并发、旧 scope 与同 ID 新 scope、
最终提交前失效，以及清理失败时仍保留输入。分页回归覆盖完整轮、固定快照、预算续页、权限和大页 artifact。
模型、Prompt、Trace 和工具策略在插件入口测试；Core 测试验证受理、数据落盘与唯一终态，不复制策略实现。
