---
kind: index
status: current
audience: maintainer
source_of_truth: self
updated: 2026-08-24
---

# Architecture Decision Records

ADR 只记录已经选择或正在评审的架构决策：背景、候选方案、决策、后果和状态。实现细节放在
spec 或 plan，验收证据放在 records。

- [ADR-0001：Runtime v2 进程监管](0001-runtime-v2-process-supervision.md)
- [ADR-0002：Runtime v2 IPC](0002-runtime-v2-ipc.md)
- [ADR-0003：用户数据兼容与 Legacy Qt 迁移参考](0003-runtime-v2-data-compatibility.md)
- [ADR-0004：跨平台基础与平台后端边界](0004-runtime-v2-cross-platform-foundation.md)
- [ADR-0005：无 Qt 薄 Assistant Adapter](0005-runtime-v2-headless-assistant-adapter.md)
- [ADR-0006：同一 Tauri App 的设置窗口宿主](0006-same-app-settings-host.md)
- [ADR-0007：设置按 feature 增量迁移](0007-incremental-settings-feature-migration.md)
- [ADR-0010：跨平台桌宠动态表面与精确命中](0010-cross-platform-pet-surface.md)
- [ADR-0011：Runtime v2 Memory generation 私有 FastEmbed/ONNX 子进程](0011-runtime-v2-memory-process-isolation.md)
- [ADR-0012：Runtime v2 使用 Rust 单写者统一运行日志](0012-runtime-v2-single-writer-observability.md)
- [ADR-0013：分离人类可读运行日志与私密 Agent Trace](0013-human-readable-runtime-log-and-private-agent-trace.md)
- [ADR-0014：Sakura Memory Manager 与无 LLM 的向量后端](0014-sakura-memory-manager-raw-vector-backend.md)
- [ADR-0015：Windows Composition Host Backdrop 实时玻璃](0015-windows-composition-host-backdrop-glass.md)
- [ADR-0016：Runtime v2 generation 私有插件 worker](0016-runtime-v2-generation-private-plugin-worker.md)
- [ADR-0017：Windows 输入栏实时高斯玻璃产品化](0017-windows-input-gaussian-glass-productization.md)
- [ADR-0018：Windows HostBackdrop 离散液态折射](0018-windows-host-backdrop-discrete-liquid-refraction.md)
- [ADR-0019：Windows 液态玻璃单一 GPU 管线](0019-windows-liquid-glass-single-gpu-pipeline.md)
- [ADR-0020：助手阶段工具直接执行](0020-assistant-direct-tool-execution.md)
- [ADR-0021：Harness 只验证产品结果](0021-product-harness-outcome-verification.md)
- [ADR-0022：macOS 输入栏使用公开 AppKit 原生玻璃](0022-macos-native-input-glass.md)
- [ADR-0023：Runtime v2 分离 TTS 合成、播放与语音留存所有权](0023-runtime-v2-tts-audio-ownership.md)
- [ADR-0024：Runtime v2 分离 TTS Provider、Endpoint 与 Managed Runtime](0024-runtime-v2-tts-provider-endpoint-runtime-separation.md)
- [ADR-0025：macOS 桌宠静止态动态包络与过渡稳定包络边界](0025-macos-dynamic-surface-envelope.md)
- [ADR-0026：截图使用 generation 私有 token 与每显示器框选层](0026-runtime-v2-generation-private-screen-resource.md)
- [ADR-0027：Sakura 使用极薄的可组合插件内核](0027-thin-composable-plugin-kernel.md)
- [ADR-0028：模型页使用动态、owner-scoped 的配置槽位](0028-dynamic-owner-scoped-model-slots.md)
- [ADR-0029：Plugin Worker 使用粗粒度生命周期](0029-coarse-plugin-worker-lifecycle.md)
- [ADR-0030：Core 明确失败并由用户手动重试](0030-core-explicit-failure-and-manual-retry.md)
- [ADR-0031：Runtime v2 删除工具确认协议](0031-retire-runtime-v2-tool-confirmation.md)
- [ADR-0032：Runtime 热应用与局部插件生命周期](0032-runtime-hot-application-and-local-plugin-lifecycle.md)

已接受的 ADR 不直接改写历史决策。新决策应创建新的编号，并在元数据或正文中明确
`supersedes` 关系。
