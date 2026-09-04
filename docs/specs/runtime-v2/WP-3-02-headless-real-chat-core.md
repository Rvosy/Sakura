---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-05
---

# WP-3-02：无 UI 的真实聊天 Core 垂直链

> 当前状态唯一真相源见
> `docs/plans/runtime-v2/work-packages.md`。本 WP 已于 2026-07-26
> 完成正式验收；以下 active/stabilizing 内容保留为历史实施记录。

## 激活记录（2026-07-26）

```text
状态：active（当前唯一 active/stabilizing Work Package）
开始日期：2026-07-26
前置条件：WP-3-01 accepted；WP-2-02 accepted
目标：让 WP-3-01 AssistantSession 成为 WP-2-02 Gateway/Router/取消/Snapshot 的首个真实消费者，并以无 UI Rust acceptance harness 验证完整聊天链
验收环境：当前 Windows 开发机、三平台 Runtime v2 platform CI；自动测试仅使用确定性 fake/local Provider 和隔离临时 app root，不访问公网或真实用户 Provider
关联 ADR：ADR-0002 已冻结的 protocol 2.2 request/response/event、generation credential、聊天 Gateway、唯一终态、取消与五字段 Snapshot
计划提交：先测试/fixture，再真实 Python chat boundary 与 history 接线，再 Rust/真实 Core acceptance，最后独立稳定化与验收文档；每个提交均可单独回退
```

## 稳定化候选记录（2026-07-26）

```text
状态：stabilizing 候选；正式 accepted 仍以本候选提交的 Windows/macOS/Linux 同 SHA CI 全绿为准
自动测试：完整 Python 1717 passed、15 skipped；前端 22 passed；locked Rust 179 passed、23 ignored；Rust debug build、fmt、diff-check 全绿
故障测试：Provider 400/401/429/500、连接失败、坏 JSON/坏结构、兼容回退、queued/running/retry-sleep/HTTP-read cancel、shutdown/EOF、history rotate 全覆盖
真实应用验收：Windows debug Shell + bundled Python Core lifecycle harness 通过；受保护 characters/data/runtime 内容摘要前后不变
已知问题：本机 npm 启动器缺少 npm-cli.js，已执行 package.json 中完全等价的 node --test；Windows 无 symlink privilege 项明确 skip，三平台 CI 仍实际执行平台门禁
回退步骤：先停止并确认 operation、Router、Provider、Core/后代和 IPC 资源归零，再按 7c691962、116e64f7、452343e9 逆序 revert；history 不删除、不截断
关联提交：452343e9、116e64f7、7402b9d7、7c691962
```

## 最终 accepted 记录（2026-07-26）

```text
状态：accepted
实现候选：b835ef2ca66a33f98eb0b4339c1ccb51abcd5e91
自动测试：完整 Python 1718 passed、15 skipped；CI 同款 Core Host/故障门禁 220 passed；前端 22 passed；locked Rust 179 passed、23 ignored；py_compile、Rust fmt、diff-check 全绿
故障测试：Provider 400/401/429/500、连接/timeout/坏 JSON/坏结构、兼容回退、queued/running/retry/HTTP-read cancel、shutdown/EOF、history rotate/降级、Router terminal 排空、打包启动图与执行期 Qt/plugin 泄漏门禁全绿
真实应用验收：纯净 Windows embedded Python 返回 chat.completed/historyStatus=saved；隔离打包 Runtime lifecycle/fault matrix 通过且资源树摘要不变；Windows/macOS/Linux 原生 Shell + bundled Core、进程树/pipe/lock/RuntimeLocator 门禁全部通过
CI：Runtime v2 platform foundation run 30200669759 在同一 SHA 上成功，Windows x64、macOS arm64、Linux x64 三个 job 全绿；Test run 30200669763 同 SHA 成功
缺陷关闭：run 30194387837 捕获真实聊天执行期 app.plugins→PySide6 泄漏；55913158 修复后 run 30200020224 又捕获打包 Core hello 前 app.core 启动图泄漏；b835ef2c 延迟业务图后两类缺陷均由最终三平台运行关闭
P0/P1：零；没有剩余可复现退出条件缺陷、数据污染或范围扩张
已知限制：本机 npm 启动器缺少 npm-cli.js，使用 package.json 完全等价的 node --test；Windows 无 symlink privilege 场景明确 skip，正式三平台 CI 已在各原生平台执行对应门禁；UI、TTS、Tools、Memory、MCP、插件与 streaming 仍为后续 WP 非目标
回退步骤：先停止并确认 generation、chat operation、Provider、Router、writer、Core/后代、pipe/fd/handle/thread/temp 和共享锁资源归零；按 b835ef2c、55913158、7c691962、116e64f7、452343e9 逆序 revert；history 只允许旧版本忽略，不删除、截断、恢复或改写
关联提交：452343e9、116e64f7、7402b9d7、7c691962、793d3ea9、c1387d44、55913158、b835ef2c
```

