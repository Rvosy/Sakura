---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-07-31
---

# WP-2-02：最小聊天取消、Gateway 与 Snapshot 边界

## 激活记录（2026-07-26）

```text
状态：active
开始日期：2026-07-26
允许目录：
  desktop/src-tauri/src/core_host_protocol.rs
  desktop/src-tauri/src/core_host_router.rs
  desktop/src-tauri/src/core_host_runtime.rs
  desktop/src-tauri/src/core_host_gateway.rs（新增）
  desktop/src-tauri/src/main.rs（仅模块声明/受控 command 注册）
  desktop/src-tauri/src/shell_lifecycle.rs（仅 generation owner 接线）
  app/core_host/protocol.py
  app/core_host/router.py
  app/core_host/server.py
  app/core_host/chat_fixture.py（新增）
  tests/unit/、tests/integration/、tests/fixtures/runtime_v2/wp_2_02/
  docs/specs/runtime-v2/WP-2-02-minimal-chat-boundary.md
  docs/plans/runtime-v2/work-packages.md
  docs/adr/0002-runtime-v2-ipc.md
明确禁止目录：desktop/frontend/；app/core_host/assistant_adapter.py；app/agent/；Assistant、Provider、真实 Chat Pipeline；Memory、Tools、MCP、插件、TTS、截图、音频、资源 token；main.py、legacy_qt_main.py；data/、characters/、runtime/；third_party/、tools/mcp/；manifest、lockfile、workflow；WP-3-02 及后续生产实现；第二 Core、第二 stdout writer、第二生命周期根
验收环境：当前 Windows 开发机；D:\Project\sakura\runtime\python.exe；现有 Rust/Cargo；窄 Fake Core/阻塞 sleep 与临时文件 I/O fixture；不得依赖真实 Sakura Assistant 或写入真实 data/、characters/、runtime/
关联 ADR：ADR-0002（protocol 2.1 lifecycle 兼容、2.2 event envelope、Gateway、取消、Snapshot、generation 与有界 control）
计划提交：docs(runtime): 激活 WP-2-02 最小聊天边界
```

## 冻结边界与故障矩阵

Rust Gateway 只允许 `chat.send` 和 `chat.cancel`；未知 command、错误窗口、非法/超限 payload 和任何调用方提交的 generation、credential、request ID、deadline、priority 或协议字段均拒绝。Rust 生成 request/operation identity、generation credential、受控 deadline 和最小调度类别。聊天只产生 `chat.started` 后的一个 `chat.completed`、`chat.failed` 或 `chat.cancelled` 终态；重复取消、完成/取消或失败/取消竞态、晚到事件均幂等。

Python 只提供可取消的 sleep/阻塞文件 I/O fixture，并构造五字段 Snapshot：`generationId`、`revision`、`readiness`、`currentCharacterSummary`、`activeInteractionSummary`。Rust 只读缓存；generation/revision 失配触发完整重取，Rust 不推导业务对象或 patch。

必须执行的窄故障矩阵：

- send/cancel/complete、send/cancel/fail、完成/取消与失败/取消竞态、重复取消；
- 半帧、EOF、未知 identity、旧 generation/credential、晚到 response/event、队列满、慢/失败 writer；
- fixture 阻塞期间 health、cancel、shutdown 的既有 deadline；窗口关闭、Core crash、Retry、Exit 和 generation 切换后的 bounded cleanup；
- Snapshot exact shape、敏感字段拒绝、revision 单调性、generation 清空和失配完整重取；
- protocol 2.1 lifecycle、protocol 2.2 request/response/event Router 回归。

## 非目标

不实现 `chat.progress`、`chat.delta`、token streaming、通用 Operation/priority/Snapshot component model/resource token，也不接入真实 Assistant、Provider、聊天 UI 或 WP-3-02。

## 回退命令

在停止当前 generation 并确认 reader/writer/dispatcher/fixture/pending/cancel registry、进程树和临时目录归零后，按逆序回退本 WP 提交：先回退 Python fixture/Router，再回退 Rust Gateway/Router/protocol 和 lifecycle 接线，最后恢复 WP-2-01 accepted 状态与文档。不得清理、恢复、截断或改写用户数据。

## 稳定化记录（2026-07-26）

```text
状态：stabilizing
自动测试：Core Host Python 187 passed；locked Rust 177 passed/23 ignored；cargo fmt --check、git diff --check 通过
故障测试：真实 Rust↔Python 窄 fixture 已验证 started/cancelled/response 顺序、cancel 小于 1 秒、health/shutdown 不排在 10 秒 sleep 后、重复取消唯一终态、旧 handle/generation 失效、关键 event 预留容量、五字段 active/settled Snapshot 与 revision 单调
真实应用验收：不适用；本 WP 仅允许窄 Fake Core/fixture
已知问题：build、frontend lifecycle、Windows acceptance 脚本语法与适用 lifecycle 候选验收尚待执行；同一 SHA 三平台 workflow 证据未授权且不执行 push
回退步骤：见上文；保持 WP-2-01 accepted，不启动 WP-3-02
关联提交：157dcc11（feat(runtime): 建立受控聊天 Gateway 与取消边界）
```

## 验收记录（2026-07-26）

```text
状态：accepted
自动测试：Core Host/Python 定向 190 passed；locked Rust 177 passed/23 ignored；frontend 22 passed；cargo build、cargo fmt --check、git diff --check 和 workflow 契约 4 passed
故障测试：send/cancel 唯一终态、terminal-before-response、旧 handle/generation、队列压力、五字段 Snapshot、health/shutdown 抢占继续全绿；旧 generation/credential 在 Rust Router 写前 fail-closed 后，健康 Core 正常 shutdown 且完整资源归零
真实应用验收：Windows Shell + bundled Python Core 的 normal、crash、reacquire、共享锁冲突、readiness 2.1 兼容矩阵、2.2 五字段 Snapshot 与 native fault matrix 在 115.9 秒内通过；characters/data/runtime 前后内容摘要一致
CI 纠正：原候选 6c36a1a 的 PR platform run 30190007246 在三平台均暴露非 canonical 临时根与 Unix 路径分隔夹具问题；96787830 修复跨平台夹具及 Router/Snapshot 迁移后的过时验收断言，17d296a6 将功能分支平台门禁收敛为单一 pull_request 运行
已知问题：96787830/17d296a6 尚待推送后的同 HEAD Windows/macOS/Linux PR workflow 复验；它们不改变聊天产品边界。仅可复现且可归因的 P0/P1 或退出条件回归重新打开 WP-2-02
范围：未接入真实 Assistant 聊天、Provider 网络、历史、UI、streaming、通用 Operation、三级 priority、resource token 或第二生命周期根
回退步骤：先停止当前 generation 并确认 reader/writer/dispatcher/fixture/pending/cancel registry、完整进程树和临时目录归零；按 96787830、157dcc11、f8c9cd22 逆序回退 WP-2-02，CI 去重 17d296a6 可独立回退；不触碰用户数据
关联提交：f8c9cd22、157dcc11、6c36a1a、96787830、17d296a6
```
