---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
updated: 2026-08-21
---

# Generic Plugin Worker Bridge

Bridge 只理解 lifecycle/status、`service.call`、`host.call`、Event、Transform、Config 与 opaque callback
handle，不得枚举 TTS、Memory、Provider 或第三方 Service 名。消息携带 generation/token/request identity，
受 pending 数量、JSON 大小和调用方 deadline 限制；大文件只通过 artifacts descriptor 传递。

Bridge 不定义通用 cancel frame。需要取消的 TTS、下载等领域任务由所属 Service 定义 cancellable job。
超时后的恢复策略固定为：

- `status.get`：同步重建并返回恢复后的 snapshot；
- `hook.transform`：原调用返回 timeout，不重试，后台重建；
- service、callback、event：不重放副作用，并保证 Worker 最终重建；
- lifecycle：Core 先保存 desired state，再同步重建并验证目标状态。

Worker 重建后接收 Core 最新的 `RuntimePluginSpec`，并在 Core 仍有 Session 时重新执行私有
`session.bind`。Bridge 不扫描安装目录，不写 `plugins.yaml`，不暴露模块名、函数名、路径、pickle 或裸
callable。