- 生产 `run_host` 已从隐式 WP-2-02 fixture 切换为 `RealChatBoundary`；fixture 仅能由测试显式注入。
- readiness 发布的单一 `AssistantSession`、角色级 recent history、严格 Provider/结构错误、exact reply
  projector、operation cancel/close 和唯一终态仲裁已经接通。
- Windows 本地确定性 Provider subprocess 与 Rust Gateway → 真实 Python Core → local Provider → history
  落盘纵向验收均已通过；Gateway 在写前拒绝 fixture/transport/history/private 字段，并校验
  `segments`/`historyStatus` exact terminal shape。
- HTTP response close-lock 取消阻塞和 Router shutdown/EOF 终态排空竞态已修复；故障矩阵与本地资源
  回收门禁全绿。候选 `b835ef2c` 已通过同一 SHA 的正式三平台 platform workflow，WP-3-02
  已迁移为 `accepted`。

本 WP 不创建第二套 Assistant application、worker pool、stdout writer 或生命周期根。旧迁移提交
`190dfafd24f5c5226bff8b4347837b6e45d9a331` 仅允许逐文件取证；禁止 cherry-pick、整包复制或恢复
`brain_host` 架构。当前实现的唯一数据流如下：

```text
Rust chat Gateway
  -> Python ConcurrentHostRouter（既有有界 worker / 单 writer）
  -> RealChatBoundary（generation、operation、取消、唯一终态）
  -> AssistantSession.pipeline.run_user_message(..., cancel_checker=...)
  -> AgentRuntime -> OpenAICompatibleClient
  -> chat.completed | chat.failed | chat.cancelled
  -> 角色级 ChatHistoryStore（允许失败、不可反向决定聊天终态）
```

## 冻结消费者与所有权

- `AssistantAdapter` 继续是 `AssistantSession` 的唯一构造和关闭所有者；初始化成功前
  `chat.send` fail closed，setup-required/degraded 的既有 readiness 语义不被聊天请求改写。
- 新增一个窄 `RealChatBoundary`（候选路径 `app/core_host/real_chat.py`）承担 operation registry、
  cancellation token、pipeline 调用、事件投影和 history best-effort 写入。它复用 WP-2-02 已冻结的
  并发上限、reserve/abandon、cancel-all、close deadline、revision 和唯一终态规则；不再复制 fixture
  的 sleep/file 业务。
- `ControlDispatcher`/`ReadinessController` 只暴露当前已发布的 session 给真实边界，不持有 Provider、
  Runtime 或 History。Router 仍不 import Assistant 领域对象，不新增线程池和 writer。
- 一代 Core 同时最多执行 WP-2-02 已允许的有界聊天数；本 WP 的产品入口先固定为单个 active
  interaction。operation identity 仍由 Rust 分配，请求 payload 只允许 `message`；图片、附件、
  system/tool role、调用方 transport 字段和任意历史数组均拒绝。
