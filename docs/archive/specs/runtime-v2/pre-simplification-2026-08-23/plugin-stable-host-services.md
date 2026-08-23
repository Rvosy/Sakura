---
kind: spec
status: archived
audience: maintainer
source_of_truth: self
updated: 2026-08-23
---

# Stable Plugin Host Services

稳定清单为 `sakura.host.context`、`sakura.host.tools`、`sakura.host.settings` 基础能力、
`sakura.host.model_slots`、`sakura.host.character` 与 `sakura.host.artifacts`。Python Host 是 descriptor
业务语义的唯一校验和投影层；Rust/WebView 只校验 envelope、schemaVersion、身份、枚举及数组/总大小
边界。`sakura.host.audio` 不属于 Host Service 契约，TTS artifact 由 Core consumer 完成录音与播放。

Settings Basic 只包含基础字段、Action、状态与单 section load/save。保存使用原子写入和
last-writer-wins，不借用全局 `plugins.yaml` revision；启停是独立管理命令。Voice 跨 owner 组合保存继续
返回明确 partial 状态。

`sakura.host.model_slots` 是稳定领域 Host Service，不是 Kernel Slot Registry。descriptor 精确为
`slotId/label/description/modelKind/required/order`；selection 精确为 `profileId/model`。凭据、endpoint、
私密 Provider 数据和推理请求不得穿过此接口。registration 绑定插件 root Effect，停用、失败或重建时撤销
并重新注册；Assistant Session 不可用时仍可读取和保存。
