---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-4-01B Memory LLM 解耦自动验证记录

## 候选与环境

- 日期：2026-08-13（Asia/Shanghai）
- 分支：`refactor/tauri-runtime-v2`
- 实现候选：`9f3a8aa1c881d4e5e30fe78d2f369dc79dbd5c1d`
- 契约范围修订：`4bdab3aa8c7b8d6f77b22e74490cdd64365d9dee`
- 平台：Windows，仓库固定 `runtime/python.exe`
- 机器报告：`temp/harness/20260812T175401.093158Z-WP-4-01B.json`（本地忽略目录）

## 结果

`harness check WP-4-01B` 通过：固定 base 是当前 HEAD 祖先，依赖 WP-4-01A 已 accepted，changed-set
全部位于 allowlist，未触碰 `data/**`、`characters/**`、`third_party/**` 或 retired activation。

`harness verify WP-4-01B` 返回 `manual_pending`：14/14 自动 case 通过，0 failed，0 blocked。覆盖 docs、
Runtime v2 frontend/shell/window、Python unit/integration、legacy Qt UI 和 Agent Trace journey。其中本轮
Python unit 为 670 passed/6 skipped，integration 为 59 passed/2 skipped，Qt UI 为 24 passed。

额外定向回归为 42 passed，覆盖无 LLM 配置、raw backend 拒绝 `infer=True`、子进程受限 RPC、初始化
半失败资源回收、Memory boundary 和既有能力集成。隔离临时目录中的真实 FastEmbed/Qdrant 探针完成
add/get/list/search/update/delete，结果 `has_llm=False`；未读取、写入或迁移真实用户 `data/**`。

首次正式 verify 因此前实机 debug Sakura 仍持有生产单实例锁，使 3 条 WP-3-06 兼容测试失败并阻塞后续
case。确认进程树来源后，仅向精确 Shell PID 发送正常关窗请求；Shell、Core 与 Memory 后代均正常回收。
随后重跑得到上述 14/14 通过结果。更早两次由外层 1 秒超时截断的 verify 没有形成测试结论，不作为证据。

## 尚待人工验收

自动门不构成人工验收。负责人需在真实应用确认：无可用 Provider/API Key 时本地 Memory 可初始化和管理；
切换聊天模型不会触发 Memory 重载；达到整理阈值时仍只由 `memory_curation` 模型产生一次可追踪的整理调用；
正常退出后无 Memory 子进程、Qdrant 锁或 SQLite 句柄残留。

## 2026-08-13 项目负责人验收与恢复基线批准

2026-08-13，项目负责人在当前开发会话中明确声明：

> 这个我也验收通过了

该声明关闭 WP-4-01B 的人工验收门。恢复任务时，Harness 发现已验收的插入工作包 WP-3-03B 位于原固定
基线之后，导致玻璃文件被识别为 WP-4-01B 越界变更；项目负责人随后明确批准：

> 批准，我们现在就是在处理这些冲突

据此将 WP-4-01B 的 `base_ref` 从
`18b0a7cbb66706074df370b90dde51409fd0d8f7` 前移到其后代、WP-3-03B 已验收收口提交
`71820032c214d9f462b6a032787ae824ad37f3a3`。修订由独立提交
`3b17cf0a7e96e7d3bdbc1b3af21fa2927256ca59` 保存；allowlist 与 required profiles 未放宽。

修订提交后，`runtime/bin/python3.12 -m harness check WP-4-01B` 全部通过。随后执行
`runtime/bin/python3.12 -m harness verify WP-4-01B`，报告为
`temp/harness/20260813T024005.878058Z-WP-4-01B.json`：14/14 自动 case 通过、0 failed、0 blocked，
返回 `manual_pending`。其中 Python unit 为 676 passed/1 skipped，integration 为 61 passed，Qt UI 为
24 passed，Runtime v2 Shell、文档和 Agent Trace journey 全部通过。

负责人接受的 Memory 解耦实现候选为 `9f3a8aa1c881d4e5e30fe78d2f369dc79dbd5c1d`，恢复基线并重跑门禁后的
集成验证 HEAD 为 `3b17cf0a7e96e7d3bdbc1b3af21fa2927256ca59`。`manual_pending` 是负责人声明写入前的正确机器结果，
不倒改为自动 accepted。

本记录只保存负责人明确给出的验收与基线修订批准，不补写未提供的逐项设备操作或额外测试结果。
Work Package 当前状态仍只维护在[总计划](../../plans/runtime-v2/work-packages.md)，本记录不形成第二个
状态真相源；本次结论不预先接受恢复为 `stabilizing` 的 WP-4L-02 或任何后续工作包。