- `chat.started` 成功发布后必须恰有一个终态。`OperationCancelled` 映射为 `chat.cancelled`；Provider/
  解析/领域异常映射为脱敏 `chat.failed`；成功 `AgentResult.reply` 映射为 `chat.completed`。响应仍只表示
  accepted，不替代 terminal event。Core 必须在 `chat.started` 发布成功后立即返回 accepted，并在
  generation 所有的有界后台执行中等待 Provider 与最终终态；不得把图片上下文或慢 Provider 的完整耗时
  绑定到 `chat.send` request deadline。
- `chat.completed.reply` 固定投影为 `segments` 数组；每段仅含 `text`、`translation`、`tone`、
  `portrait`、`suppressTts`。禁止序列化 `_debug`、actions、tool continuation、prompt、endpoint、model、
  API key、generation credential 或 Python 对象。WP-3-02 的空 `ToolRegistry` 下出现 action 视为边界错误。
- `chat.failed.error` 使用稳定 code、message、retryable、details 空对象。网络不可达、timeout、HTTP、
  Provider 响应格式错误均只终止本 operation；Core readiness 仍为 ready/degraded，health/control 可用。
  用户可修复的 Provider 网络类错误为 retryable，配置/协议/内部投影错误不自动重试。
- 远程 Provider 在每次 HTTP 尝试前读取当前系统代理；同一 Core 运行期间开关系统代理不要求重启。
  标准代理环境变量仍遵循进程环境语义，本地回环地址始终直连。

## 历史写入契约

- 复用 `StoragePaths(app_root).chat_history_for(character.id)` 与 `ChatHistoryStore`，不发明新 schema、
  DB 或迁移。History 是本 WP 明确授权的产品写入，不设置 `data/` 全目录只读门禁。
- 为保持 legacy 聊天语义，接受请求后先 best-effort 写入一条 user 记录；成功时按非空 reply segment
  顺序 best-effort 写入 assistant 记录。失败或取消不伪造 assistant 记录。
- History 读写失败不能把已生成回复改成 `chat.failed`，也不能改变 Core readiness。终态 payload 以
  最小 `historyStatus: saved|degraded` 状态表达“本轮可能未完整保存”；诊断仅记录脱敏错误
  分类，不记录消息正文、路径、Provider 配置或凭据。
- 真实边界构造发送给 Pipeline 的消息来自角色级 history 的有界 recent window 加当前消息；读取失败
  时退化为仅当前 user 消息。禁止接受 WebView/调用方提交的 history，以免绕过角色和 generation
  所有权。精确窗口上限在首个测试提交中冻结，并与 legacy `trim_messages_for_model` 行为对拍。
- 正常产品写入遵守既有目录所有权、JSONL、rotate/repair 和共享锁契约。破坏性 history fault、截断、
  权限和满盘模拟必须使用隔离临时根；不得清理、恢复、截断或改写仓库真实用户数据。

## 取消、关闭与故障顺序

取消 checker 必须贯穿 `ChatPipeline`、`AgentRuntime`、Provider retry/sleep/HTTP 读取边界。取消胜出后，
晚到 Provider 结果、history 回调或 terminal publish 均不得产生第二终态。`chat.cancel` 重复调用返回
`accepted=false`；未知/旧 operation 不影响当前请求。generation invalidate、Core shutdown、EOF、
窗口关闭和 Retry 先 signal 全部 operation，再按 WP-2-01/02 已接受的有界顺序 join/close；不合作
Provider/线程的最终收束仍由 Rust `ManagedProcessTree` 与共享 shutdown deadline 负责。

必须按以下优先顺序分类终态：generation invalidated/显式取消 -> `chat.cancelled`；确定的 Provider 或
领域错误 -> `chat.failed`；仅在结果投影完成且 terminal arbitration 胜出后 -> `chat.completed`。
History 失败只降级 `historyStatus`，不参与前三者仲裁。

## 启动与调用边界

