---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-02
---

# WP-3-05 本地自动验证记录

## 环境与范围

- 日期：2026-08-02（Asia/Shanghai）
- 分支：`refactor/tauri-runtime-v2`
- 候选：本地 `WORKTREE`；尚未创建候选提交或推送远端
- Work Package 基线：`0ad1a1af3922d9263dac45fb0320d655e18c3a08`
- 用户数据边界：`data/**`、`characters/**`、`third_party/**` 无变化；自动故障只使用现有隔离 fixture

## 实现与故障覆盖

实现沿用唯一 `CoreSupervisor` 和既有三次 restart budget。Core 失败时先撤销旧代 Gateway、ChatBridge、
settings transport、Snapshot 与角色资源可用性，再完成进程树回收和串行重启。前端在同一 WebView 内封闭
旧 send/cancel/event/图片回调，将活动回复收束为一个本地 interrupted 终态，并保留此前完成的回复导航与
未发送草稿。新代完整 Snapshot 和角色资源准备完成前保持 `rehydrating`，不开放发送或自动重发；预算
耗尽后只在确定性失败状态开放同一 Supervisor 的手动重试。

真实 Core 故障测试依次验证 generation 1 强杀、旧公开面即时失效、generation 2 串行恢复；随后连续强杀
至 generation 4，确认第四次崩溃耗尽三次自动预算，再经手动重试进入 generation 5 并正常退出。测试同时
要求 Snapshot/角色 publication 清空、旧资源不可用和最终 lifecycle worker、管道与完整进程树回收。

## 本地自动结果

```text
node --test desktop/frontend/tests/*.test.js
-> 105 passed / 0 failed

cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --quiet -- --test-threads=1
-> 232 passed / 24 ignored / 0 failed

cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
-> passed

git diff --check
-> passed

runtime\python.exe -m harness check WP-3-05
-> scope/dependencies passed；越界、禁止、受保护、依赖文件与测试删除均为零

runtime\python.exe -m harness verify WP-3-05
-> manual_pending；23 passed / 0 failed / 3 manual pending
```

第一次 `verify` 报告为 `temp/harness/20260802T132959Z-WP-3-05.json`，运行 168.015 秒。`docs`、
`smoke`、`core-host`、`runtime-v2-shell`、`python-full` 全部 required profiles 通过；6 项自动验收均为
passed。该本地结果包含三平台 workflow 契约门，但不冒充项目负责人对同一最终候选 SHA 平台运行证据的
人工审查。

## 结论与待验收项

自动门通过；由于 Work Package 状态真相源属于项目负责人审查范围，WP-3-05 当前仍保持 `active`，等待
负责人决定是否进入 `stabilizing`。仍由项目负责人完成三项人工验收：在真实 Windows
Tauri/WebView2 下分别于 idle、活动回复和已完成回复后强杀 Core；验证恢复期中文 IME、连续崩溃失败、
手动重试与零相关进程残留；审查同一候选 SHA 的三平台证据和回退边界。

本文不填写上述人工结果，不把 WP-3-05 标记为 `accepted`；当前状态唯一见
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)。
