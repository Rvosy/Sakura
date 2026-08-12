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
