---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-05
---

# ADR-0002：Runtime v2 IPC

> 状态：Technically Validated for Phase 1C transport foundation（最小 Router/真实 Assistant 消费验证待完成）
> 日期：2026-07-15
> 适用范围：Tauri/Rust 与 Python Core 之间的请求、响应、事件和状态同步

## 背景

Runtime v2 第一版保留 stdin/stdout framed transport，避免本地端口、防火墙和额外服务发现问题。

#140 的主要问题不是 stdio 本身，而是同步串行调度：一个长请求会阻塞事件、取消、health 和 shutdown。v2 必须改变 Router 和任务模型，而不是单纯替换传输技术。

## 不可妥协的约束

- Python 先建立 IPC，再初始化重型 Assistant 组件。
- transport reader、control dispatcher 和 stdout writer 不执行或等待任何 Assistant 领域代码。
- 支持多个并发 in-flight request。
- health、cancel、shutdown 始终可处理。
- 第一条聊天为每次请求分配唯一 request/operation identity，并通过事件报告唯一终态；是否提取通用 Operation 必须等待第二个真实消费者。
- Core 重启后旧 generation 的请求、事件、Operation 和 Snapshot 全部失效。
- 前端不能调用任意内部 Python 方法。
- 队列有界，帧大小受限，协议异常安全失败。
- stdout 只传协议帧，日志走 stderr 或日志文件。
- Rust 必须持续消费 stderr；日志过载不能反向阻塞 Python Core。

## 当前推荐方案

### Transport

- stdin/stdout 长度前缀 JSON 帧。
- Rust 独立 reader、writer、pending request router 和进程监管任务。
- Python 常驻 reader 和 control dispatcher，只做帧处理、协议校验、控制命令和任务投递。
- Python 的 response 和 event 共用 `ResponseWriter` 的单个有界队列及写入确认；业务任务不能直接写 stdout。
  Router 不保留额外的事件转发队列或线程，关闭时先排空领域事件生产者，再关闭 writer。
- transport 抽象保留未来替换 Named Pipe/Unix Domain Socket 的可能，但当前没有迁移承诺。
- stdin/stdout/stderr 的创建、继承、关闭和强制回收由 ADR-0004/ADR-0001 的平台进程树 backend 承担；IPC 公共层不得调用 shell，也不得依赖 `.exe`、Win32 handle 或 POSIX fd 的具体表示。
- Windows、macOS、Linux 必须共享字节级 framing、Envelope、generation、deadline 和错误语义；平台不能各自扩展业务字段。

Python 内部明确拆分：

```text
Transport / Control Plane
├─ reader
├─ control dispatcher
├─ writer queue
└─ pending / chat task registry

Domain Execution Plane（按真实消费者逐步验证）
├─ bounded chat tasks
├─ 聊天确需的 bounded executor
└─ 后续消费者证明必要时的受控 worker process
```

- 同步 SDK、阻塞 I/O 和同步领域方法不得在 transport/control 事件循环上执行。
- 可能长时间占用 CPU、GIL 或运行不可信插件代码的未来操作，必须由所属真实消费者 WP 选择可终止 worker process 或证明其他隔离足够；基础聊天前不建设通用 worker 框架。
- Rust 的进程树 deadline/强杀仍是非协作领域任务的最终退出保证。

### stderr 与日志排水

- Rust 为每个 generation 持续读取 stderr，直到进程退出和管道 EOF。
- stderr 日志队列有界，日志过量时允许采样、丢弃旧记录或滚动文件，但不得阻塞 Core。
- 每条结构化日志附加 generationId 和 Core PID。
- Credential、API Key、Authorization header、完整 Prompt 和插件私密配置必须在进入 UI 或持久日志前脱敏。

### 握手

```text
Python 进程启动
-> 最小 transport ready
-> Rust 发送 system.hello
-> Python 校验协议版本、generation 和一次性 credential
-> Python 立即响应 hello
-> CoreReadiness = transport_ready
-> Rust 请求 core.initialize
-> Python 后台初始化 Adapter/Facade
-> setup_required / ready / degraded / failed
```

