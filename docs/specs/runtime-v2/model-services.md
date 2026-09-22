---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-22
---

# 模型 Service 与调用方边界

## 职责

默认远程模型服务为 `sakura.model.openai_compatible`，由同名普通插件提供。它拥有连接配置、凭据、
模型目录、HTTP 超时、代理、证书和协议适配。Core 只保存 `{serviceKey, profileId, modelId}` 引用，
通过公开模型目录展示选择项，不接收凭据或生成请求。API SDK 安装在模型插件的私有依赖目录中。

Assistant 和 Mem0 都通过公开 `sakura_model.ModelClient` 消费模型服务。Assistant 保留 Prompt、
上下文预算、工具循环、ChatReply 分段解析、语义修复和 Trace；Mem0 保留整理规则、JSON 修复、
去重、幂等来源和整理游标。模型缺失只阻止需要模型的操作，不阻止 Mem0 本地召回及手工管理。
默认实现连接远程 API，不提供本地推理进程、权重下载或离线发行。

## 旧模型插件升级

模型槽位能力使用 `sakura.host.model_slots.v2`，插件需在 manifest 的 `requires` 和
`context.get()` 中使用同一服务名。Plugin API 仍为 4；能力服务名区分模型契约的两代版本。
旧 `sakura.host.model_slots` 不再由宿主提供，声明该依赖的插件会在导入入口前被依赖检查阻止，
设置页显示 `MODEL_API_UPDATE_REQUIRED` 和更新提示。未声明依赖而直接获取旧接口，也返回同一诊断。
该提示在预览、刷新和启停结果中保持一致；已经启用的旧插件仍可停用，安装兼容版本后可重新启用。
其他 v4 插件沿用原合同，不要求更改 API 版本。

旧消费者须把 `{profileId, model}` 改为 `{serviceKey, profileId, modelId}`，通过 `ModelClient`
执行请求，不再从 `resolve()` 取得地址、密钥或协议参数。SDK 仅保留 `ApiSettings` 纯数据类型的导入兼容，
不恢复旧 HTTP 客户端或凭据解析路径。此版本不承诺旧模型消费者无需更新即可运行。

## 普通服务合同

| 方法 | 参数 | 返回 |
|---|---|---|
| `catalog()` | 无 | 当前进程有效的 profiles 及模型条目，不请求网络 |
| `describe(profileId, modelId)` | 已保存的模型引用 | `contextWindowTokens/contextWindowSource/inputModalities/supportsTools` |
| `begin(descriptor)` | `operationId/profileId/modelId` 与 `request` 或 `requestArtifact` | `{operationId}` |
| `begin_probe(descriptor)` | `operationId/operation/profileId/values` | `{operationId}`；operation 为 `list_models/test_connection` |
| `poll(operationId, afterSequence=0, waitMs=250)` | 游标及最长 500 ms 的等待 | `state/sequence/progress/truncated` |
| `result(operationId)` | 同一调用方的任务 ID | `{response}`、`{responseArtifact}` 或 `{failure}` |
| `cancel(operationId)` | 同一调用方的任务 ID | `{cancelled: true}`，只请求取消 |
| `release(operationId)` | 同一调用方的任务 ID | `{released: bool}`；运行中的任务先取消，结束后释放 |

`request` 和 `requestArtifact` 必须且只能出现一个。任务 ID 由调用方预先生成，格式为 1–128 位
字母、数字、下划线或短横线。begin 没有重试语义；未知 ACK 不允许重新发送生成请求。

`request.messages` 是消息数组。消息包含 `role/content`，可带 `toolCalls/toolCallId`。
content 支持字符串，或由 `{type: "text", text}` 与 `{type: "image", dataUrl, detail}` 组成的数组。
工具声明放在 `tools`，每项为 `name/description/parameters`；`toolChoice`、`responseFormat`、
`parameters` 和 `stream` 都可省略。生成参数由消费者明确传入，连接信息和凭据不在请求中。

结果包含 `message/usage/finishReason`，可带脱敏 `diagnostics`。`message.toolCalls` 的每项包含
`id/name/arguments`，arguments 保留提供方返回的原文，格式错误交给消费方处理。
消息及工具调用中的 `providerData` 是不透明的 continuation 元数据；消费者保留并回传，
不得把它当成工具指令。OpenAI 兼容插件用它保留供应商要求的 thought signature 等字段。
流式响应同样保留消息级扩展字段：`reasoning_content/reasoning/refusal` 的文本增量按顺序拼接，
其他不透明元数据保留最近的非 null 值；最终随 `providerData` 返回，供下一次工具结果请求原样回传。

