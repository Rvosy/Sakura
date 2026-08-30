---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-07-31
---

# WP-2-01：最小并发 request/response/event Router

## 1. 状态与目标

当前状态只以 `docs/plans/runtime-v2/work-packages.md` 第 2 节为准。

本 Work Package 把 WP-1C 已验证的单请求串行 stdio transport 收敛为一个 generation-scoped、资源有界、可并发的最小 Router，为 WP-2-02 和第一条真实聊天提供基础。它只验证“聊天形状”的阻塞 fixture，不接入真实 Assistant 聊天，也不建设通用任务平台。

当前实现基线：

- Rust `CoreHostRuntime::request*` 仍由调用线程执行 write → read → validate，同一 owner 不能安全承载多个 in-flight request。
- Python `run_host` 由 reader 同步调用 `ControlDispatcher.dispatch`；已有 `ResponseWriter` 是单 writer 和有界队列，但 `send` 会等待本条写完成，尚不是独立 Router。
- protocol 2.1 的 validator 只接受 `request`/`response`，因此 event 不能在不协商的情况下塞入现有 minor。
- `system.hello`、readiness、generation credential、Snapshot、stderr 排水、Supervisor 和受控进程树均已验证，必须保留而不是重写。

## 2. 冻结边界

### 2.1 Wire 兼容

- `protocolMajor` 保持 2。
- event 支持使用新的 protocol minor 2.2，并通过 `transport.concurrent-router` capability 协商；不得静默改变 2.1 validator 的含义。
- 2.0/2.1 peer 仍可完成已有 hello/health/initialize/snapshot/shutdown lifecycle；只有调用 Router/event 能力时才要求 2.2 capability。
- event 最小字段为 `protocolMajor`、`protocolMinor`、`kind=event`、`generationId`、`generationCredential`、`id`、`name`、`payload`。`id` 关联产生事件的 request；本 WP 不另造通用 operationId。
- event 不携带 request 的 `deadlineMs`/`priority`，也不携带 response 的 `ok`/`error`。
- 本 WP 不增加 `sequence`。stdio 单 writer 已提供字节顺序；只有后续真实消费者证明需要检测应用层丢帧/重复时才能加入。

### 2.2 Rust Router

- Core generation 只有一个 stdin writer owner、一个 stdout reader owner 和一个 pending registry。
- request 注册成功后才可入 writer queue；注册/入队失败必须同步撤销，不能留下孤儿 waiter。
- pending identity 至少绑定当前 generation、request id 和预期 name；重复 id、错 name、错 generation 或 credential fail closed。
- stdout reader 持续分类 response/event。response 只完成对应 waiter；event 进入有界订阅/验收通道，不得误完成 request。
- generation 失效、Core crash、stdout EOF、fatal transport、Retry、Exit 和窗口关闭都必须一次性完成或拒绝所有 pending waiter，并停止/join reader、writer。
- 不在 pending/owner mutex 内做 pipe read/write、线程 join、进程等待或 UI 回调。
- 保留现有 Supervisor、shutdown 5 秒总 deadline、generation 清理顺序和 stderr drainer；Router 不能成为第二生命周期根。

### 2.3 Python Router

- 一个常驻 reader 只负责读取/校验帧和投递；一个 dispatcher 负责 control 与任务分派；只有一个 stdout writer 可以调用 `write_frame`。
- `system.hello`、`system.health`、`system.shutdown` 不等待阻塞 fixture。`system.shutdown` 必须能停止接收新任务并进入现有有界清理链。
- 非 control fixture 使用有界执行槽；不得为未来 Tools/MCP/插件建立通用 worker process 或三级调度器。
- 现有 `ResponseWriter` 可以演进或被窄 Router writer 替代，但不能形成两个 stdout owner。
- writer/dispatcher/fixture task 的异常必须聚合到既有确定性 cleanup；关闭顺序必须避免满队列时 sentinel 无法入队、join 自锁或 transport reader 永久阻塞。
- 生产 Host 不新增 `chat.send`、`chat.cancel` 或真实 Assistant 调用。阻塞 sleep/I/O 和 terminal-shaped event 由注入式测试 handler 或 `wp_2_01` 专用 fixture 提供。

### 2.4 有界与过载

- pending request 数、Rust writer/event 队列、Python dispatch/writer 队列和 fixture 并发槽均使用命名常量。
- 达到上限时返回稳定、脱敏、可归因的过载错误，或安全关闭当前 generation；不得无限增长，也不得静默丢 response/terminal-shaped event。
- 可以丢弃的 progress 类事件不在本 WP 实现，因此不要为“以后可能需要”建设合并、采样或多等级配额。
- 任何 queue-full/close/write failure 路径都必须有有界退出测试。

