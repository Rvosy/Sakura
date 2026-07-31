---
kind: index
status: current
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-07-31
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
- [供应商与模型设置](WP-3S-01-provider-model-settings.md)
- [设置窗口宿主](WP-3U-01-same-app-settings-window.md) · [角色可见能力](WP-3U-02-character-visible-capabilities.md)

执行状态唯一来源：[Runtime v2 Work Package 总计划](../../plans/runtime-v2/work-packages.md)。
