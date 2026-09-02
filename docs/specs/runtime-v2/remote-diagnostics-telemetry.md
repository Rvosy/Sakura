---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-02
---

# 远程诊断与匿名统计合同

## 目的与边界

Sakura 可以把少量结构化数据发送到独立的 Telemetry Edge，用于定位程序错误、了解版本与平台分布，并分析模型请求的
Context 占用。该能力默认开启，用户可以在设置中一次关闭全部发送。

远程数据只回答维护问题，不记录聊天过程。Sakura 不自动上传完整 Runtime Log，也不上传 Agent Trace、聊天、Prompt、
Memory、工具参数或结果。Telemetry Edge 不是启动、聊天、设置、更新或退出的前置依赖。

生产服务根固定为：

```text
https://telemetry.cialloo.cn/
```

生产请求经过多吉云 CDN，再由受 Origin Secret 保护的源站链路转发到 VPS Nginx。Nginx 只把请求交给监听
`127.0.0.1:8765` 的 FastAPI，数据写入 VPS 本机 SQLite。Origin Secret 只用于 CDN 到源站的连接，不进入客户端、
公开文档示例或应用日志；它的值不得提交到仓库。

现有 Cloudflare Worker + D1 和 `workers.dev` 地址只用于旧 PoC，不属于生产请求路径或客户端合同。阿里云 VPS 上的
[`sakura.cialloo.cn/service/v1/`](sakura-service.md) 仍是公开、只读的静态控制面，不接收 telemetry；Telemetry Edge
虽然部署在同一台 VPS，仍使用独立域名、Nginx vhost、FastAPI 进程和数据库。

## 用户设置

“设置 → 系统”提供一个“匿名统计”分组。分组内只有一个开关：

```text
发送匿名统计数据
```

该开关同时控制错误报告、基础运行事件和模型运行指标。产品行为如下：

- 新安装与升级安装都默认开启；缺少配置字段等同于 `true`。
- 用户明确关闭后保存 `false`，以后升级和重启不得恢复为开启。
- 关闭时立即停止接收新事件，清空待发送内存队列，并取消当前 HTTP 请求。
- 关闭不删除已经发送的数据，也不影响 Sakura 的任何本地能力。
- 首次运行不弹出 telemetry 提示或确认页。设置行必须直接说明默认开启、可随时关闭和主要排除项。

开关标题旁放置可聚焦的 `?` 按钮，`aria-label` 为“了解匿名统计数据”。按钮通过 Rust 中的固定 HTTPS 打开函数访问：

```text
https://github.com/Rvosy/Sakura/blob/main/docs/userdocs/REMOTE_DIAGNOSTICS_AND_TELEMETRY.md
```

该 URL 不由远程配置、插件或用户输入提供。

## 配置与设置接口

Runtime v2 `config/ui.json` schema 1 增加：

```json
{
  "settings": {
    "telemetry": {
      "enabled": true,
      "installation_id": "550e8400-e29b-41d4-a716-446655440000"
    }
  }
}
```

字段合同：

| 字段 | 行为 |
|---|---|
| `settings.telemetry.enabled` | 只接受布尔值；缺失按 `true` 读取。 |
| `settings.telemetry.installation_id` | 可缺失；存在时必须是规范 UUID v4。 |

整个 `telemetry` 对象缺失时也按默认开启处理。字段类型错误、UUID 非法或 `ui.json` 无法安全读取时，telemetry 在当前运行
中按关闭处理，不发送网络请求，也不自动覆盖原文件。设置页应返回稳定错误，允许用户通过保存合法状态修复。

启用状态下缺少 `installation_id` 时，Rust 必须在取得共享应用锁后生成并原子保存 ID，再提交第一条 `app.started`。保存失败时
按关闭处理，Sakura 继续启动。关闭状态且没有历史 ID 时不因打开设置页生成新值；Snapshot 返回 `null`，界面显示“开启后生成”。

保存继续使用共享 `UiConfigRepository`，保留未知字段并执行同目录临时文件、同步和原子替换。设置链冻结以下窄命令：

