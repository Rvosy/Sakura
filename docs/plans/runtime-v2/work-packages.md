---
kind: plan
status: active
audience: maintainer
source_of_truth: self
active_work_package: null
updated: 2026-08-28
---

# Runtime v2 路线图

Runtime v2 的目标是完成可发布的 Tauri 桌宠，而不是建设一套自动治理平台。当前只保留三个能力边界：

```text
Tauri Shell -> Python Core -> PluginRuntimeManager -> per-plugin processes
```

跨边界机制必须有当前消费者。保护用户数据、回收进程树、隔离旧 generation、限制 IPC 和保护截图资源的
保险丝继续保留；自动重试、动态调和、自愈和迁移期验收后门不属于产品能力。

## 阶段状态

| 阶段 | 结果 | 状态 |
|---|---|---|
| Phase 0–3 | Tauri Shell、受控 Core、真实聊天、设置宿主、干净 v1 数据契约 | accepted |
| Phase 4 | Memory、Tools、MCP、Plugin Runtime v4、TTS、截图和主动能力 | active |
| Phase 5 | 设置收口、角色/Session、系统集成与本地桥接 | planned |
| Phase 6 | Studio Workspace、导入、预览与发布 | planned |
| Phase 7 | 三平台发布验证、v1 数据完整性与打包 | planned |

历史逐日状态、候选证据和已完成 WP 的原始记录已归档到
[`docs/archive/plans/runtime-v2/pre-simplification-2026-08-23/`](../../archive/plans/runtime-v2/pre-simplification-2026-08-23/)。

## 当前工作

- WP-4-07 已通过自动门和项目负责人验收；CAP-016 已转为 `parity-accepted`。
- WP-4-09 Plugin Runtime v4 已通过实现、独立 Review 和验收门，状态为 `accepted`；当前没有激活中的
  Work Package。WP-4-07R 与 WP-4-08 保持 `planned`，等待单独激活。WP-4-07R 已冻结
  Spec/ADR，但这不表示 Timeline、预算或数据切换已经实现。
- Runtime v2 简化：Core 明确失败并由用户显式恢复；Plugin v4 只响应 generation 启动和用户 lifecycle 操作，
  不保留后台 reconcile、自愈或调用重放。
- Plugin Runtime v4 已完成 v4-only 切换和完整验收：官方默认实现可替换，每插件独立进程和 dependency
  root，跨进程 ServiceProxy，官方插件依赖与实现不进入 Core Runtime。
- Legacy Qt 已按 ADR-0034 退役；旧行为通过 Git 历史查看，当前运行时不保留旧 schema parser 或 migration。

## 未完成 Work Package

| Work Package | 目标 | 状态 |
|---|---|---|
| WP-3-03D | Windows 输入栏液态折射实验 | paused |
| WP-4-07R | 类型化交互时间线、自适应上下文与 Memory 增量读取 | planned |
| WP-4-08 | Phase 4 组合稳定化与资源回收 | planned |
| WP-5-01 | 设置仓库与剩余外观/布局缺口 | planned |
| WP-5-02 | 设置迁移关闭清单与首次设置 | planned |
| WP-5-03 | 角色切换、Session 与历史分页 | planned |
| WP-5-04 | 托盘、置顶、快捷键与开机启动 | planned |
| WP-5-05 | 浏览器与移动/本地桥接生命周期 | planned |
| WP-5-06 | 扩展诊断、Repair 与更新前置检查 | planned |
| WP-6-01–05 | Studio 数据、导入、预览、发布与大文件操作 | planned |
| WP-7-01–02 | 自动化矩阵与三平台真实 WebView E2E | planned |
| WP-7-03 | 功能等价、v1 数据完整性与历史残留审查 | planned |
| WP-7-04–06 | 打包、长稳与最终发布审查 | planned |

## WP-4-07 accepted 边界

WP-4-06 已通过自动门、三平台 CI 与项目负责人实机验收，CAP-015 转为 `parity-accepted`。WP-4-07
也已通过自动门和项目负责人验收，CAP-016 转为 `parity-accepted`。其 accepted 范围只有一个真实消费者：
定时截取鼠标所在屏幕，将最新若干张作为一次普通聊天请求发送。

- WebView 使用 10 秒普通轮询；忙时跳过，休眠后最多立即截一张，不补跑。
- Rust 只保留有界 JPEG 内存批次；发送时短暂生成 generation 私有资源，Core 单次消费后删除。
- Core 的一个 attachment ID 可对应多张图片；自动请求复用 `chat.send`、回复、TTS 和历史链。
- WP-4-07R accepted 前，定时截图继续按 WP-4-07 的现有 user-role 请求和 JSONL 历史语义运行；计划中的
  observation 类型与 SQLite Timeline 不得被表述为当前产品能力。
- CAP-017 提醒与待办移出本 WP，保持未排期；出现明确需求时单独立项，不预留协议。
- 不迁移 legacy `screen_awareness_check`、视觉摘要任务或主动事件系统，不建设 Scheduler、Worker、
  trigger queue、lease、outbox、ack、自动重试或恢复协议。

## 维护规则

- 长期行为写入 Spec，重要取舍写入 ADR；路线图不复制逐日测试流水。
- 一个阶段开始前只需明确真实消费者、最小接口、失败方式和聚焦验证。
- 默认采用明确失败和用户发起的重试。能通过重启 Worker/Core 恢复的场景，不增加局部热更新或调和层。
- WP-4-07 stash 不作为当前实现输入；必须在重新设计时单独审查。
