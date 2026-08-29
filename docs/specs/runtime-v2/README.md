---
kind: index
status: current
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-29
---

# Runtime v2 Specs

这里是 Runtime v2 的规范层。它定义产品等价目标、设置迁移契约、IPC、平台后端、Core 生命周期
和聊天边界；不在这里维护 Work Package 的执行状态。

## 总规范

- [产品功能等价规范与发布台账](product-capability-parity.md)
- [设置功能增量迁移规范](settings-incremental-migration.md)
- [Sakura Plugin Runtime v4](sakura-plugin-runtime-v4.md)
- [Sakura Plugin API v3（已取代）](sakura-plugin-kernel-v3.md)
- [Runtime v2 热应用规范](runtime-hot-application.md)
- [Core 明确失败与手动恢复](WP-3-05-core-crash-ui-rehydration.md)
- [Runtime v2 单一运行时边界](runtime-v2-only-boundary.md)
- [发行与存储合同](release-distribution-and-storage.md)

## Work Package Specs

- [Phase 1C](WP-1C-03-protocol-transport.md) · [bundled Core 生命周期](WP-1C-04-bundled-core-lifecycle.md)
- [Phase 1P 平台契约](WP-1P-01-platform-contract.md) · [Runtime 布局](WP-1P-02-runtime-layout.md)
- [共享应用锁](WP-1P-03-shared-instance-lock.md) · [受控进程树](WP-1P-04-managed-process-tree.md)
- [窗口与诊断](WP-1P-05-window-diagnostics.md) · [macOS 稳定化](WP-1P-05A-macos-corrective-stabilization.md)
- [Shell/Core 生命周期](WP-1P-06-shell-core-lifecycle.md)
- [Router](WP-2-01-minimal-concurrent-router.md) · [聊天边界](WP-2-02-minimal-chat-boundary.md)
- [Assistant Adapter](WP-3-01-qt-free-assistant-adapter-readiness.md) · [真实聊天 Core](WP-3-02-headless-real-chat-core.md)
- [跨平台桌宠动态表面与精确命中](WP-3-03A-cross-platform-pet-surface.md)
- [Windows Composition 实时玻璃 PoC](WP-3-03B-windows-composition-glass-poc.md)
- [Windows 输入栏实时高斯玻璃产品化](WP-3-03C-windows-input-gaussian-glass.md)
- [Windows HostBackdrop 输入栏液态折射 PoC](WP-3-03D-windows-input-liquid-refraction-poc.md)
- [macOS 输入栏原生高斯与液态玻璃](WP-3-03E-macos-input-native-glass.md)
- [真实聊天接入已冻结桌宠 UI](WP-3-04-real-chat-frozen-pet-ui.md)
- [Core 明确失败与手动恢复](WP-3-05-core-crash-ui-rehydration.md)
- [已废止的历史数据兼容门禁](WP-3-06-legacy-tauri-data-compatibility.md)
- [Memory 能力等价](WP-4-01-memory-capability.md)
- [Runtime v2 迁移可观测性基础](WP-4L-01-runtime-observability.md)
- [MCP 生命周期与工具调用等价](WP-4-03-mcp-lifecycle-tool-parity.md)
- [人类可读运行日志与 Prompt Trace](WP-4L-02-human-readable-runtime-log-agent-trace.md)
- [本次运行日志查看器](WP-5-06-runtime-log-viewer.md)
- [安全角色切换、Session 与历史分页](WP-5-03-safe-character-switch.md)
- [TTS、播放与音频设备门禁](WP-4-05-tts-playback-audio-device-gate.md)
- [手动截图、受控图像资源与平台权限](WP-4-06-screen-capture-controlled-image-resource.md)
- [定时截图与主动请求](WP-4-07-proactive-reminders-todos.md)
- [类型化交互时间线与自适应上下文](WP-4-07R-typed-timeline-adaptive-context.md)
- [供应商与模型设置](WP-3S-01-provider-model-settings.md)
- [设置窗口宿主](WP-3U-01-same-app-settings-window.md) · [角色可见能力](WP-3U-02-character-visible-capabilities.md)

执行状态唯一来源：[Runtime v2 Work Package 总计划](../../plans/runtime-v2/work-packages.md)。

精简前的 Plugin 拆分草案、Fake Core、工具确认和逐 WP 规范保留在
[`docs/archive/specs/runtime-v2/pre-simplification-2026-08-23/`](../../archive/specs/runtime-v2/pre-simplification-2026-08-23/)，
只作历史参考。
