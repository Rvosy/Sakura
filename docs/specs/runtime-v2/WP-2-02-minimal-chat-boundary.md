---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-05
---

# WP-2-02：最小聊天取消、Gateway 与 Snapshot 边界

## 适用范围

本规范定义聊天 Gateway、取消、Snapshot 和 generation 的公共边界。早期工作包于 2026-07-26
通过阻塞 fixture 验证并发行为；真实聊天后来由 WP-3-02 接入。历史文件白名单和提交安排已废止。

测试使用隔离临时根和确定性 fixture/local Provider；不依赖真实用户凭据，不污染用户数据。
Router、Gateway 与领域实现共享既有 Core 生命周期和单 stdout writer，不另建生命周期根。

## 冻结边界与故障矩阵

Rust Gateway 只允许 `chat.send` 和 `chat.cancel`；未知 command、错误窗口、非法/超限 payload 和任何调用方提交的 generation、credential、request ID、deadline、priority 或协议字段均拒绝。Rust 生成 request/operation identity、generation credential、受控 deadline 和最小调度类别。聊天只产生 `chat.started` 后的一个 `chat.completed`、`chat.failed` 或 `chat.cancelled` 终态；重复取消、完成/取消或失败/取消竞态、晚到事件均幂等。

当前产品在不增加 command 类型的前提下，把 `chat.send` 输入冻结为严格联合：

- 用户分支仍为 `{ message, attachmentId? }`，WebView 不提交 operation/generation/credential 或模型字段；
- 更新主动分支只为 `{ event: { type: "update_available", payload } }`，payload 精确包含
  `currentVersion`、`version`、`notes`、`pubDate`、`mode`；它只能由无版本参数的受限 Tauri command 根据
  Rust 缓存候选构造，WebView 不提交提示词、release notes 或版本事实；
- 两个分支不得混合，也不得增加 prompt、history、model、priority 或任意扩展字段。Rust Gateway 与 Python
  RealChatBoundary 分别做一次 exact-shape 校验。

`update_available` 进入 `AgentRuntime.handle_event()` 的独立更新提示词。release notes 是有界不可信运行时事实，
必须置于事实非指令信封内，不能覆盖人格、系统提示或回复协议。回复必须明确新版本、引导“设置 → 关于”，只可
概括已提供事实，不得声称已下载、安装或重启。该分支不写伪造 human Timeline；成功 assistant 以
`origin=proactive` 保存，并继续使用既有 segment、角色表现和 TTS。

Rust 仅在内部把候选版本绑定到 operation，公开 `chat.started/completed/failed/cancelled` 不携带版本私有字段。
只有对应 `chat.completed` 可确认主动播报成功；终态先于 send response、取消、失败和 generation 失效仍沿用
本规范的唯一终态与幂等规则。

Python 只提供可取消的 sleep/阻塞文件 I/O fixture，并构造五字段 Snapshot：`generationId`、`revision`、`readiness`、`currentCharacterSummary`、`activeInteractionSummary`。Rust 只读缓存；generation/revision 失配触发完整重取，Rust 不推导业务对象或 patch。

必须执行的窄故障矩阵：

- send/cancel/complete、send/cancel/fail、完成/取消与失败/取消竞态、重复取消；
- 半帧、EOF、未知 identity、旧 generation/credential、晚到 response/event、队列满、慢/失败 writer；
- fixture 阻塞期间 health、cancel、shutdown 的既有 deadline；窗口关闭、Core crash、Retry、Exit 和 generation 切换后的 bounded cleanup；
- Snapshot exact shape、敏感字段拒绝、revision 单调性、generation 清空和失配完整重取；
- protocol 2.1 lifecycle、protocol 2.2 request/response/event Router 回归。

## 非目标

本边界不定义 `chat.progress`、`chat.delta`、token streaming 或通用 Operation/priority/Snapshot component model/resource token。真实 Assistant、Provider 和聊天 UI 的行为见对应聊天 Spec。

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