```text
settings_telemetry_get
settings_telemetry_set_enabled
settings_telemetry_regenerate_installation_id
settings_telemetry_open_documentation
```

设置 capability key 为 `telemetry.anonymous_statistics`，归入现有 `system` section。`get`、`set_enabled` 和
`regenerate_installation_id` 都返回同形 Snapshot；`set_enabled` 只接受一个布尔参数，另外两个状态命令不接受业务参数。
`open_documentation` 不接受 URL，只返回成功或稳定错误码。

读取结果固定为：

```json
{
  "schemaVersion": 1,
  "enabled": true,
  "installationId": "550e8400-e29b-41d4-a716-446655440000"
}
```

`installationId` 尚未生成时为 `null`。关闭命令先停用当前发送器，再保存 `false`；保存失败时当前进程仍保持关闭，并向设置页
返回 `TELEMETRY_SETTINGS_SAVE_FAILED`，不得把关闭状态伪装成已永久保存。开启命令必须先保存成功，再允许入队和发送。

## 诊断 ID 与关联

`installation_id` 是首次需要发送数据时生成的 UUID v4。默认开启的安装会在第一次启动发送器时生成。它与账号、用户名、硬件信息、
MAC 地址、磁盘序列号和系统机器 ID 无关，升级后保持不变。设置页直接显示该值，并提供“复制”和“重新生成”。

重新生成时必须暂停发送、取消当前请求并清空旧 ID 的待发送队列。新 ID 原子保存成功后才恢复发送；保存失败则保留旧 ID。
重新生成只影响以后发送的数据，不删除服务端历史记录。

每份错误报告另有客户端生成的 `report_id` UUID v4。现有 `run_id`、`operation_id` 和 `model_call` 继续承担运行和调用关联，
不再建立另一套链路 ID。所有 ID 都是关联字段，不是鉴权凭据。

## 客户端所有权与发送行为

Rust Shell 是唯一远程 HTTP 出站 owner。WebView、Python Core 和插件不得直接连接 Telemetry Edge。

客户端只实现一个小型发送器：

- 一个后台任务和容量 128 的内存队列；
- Error 每次只发送一条；Event 和 ModelCall 使用 1 至 10 条的 batch envelope，单条也必须放在 `items` 中；
- sender 可以合并队列中已经就绪、端点相同的记录，但不得为了凑满 batch 延迟发送；
- HTTP 总超时 2 至 3 秒，每条记录最多进入一次请求，不自动重试，也不补传历史数据；
- 队列满、网络失败、超时、服务端拒绝或退出期限到达时允许丢弃；
- 不写磁盘队列，不读取磁盘日志，不等待队列排空后才启动、聊天或退出；
- 发送失败只记有界的本地 debug 诊断，不弹窗，也不再生成远程错误报告。

发送器只接受三个内部枚举 DTO：Error、Event 和 ModelCall。不得提供可携带任意 JSON 的通用上报接口。

## Core 到 Rust 的本地投影

Core 已有 Provider usage、`model_call`、`purpose` 和 Context 构建摘要。远程指标必须在这些内存对象仍然存在时生成，禁止解析
`sakura-agent-trace.log` 或其他磁盘文件。

Core 只通过 generation 绑定、schema 固定的本地 `TelemetryErrorCandidateV1` 和
`TelemetryModelCallMetricV1` 向 Rust 提交候选数据。该通道沿用现有受控 Core → Rust 传输和 generation 校验，但 DTO 不得包含
自由 attributes、消息正文或任意嵌套 payload。Rust 必须再次验证字段，并注入应用版本、系统信息、安装 ID 和运行关联信息。

Rust、Core 和 WebView 的错误边界各自负责生成安全候选。遥测关闭时可以跳过候选构造；即使候选仍到达 Rust，也必须在入队前
丢弃。

## Error Report

`POST /v1/errors` 的 request body 上限为 32 KiB。只报告可能代表 Sakura 缺陷的错误：Rust panic、Core 未处理异常、
WebView `error`/`unhandledrejection`、Core 异常退出、启动或迁移硬失败、TTS 内部链路故障，以及维护者加入 allowlist 的
invariant 错误。