hello 前不得导入或初始化 Assistant、Memory、MCP、插件和 TTS 重型模块。

生命周期期限采用 ADR-0001 的当前值：`system.hello` 10,000 ms、`core.initialize` 接受响应 5,000 ms、readiness watchdog 30,000 ms、`system.shutdown` 协议优雅期 3,000 ms，完整进程树停止从 shutdown 意图起不超过 5,000 ms。hello 期限已根据 Windows 10 稳定版的冷启动报告调整；`core.initialize` 必须先快速返回接受或拒绝结果，不能把 30 秒 readiness watchdog 当成同步 request deadline。

### 协议版本与能力协商

`system.hello` 至少交换：

```text
protocolMajor
protocolMinor
desktopVersion
coreVersion
capabilities
```

- `protocolMajor` 不兼容：拒绝初始化，进入 diagnostics，不对同一个不兼容 Core 自动重复重启。
- `protocolMinor` 不同：通过 capabilities 协商可用功能。
- 缺少启动所需 capability：返回明确不兼容错误，不进入业务初始化。
- `setup_required`、确定性配置/数据错误、bundled Runtime 缺失或不兼容、generation credential 失败均按 ADR-0001 的不可自动重试分类进入 diagnostics；Provider 网络错误仍是普通领域请求错误。
- Runtime Repair/diagnostics 必须显示 Desktop、Core 和 Protocol 版本。

### 受控 Gateway

前端到 Rust 可以使用一个统一入口，但只提交业务意图：

```text
core_request({
  command: "chat.send",
  payload: {...}
})
```

Rust Gateway 必须：

- command 名称来自固定 schema/注册表。
- 校验调用窗口权限和 payload。
- 分配 request ID。
- 注入当前 generationId 和协议版本。
- 将 deadline 限制在 command 注册表允许范围内。
- 根据当前窄 allowlist 选择内部调度类别；WebView 不提供 priority。完整业务 priority 注册表等待多个真实消费者。
- 构造真正发送给 Python Core 的 Envelope。
- 每个窗口有独立权限集合。
- 前端通过封装 client 调用。
- 未知 command 默认拒绝。

WebView 可以使用 Rust 返回的 operation/request handle 请求取消，但不能自行指定或伪造 generation、request ID、priority、deadline、credential 和协议版本。

反对的是无限制 `host_call(method: string, params: any)`，不是统一 Gateway 本身。

## 推荐 Envelope

推荐字段：

```text
protocolMajor
protocolMinor
kind
generationId
id
name
payload
deadlineMs
priority
sequence（可选，等待技术验证）
```

`generationId` 使用 UUID，是隔离旧消息的权威身份。可另保留递增的 `generationNumber`，但它只用于诊断展示，不参与协议授权。

`sequence` 是否保留为强校验字段由 WP-2-01/02 与真实 Assistant 技术验证决定。stdio 和单 writer queue 已提供字节顺序；只有确实需要检测应用层丢帧、重复或多来源合并时才加入 sequence。基础聊天中的 `priority` 只允许由 Rust 表达 control 与 chat 的最小内部区分，WebView 不可提交；完整业务枚举仍是后续方向。

普通帧初始建议限制为 8 MiB，但具体值属于实现参数。截图、音频和大文件必须使用受控资源描述符。

## Priority（方向性设计，不阻塞基础聊天）

```text
control
  hello、health、shutdown、cancel

interactive
  chat、角色选择、用户触发操作

background
  资源扫描、Memory 整理、主动调度
```

Priority 是后续可能的调度语义，不要求实现三个物理管道，也不要求在第一条聊天前完整实现 control/interactive/background 平台。最小聊天链只证明 health/shutdown/cancel 不排在聊天任务之后；在 Tools、Memory、MCP 等真实消费者出现前，不冻结通用业务优先级。

## Operation（方向性设计，不阻塞基础聊天）

