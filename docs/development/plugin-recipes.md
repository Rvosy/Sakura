# 插件能力接入

本页补充[公共 SDK](plugin-sdk.md)的领域接口。插件在 Manifest 的 `requires` 中声明所用服务，使用公开 SDK，不导入 Core 或其他插件的私有代码。服务代理、任务和资源始终属于取得它们的插件实例，失效后重新建立绑定，不按同名服务重放旧请求。

## 使用模型

连接、凭据、模型选择与任务隔离见[模型服务规格](../specs/model-services.md)。

模型连接由提供方保存；消费者只保存 `{serviceKey, profileId, modelId}` 引用，不接收密钥。`sakura.host.model_slots.v2` 提供：

| 方法 | 用途 |
|---|---|
| `register(descriptor, load=..., save=...)` | 在模型设置中登记本插件的选择槽，返回撤销函数 |
| `catalog()` | 返回提供方、连接与模型目录 |
| `resolve(reference)` | 解析选择，返回模型引用 |
| `active()` | 取得当前 `chat`、`vision_chat` 引用 |
| `register_provider(descriptor, catalog=...)` | 模型提供方登记自己的目录 |

槽位 descriptor 使用 `slotId`、`label`、可选 `description`、`modelKind: "chat_completion"`、`required`、`order`。load 返回已存引用，save 只保存此槽位。可选槽位的空选择继承当前聊天模型，不复制它的凭据。

```python
from sakura_model import ModelClient

reference = context.get("sakura.host.model_slots.v2").active()["chat"]
client = ModelClient(context, reference)
try:
    response = client.complete({
        "messages": [{"role": "user", "content": "概括这段文字"}],
        "parameters": {"temperature": 0.4},
    }, cancel_checker=is_cancelled)
finally:
    client.close()
```

`is_cancelled` 是消费者自己的取消查询函数。客户端负责固定服务身份、轮询、取消和 Artifact 交付，大请求无需另写文件传输逻辑。complete 也可接收 `progress_callback`。

请求使用 `messages`，可带 `tools`、`toolChoice`、`responseFormat`、`parameters`、`stream`。消息含 `role/content`，工具消息可带 `toolCalls/toolCallId`；content 可以是文本或文字、图片内容列表。工具定义为 `{name, description, parameters}`，parameters 是 JSON Schema。结果为 `{message, usage, finishReason, diagnostics?}`；工具调用为 `{id, name, arguments}`，arguments 保持 JSON 字符串。提供方附带的 `providerData` 作为不透明数据保留。

### 实现模型提供方

提供方暴露独立服务，并用 `register_provider({serviceKey, label}, catalog=...)` 登记。目录返回 `{profileId, label, models: [{modelId, label}]}` 列表，只读取本地已保存配置；刷新远端模型由用户明确发起。

| 导出方法 | 输入与结果 |
|---|---|
| `catalog()` | 返回本地目录 |
| `describe(profileId, modelId)` | 返回 `contextWindowTokens/contextWindowSource/inputModalities/supportsTools` |
| `begin(request)` | 接受 `operationId/profileId/modelId` 及 `request` 或 `requestArtifact`，返回 operationId |
| `begin_probe(request)` | operation 为 `list_models` 或 `test_connection`，使用提交的 `profileId/values` 草稿 |
| `poll(operationId, afterSequence=0, waitMs=250)` | 返回 `state/sequence/progress/truncated`；单次等待最多 500 ms |
| `result(operationId)` | 返回 `response`、`responseArtifact` 或 `failure` |
| `cancel(operationId)` | 取消本次真实请求 |
| `release(operationId)` | 回收任务和交付资源 |

failure 保留 `code/message/diagnostics`。同一操作的配置在开始时固定，修改连接只影响后续操作；多个消费者的任务互不干扰。HTTP 成功但响应体包含服务错误仍是失败。只有明确拒绝某个可选请求参数时才做对应兼容处理，不因普通故障自动重复请求。参考[远程模型插件](../../plugins/builtin/sakura_model_openai_compatible/plugin.py)。

## 实现 Assistant

对话受理、上下文、取消与提交规则见[聊天规格](../specs/conversation.md)，历史读取规则见[历史与记忆规格](../specs/timeline-memory.md)。

`sakura.assistant` 是普通、可替换的插件服务。Core 负责聊天受理、会话身份、取消和最终写入；Assistant 负责上下文、模型、工具及回复生成。用户输入和插件对话复用同一入口，不在 Assistant 内另建聊天会话。

| 导出方法 | 输入与结果 |
|---|---|
| `prepare(session)` | 返回 `state: ready/degraded/setup_required/failed`、`code/message/retryable`，可附 modelBindings |
| `begin({operationId, input})` | input 为已交付的 JSON Artifact，返回 operationId |
| `poll(operationId, afterSequence=0, waitMs=500)` | 返回 `state/sequence/progress`，失败可附 failure |
| `result(operationId)` | 返回已提交的 JSON Artifact |
| `cancel(operationId)` | 请求取消，返回 `{cancelled: true}` |
| `release(operationId, terminalStatus)` | 告知 Core 裁决并回收；`released: false` 表示工作仍在退出 |

