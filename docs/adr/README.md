---
kind: index
status: current
audience: maintainer
source_of_truth: self
updated: 2026-08-12
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
- [ADR-0009：Harness 收敛为测试执行与安全边界](0009-lean-agent-development-harness.md)
- [ADR-0010：跨平台桌宠动态表面与精确命中](0010-cross-platform-pet-surface.md)
- [ADR-0011：Runtime v2 Memory generation 私有 FastEmbed/ONNX 子进程](0011-runtime-v2-memory-process-isolation.md)
- [ADR-0012：Runtime v2 使用 Rust 单写者统一运行日志](0012-runtime-v2-single-writer-observability.md)
- [ADR-0013：Runtime v2 generation 私有插件 worker](0013-runtime-v2-generation-private-plugin-worker.md)

已接受的 ADR 不直接改写历史决策。新决策应创建新的编号，并在元数据或正文中明确
`supersedes` 关系。
