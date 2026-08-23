---
kind: plan
status: active
audience: maintainer
source_of_truth: self
active_work_package: WP-4-06
updated: 2026-08-23
---

# Runtime v2 路线图

Runtime v2 的目标是完成可发布的 Tauri 桌宠，而不是建设一套自动治理平台。当前只保留三个能力边界：

```text
Tauri Shell -> Python Core -> Plugin Worker
```

跨边界机制必须有当前消费者。保护用户数据、回收进程树、隔离旧 generation、限制 IPC 和保护截图资源的
保险丝继续保留；自动重试、动态调和、自愈和迁移期验收后门不属于产品能力。

## 阶段状态

| 阶段 | 结果 | 状态 |
|---|---|---|
| Phase 0–3 | Tauri Shell、受控 Core、真实聊天、设置宿主、数据兼容 | accepted |
| Phase 4 | Memory、Tools、MCP、Plugin v3、TTS、截图和主动能力 | active |
| Phase 5 | 设置收口、角色/Session、系统集成与本地桥接 | planned |
| Phase 6 | Studio Workspace、导入、预览与发布 | planned |
| Phase 7 | 三平台发布验证、Legacy 删除审查与打包 | planned |

历史逐日状态、候选证据和已完成 WP 的原始记录已归档到
[`docs/archive/plans/runtime-v2/pre-simplification-2026-08-23/`](../../archive/plans/runtime-v2/pre-simplification-2026-08-23/)。

## 当前工作

- WP-4-06：稳定手动截图、generation 私有 token、多显示器/DPI 和平台权限。
- Runtime v2 简化：Core 明确失败并手动恢复；Plugin v3 一次加载并以整 Worker 重建处理管理变更；删除无消费者
  的确认协议、Fake Core 和 Phase 1B/1C 后门。
- Legacy Qt 冻结：只作数据 parser/oracle 和必要回归，不接入 Runtime v2、新插件 API 或新增能力。

## 未完成 Work Package

| Work Package | 目标 | 状态 |
|---|---|---|
| WP-3-03D | Windows 输入栏液态折射实验 | paused |
| WP-4-06 | 手动截图与受控图像资源 | active |
| WP-4-07 | 自动观察、主动互动、提醒与待办 | planned |
| WP-4-08 | Phase 4 组合稳定化与资源回收 | planned |
| WP-5-01 | 设置仓库与剩余外观/布局缺口 | planned |
| WP-5-02 | 设置迁移关闭清单与首次设置 | planned |
| WP-5-03 | 角色切换、Session 与历史分页 | planned |
| WP-5-04 | 托盘、置顶、快捷键与开机启动 | planned |
| WP-5-05 | 浏览器与移动/本地桥接生命周期 | planned |
| WP-5-06 | 扩展诊断、Repair 与更新前置检查 | planned |
| WP-6-01–05 | Studio 数据、导入、预览、发布与大文件操作 | planned |
| WP-7-01–02 | 自动化矩阵与三平台真实 WebView E2E | planned |
| WP-7-03 | 功能等价、数据兼容与 Legacy 删除审查 | planned |
| WP-7-04–06 | 打包、长稳与最终发布审查 | planned |

## WP-4-07 设计边界

WP-4-07 尚未实现，也不在本轮创建任何协议或数据文件。日后开始时采用局部、普通的 timer：

- Shell timer 触发自动截图；忙时跳过，只保留内存批次。
- Core timer 扫描提醒；提醒成功交给回复链或固定兜底后标记完成。
- 待办只提供普通 CRUD。极端崩溃窗口允许提醒重播。

不引入通用 Scheduler、trigger queue、fake-clock `tick_once`、lease、occurrence ledger、claim、outbox、ack、
自动退避或 crash-recovery 协议。只有出现可复现的数据损失或重复副作用后，才重新评估更强机制。

## 维护规则

- 长期行为写入 Spec，重要取舍写入 ADR；路线图不复制逐日测试流水。
- 一个阶段开始前只需明确真实消费者、最小接口、失败方式和聚焦验证。
- 默认采用明确失败和用户发起的重试。能通过重启 Worker/Core 恢复的场景，不增加局部热更新或调和层。
- WP-4-07 stash 不作为当前实现输入；必须在重新设计时单独审查。