以下是多个真实长任务消费者出现后的候选统一结构，不是基础聊天前的完整实现门禁：

```json
{
  "operationId": "operation-uuid",
  "state": "accepted"
}
```

事件：

```text
operation.started
operation.progress
operation.completed
operation.failed
operation.cancelled
```

Operation：

- 归属于当前 generation。
- Core 重启时失效。
- 可取消时响应 `operation.cancel`。
- 不可取消时返回明确原因。
- 完成后从 registry 清理。

基础聊天只要求 Rust 分配唯一 request/operation identity、`chat.cancel` 和唯一终态。只有聊天与第二个真实消费者证明生命周期、取消、progress 和权限语义确实相同时，才允许由所属 WP 提取上述通用 Operation。

## Chat 事件

```text
chat.started
chat.completed
chat.cancelled
chat.failed
chat.progress（预留，不要求基础聊天实现）
chat.delta（预留，不要求实现）
```

Phase 3 使用完整回复 + WebView 打字机展示，不要求 Provider token streaming。

- `chat.cancel` 取消实际模型请求。
- 跳过打字机属于 WebView 本地 presentation 行为，不调用 Python。

## Snapshot

第一条聊天前冻结的最小 Snapshot 只表达：

```text
generationId
revision
readiness
currentCharacterSummary
activeInteractionSummary
```

规则：

- Snapshot 由 Python Core 构造，Rust 只读缓存。
- 新 generation 建立时立即清空旧 Snapshot。
- revision 不匹配时请求完整 Snapshot，不在 Rust 中猜测业务补丁。
- Snapshot 不包含 API Key、Credential、完整系统 Prompt、插件私密配置和任意本地文件裸路径。
- `currentCharacterSummary` 只包含渲染 UI 所需的公开字段。
- `schemaVersion`、`generationNumber`、通用 `components`、`capabilities`、`coreConfigRevision` 和未来 patch/component 类型是候选扩展，只在对应真实消费者出现时验证；它们不得阻塞基础聊天。

### 受控资源描述符（方向性设计，不阻塞基础聊天）

截图、音频、角色导入和其他大文件通过 generation-scoped opaque token 传递，而不是裸文件路径。token 至少具有：

- 资源类型。
- generationId。
- TTL。
- 大小上限。
- 允许访问的 command/窗口范围。
- 单次或有限次数读取策略。

WebView 不得通过 token 扩展为任意文件系统访问。

基础聊天不传递截图、音频或导入大文件，因此不要求先实现通用 token、所有资源类型或完整资源权限平台。首个真实资源消费者由所属 WP 验证最窄 token；多个消费者证明共性后才能冻结通用资源模型。

## Backpressure

- reader、writer 和 event queue 必须有界。
- 基础聊天的最小门禁是：response、chat terminal、health/shutdown/cancel 使用预留容量或等价有界机制，不被可丢弃消息挤出。
- 完整 progress 合并、多等级配额、跨业务公平性和通用过载策略是方向性设计，等待产生 progress 的真实消费者。
- 最小有界机制仍无法恢复时，安全关闭当前 generation 的 IPC 连接；不得无限增长队列或阻塞 transport reader。
- 帧超限、stdout 污染或协议损坏时立即关闭当前 generation 的 IPC 连接。
- request deadline 到期或窗口关闭后，Rust 清理 pending waiter。已经写入 Core、但只因本地 deadline 到期
  而失去 waiter 的 request ID 必须进入有界迟到隔离；隔离中的迟到 event/response 直接丢弃，不能把仍健康的
  generation 升级为 transport fatal。隔离中的 ID 不得复用；从未 pending、也不在隔离中的真正未知 ID 继续
  fail-closed。

## 错误模型

```text
code
message
retryable
details
```

错误不得包含：

- API Key。
- 系统 Prompt。
- 面向普通 UI 的私有文件绝对路径。
- 工具 continuation context。
- transport credential。

本机 diagnostics 可以在受控页面显示必要绝对路径，但不得将路径作为可直接读取任意文件的 WebView 能力。

