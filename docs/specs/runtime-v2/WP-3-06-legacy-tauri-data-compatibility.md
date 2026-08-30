---
kind: spec
status: superseded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
superseded_by: docs/adr/0035-clean-runtime-v2-layout-and-explicit-legacy-import.md
updated: 2026-08-27
---

# WP-3-06：历史数据兼容门禁（已废止）

Runtime v2 使用全新的用户根和当前 v1 数据契约。正常启动不扫描、读取、迁移、规范化或双写旧 main / Legacy
Qt 数据，也不接受开发期产生的其他 schema 版本。旧实现和验收事实只在 Git 历史、`docs/archive/` 与
`docs/records/` 中保留，不构成当前产品、测试或发布合同。

当前约束由
[ADR-0035](../../adr/0035-clean-runtime-v2-layout-and-explicit-legacy-import.md) 和各领域规范定义。