输入含 `operationId/session/turnId/message/historyCursor/historyNow/entryIds/historyToken`，可带 `event/attachment/humanEntryId/observationEntryIds`。session 含角色、循环设置、应用版本、模型引用、绑定及 `replyVisual`；不携带模型凭据。结果形状为：

```json
{
  "reply": {"segments": [{"text": "你好", "translation": "", "tone": "", "portrait": "", "suppress_tts": false, "control": null}]},
  "actions": [],
  "visual_observation": null
}
```

`sakura.host.timeline.read_turn_page(request)` 为 Assistant 提供完整轮次。request 必须包含 `historyToken/characterId/snapshotCursor/category/limit/observationSince/proactiveSince`，可带 `beforeCursor`。`observationSince` 与 `proactiveSince` 使用 ISO 日期时间字符串；category 为 `conversation/observation/proactive`。返回 `turns/nextCursor/snapshotCursor`，大结果通过 Artifact 返回。一次聊天固定历史快照，分页不混入后来的消息。

可选上下文失败是否跳过由其已声明策略决定；取消不能变成空回复成功，也不暗中换回另一套 Assistant。参考[默认 Assistant](../../plugins/builtin/sakura_assistant/plugin.py)。

## 接入 MCP

工具执行、连接归属与取消规则见[工具与 MCP 规格](../specs/tools-mcp.md)。

`sakura.mcp` 提供连接和协议请求，消费者拥有设置、工具展示与业务数据。它支持 stdio、Streamable HTTP、SSE，并复用 MCP SDK；不要求消费者再维护一套传输实现。

```python
mcp = context.bind("sakura.mcp")
server = mcp.registerServer({
    "transport": "stdio",
    "command": "example-mcp-server",
    "args": [],
})
handle = server["handle"]
context.effect(lambda: mcp.unregisterServer(handle))
operation = mcp.begin(handle, "tools/list", {})
```

连接在后台建立。descriptor 根据传输使用 `command/args/env/cwd` 或 `url/headers/proxy`，并可配置 `oauth/roots/elicitation/sampling/extensions`。可省略 transport，让组件按 command/url 选择。需要跨重载保存 OAuth 凭据时传 `credential_key`；凭据同时绑定消费者插件和完整服务地址。

| 方法 | 用途 |
|---|---|
| `capabilities()`、`status()` | 查询组件能力和本消费者连接状态 |
| `begin(handle, method, params=None, options=None)` | 开始 SDK 方法或显式 raw 请求，返回 operationId |
| `inspect(operationId)` | 返回 running/completed/error/cancelled，以及 progress、result 或大结果 length |
| `readResult(operationId, offset=0, limit=8192)` | 分段读取 JSON 文本，返回 text/nextOffset/done，offset 按字符计算 |
| `cancel(operationId)`、`release(operationId)` | 取消与回收请求 |
| `events(handle, after=0, pending_offset=0)` | 读取通知和待响应的服务端请求 |
| `readEvent(handle, sequence, offset=0)`、`readInput(handle, requestId, offset=0)` | 读取较大的事件或输入 |
| `respond(handle, requestId, response)` | 回复 OAuth、elicitation、sampling 交互 |
| `unregisterServer(handle)` | 结束连接及其请求 |

目录结果的 `nextCursor` 按协议继续传给下一页。OAuth 授权地址交给消费者展示；sampling 和 elicitation 由消费者明确启用并处理，不能自动替用户同意。请求、事件和凭据不能跨消费者 scope 读取。完成或放弃后 release，不以清理错误覆盖原始业务错误。参考[MCP 组件](../../plugins/builtin/sakura_mcp/plugin.py)。

## 语音引擎

录音、合成、播放与历史音频的分工见[语音规格](../specs/voice.md)。SakuraTTS 的整合包与推理进程见[专用规格](../specs/sakuratts.md)。

TTS Hub `sakura.tts` 和 ASR Hub `sakura.asr` 分别负责提供方选择与任务管理，具体引擎提供独立服务。提供方使用 `registerProvider({providerId, serviceKey, label})` 登记，退出时 `unregisterProvider(providerId, serviceKey)`。ASR descriptor 还声明 `processingLocation: local/remote`，让用户知道录音在哪里处理。

TTS 提供方导出 `status()`、`warmup(characterId)`、`begin(request)`、`poll(jobId)`、`cancel(jobId)`。begin 返回 jobId，合成结果交给 Hub；播放、历史留存与任务文件各有自己的所有者，不能把播放结束当作可以删除历史录音。外部 TTS 服务不归 Sakura 回收，也不由连接测试擅自切换模型或声线。预热只准备所选引擎与资源，不合成伪造句子。