## 测试

基础聊天架构验证前的强制测试：

- 分片帧和合并帧。
- 非法 JSON 和超大帧。
- 并发响应乱序。
- event 与 response 交错。
- control 请求不被长任务阻塞。
- 同步 sleep 和阻塞文件 I/O 在领域执行期间，health 和 shutdown 仍响应。
- CPU 密集循环无法协作结束时，Rust 在 deadline 后仍能终止完整进程树。
- deadline、取消和窗口关闭。
- 慢 writer、队列饱和和 chat 终态/control 不丢失。
- stdout 污染。
- stderr 持续输出和日志队列过载。
- Core 重启后的旧 generation 消息。
- protocol major/minor 不兼容和缺失 capability。
- Rust 主动关闭 stdin 后 Python 能有界退出。
- Rust/Python golden fixtures。
- Windows、macOS、Linux 使用同一 golden fixtures；对应平台的真实 Python Host 都完成 hello、health、shutdown 和损坏 transport 回收。

完整 Operation progress 合并、通用三级优先级、跨资源公平性和多类 resource token 测试由出现对应真实消费者的后续 WP 增加，不是 WP-3V-01 前置。

## 允许调整的范围

以下内容可以根据最小 Router、真实 Assistant 纵向验证及后续消费者调整：

- sequence 是否必需。
- frame 上限具体数值。
- schema 是否生成静态类型。
- credential 的具体传递方式。
- executor/async runtime 选型。
- 是否在未来替换为 Named Pipe/Unix Domain Socket。

不可调整的是并发、取消、生命周期优先级、generation 隔离、WebView 权限边界、控制面隔离和安全失败边界。

方向性内容统一遵循：设计方向已记录，不阻塞基础聊天架构验证；在出现对应真实消费者时由所属 Work Package 验证并冻结。

## ADR 状态门禁

本 ADR 在 Phase 1P 已提供三平台 transport/process backend、Phase 1C 的握手、版本、stderr 和故障 transport 门禁通过后更新为 `Technically Validated`。只有 WP-2-01/02 的最小 Router/聊天边界通过，并由 WP-3V-01 使用真实 Sakura Assistant 证明并发、health/shutdown 隔离、取消唯一终态、generation 重建、最小 Snapshot 水合、队列压力和完整资源清理后，才更新为 `Accepted`。完整通用 Operation、resource token、三级优先级、Snapshot component model 和多等级背压不属于该状态前置。单平台结果只能作为该 backend 证据；验证失败时应更新或 Supersede 本 ADR，不得用 Fake Core、静态契约或直接 Python 调用替代真实纵向故障测试。

## WP-1C-01 基础 transport 验证记录（2026-07-22）

本记录只验证 Phase 1C 的最小无 Qt Core Host 和基础握手子集，不提前满足版本/capability、generation credential、持续 stderr 排水、并发 Router、initialize、Snapshot 或业务请求门禁，因此 ADR 状态保持 `Proposed`。

