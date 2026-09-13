---
kind: plan
status: active
audience: maintainer
source_of_truth: self
updated: 2026-09-14
---

# Runtime v2 路线图

本表记录计划和已知验收进度，不是开发许可清单。当前用户任务可以涉及任一相关能力，无需先“激活”工作包；
开始实现也不等于通过验收。状态更新必须依据实际证据，自动检查和人工结果分别说明。

Runtime v2 的目标是完成可发布的 Tauri 桌宠，并由薄宿主与可替换的默认插件提供日常能力。
插件生态方向见[开放插件生态总计划](open-plugin-ecosystem.md)：M1 的 Context 修正保留，M2 的主设置选择入口已撤回，
执行合同、进程绑定和取消作为开发基础保留。M3/M4 仍是长期方向，后续拆分与界面待真实需求和用户方案明确。
本轮从 M5 推进普通服务显式绑定及 TTS 旧任务隔离、可选便签记忆两个实际消费者，不恢复主设置或模式选择。
该计划不改变下表尚未核对的历史验收状态。
当前运行拓扑如下；部署位置不等于所有业务策略都应永久归 Core：

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
| Phase 5 | 设置收口、角色/Session、系统集成与本地桥接 | active |
| Phase 6 | Studio Workspace、导入、预览与发布 | stabilizing |
| Phase 7 | 三平台发布验证、v1 数据完整性与打包 | planned |

历史逐日状态、候选证据和已完成 WP 的原始记录已归档到
[`docs/archive/plans/runtime-v2/pre-simplification-2026-08-23/`](../../archive/plans/runtime-v2/pre-simplification-2026-08-23/)。

## 当前工作

- WP-4-07 已通过自动门和项目负责人验收；CAP-016 已转为 `parity-accepted`。
- WP-4-09 Plugin Runtime v4 已通过实现、独立 Review 和验收门，状态为 `accepted`。
  WP-4-07R 与 WP-4-08 在本表仍记为 `planned`。WP-4-07R 已有 Spec/ADR；这些状态和设计文档本身
  不能证明 Timeline、预算或数据切换的实现情况，相关任务需核对当前调用链和测试。
- WP-5-03 安全角色切换纵向链已实现并通过隔离 Harness：角色变更只跨完整 Core generation，Memory、
  Timeline、TTS/资源和前端迟到状态按 generation/角色隔离。当前为 `stabilizing`，等待真实设置窗口
  A→B→A 交互验收后再转 `accepted`。
- WP-6-01–05 已接入主 Tauri 应用：工坊窗口、schema v1 Core 边界、旧草稿、资源导入、试听 URL、事务发布、
  `.char` 导出、大文件取消和当前角色重载已有自动覆盖。当前为 `stabilizing`；Windows x64 与 macOS arm64
  的真实 WebView、带语音大文件和多显示器取色尚未验收。
- Runtime v2 简化：Core 明确失败并由用户显式恢复；Plugin v4 只响应 generation 启动和用户 lifecycle 操作，
  不保留后台 reconcile、自愈或调用重放。
- Plugin Runtime v4 已完成 v4-only 切换和完整验收：官方默认实现可替换，每插件独立进程和 dependency
  root，跨进程 ServiceProxy，官方插件依赖与实现不进入 Core Runtime。
  这里的默认实现指已迁入插件的能力；正常聊天当前使用默认 Assistant，模型客户端、Agent 和管线尚未完成插件迁移。
- 开放插件生态 M1 已提交 `2097b739`。M2 的历史实现 `f9fde091` 曾提供执行器选择，现按用户要求撤回主设置和正常聊天入口，
  旧 `chat_executor` 直接忽略，`settings.executor` 端点移除。执行服务与专注样例保留供开发验证，不形成隐藏模式或由插件开关接管聊天。
  当前日常验收复用已有数据、默认 Assistant 和插件自身的对话规则文本；M3/M4 不自动进入产品改造。详细范围见总计划第 12 节。
- 开放插件生态 M5 本轮分两项实施：公开 `context.bind()`，让 TTS Hub 的旧 job 查询/取消固定到原 Provider 进程；
  增加可选[便签记忆](../../specs/runtime-v2/fact-memory.md)，由插件自己的 Collection、私有 SQLite、Context 和只读工具完成管理与召回。
  两项按各自实际测试和 Git 节点记录结果，当前范围不等于已通过验收。日常使用由用户通过已有数据启动器安装、启用可选插件，
  不自动改写原角色、模型、插件开关或 Mem0 数据。范围与验收见总计划第 13 节。
- Legacy Qt 已按 ADR-0034 退役；旧行为通过 Git 历史查看，当前运行时不保留旧 schema parser 或 migration。

## 未完成 Work Package

| Work Package | 目标 | 状态 |
|---|---|---|
| WP-3-03D | Windows 输入栏液态折射实验 | paused |
| WP-4-07R | 类型化交互时间线、自适应上下文与 Memory 增量读取 | planned |
| WP-4-08 | Phase 4 组合稳定化与资源回收 | planned |
| WP-5-01 | 设置仓库与剩余外观/布局缺口 | planned |
| WP-5-02 | 设置迁移关闭清单与首次设置 | planned |
| WP-5-03 | 角色切换、Session 与历史分页 | stabilizing |
| WP-5-04 | 托盘、置顶、快捷键与开机启动 | planned |
| WP-5-05 | 浏览器与移动/本地桥接生命周期 | planned |
| WP-5-06 | 扩展诊断、Repair 与更新前置检查 | planned |
| WP-6-01–05 | Studio 数据、导入、预览、发布与大文件操作 | stabilizing |
| WP-7-01–02 | 自动化矩阵与三平台真实 WebView E2E | planned |
| WP-7-03 | 功能等价、v1 数据完整性与历史残留审查 | planned |
| WP-7-04–06 | 打包、长稳与最终发布审查 | planned |

WP-5-06 的本次运行日志查看器已作为独立切片实现；这不表示完整 WP-5-06 已完成。历史日志读取、诊断设置、
Repair、自动修复和更新前置检查继续保持 `planned`。

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
