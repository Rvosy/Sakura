---
kind: plan
status: archived
audience: maintainer
source_of_truth: self
status_source: self
updated: 2026-08-23
---

# WP-4L-01 Runtime v2 迁移可观测性实施计划

## 1. 目标、基线与边界

以项目负责人接受的 WP-4-02 最终候选
`6843dd40e9513d8015acde8db39fe93eedb2a134` 为固定 base，实现
[`normative Spec`](../../../../specs/runtime-v2/WP-4L-01-runtime-observability.md) 与
[`ADR-0012`](../../../../adr/0012-runtime-v2-single-writer-observability.md)。任务契约为
`harness/tasks/WP-4L-01.json`，不创建 activation。

本 WP 不修改 `data/**`、`characters/**`、`third_party/**` 或 `tools/mcp/**`，不实现查看器、日志配置、
读取/导出 API、上传、遥测或 Repair。

## 2. 分阶段实施

### A. 治理与 RED

- 提交负责人验收记录、ADR、Spec、本文、CAP-025、索引、用户/开发文档、CHANGELOG、task v2 和
  `journey-observability` profile。
- 运行 `harness check WP-4L-01`，再增加 Rust/Python/frontend RED，证明现有路径缺少单写者、bridge、
  严格 WebView command 与统一诊断。

退出条件：固定 base、依赖、allowlist、required profiles 和文档门均通过；产品修改前 task 已提交。

### B. Rust 单写者

- 新增独立 runtime log 模块，完成 run/sequence、等级过滤、统一脱敏、4 KiB 上限、1024 队列、优先级
  淘汰、聚合丢弃、10 MiB/5 备份轮转、250 ms/即时刷新和 500 ms shutdown。
- 共享锁成功后创建服务并注入 Tauri state/lifecycle；替换 Memory 文件写入和 interaction latency writer。
- 为 Shell、Core 生命周期、Gateway、设置、聊天、Memory、Tools 和 Rust panic 安装首批固定事件。

退出条件：Rust 单元测试覆盖并发顺序、混合旧行、轮转、过载、故障和退出；日志故障不改变控制流。

### C. Python bridge 与 Core drainer

- 安装 256 条 stderr bridge，把 Core 的 `log_event` 和 `app.*` logging 转成 V1 prefixed JSON；Core 模式
  禁止直接文件写入，stdout 保持协议纯净。
- 聊天 operation ID 注入 interaction context；在真实 Agent/模型/Tools/Memory 日志中验证关联与清理。
- Rust drainer 支持任意分片、非法/超限记录、64 KiB 普通 stderr tail、首告警/退出摘要、generation/PID
  注入和旧 generation 晚到。

退出条件：Python unit/integration 与 Rust 测试证明 bridge 背压、脱敏、stdout 零污染、崩溃和重建安全。

### D. WebView 受控诊断

- 新增只接收批准字段的批量 command 与前端 best-effort batching/invoke 包装器。
- 接入主窗口 lifecycle/chat/Memory/Tools 和设置窗口关键动作；记录稳定结果，不记录参数、返回值、正文、
  异常原文或 console。

退出条件：前端测试证明包装器返回/拒绝语义透明，批次字段严格，诊断失败不影响产品流程。

### E. Journey 与候选

- required profiles 运行 `docs`、`runtime-v2-shell`、`python-full`、`journey-observability`；Harness 禁止
  `python-full` 与其已覆盖的 `smoke`/`core-host` 同时登记，以免重复收集。另运行完整 Rust 回归、
  fmt/check 和相关前端组合回归。
- 使用系统临时目录下的隔离 assistant root 做真实 Windows 启动、聊天、设置、Tools、Core crash/recovery、
  正常退出和第二实例冲突；对统一日志执行敏感 sentinel scan，并确认真实 `data/**` 清单零变化和进程零残留。
- 同一候选 SHA 的 Windows x64、macOS arm64、Linux x64 Runtime v2 CI 全绿后写自动验证 record，执行
  `harness verify WP-4L-01`。

退出条件：自动门全绿时只进入 `manual_pending`/`stabilizing`，不得代填负责人实机验收或声明 accepted。

## 3. 故障矩阵

覆盖日志目录不存在/只读、open/write/flush/rename 失败、旧/损坏/超大 JSONL、并发轮转、队列全低级/
全高级、worker panic/提前退出、Core bridge 队列满、stderr broken pipe、任意 UTF-8/字节分片、非法前缀
JSON、stderr flood、Core crash/restart、旧 generation 晚到、WebView extra fields/超长批次/NaN 时长、页面
关闭与应用 shutdown 竞态。所有故障不得阻塞 control、health、chat terminal、设置关闭或退出。

## 4. 回退

停止新事件接线并正常退出，确认 writer/Core/WebView timer/进程树归零；按 WP-4L-01 产品提交逆序 revert，
恢复既有 stderr 排水和各调用方无日志状态。回退不删除、截断、改写或迁移
`sakura-runtime.log*`、`memory-initialization.jsonl` 或任何用户数据；混合旧/v2 JSONL 保持原样。