- Python 与 Rust 已实现并互验 4-byte big-endian 长度前缀 UTF-8 JSON 帧、8 MiB 上限、基础 Envelope、稳定错误 DTO 和 generation identity；覆盖任意 header/payload 分片、合并帧、非法 UTF-8/JSON、零长/超大/半帧和 stdout 污染。
- Python 最小 Host 在捕获二进制 stdout 后安装文本写入 guard，只提供 `system.hello`、`system.health`、`system.shutdown`；import guard 证明 hello 前未导入 PySide6、`app.ui`、Assistant、Memory、MCP、插件或 TTS。
- Rust 使用显式 Python 路径、匿名 stdin/stdout/stderr 管道和 Windows kill-on-close Job 启动真实 Host；所有 control response 均有 deadline，超时、stdin EOF、损坏 stdout 和忽略 shutdown 均有界结束或强制回收完整 Job。本项是 Windows backend 证据，macOS/Linux 由 WP-1P-04/06 回补。
- 自动门禁结果：Python 19 passed；Rust 72 passed、13 ignored fixture、0 failed；`cargo fmt --check`、Debug/Release `cargo build --locked`、PowerShell parser、Python `py_compile` 和 `git diff --check` 通过。
- 两轮真实 Debug Tauri + `runtime/python.exe` 验收均观察到可见窗口、hello、两次 health、协议 shutdown 和根退出码 0；每轮登记 9 个进程身份，最终进程和系统临时目录残留均为 0。
- 两轮验收前后真实 `data/` 完整路径/长度/mtime/SHA-256 清单均为 121 文件、1,045,983,998 bytes，canonical SHA-256 均为 `300b89fa68dd973f6970f3435ad0c5cc15fc84a2088baf3514e20dae25d0b62b`，证明零变化。
- 当时已知限制：generation credential、持续 stderr 排水和协议协商尚未实现；这些缺口现已由下一节的 WP-1C-03 门禁关闭。`CreateProcessW` 的受控 stdio handle allowlist 仍属于平台 spawn hardening，不改变本 ADR 的公共 transport 语义。

## WP-1C-03 transport foundation 验证记录（2026-07-24）

WP-1C-03 已在同一公共 stdio framing 上冻结 protocol `2.1`、`2.0..2.1` minor/capability 协商、
每 generation 128-bit credential、64 KiB 有界 stderr 排水/脱敏和稳定 transport 故障分类。Windows
x64、macOS arm64、Linux x64 的最新实现 HEAD `af79255` 均通过原生 Core lifecycle、故障 fixtures、
完整树/pipe/fd/handle/thread/temp 清理和 shared lock 重获；Unit/UI 与 push/pull_request platform
runs 分别为 `30074854468`、`30074851836`、`30074854406`。

major/capability 失败禁止 initialize；旧 generation/stale response credential fail closed；stderr flood、
分片 UTF-8/非法字节/二进制/无换行长记录、secret 跨 read chunk、stdout 前缀/后缀污染、半帧 EOF、
deadline、Core crash、writer queue closed 和 shutdown 竞态均有可执行证据。credential 不进入 argv、
环境、日志、错误、Debug、Snapshot 或 diagnostics。平台原生 process/pipe identity 没有进入公共 DTO。

据此 ADR 更新为 `Technically Validated for Phase 1C transport foundation`。这尚不接受最小 Router、
聊天 cancel/唯一终态、乱序 response/event、终态保留和真实 Assistant 控制隔离；这些必须由
WP-2-01/02 与 WP-3V-01 完成。通用 Operation、完整 progress backpressure、资源平台和未来消费者
模型仍只是方向，不需要在本 ADR Accepted 前完整实现。

## WP-1C-04 bundled lifecycle 技术验证记录（2026-07-24）

WP-1C-04 在既有 transport foundation 上增加由 `RuntimeLocator` 唯一提供的结构化 Runtime layout，
冻结 target/architecture、bundled Python、资源根、Core entry/module、工作目录和 source identity。
Core 启动前 fail closed 验证 absolute/canonical containment；产品路径不扫描 `PATH`、不回退系统
Python、不推断 build directory，也不在公共逻辑假设 `.exe`。共享 lifecycle golden 同时约束 Rust、
Python 和真实 Shell 的三平台 packaged layout、协议 capability、既有 deadline 与最小生命周期顺序。

Windows x64 预验收已通过固定 Python 3.12.8 AMD64 archive 校验、packaged lifecycle/fault matrix、
真实 Tauri Shell normal/crash/reacquire 三轮、连续 generation 隔离、failed readiness、Core crash、
忽略 shutdown 强制整树回收、共享锁冲突/立即重获以及 packaged/Core/characters/data/runtime 前后
内容摘要一致。最终精确进程和验收临时目录残留均为 0；Rust/Python 定向测试全绿。

