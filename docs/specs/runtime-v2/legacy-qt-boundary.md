---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-23
---

# Legacy Qt 冻结边界

Legacy Qt 不再是 Runtime v2 的回退产品，也不是新功能宿主。保留代码只有两个用途：

- 解析既有角色包、配置和用户数据，作为兼容 oracle。
- 运行必要的回归测试，证明 Tauri/Python Core 没有改变既有数据语义。

从本规范生效起：

- 不把 Runtime v2 协议、Plugin API v3、主动能力或新设置接回 Qt。
- `PendingToolAction` 与 `PermissionPolicy` 只允许 Legacy 现有调用链继续使用；Runtime v2 不引用它们。
- 修复 Legacy 仅限数据 parser/oracle、测试可运行性和阻止真实数据损坏，不做界面或能力扩展。
- 新代码不能以“同时兼容 Qt”为由增加 DTO、分支、shim 或双生命周期所有权。

WP-7-03 应核对 Runtime v2 已覆盖发布能力、数据往返与迁移输入，形成一次性删除清单。审查通过前不提前
删除仍被 oracle 测试使用的 parser；审查通过后整体删除 Legacy Qt，而不是长期维持双入口。