## 3. 必须证明的场景

### 3.1 单元与 golden

- Rust/Python 对 protocol 2.2 event 的合法/非法 envelope 产生一致结论。
- 2.1 lifecycle 兼容保留；2.1 peer 不宣告 Router capability 时不能接收 event。
- 两个以上 in-flight request 以反向顺序返回，waiter 不串线。
- response 与 event 任意交错；重复 response、未知 id、错 name、旧 generation 和旧 credential 均安全失败。
- pending/queue 上限、慢 writer、writer failure、半帧、EOF、stdout pollution 和关闭竞态有确定结果。

### 3.2 真实 Host 与 lifecycle

- 在聊天形状的 sleep fixture 和阻塞文件 I/O fixture 运行期间，health 能在既有 deadline 内返回，shutdown 能进入既有 5 秒完整树停止门。
- Router 正常关闭、Core crash、Retry 和窗口 Exit 后，Rust reader/writer、Python reader/dispatcher/writer/task、pending waiter、pipe/fd/handle、进程树和验收 temp 全部归零。
- 第一代晚到 response/event 不能影响第二 generation；旧 waiter 得到稳定 generation-invalidated 结果。
- protected credential、API key、Prompt、Provider endpoint、异常 repr 和私有绝对路径不进入 response/event、普通日志或测试证据。

### 3.3 继承回归

- frontend lifecycle 测试保持全绿，WP-1D 的 retry/exit/diagnostics 所有权不变。
- Core Host protocol/readiness/Assistant lifecycle 定向 Python 测试全绿。
- Rust `core_host_protocol`、`core_host_runtime`、`shell_lifecycle` 及完整 locked test 全绿。
- Windows 窗口交互脚本只删除过时的“不得启动 Python”断言；仍登记受控 Core/Python 后代，并在退出后证明全部后代归零。

## 4. 实施顺序

1. 先以 test-only commit 修正继承的 Windows no-Python 断言，不混入 Router 生产代码。
2. 先写 protocol 2.2/event/capability 的 Rust/Python RED，再实现共同 wire contract 和 `wp_2_01` golden。
3. 先写 Rust 乱序、交错、失效、饱和和清理 RED，再实现单 writer/reader/pending Router。
4. 先写 Python control 隔离、单 writer、阻塞 fixture、饱和和关闭 RED，再实现 reader/dispatcher/writer 分离。
5. 使用真实 bundled Host 跑并发、阻塞 I/O、Core crash、Retry/Exit 和连续 generation 门禁；复用输入未变化的 WP-1C/1D 成功证据。
6. 生产实现完成后把 WP-2-01 登记为 `stabilizing`；候选验收关闭退出条件后登记 `accepted`，然后停止，不开始 WP-2-02。

建议单一目的提交：

- `test(runtime): 更新窗口交互验收的 Core 预期`
- `feat(runtime): 扩展并发 Router 协议契约`
- `feat(runtime): 建立 Rust 并发请求路由`
- `feat(runtime): 建立 Python 并发调度与单写队列`
- `test(runtime): 补齐 Router 故障与资源门禁`
- `docs(runtime): 稳定化 WP-2-01 最小并发 Router`
- `docs(runtime): 接受 WP-2-01 最小并发 Router`

## 5. 停止条件与非目标

出现以下任一情况立即停止生产扩展并回到设计/稳定化：

- 为通过 WP-2-01 必须接入真实 Assistant、聊天 UI、Gateway、cancel 或 Snapshot 扩展。
- control 只能依赖第二 Core、第二 stdout writer、隐藏 Qt、无限队列或延长既有 lifecycle deadline 才能响应。
- 同一 generation 出现不能唯一归属的 response/event，或关闭需要遗留后台线程/任务。
- 协议实现要求静默重定义 2.1、暴露 credential，或把平台 handle/fd 细节放进公共 envelope。
- 需要修改 manifest/lockfile、工作流、用户数据、Assistant Adapter、Memory、Tools、MCP、插件、TTS 或截图链。

WP-2-02 的 `chat.send`/`chat.cancel`、唯一聊天终态、受控 Gateway 和最小聊天 Snapshot 明确不属于本 WP。

## 6. 回退

先通过现有 AppShutdown 使当前 generation 失效，确认 Shell/Core/后代、reader/writer/dispatcher、pending waiter、pipe/fd/handle 和 temp 归零；再按相反顺序回退 Python Router、Rust Router、protocol 2.2/event 和测试增量。回退后恢复 WP-1D-01 的串行 lifecycle transport，不删除、恢复、截断或改写用户数据。