实现 HEAD `7d4067f` 的 Windows x64、macOS arm64、Linux x64 已在 push platform run
`30091500680` 和 pull_request platform run `30091504687` 全绿，Unit/UI run `30091504697` 也全绿。
三个 runner 均验证固定 archive identity、development/packaged RuntimeLocator、完整 lifecycle、
failed readiness、Core crash、忽略 shutdown 强制整树回收、连续 generation、pipe/thread/handle/fd/
进程树/临时目录清理、锁冲突与立即重获，以及 packaged/Core/characters/data/runtime 前后摘要一致。

首轮 macOS/Linux 只读摘要曾捕获 test fixture 写入 bytecode；修正只给 test launcher 增加与产品一致
的 `-I -B -X utf8`，未放宽门禁。PR #147 保持 Draft 且 merge state CLEAN，P0/P1 为 0。WP-1C-04
据此进入 stabilizing；本节不改变 ADR 对 Router、cancel、真实 Assistant 和 Accepted 状态的后续门禁。

Stabilizing 文档 HEAD `18a3cab` 的 push/pull_request platform runs `30091910794`/`30091915123`
与 Unit/UI run `30091915140` 再次全绿；最终白名单、资源摘要、进程/临时目录、PR 和独立回退审查
均通过，WP-1C-04 accepted。该结论完成 ADR-0002 所要求的 Phase 1C bundled lifecycle 技术验证，
ADR 本身仍保持 `Technically Validated for Phase 1C transport foundation`；Router、cancel 和真实
Assistant 消费验证仍是后续门禁，不因本 WP 提前 Accepted。

## WP-2-01 激活决策（2026-07-26）

当前 protocol 2.1 的 Rust/Python validator 只接受 `request` 和 `response`。WP-2-01 需要用真实 wire
event 验证 response/event 交错，因此该能力按兼容扩展登记为 protocol minor 2.2，并通过
`transport.concurrent-router` capability 协商；不得在不变更 minor 的情况下重新解释 2.1。

2.0/2.1 peer 继续允许完成已经冻结的 hello/health/initialize/snapshot/shutdown lifecycle。2.2 event
只包含公共 Envelope identity、generation credential、关联 request id、name 和 payload，不携带 request
deadline/priority 或 response ok/error；本 WP 不增加 sequence 或通用 operationId。具体允许目录、故障矩阵、
兼容门和回退见 `docs/specs/runtime-v2/WP-2-01-minimal-concurrent-router.md`。本激活决策不改变 ADR 当前状态；
只有总表定义的 WP-2-01/02 与 WP-3V-01 验证全部完成后才可更新为 Accepted。

## WP-2-01 stabilizing 验证记录（2026-07-26）

候选实现已把 Rust production Core generation 接到单 stdin writer、单 stdout reader、64 个 pending
上限和 32 个 event 上限，并提供 capability 门控的并发 handle；Python Host 使用独立 reader role、
dispatcher、单 writer、32 个 dispatch/writer 上限、8 个请求排队上限和 4 个执行槽。Rust/Python
共享 2.1 request 与 2.2 event fixture，真实 Host 已验证两个并发 in-flight waiter 不串线；注入式 sleep/
阻塞文件读取期间 health 先返回，shutdown 保留既有 3000ms/5000ms deadline。

本地候选证据为 Core Host Python 181 passed、locked Rust 172 passed/23 ignored、frontend 22 passed，
并覆盖乱序、event/response 交错、未知/重复/错 name/stale identity、各有界队列饱和、慢/失败 writer、
半帧/EOF/stdout pollution、Core crash、Retry/Exit 和连续 generation 清理。ADR 状态仍只保持 Phase 1C
transport foundation 的 Technically Validated；WP-2-02 与 WP-3V-01 尚未发生，不能提前改为 Accepted。

候选验收已于同日完成并登记为 accepted（总表为唯一状态真相源）。这只表示 WP-2-01 的最小 Router
边界和本地 Windows 候选证据已通过，不改变 ADR 对 WP-2-02 聊天边界或 WP-3V-01 真实 Assistant
纵向验证的 Accepted 门禁；writer 内部编码失败也已显式 fail closed 并复跑完整 locked Rust 测试。
当前 SHA 未推送，三平台同 SHA 原生结果仍是后续发布监控证据。