TTS 白名单覆盖运行环境或权重不可用、进程启动或探测失败、Provider 请求失败、合成结果或产物异常、部分设置未保存，以及音频设备
或已生成音频不可用。用户未启用 TTS、尚未选择 Provider、主动取消和端口被其他程序占用不生成完整 Error Report。TTS 候选只
接受 Core 产生的固定 `tts.*` 事件，以及 Rust 产生的 `tts.playback.failed`；具体错误码仍使用固定白名单。

高信号的非致命警告也使用 Error Report，但同一运行内的 `component/event/code` 组合最多发送一次。首批包括 Memory 召回或整理
降级、Prompt 依赖降级、回复处理使用安全兜底、截图失败、MCP 配置或连接失败，以及 TTS 环境、权重、Provider、播放和部分保存
问题。模型调用失败继续使用 ModelCall；用户取消、普通工具业务失败和没有固定事件与错误码的 warning 不生成报告。

用户取消、模型 API Key 缺失、模型 Provider 配置错误、普通模型网络超时、模型 Provider 401/quota 和预期内的插件降级不生成完整 Error Report。
它们可以形成不带 stack 与 breadcrumbs 的轻量运行事件，但不得借此上传原始错误正文。

Payload schema 1 固定为：

```json
{
  "schema": 1,
  "reportId": "uuid-v4",
  "installationId": "uuid-v4",
  "runId": "bounded-token",
  "operationId": "bounded-token",
  "app": {
    "version": "1.0.3",
    "build": "public-build-id",
    "channel": "stable"
  },
  "system": {
    "platform": "windows",
    "osVersion": "10.0.19045",
    "arch": "x86_64",
    "webviewVersion": "bounded-token"
  },
  "error": {
    "component": "core",
    "event": "core.error.unhandled",
    "code": "CORE_UNHANDLED_ERROR",
    "exceptionType": "RuntimeError",
    "fingerprint": "bounded-token"
  },
  "context": {
    "installKind": "upgrade",
    "upgradedFrom": "1.0.2"
  },
  "stack": [
    {
      "module": "app.plugins.runtime",
      "function": "start_worker",
      "file": "app/plugins/runtime.py",
      "line": 142
    }
  ],
  "breadcrumbs": [
    {
      "offsetMs": -1850,
      "source": "core",
      "severity": "warning",
      "channel": "plugin",
      "event": "plugin.runtime.failed",
      "code": "PLUGIN_DEPENDENCIES_MISSING",
      "outcome": "failed",
      "elapsedMs": 813
    }
  ]
}
```

`runId`、`operationId`、`build`、`osVersion`、`arch`、`webviewVersion`、`exceptionType`、`fingerprint`、`context`、
`stack` 和 `breadcrumbs` 可以省略；未知值不得使用自由文本占位。`channel` 只接受
`stable/prerelease/development`，`platform` 只接受
`windows/macos/linux`，`installKind` 只接受 `fresh/upgrade/legacy_import/unknown`。

`stack` 最多 16 个 frame，`breadcrumbs` 最多 40 条。单个 stack frame 只允许 `module/function/file/line`。`file` 必须是
repo-relative 或 module-relative 路径；不得包含绝对路径、源码文本、locals、参数或异常 message。无法可靠规范化的 frame
直接丢弃。Fingerprint 只由稳定错误分类和规范化 frame 计算。

Breadcrumbs 来自 RuntimeLogService 同一规范化事件流中的独立白名单环，不读取 `sakura-runtime.log`，也不直接复用带有人类说明
的 Viewer DTO。单条只允许相对时间、source、severity、channel、event、stable code、outcome、elapsed time 和有限数值状态。
`message`、`diagnostic`、URL、路径及任意正文一律排除。

Breadcrumb 的 `severity` 只使用 `debug/info/warning/error/critical`，Runtime Log 的 `trace` 投影为 `debug`。`outcome` 只使用
`success/failed/cancelled/degraded/skipped`，Runtime Log 的 `completed` 投影为 `success`；`started`、`ready` 等中间状态不发送
`outcome`。相对时间与单步耗时都限制在 24 小时以内，超出范围的值直接省略。

