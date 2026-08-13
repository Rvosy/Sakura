---
kind: index
status: current
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# Runtime v2 Specs

这里是 Runtime v2 的规范层。它定义产品等价目标、设置迁移契约、IPC、平台后端、Core 生命周期
和聊天边界；不在这里维护 Work Package 的执行状态。

## 总规范

- [产品功能等价规范与发布台账](product-capability-parity.md)
- [设置功能增量迁移规范](settings-incremental-migration.md)

## Work Package Specs

- [Phase 1C](WP-1C-03-protocol-transport.md) · [bundled Core 生命周期](WP-1C-04-bundled-core-lifecycle.md)
- [Phase 1P 平台契约](WP-1P-01-platform-contract.md) · [Runtime 布局](WP-1P-02-runtime-layout.md)
- [共享应用锁](WP-1P-03-shared-instance-lock.md) · [受控进程树](WP-1P-04-managed-process-tree.md)
- [窗口与诊断](WP-1P-05-window-diagnostics.md) · [macOS 稳定化](WP-1P-05A-macos-corrective-stabilization.md)
- [Shell/Core 生命周期](WP-1P-06-shell-core-lifecycle.md)
- [Router](WP-2-01-minimal-concurrent-router.md) · [聊天边界](WP-2-02-minimal-chat-boundary.md)
- [Assistant Adapter](WP-3-01-qt-free-assistant-adapter-readiness.md) · [真实聊天 Core](WP-3-02-headless-real-chat-core.md)
- [桌宠聊天表现](WP-3-03-fake-core-pet-chat-presentation.md)
- [跨平台桌宠动态表面与精确命中](WP-3-03A-cross-platform-pet-surface.md)
- [Windows Composition 实时玻璃 PoC](WP-3-03B-windows-composition-glass-poc.md)
- [真实聊天接入已冻结桌宠 UI](WP-3-04-real-chat-frozen-pet-ui.md)
- [Core 崩溃恢复与 UI 重新水合](WP-3-05-core-crash-ui-rehydration.md)
- [Legacy 数据参考与 Tauri v2 兼容门禁](WP-3-06-legacy-tauri-data-compatibility.md)
- [Memory 能力等价](WP-4-01-memory-capability.md)
- [内置 Tools、Operation 与 Action ID 确认](WP-4-02-tools-operation-action-confirmation.md)
- [Runtime v2 迁移可观测性基础](WP-4L-01-runtime-observability.md)
- [MCP 生命周期与工具调用等价](WP-4-03-mcp-lifecycle-tool-parity.md)
- [人类可读运行日志与 Prompt Trace](WP-4L-02-human-readable-runtime-log-agent-trace.md)
- [Python 插件能力等价](WP-4-04-python-plugin-capability-parity.md)
- [供应商与模型设置](WP-3S-01-provider-model-settings.md)
- [Harness 删除型减负](WP-H-02-lean-agent-development-harness.md)
- [Harness 短超时输出测试确定化纠正](WP-H-02A-harness-timeout-output-capture.md)
- [设置窗口宿主](WP-3U-01-same-app-settings-window.md) · [角色可见能力](WP-3U-02-character-visible-capabilities.md)

执行状态唯一来源：[Runtime v2 Work Package 总计划](../../plans/runtime-v2/work-packages.md)。