`failure` 包含 `code/message` 和可选 `diagnostics`。HTTP 状态通过 `diagnostics.httpStatus`
传递，不将含凭据的网络异常链发送给消费者。普通 HTTP 错误、网络错误和未知响应不会触发重试。
明确拒绝 `response_format` 或自定义 `temperature` 的 400/422 响应可删去对应参数再请求，
单次任务最多三次尝试；参数越界、认证失败、限流及服务端错误不能通过剥参掩盖。

未知能力用 `null` 表示，不能当成明确不支持。未设置上下文窗口时返回 32768，来源为 `fallback`；
它是预算默认值，不是供应商能力证明。用户配置的窗口及能力元数据优先于默认值。

## 任务身份、取消与大消息

Provider 从运行时认证的 `caller_id/caller_scope` 取得 owner，不能信任 descriptor 中自报的插件身份。
operationId 只在该 owner 内查找。另一个消费者无法读取或取消同名任务；一个消费者退出不会停止共享
Provider。scope.closed 先撤销 owner 再取消其任务，迟到 begin 不得恢复该 scope 的工作。

ModelClient 在创建时绑定精确 `{providerId, scopeId}`。Assistant 的 prepare 把模型绑定交回 Core，
Core 随已发布 Session 冻结它。每轮创建客户端时验证期望身份；模型插件重载后旧 Session 明确失效，
不偷偷绑定新配置。正常模型生成没有跨任务的总期限；取消、未知 ACK 和 release 共用有界清理预算。
清理优先调用 `release`，它同时请求取消并登记 worker 结束后释放；`released: false` 表示已接管延后释放，
不能解释为未受理。未知清理回执及未完成的请求 artifact 回收保留在客户端，后续 `close()` 在自身有界预算内继续处理。
仍无法确认时由原 scope 回收，不重放 begin，也不以取消一个消费者为由杀共享模型进程。

超过 32 KiB 的模型请求或结果使用独享 JSON artifact。发送方 commit 后通过
`artifacts.deliver(artifactId, receiverIdentity, operationId)` 明确交付，接收方 resolve 后校验宿主提供的
`delivery.senderId/senderScope/operationId`，读完 release_received。错误的 owner 不得读取或释放
其他任务的资源。发送方通过 release_delivered 回收未消费交付；任一参与 scope 退出也会回收交付。
图像 data URL 只存在于这份本地 JSON 中，跨 RPC 传描述符，不展开为超大帧。

stream 可产生批量 `text_delta` 进度，每批按约 50 ms 或 2048 字符合并，Provider 最多保留 128 批，每次 poll 最多返回 32 批。
游标落后时返回 `truncated: true`，完整 result 仍是最终依据。Assistant 的分段字幕、立绘和语音继续
消费经过 ChatReply 解析的 segments，不直接播放模型网络增量。

## 配置生效

Provider 启动时复制有效 profiles，每个 job 再复制所用配置。Settings 保存只改变磁盘；目录和
describe 使用当前进程快照，底部统一保存链以 `restart_required` 请求普通插件重载。应用前检查到运行中任务时明确报忙；
检查后进入的后台任务由 Runtime 重载取消、收尾，不能绑定替代实例重放。Core 在整个重载期间暂停新对话受理，
但不跨插件 RPC 持有聊天锁。重载或会话准备失败必须明确报告应用失败，不能把“已保存”当作“当前已使用”。
Assistant 的 `generation` 配置也按自身插件进程冻结，独立于模型连接和引用。

连接测试和获取模型列表属于 Provider 的通用 Settings actions，使用当前窗口草稿与相同任务生命周期和取消机制，不自动保存。发现结果由用户勾选后进入草稿。
设置探测与生成请求共用结果解码，均支持内联结果和 `responseArtifact`；读取文件前核对提供者实例和任务身份，
读取后释放资源。模型列表大小不会改变成功结果的语义。
模型引用、生成参数和连接配置分别保存；旧 `api.yaml` 的一次性交接保持原文件不变，已有目标配置优先。