## 基础运行事件

`POST /v1/events` 接受 1 至 10 条记录，整个 request body 上限为 8 KiB。允许的事件只有：

```text
app.started
app.ready
migration.completed
migration.failed
feature.used
```

`app.ready` 可以携带 `durationMs`。迁移事件可以携带 from/to version、duration 和 stable error code。
`feature.used` 的能力值只允许 `chat/tts/memory/tools/plugins`，同一 run、同一能力最多发送一次。

Payload schema 1 固定为：

```json
{
  "schema": 1,
  "items": [
    {
      "installationId": "uuid-v4",
      "runId": "bounded-token",
      "appVersion": "1.0.3",
      "platform": "windows",
      "osVersion": "10.0.19045",
      "arch": "x86_64",
      "event": "feature.used",
      "feature": "tools",
      "durationMs": null,
      "fromVersion": null,
      "toVersion": null,
      "errorCode": null
    }
  ]
}
```

公共字段以外的五个可空字段始终存在，并按事件限制非 `null` 值：`app.started` 全部为 `null`；`app.ready` 只允许
`durationMs`；迁移事件只允许 `durationMs/fromVersion/toVersion`，其中 `migration.failed` 还可带 `errorCode`；
`feature.used` 只允许 `feature`。未知值用 `null`，不能用空字符串或任意对象。

服务端先校验 envelope 和全部 items，再打开一个 SQLite transaction 批量插入。任意一条非法时整体返回 `400`，数据库不得
出现部分写入。成功返回 `202` 和 `{"ok":true,"accepted":N}`。

首版不发送心跳、使用时长、细粒度点击、行为路径、角色名、插件 ID、工具名列表或 TTS 文本。

## 模型运行指标

`POST /v1/model-calls` 接受 1 至 10 条记录，整个 request body 上限为 16 KiB。每次进入现有 Provider 调用边界最多形成一条记录。
兼容性回退会重新进入该边界，因此使用新的 `model_call`；同一次调用内部的 HTTP 传输重试继续沿用原编号。Payload schema 1 固定为：

```json
{
  "schema": 1,
  "items": [
    {
      "installationId": "uuid-v4",
      "runId": "bounded-token",
      "operationId": "bounded-token",
      "appVersion": "1.0.3",
      "modelCall": 3,
      "purpose": "agent_step",
      "modelFamily": "custom",
      "outcome": "success",
      "errorCode": null,
      "latencyMs": 4281,
      "contextWindowTokens": 32768,
      "contextWindowSource": "provider",
      "usage": {
        "promptTokens": 28341,
        "completionTokens": 762,
        "totalTokens": 29103,
        "inputTokens": null,
        "outputTokens": null,
        "cachedInputTokens": null,
        "reasoningTokens": null
      },
      "estimate": {
        "requestTokens": 27820,
        "historyTokens": 10240,
        "memoryTokens": 6180,
        "dynamicContextTokens": 3120,
        "toolSchemaTokens": 5460,
        "historyMessages": 24,
        "memories": 8,
        "toolCount": 23
      }
    }
  ]
}
```

`purpose` 只接受
`agent_step/final_reply/reply_repair/screen_observation/proactive_reply/background_agent/memory_curation/memory_curation_repair`；
`outcome` 只接受 `success/failed/cancelled`；`contextWindowSource` 只接受
`provider/configured/fallback/unknown`；`modelFamily` 只接受 `openai/anthropic/gemini/deepseek/custom/unknown`。所有 token、
计数和耗时必须是非负整数，`modelCall` 从 1 开始。

`operationId` 可以为 `null`。失败或取消时 `errorCode` 可以使用稳定错误码，成功时必须为 `null`。Provider 没有返回任何 usage 时
整个 `usage` 为 `null`；单个值未知时该字段为 `null`。`estimate` 遵循同一规则，但应保留失败前已经完成的 Context 估算。

Provider usage 与 Sakura estimate 必须使用不同字段。Provider 未返回 usage 时保存 unknown，不得伪造为 `0`。失败调用在可用时仍可
保存 Context estimate 和稳定错误码。

