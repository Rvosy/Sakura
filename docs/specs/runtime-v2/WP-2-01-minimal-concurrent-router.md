---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-05
---

# WP-2-01：最小并发 request/response/event Router

## 1. 职责

Router 在一个 Core generation 内分派 control、设置和聊天请求，保持 health、cancel 和 shutdown
可响应。真实聊天的唯一终态、取消及数据提交由聊天边界负责，见 WP-2-02 和 WP-3-02。

Python 的 response 和 event 共用 `ResponseWriter` 的有界队列与写入确认。Router 不再用第二个事件队列、
转发线程或 `FixtureResult` 包装响应；领域边界通过 `publish_event` 发布事件，通过返回值提交响应。

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

- 一个常驻 reader 读取和校验帧，一个 dispatcher 分派 control 和请求；只有 `ResponseWriter` 的线程调用 `write_frame`。
- 同步设置请求使用 4 个执行槽与有界队列；聊天由边界启动可取消任务，不让长模型请求占住 control。
- response 和 event 都直接进入 `ResponseWriter` 的 32 项队列。发布者等待本条写入完成；队列饱和、写入失败或确认超时会使 generation 失败，不丢弃终态或重放写入。
- Router 先停止接收请求并取消领域任务，再等待执行槽和聊天事件生产者收尾。收尾期间仍允许发布终态；排空后拒绝新事件，随后由 Host 关闭唯一的协议 writer。
- Python `queue.Queue` 已提供并发入队和背压，不再额外维护事件 ticket、转发队列或专用事件线程。
- 领域 cleanup 与 transport failure 进入既有确定性清理链；Shell 保留最终的进程树停止权。

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

`tests/unit/test_core_host_protocol.py` 覆盖事件写入确认、失败传播和关闭时终态排空；
真实 Core 的聊天、取消、EOF 和 shutdown 链路由 `core-host` profile 验证。

- frontend lifecycle 测试保持全绿，WP-1D 的 retry/exit/diagnostics 所有权不变。
- Core Host protocol/readiness/Assistant lifecycle 定向 Python 测试全绿。
- Rust `core_host_protocol`、`core_host_runtime`、`shell_lifecycle` 及完整 locked test 全绿。
- Windows 窗口交互脚本只删除过时的“不得启动 Python”断言；仍登记受控 Core/Python 后代，并在退出后证明全部后代归零。

## 4. 维护与验证

协议、Rust Router 和 Python 调度修改依据受影响的行为选择回归，重点覆盖乱序、失效、饱和和清理。
跨边界修改使用真实 bundled Host 验证阻塞 I/O、Core crash、Retry/Exit 与连续 generation；
无需按早期工作包的 test-only、实现、稳定化、接受顺序拆分提交。

## 5. 失败边界

- control 不能依赖第二 Core、第二 stdout writer、隐藏 Qt、无限队列或延长既有 lifecycle deadline 才能响应。
- 同一 generation 的 response/event 必须唯一归属；关闭不能遗留后台线程或任务。
- 协议演进不能静默重定义 2.1、暴露 credential，或把平台 handle/fd 细节放进公共 envelope。

真实聊天、Gateway 和取消的领域契约见 WP-2-02 及后续聊天 Spec；涉及这些模块不构成停止调查或修复的理由。

## 6. 回退

先通过现有 AppShutdown 使当前 generation 失效，确认 Shell/Core/后代、reader/writer/dispatcher、pending waiter、pipe/fd/handle 和 temp 归零；再按相反顺序回退 Python Router、Rust Router、protocol 2.2/event 和测试增量。回退后恢复 WP-1D-01 的串行 lifecycle transport，不删除、恢复、截断或改写用户数据。
