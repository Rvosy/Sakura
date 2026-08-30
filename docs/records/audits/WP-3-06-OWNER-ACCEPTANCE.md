---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-02
---

# WP-3-06 项目负责人验收声明

## 日期与结论

2026-08-02，项目负责人在当前开发会话中明确声明：

> 我确认 WP-3-06 人工验收通过，批准标记 accepted 并进入 WP-3V-01。

该声明关闭任务契约中的三项人工门，并授权将 WP-3-06 标记为 `accepted`。状态只维护在
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)，本记录不形成第二个状态真相源。

## 人工验收边界

- 可见 UI 验收对象是直接启动的真实 Runtime v2 EXE；负责人确认兼容历史、角色与配置行为保持正常，
  并完成基础聊天。`start.bat` 不是最终产品入口，也不作为本次可见 UI 验收对象。
- Legacy Qt 不要求启动可见 UI，不是用户回退入口；其作用限于迁移期实现参考、数据 parser/oracle 和
  隔离兼容测试进程。负责人确认接受自动证据中的 reference → Tauri → reference 往返结果。
- 负责人确认双向锁冲突、未来/损坏数据只读诊断、保存失败保护、强杀后锁重获和相关进程零残留证据，
  并审查同一候选平台边界、脱敏 manifest、Runtime v2 独立回退与 Phase 7 Legacy Qt 删除边界。

## 证据与候选

产品实现候选为 `ed16b7385`；负责人批准的方向修订为 `1e157909`，当前有效 Harness 契约激活修订为
`b08de25a6`，自动验证审计收口提交为 `ca36dfc1`。自动门与真实 Windows 进程结果见
[`WP-3-06-AUTOMATED-VALIDATION.md`](WP-3-06-AUTOMATED-VALIDATION.md)，最终 Harness 报告为
`temp/harness/20260802T153322Z-WP-3-06.json`：`23 passed / 0 failed / 3 manual pending`；其中 pending 是本次
负责人声明前的历史状态，不倒改自动报告。

真实 Windows 脚本已证明批准的 history 与 Runtime v2 UI 路径是唯一变化，隔离验收根已删除，Provider
请求为一次，两个持锁方向均被覆盖，结束后没有该轮相关进程残留。真实 `data/**`、`characters/**` 和
`third_party/**` 未被验收脚本修改。

## 后续处理

本次验收只授权进入 WP-3V-01，不把 CAP-004 提前标记为 `architecture-validated`，也不预先通过后续
Work Package。Legacy Qt 当前继续作为迁移参考与 oracle；只有全部能力迁移完成、WP-7-03 批准删除清单
且 Phase 7 总门通过后，才删除其桌宠入口、实现和发布引用。

若 WP-3V-01 组合纵向验证发现可归因于某个前置 WP 的生产缺陷，应按治理规则将 WP-3V-01 退回
`planned`，只重新打开该责任 WP，不得在验证 WP 内放宽数据、进程、credential 或 generation 门禁。