已知公共模型只上传稳定 model family；用户自定义或无法识别的 model ID 一律投影为 `custom`。不得上传 raw model ID、Provider URL、
Prompt、messages 或工具 schema 正文。

服务端先校验 envelope 和全部 items，再打开一个 SQLite transaction 批量插入。任意一条非法时整体返回 `400`，数据库不得
出现部分写入。成功返回 `202` 和 `{"ok":true,"accepted":N}`。

## 服务端数据面

生产 FastAPI 只提供：

```text
GET  /health
POST /v1/errors
POST /v1/events
POST /v1/model-calls
```

三个写端点使用独立 schema，拒绝未知字段、错误 Content-Type、非法类型、未知 enum、超长 token、非有限数值和超大 body。SQL 只使用
参数绑定。Pydantic model 全部使用 `extra="forbid"`，不得接收任意 attributes 或先保存 raw body。POST 只接受
`application/json`；错误 Content-Type 返回 `415`，非法 JSON 或 schema 返回 `400`，错误 method 和未知路径分别保持 `405` 和
`404`。成功接收返回 `202`；客户端不得因响应丢失而重试。

各端点必须在 JSON 解析前执行独立 body hard cap。Content-Length 可以用于快速拒绝，但流式读取也必须计数，不能让缺少
Content-Length 或 chunked request 绕过限制。Nginx 的 `client_max_body_size` 保持 64 KiB，它不能替代应用层的
32/8/16 KiB 上限。验证失败响应不得回显 request body。

`GET /health` 必须执行最小 SQLite `SELECT 1`。应用和数据库都正常时返回 `200` 与
`{"ok":true,"service":"sakura-telemetry"}`；数据库不可用时返回 `503`，不得泄露文件路径或异常正文。

FastAPI 使用 Pydantic、Python `sqlite3` 和单个 Uvicorn 进程，不引入 ORM 或独立数据库服务。Uvicorn 只监听
`127.0.0.1:8765` 并使用 `--no-access-log`；Nginx access log 也关闭。错误日志可以保留，但应用不得把请求 body、凭据或
Origin Secret 写入日志。

SQLite 数据库固定为 `/var/lib/sakura-telemetry/telemetry.db`，不放在 HTTP 可下载目录。应用连接必须设置：

```text
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;
```

数据库目录由 `www:www` 持有并使用 `0750`；数据库、WAL 和 SHM 文件由 `www:www` 持有并使用 `0640`。

数据库使用三张明确表：

- `error_events`：错误、安装/报告/运行关联、应用和系统信息，以及有界 stack/breadcrumb JSON；
- `telemetry_events`：运行事件、版本、平台、feature、duration、迁移版本和错误码；
- `model_call_metrics`：调用关联、终态、Context window、Provider usage 和各类 estimate。

不使用 EAV 或任意 attributes JSON，也不为首版建立 installations 表、账号表或 Dashboard。

三张表都使用 SQLite 自增主键和服务端生成的 UTC `received_at`，不把客户端时间当作数据库主时间。业务列固定为：

```text
error_events:
  report_id, installation_id, run_id, operation_id,
  app_version, build, release_channel,
  platform, os_version, arch, webview_version,
  component, event, error_code, exception_type, fingerprint,
  install_kind, upgraded_from, stack_json, breadcrumbs_json

telemetry_events:
  installation_id, run_id,
  app_version, platform, os_version, arch,
  event, feature, duration_ms, from_version, to_version, error_code

model_call_metrics:
  installation_id, run_id, operation_id, app_version,
  model_call, purpose, model_family, outcome, error_code, latency_ms,
  context_window_tokens, context_window_source,
  prompt_tokens, completion_tokens, total_tokens,
  input_tokens, output_tokens, cached_input_tokens, reasoning_tokens,
  request_estimated_tokens, history_estimated_tokens, memory_estimated_tokens,
  dynamic_context_estimated_tokens, tool_schema_estimated_tokens,
  history_messages, memories, tool_count
```

