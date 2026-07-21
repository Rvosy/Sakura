# ADR-0002：Runtime v2 IPC

> 状态：Proposed
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
- 长任务返回 operation ID，并通过事件报告结果。
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
- Python 只有一个 writer queue，业务任务不能直接写 stdout。
- transport 抽象保留未来替换 Named Pipe/Unix Domain Socket 的可能，但当前没有迁移承诺。
- stdin/stdout/stderr 的创建、继承、关闭和强制回收由 ADR-0004/ADR-0001 的平台进程树 backend 承担；IPC 公共层不得调用 shell，也不得依赖 `.exe`、Win32 handle 或 POSIX fd 的具体表示。
- Windows、macOS、Linux 必须共享字节级 framing、Envelope、generation、deadline 和错误语义；平台不能各自扩展业务字段。

Python 内部明确拆分：

```text
Transport / Control Plane
├─ reader
├─ control dispatcher
├─ writer queue
└─ pending / operation registry

Domain Execution Plane
├─ bounded async tasks
├─ bounded thread executor
└─ 必要时的受控 worker process
```

- 同步 SDK、阻塞 I/O 和同步领域方法不得在 transport/control 事件循环上执行。
- 可能长时间占用 CPU、GIL 或运行不可信插件代码的操作，应进入可终止的受控 worker process，或由技术验证证明 thread executor 足够。
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

初始 lifecycle deadline 统一采用 ADR-0001 的建议值：`system.hello` 3,000 ms、`core.initialize` 接受响应 5,000 ms、readiness watchdog 30,000 ms、`system.shutdown` 协议优雅期 3,000 ms，完整进程树停止从 shutdown 意图起不超过 5,000 ms。这些是待 Phase 1B/1C 验证的初值，不是已验证性能承诺。`core.initialize` 必须先快速返回接受/拒绝结果，不能把 30 秒 readiness watchdog 当成同步 request deadline。

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
- 根据 command 注册表决定 priority。
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

`sequence` 是否保留为强校验字段由 Phase 2 技术验证决定。stdio 和单 writer queue 已提供字节顺序；只有确实需要检测应用层丢帧、重复或多来源合并时才加入 sequence。

普通帧初始建议限制为 8 MiB，但具体值属于实现参数。截图、音频和大文件必须使用受控资源描述符。

## Priority

```text
control
  hello、health、shutdown、cancel

interactive
  chat、角色选择、用户触发操作

background
  资源扫描、Memory 整理、主动调度
```

Priority 是调度语义，不要求实现三个物理管道。

## Operation

预计不能在短 request deadline 内完成的任务返回：

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

## Chat 事件

```text
chat.started
chat.progress
chat.completed
chat.cancelled
chat.failed
chat.delta（预留，不要求实现）
```

Phase 3 使用完整回复 + WebView 打字机展示，不要求 Provider token streaming。

- `chat.cancel_request` 取消实际模型请求。
- 跳过打字机属于 WebView 本地 presentation 行为，不调用 Python。

## Snapshot

Core Snapshot 至少表达：

```text
schemaVersion
generationId
generationNumber
revision
readiness
components
capabilities
currentCharacterSummary
activeInteractionSummary
coreConfigRevision
```

规则：

- Snapshot 由 Python Core 构造，Rust 只读缓存。
- 新 generation 建立时立即清空旧 Snapshot。
- revision 不匹配时请求完整 Snapshot，不在 Rust 中猜测业务补丁。
- 组件状态显式区分 disabled、initializing、ready、degraded 和 failed。
- Snapshot 不包含 API Key、Credential、完整系统 Prompt、插件私密配置和任意本地文件裸路径。
- `currentCharacterSummary` 只包含渲染 UI 所需的公开字段。

### 受控资源描述符

截图、音频、角色导入和其他大文件通过 generation-scoped opaque token 传递，而不是裸文件路径。token 至少具有：

- 资源类型。
- generationId。
- TTL。
- 大小上限。
- 允许访问的 command/窗口范围。
- 单次或有限次数读取策略。

WebView 不得通过 token 扩展为任意文件系统访问。

## Backpressure

- reader、writer 和 event queue 必须有界。
- progress 在入队前优先合并，只保留对 UI 有意义的最新中间值。
- 队列紧张时允许丢弃旧的非终态 progress。
- control、response 和终态事件使用预留容量或独立优先配额。
- shutdown/cancel response 以及 operation.completed/failed/cancelled 不得被普通 progress 挤出或丢失。
- 合并和优先级配额仍无法恢复时，才关闭当前 generation 的 IPC 连接。
- 帧超限、stdout 污染或协议损坏时立即关闭当前 generation 的 IPC 连接。
- request deadline 到期或窗口关闭后，Rust 清理 pending waiter。

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