支持后台补全时读取 `options.background`；前台繁忙可以返回 `TTS_BACKGROUND_DEFERRED`。支持资源原位更新的引擎可提供 `prepareResourceUpdate/finishResourceUpdate` 并返回 true，让角色编辑保留推理进程。失败和取消的后台收尾不依赖界面继续轮询。参考[SakuraTTS](../../plugins/builtin/sakura_sakuratts/plugin.py)与[TTS Hub](../../plugins/builtin/sakura_tts_hub/plugin.py)。

ASR 提供方导出 `status()`、`warmup()`、`begin(request)`、`poll(jobId)`、`cancel(jobId)`。提供方收到的 request 含 `requestId/configVersion/audio/language`；`providerId` 由 Hub 用于路由，不传入提供方。audio 为 `{resourceId, mediaType, byteLength, sampleRate, channels, durationMs}`，宿主录音使用单声道 16 kHz PCM16 WAV。通过 `sakura.host.audio_input.acquire(resourceId)` 取得 path/lease，读完 `release(leaseId)`；撤销后不能新增读取，已有读取结束才删除文件。

ASR poll 返回 running、succeeded（text/language）、failed（errorCode/diagnostics）或 cancelled。识别只把文字放回当前草稿，不自动发送；取消、切换角色或输入框已有新内容时，旧结果不能覆盖。模型下载由用户发起，启动和预热不静默下载。关闭语音功能不影响文字聊天。参考[SenseVoice](../../plugins/optional/sakura_asr_sensevoice/plugin.py)。

## 从插件发起用户对话与取得语音

手机端等用户输入使用 `sakura.host.conversation`：`begin(character_id, text, artifact_or_none)` 返回 jobId/operationId；`poll(jobId)` 返回 running 或 completed/result，结果只交付一次。cancel 请求取消，release 放弃并回收未结束的任务。输入归类为用户消息，沿用桌面聊天链路和历史。结果包括 `reply/reply_raw/segments/actions/historyEntryId/operationId`，段落保留原始 segmentIndex。

`poll` 交付完成结果或任务失败时都会回收任务，调用方不要再次 release。日志沿用 begin 返回的 operationId，关联模型请求和语音播放。

可选第四个参数 `presentation="queued"` 用于五子棋等连续互动：模型请求和结果交付立即进行，桌面字幕与语音等正在播放的回复结束后接续；默认 `interactive` 保留普通用户输入的打断行为。

需要把动作与回复播放同步时，订阅 `sakura.host.chat.presentation.started`，用事件的 `operationId` 对应 `begin` 返回的操作编号。事件在第一段语音开始播放时发出；未启用语音或播放失败时，随文字显示发出。模型结果仍由 `poll` 获取。结束活动或切换角色时停止等待，丢弃旧操作。

回复在播放前被新输入打断或从队列移除时，会收到 `sakura.host.chat.presentation.cancelled`，同样通过 `operationId` 对应操作。已经开始播放的动作不因后续取消撤销。

需要回复录音时使用 `sakura.host.speech.begin(character_id, history_entry_id, segment_index, options=None)` 取得 jobId，再 poll/cancel。poll 返回 `status: running` 或 `status: completed` 与 result。result 含 `recordingId/characterId/historyEntryId/segmentIndex`，仅在 `exportAudio` 为 true 时附带 artifact，默认导出。它读取已经保存的历史段落，不接受任意待合成文本，也不触发桌面播放。Artifact 是导出副本，接收者读完 release_received，不删除历史原录音。

后台补全可以传 `{background: true, exportAudio: false}`。`cache_status(headroom=0)` 返回当前角色、idleFill/busy/hasRoom/latestCursor；`cache_page(character_id, before_cursor, limit)` 返回 entries/nextCursor/hasMore，各段标明 hasText/suppressed/recorded。消费者按需要补全，不重新合成已有录音。

## 主动互动、截图和表现

定时观察与截图消费见[屏幕感知规格](../specs/screen-awareness.md)，表现控制与段落播放见[窗口与表现规格](../specs/window-presentation.md)。

`sakura.host.chat.current()` 返回当前 sessionId、characterId、characterName 与 idle。主动插件调用 `submit({sessionId, message, resources})`，resources 为本次截图的 resourceId 列表。受理返回操作身份；繁忙或会话变化时返回 accepted false 及 reasonCode，不排队等待旧意图变得可执行。需要在历史中保留更新通知时可附 `notification: {kind: "update", text}`。

`sakura.host.screen.capture({operationId, sessionId, resolution})` 请求受控截图，resolution 为 fullscreen/720p/1080p/2160p。返回 resourceId、尺寸与拍摄信息；宿主保管图像正文。未交付的资源调用 release(resourceId)，未完成或结果未知的采集调用 release_capture(operationId)。不能把旧会话截图用于新会话。

`sakura.host.visual.current()` 返回 characterId/bindingId/resourceId 目标；`apply({target, control: {version, resourceId, payload}})` 返回 accepted/requestId/status，后续 status/release 管理请求。`list(character_id?)` 取得资源目录，`select({target, resourceId})` 选择资源，null 恢复包默认。保存偏好与实际应用分别报告；繁忙时拒绝，不覆盖用户正在进行的互动。表现格式和前端接入见[角色与表现](characters.md)。