`real_chat` 的 operation/Gateway 控制面必须能在仅含 `app/core_host` 的打包 Core 中导入和构造。
Pipeline、Provider、history、runtime log 与取消异常等业务依赖在首个已接受的真实聊天执行时加载，
不在 `system.hello` 前扩大启动图。业务图不得意外导入 Qt 或启动未启用的插件。

Core stdout 只承载协议；运行日志绑定正确的应用根，不能由旧 console/file sink 写入协议流或错误的数据目录。
取消应贯穿活动 HTTP 请求，关闭响应不能阻塞 control 面。这些契约允许在实际拥有根因的模块中修正。

## 确定性验收矩阵

| 门类 | 必测情形 | 核心断言 |
|---|---|---|
| 正常回复 | 单段、多段、空白段、日文/译文/tone/portrait | exact reply projector；started 后唯一 completed；无 `_debug`/secret/action 泄漏；history 顺序正确 |
| Provider | DNS/连接失败、timeout、HTTP 4xx/5xx、坏 JSON、空/坏结构化回复、兼容参数回退、accepted 后慢返回 | accepted 不等待 Provider；稳定脱敏 code/retryable；每次仅一个 failed；readiness/health 不降级为启动失败；无自动公网访问 |
| 取消竞态 | queued/running/retry sleep/HTTP read/解析前后取消、完成/失败同时取消、重复取消 | checker 贯穿；唯一 cancelled 或已胜出的单一终态；晚结果丢弃；cancel/health 不被聊天阻塞 |
| History | 无文件、既有多轮、坏尾修复、read fail、user append fail、assistant append fail、rotate | 有界 recent window；失败仍可聊天；`historyStatus=degraded`；真实数据不被故障注入污染 |
| generation/安全 | 未 ready、旧 credential/generation、重复 identity、超限消息、调用方 transport/history 字段 | Rust 写前和 Python 边界双重 fail closed；旧事件不进入当前代；Snapshot 保持已冻结的五字段 exact shape，history 状态仅存在于本轮终态 |
| 生命周期 | shutdown/EOF/Core crash/Retry/外部 kill，Provider 协作与不合作 | control 有响应；operation/worker/pending/pipe/fd/handle/进程树/temp 在既有 deadline 内归零；锁可立即重获 |
| 兼容回归 | legacy Qt pipeline/history、protocol 2.1 readiness、2.2 fixture、三平台真实 Core | legacy 行为不变；WP-2-01/02/3-01 门禁全绿；Windows/macOS/Linux 使用同一确定性 local Provider harness |

按受影响契约选择 Python、Core Host subprocess、Rust 或 bundled Core lifecycle 测试；涉及跨边界行为时
使用本地 Provider 纵向验证。CI 承担完整平台矩阵，不要求每次修改重复全部测试。人工开发配置只能作为补充，
不能取代确定性 local Provider；凭据和真实消息不得进入 fixture、日志、CI artifact 或验收文档。

## 提交与回退

提交按可理解、可验证的改动组织，不要求固定数量或先后顺序。以下回退路径记录早期迁移方案；
当前回退须按现有依赖评估，不重新启用已退役的产品入口。

回退前先停止当前 generation，确认 chat operations、Router 队列、writer、Provider 请求、Core 根及后代、
pipe/fd/handle/thread/temp 均归零。随后按逆序 revert WP-3-02 实现/测试提交，并禁用真实 chat boundary，
恢复 WP-2-02 fixture-only Gateway 与 WP-3-01 readiness。History 是 append-only 产品数据：回退代码不得
删除、截断、恢复或重写任何用户 history；最多让旧版本忽略新终态中的 `historyStatus`。

## 退出条件与非目标

正常回复、Provider 或格式故障、取消、history 降级和 shutdown/restart 都需保持唯一终态，
control 不饥饿，关闭后资源归零。UI、TTS、工具、插件和设置的行为由各自 Spec 定义。
自动验证与人工验收分别提供证据；工作包状态不限制相关缺陷的修复范围。