## WP-2-02 激活决策（2026-07-26）

WP-2-02 复用 protocol 2.2 的 generation-scoped event envelope 和
`transport.concurrent-router` capability；新增 `chat.started`、`chat.completed`、`chat.failed`、
`chat.cancelled` 业务 event name 本身不改变 Envelope 语义，因此不升级 protocol minor、不增加
sequence。protocol 2.0/2.1 lifecycle 继续保持原义。

Rust Gateway 的首个固定 allowlist 仅为 `chat.send`/`chat.cancel`，并独占 generation credential、
request identity、受控 deadline 和内部调度类别的注入权。Snapshot 收窄为 Python 构造的五字段完整
快照，Rust 只读校验和缓存；不实现通用 Operation、三级 priority、patch/component model、resource
token、streaming 或真实 Assistant/UI 接线。允许目录、故障矩阵、验收环境和回退命令见
`docs/specs/runtime-v2/WP-2-02-minimal-chat-boundary.md`。本激活不改变 ADR 当前技术验证状态；仍须 WP-2-02
候选验收和后续 WP-3V-01 真实纵向验证后，才能按本 ADR 状态门禁更新。

## WP-2-02 stabilizing 验证记录（2026-07-26）

候选实现已建立 Rust 固定 `chat.send`/`chat.cancel` Gateway、opaque cancel handle、Rust-owned identity/
deadline/调度类别、有界 terminal registry 与 generation 失效；Python Host 只增加 sleep/隔离文件读取
fixture、即时 cancel 仲裁、terminal-before-response single-writer 确认和五字段完整 Snapshot。2.1 lifecycle
继续返回既有 shape，2.2 Snapshot 才启用 WP-2-02 exact shape；未升级协议、未增加 sequence。

本地候选前证据为 Core Host Python 187 passed、locked Rust 177 passed/23 ignored，并含真实
Rust↔Python chat started/cancelled/response、cancel 小于 1 秒、active/settled Snapshot、revision 单调、
旧 generation/handle 清理和关键 terminal event 预留容量。实现提交为 `157dcc11`。ADR 状态仍保持
Phase 1C transport foundation 的 Technically Validated；完整候选验收与 WP-3V-01 尚未完成，不能提前
更新为 Accepted。

## WP-2-02 验收记录（2026-07-26）

WP-2-02 已完成固定 `chat.send`/`chat.cancel` Gateway、opaque handle、唯一终态、generation 清理和
五字段 Snapshot 的本地候选验收。Core Host/Python 定向 190 passed、locked Rust 177 passed/23 ignored、
frontend 22 passed；Windows Shell + bundled Core 的 normal/crash/reacquire、共享锁、readiness 2.1
兼容矩阵、2.2 Snapshot 与 native fault matrix 在 115.9 秒内通过，受保护的 characters/data/runtime
前后摘要一致。

候选 `6c36a1a` 的 PR platform run `30190007246` 在 Windows/macOS/Linux 同时捕获 RuntimeLocator 测试
临时根非 canonical 与 Unix 分隔负例问题；`96787830` 已把夹具冻结为 canonical 根，并纠正 Router
写前拒绝旧 generation/credential 后仍沿用旧 transport 预期的验收债务。`17d296a6` 让功能分支只经
pull_request 执行一次平台矩阵，main/dev 仍保留 push 门禁。新 HEAD 三平台结果在推送后跟踪；可归因
P0/P1 或退出条件回归会重新打开 WP-2-02。

这使 WP-2-02 本身进入 accepted，但 ADR 顶层状态仍保持
`Technically Validated for Phase 1C transport foundation`：只有 WP-3V-01 使用真实 Sakura Assistant
完成聊天、取消、历史、Core 强杀、新 generation 水合和资源归零的组合纵向验证后，ADR 才能更新为
Accepted；通用 Operation、resource token、三级 priority 和 component model 仍不是当前前置。