`error_events.report_id` 使用 unique index。只为 `error_events` 额外建立 `installation_id`、`received_at`、`error_code` 和
`app_version` 四个普通索引；首版不给另外两张表预建索引。Stack 与 breadcrumbs 以通过请求 schema 校验后的有界 JSON TEXT
保存，不拆子表。重复 `report_id` 不写第二行，但仍返回 `202`，使 `/v1/errors` 保持幂等。

`/v1/events` 和 `/v1/model-calls` 的 batch 在完成全量 schema 校验后，各用一个 SQLite transaction 写入。数据库不得保存
IP、User-Agent、raw body 或任意请求 JSON；也不得建立 `raw_body`、`payload`、`payload_json`、`request_json` 或
`raw_event` 字段。

## 隐私、网络元数据与保留期

所有自动发送路径都禁止包含：

- 聊天、Prompt、Memory、模型回复和角色内容；
- Tool 参数、结果和 schema 正文；
- Agent Trace、完整 Runtime Log 和原始 exception message；
- API Key、Cookie、Authorization、credential 和完整 API URL；
- 用户名、绝对路径、机器指纹和 raw custom model ID；
- 图片、音频及其他二进制正文。

多吉云 CDN 在转发和限制滥用时会接触客户端 IP 等网络元数据。FastAPI 不读取 `X-Forwarded-For`，SQLite 不保存 IP 或
User-Agent；Nginx 和 Uvicorn 的 access log 均关闭。客户端不携带固定 API Secret、HMAC key 或混淆后的共享秘密。
服务端接受数据可能被伪造，因此只能把这些记录用于诊断和粗略统计，不能用于计费、权限或官方客户端证明。

三张表的原始记录最多保留 90 天。系统 cron 每天 03:17 运行一次 `cleanup.py`，按服务端 `received_at` 删除三张表中的过期记录，
随后执行 `PRAGMA wal_checkpoint(PASSIVE)`；日常清理不执行 VACUUM。关闭开关或重新生成诊断 ID 不会删除历史数据。用户可以在
GitHub Issue 中只提供诊断 ID 并请求查询或删除；维护者按该 ID 删除三张表中的对应记录。Issue 不应附带聊天、Agent Trace、密钥或
未经检查的日志。

## 验证

实现必须覆盖以下自动测试：

- 配置缺失、显式 `true`、显式 `false` 和非法值四种读取状态；新安装和升级时缺失字段都默认开启；
- 关闭后零新请求、待发送队列清空、在途请求取消，重启后仍关闭；
- ID 生成、复制、重新生成、保存失败和旧 ID 队列清理；
- 队列溢出、超时、无网络、HTTP 拒绝和退出期间丢弃均不改变产品结果；
- 三类 DTO 的字段、长度、枚举和 32/8/16 KiB body 上限，以及 FastAPI 对非法请求的拒绝；
- Content-Type、非法 JSON、未知字段、错误 method、未知路径、缺少 Content-Length 和 chunked 超限请求；
- Error report 重复提交仍为一行；两个 batch 的 1 至 10 条边界、全量校验、单事务写入和零部分写入；
- Error candidate allowlist、安全 stack、40 条 breadcrumb 上限和日志文件零读取；
- Provider usage 与 estimate 分离、usage unknown、失败调用、真实调用编号和 custom model 投影；
- `/health` 的 SQLite 检查及数据库失败 `503`；90 天清理和按 installation ID 删除三张表记录。

Privacy Sentinel 必须分别注入聊天、Prompt、Memory、Tool args/result、API Key、绝对路径、原始异常 message、自定义 model ID 和
Agent Trace 内容。扫描客户端 request body、SQLite 主文件/WAL/SHM、Uvicorn 日志和 Nginx error log 时，任何 sentinel 命中都算
失败。

真实设置页还要验证键盘焦点、`?` 按钮、固定 GitHub URL、开关即时状态、诊断 ID 复制/重新生成，以及关闭窗口再打开后的状态一致性。

相关决策见 [ADR-0043](../../adr/0043-vps-fastapi-sqlite-telemetry-edge.md)。本地日志边界继续由
[人类可读运行日志与 Prompt Trace](WP-4L-02-human-readable-runtime-log-agent-trace.md) 维护。
