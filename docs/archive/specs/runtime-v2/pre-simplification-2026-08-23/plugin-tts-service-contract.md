---
kind: spec
status: archived
audience: maintainer
source_of_truth: self
updated: 2026-08-23
---

# TTS Plugin Service Contract

TTS Hub 提供 Worker-local `sakura.tts`，Provider 通过 Effect-scoped registration 接入。Hub 按请求的
character ID 读取角色 extension 并冻结 Provider 选择；失败不得按安装顺序或健康状态静默换声线。

耗时合成由 Provider 自有 cancellable job 完成，稳定短调用为 `begin/poll/cancel/status`。Generic Bridge
不为此增加领域分支或通用 cancel frame。Provider 通过 `sakura.host.artifacts` 提交音频，Core TTS consumer
在已有 segment authorization 内一次性消费，并继续拥有 recording 与 opaque playback；不存在
`sakura.host.audio` Worker 能力。

Provider 停用时先 cancel/join 自有 job，再释放未提交 artifact；无法协作结束时由 Worker lifecycle deadline
终止并重建。详细产品播放门仍由 [WP-4-05](../../../../specs/runtime-v2/WP-4-05-tts-playback-audio-device-gate.md) 约束。