- 分片帧和合并帧。
- 非法 JSON 和超大帧。
- 并发响应乱序。
- event 与 response 交错。
- control 请求不被长任务阻塞。
- 同步 sleep 和阻塞文件 I/O 在领域执行期间，health 和 shutdown 仍响应。
- CPU 密集循环无法协作结束时，Rust 在 deadline 后仍能终止完整进程树。
- deadline、取消和窗口关闭。
- 大量 progress、慢 writer 和终态事件的背压优先级。
- stdout 污染。
- stderr 持续输出和日志队列过载。
- Core 重启后的旧 generation 消息。
- protocol major/minor 不兼容和缺失 capability。
- Rust 主动关闭 stdin 后 Python 能有界退出。
- Rust/Python golden fixtures。
- Windows、macOS、Linux 使用同一 golden fixtures；对应平台的真实 Python Host 都完成 hello、health、shutdown 和损坏 transport 回收。

## 允许调整的范围

以下内容可以根据 Phase 2 验证调整：

- sequence 是否必需。
- frame 上限具体数值。
- schema 是否生成静态类型。
- credential 的具体传递方式。
- executor/async runtime 选型。
- 是否在未来替换为 Named Pipe/Unix Domain Socket。

不可调整的是并发、取消、生命周期优先级、generation 隔离、WebView 权限边界、控制面隔离和安全失败边界。

## ADR 状态门禁

本 ADR 在 Phase 1P 已提供三平台 transport/process backend、Phase 1C 的握手、版本、stderr 和故障 transport 门禁通过后更新为 `Technically Validated`，在 Phase 2 的并发、阻塞隔离、取消与背压门禁通过后更新为 `Accepted`。单平台结果只能作为该 backend 证据。验证失败时应更新或 Supersede 本 ADR，不得用静态契约测试替代真实阻塞与故障测试。

## WP-1C-01 基础 transport 验证记录（2026-07-22）

本记录只验证 Phase 1C 的最小无 Qt Core Host 和基础握手子集，不提前满足版本/capability、generation credential、持续 stderr 排水、并发 Router、initialize、Snapshot 或业务请求门禁，因此 ADR 状态保持 `Proposed`。

- Python 与 Rust 已实现并互验 4-byte big-endian 长度前缀 UTF-8 JSON 帧、8 MiB 上限、基础 Envelope、稳定错误 DTO 和 generation identity；覆盖任意 header/payload 分片、合并帧、非法 UTF-8/JSON、零长/超大/半帧和 stdout 污染。
- Python 最小 Host 在捕获二进制 stdout 后安装文本写入 guard，只提供 `system.hello`、`system.health`、`system.shutdown`；import guard 证明 hello 前未导入 PySide6、`app.ui`、Assistant、Memory、MCP、插件或 TTS。
- Rust 使用显式 Python 路径、匿名 stdin/stdout/stderr 管道和 Windows kill-on-close Job 启动真实 Host；所有 control response 均有 deadline，超时、stdin EOF、损坏 stdout 和忽略 shutdown 均有界结束或强制回收完整 Job。本项是 Windows backend 证据，macOS/Linux 由 WP-1P-04/06 回补。
- 自动门禁结果：Python 19 passed；Rust 72 passed、13 ignored fixture、0 failed；`cargo fmt --check`、Debug/Release `cargo build --locked`、PowerShell parser、Python `py_compile` 和 `git diff --check` 通过。
- 两轮真实 Debug Tauri + `runtime/python.exe` 验收均观察到可见窗口、hello、两次 health、协议 shutdown 和根退出码 0；每轮登记 9 个进程身份，最终进程和系统临时目录残留均为 0。
- 两轮验收前后真实 `data/` 完整路径/长度/mtime/SHA-256 清单均为 121 文件、1,045,983,998 bytes，canonical SHA-256 均为 `300b89fa68dd973f6970f3435ad0c5cc15fc84a2088baf3514e20dae25d0b62b`，证明零变化。
- 当前已知限制：`CreateProcessW` 的受控 stdio 继承依赖父进程其他句柄保持默认不可继承；显式 handle allowlist、generation credential、持续 stderr 排水和协议协商留待 WP-1C-03 门禁验证。
