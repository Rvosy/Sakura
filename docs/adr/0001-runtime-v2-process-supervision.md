# ADR-0001：Runtime v2 进程监管

> 状态：Accepted（Supervisor 语义与 Windows backend）；跨平台 backend 受 ADR-0004 / Phase 1P 约束
> 日期：2026-07-15
> 验证日期：2026-07-22
> 适用范围：Tauri Runtime 对 Python Core 及其后代进程的生命周期管理

## 背景

Runtime v2 要求 Tauri 成为唯一桌面生命周期根。Python Core 可以创建 MCP、TTS、本地模型或浏览器 Worker，但这些进程不能在 Core 崩溃、重启或 Tauri 退出后成为孤儿进程。

#140 的问题之一是优雅关闭、强杀和后代进程回收分散在 Python、Qt 和各服务内部，最终所有权不清晰。

## 不可妥协的约束

- Tauri/Rust 拥有整个 Core 进程树的最终停止权。
- Python 负责业务级优雅关闭，但不承担最终兜底回收。
- 每个 Core generation 使用独立的进程树容器。
- 旧 generation 的进程、管道和临时资源清理完成后，才能认为恢复完成。
- Tauri 退出后不得遗留 Core、MCP、TTS、浏览器或模型服务后代进程。
- 无法建立受控进程树时安全失败，不能继续运行未受监管 Core。
- 所有 spawn、stop、restart 和 app shutdown 命令由一个串行 Supervisor 状态机处理。
- 每个 generation 拥有独立 cancellation token；旧 generation 回调不得改变新 generation 状态。
- 同一时间最多存在一个 spawn、stop 或 restart 流程。
- App shutdown 一旦开始，必须取消重启计时器并永久禁止本次进程产生新 generation。
- terminate、wait、资源释放和重复 shutdown 必须幂等。

## 当前推荐方案

实现统一 Rust 接口；具体资源所有权由平台 backend 承担：

```text
ManagedProcessTree
├─ spawn(spec)
├─ pid()
├─ wait()
├─ terminate_tree(reason)
├─ verify_tree_exited()
└─ release_exited_handles()
```

协议级优雅关闭属于 Supervisor，而不是平台进程树抽象：

```text
CoreSupervisor
├─ request_protocol_shutdown(deadline)
├─ wait_grace_period()
├─ terminate_tree(reason)
└─ finalize_generation()
```

### Windows

推荐每个 generation 创建独立 Job Object：

- 启用 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`。
- 不允许领域子进程静默 breakaway。
- 优先使用 suspended spawn：先创建 Python，加入 Job，再恢复主线程。
- Core 意外退出后，终止 Job 内仍存活的后代，再关闭 Job。
- 新 generation 创建新 Job、generation ID 和管道。
- 正常句柄释放、强制终止存活进程树和 Rust `Drop` 最终保险必须使用不同语义；不得用含糊的 `close()` 同时表示三者。

suspended spawn 是当前推荐实现，不是产品层不可变要求。如果技术验证证明“立即 spawn 后可靠加入 Job”在目标环境中同样不会产生逃逸进程，可以更新本 ADR 采用更简单方案。

### macOS 与 Linux

ADR-0004 已把 macOS arm64 和 Linux x64 纳入基础正式矩阵。`ManagedProcessTree` 公共语义保持不变，平台实现必须移入 backend：

- macOS 在 spawn/exec 边界建立独立 session/process group，使用组级 signal 和 `waitpid`/等价机制完成优雅期后的整树终止与退出验证。
- Linux 同样建立独立 session/process group；parent-death signal 可以作为保险，但不能代替 Tauri 持有的组级最终停止权。
- spawn 与加入监管边界之间不得存在允许 Core 提前创建逃逸后代的未验证窗口。
- 无法建立进程组、无法确认目标 identity 或无法验证整组退出时安全失败，不启动未监管 Core。
- `cfg(not(windows)) => UnsupportedPlatform` 只允许在 Phase 1P 实现前作为历史占位，不能进入 WP-1P-06 accepted 结果。

## 关闭流程

```text
SupervisorState = stopping
-> 设置 app_shutdown/restart_inhibited（如适用）
-> 取消当前 generation 和所有重启计时器
-> 拒绝新的普通业务请求
-> 发送 system.shutdown
-> 等待 Core 业务资源优雅释放
-> 超时后 terminate_tree
-> 关闭 stdin/stdout/stderr 和后台任务
-> verify_tree_exited
-> 清理 generation 临时资源并释放已退出句柄
-> SupervisorState = stopped/exited
```

shutdown、health 和 cancel 不得排在普通长任务之后。

### 初始 lifecycle deadline 建议

以下数值是 Phase 1B/1C 的首轮故障测试输入，不代表已经 `Technically Validated`；技术门可以基于分布数据调整，但必须同时更新 ADR、测试 fixture 和诊断文案：

| 生命周期动作 | 初始 deadline | 超时后的动作 |
|---|---:|---|
| `system.hello` | 3,000 ms | 当前 generation 启动失败；在 restart budget 内可按暂时性启动失败重试 |
| `core.initialize` 响应/接受 | 5,000 ms | 当前 generation 初始化协议失败；清理旧树后可在 budget 内重试 |
| readiness watchdog | 30,000 ms | 与 initialize 请求 deadline 分离；进入 diagnostics/restarting，不阻塞 health/shutdown |
| `system.shutdown` 协议优雅期 | 3,000 ms | 到期立即 `terminate_tree`，不继续等待领域任务 |
| 完整停止并验证进程树退出 | 从 shutdown 意图起 5,000 ms | 超过即为 P1；Shell 记录强杀/残留证据并禁止新 generation |

deadline 从 Rust 侧发出对应意图并成功写入当前 generation transport 时计时；WebView 不能覆盖这些 lifecycle 值。调试器附加、首轮依赖下载或人工断点不能改变正式验收 deadline。

## 竞态与幂等规则

- `shutdown during spawn`：取消 spawn 流程；若进程已创建则立即进入同一 stop 流程，不发布 running。
- `shutdown during hello/initialize`：停止等待 readiness，发送协议关闭；超时后回收进程树。
- `shutdown during restart backoff`：取消计时器，不能再创建新 generation。
- `manual retry while stopping`：合并为一个待处理意图，旧树完整退出前不得 spawn。
- 连续点击重试：只产生一个有效 restart 流程。
- shutdown 响应与进程退出同时到达：两条路径汇合到幂等 `finalize_generation`。
- 新 generation 启动后旧 reader 才报错：旧回调只能完成自身清理，不能修改当前 SupervisorState/CoreReadiness。
- 重复 shutdown、terminate、wait 和 handle release 必须安全返回同一最终结果。

## Core 崩溃与恢复

```text
检测 Core 退出
-> CoreReadiness = transport_unavailable
-> UI 保持存在并进入 diagnostics/restarting 状态
-> 终止旧进程树剩余后代
-> 关闭旧管道和 pending request
-> 失效旧 snapshot、operation 和资源 token
-> 根据有限重启策略创建新 generation
```

可选组件自身失败不应触发 Supervisor 重启；是否重启由 CoreReadiness 和错误类型决定。

自动重启必须使用有限 budget/backoff，并按结构化失败原因决定：

- 意外退出、暂时性 OS spawn/pipe 失败、hello/initialize timeout 和连接意外关闭可以在 budget 内重试；同一确定性原因重复出现仍受 budget 限制。
- 以下启动结果明确不得自动重试：
  - `protocol_major_incompatible`。
  - `missing_required_capability`。
  - `setup_required`；它是等待用户配置的稳定 readiness，不是崩溃。
  - 必需配置缺失、字段类型无效、未知 Provider 类型、未来/不支持 schema、损坏必要数据等确定性配置或数据错误。
  - bundled Python 缺失、架构/版本不兼容、Core 入口缺失、import guard 发现 Qt/禁止模块等确定性 Runtime/打包错误。
  - generation credential 不匹配、握手认证失败或其他安全边界错误。
  - 共享应用锁 `already_running` 或 mutex API fatal failure；它们属于桌面入口结果，不应创建 Core restart loop。
- Provider 网络不可达、模型认证失败和普通聊天请求错误属于领域请求结果，不改变 Core 启动 readiness，也不触发 Supervisor 重启。
- 用户手动重试仍经过同一个串行状态机和旧 generation 清理门禁。

不可自动重试不等于永远禁止用户重试。diagnostics 必须先展示可执行修复动作；外部状态发生变化后，用户手动 retry 才能通过同一 Supervisor 状态机创建新 generation。

## Fake Core 验证矩阵

必须自动覆盖：

- 正常启动和退出。
- 延迟建立 IPC。
- 初始化卡死。
- spawn 期间 shutdown。
- hello 期间 shutdown。
- initialize 期间 shutdown。
- restart backoff 期间 shutdown。
- 运行中崩溃。
- 忽略 shutdown。
- 创建一个和多个后代进程。
- 后代进程忽略正常退出。
- 长任务不返回。
- 重启后旧 generation 继续发事件。
- 旧进程树停止期间手动 retry。
- 快速连续 retry。
- 重复 shutdown。
- Tauri 主动退出。
- Job Object 建立失败。
- macOS/Linux session/process group 建立失败。
- macOS/Linux 根进程退出但后代继续存活。
- 向旧 PID/进程组 identity 发信号前发生 PID/PGID 复用或 identity 不匹配。

## 可观测性

诊断信息至少包含：

- SupervisorState。
- Core PID、generationId 和仅用于诊断的 generationNumber。
- 启动、ready、退出和强杀时间。
- 最近一次退出码或终止原因。
- 是否发生强制进程树回收。
- 重启次数和下一次重试状态。

不得向普通 UI 暴露 transport credential 或敏感环境变量。

## 结果与代价

收益：

- 生命周期最终所有权清晰。
- Core 崩溃不会带走 UI。
- 退出和恢复不依赖每个 Python 服务都正确实现清理。

代价：

- Windows Job Object 与 POSIX process group 都需要平台代码和故障注入测试。
- suspended spawn 可能增加 Windows 实现复杂度，因此必须通过技术验证确认是否必要。
- macOS/Linux 必须处理 signal、wait、PID/PGID identity 和父进程异常退出差异。

## 允许调整的范围

只要以下验收保持成立，可以调整具体 API、crate 或 spawn 方式：

- 无孤儿领域进程。
- Tauri 拥有最终终止权。
- Core 崩溃时 UI 保持可用。
- 关闭和恢复具有确定 deadline。
- Fake Core 故障矩阵通过。

## Phase 1B 验证结论

WP-1B-01 至 WP-1B-04 已在 Windows 11 23H2、x86_64-pc-windows-msvc、Rust/Cargo 1.96.0 和 Tauri 2.11.3 上完成实现审查与技术验证：

- `ManagedProcessTree` 使用 suspended spawn，在恢复主线程前把每个 generation 加入独立 Windows Job Object；Job 建立或分配失败时安全终止尚未受监管的进程。
- `CoreSupervisor` 是串行 generation 状态的唯一所有者；有限自动恢复最多 3 次，backoff 固定为 250ms、1s、3s，旧 generation 和旧 restart token 均不能改变当前状态。
- Fake Core 自动矩阵覆盖正常关闭、spawn/hello/初始化占位阶段关闭、忽略关闭、崩溃并遗留后代、旧回调、手动 retry、重复意图、backoff 关闭和 Job 失败；最终自动结果为 63 passed、13 ignored fixture、0 failed。
- 最终 debug Tauri 真实验收连续两轮覆盖可见窗口、pending hello 时主动退出和第三次 restart backoff 时主动退出；每轮根退出码为 0，计时器按合同取消，登记的 15/16 个根与后代身份、Job、worker、句柄、timer 和隔离临时目录均为零残留。
- 两轮验收前后均对真实 `data/` 的 path、length、mtime 和 SHA-256 生成完整清单；121 个文件、1,045,983,998 bytes 的 canonical SHA-256 均保持 `300b89fa68dd973f6970f3435ad0c5cc15fc84a2088baf3514e20dae25d0b62b`。

本结论验证 Windows 进程监管 backend、Supervisor 机制和 Fake Core 边界，不代表 macOS/Linux backend 已验证，也不代表真实 Python Core、业务 IPC、initialize/Snapshot 或任何领域能力已经接入；这些仍由后续工作包独立交付和回退。

## ADR 状态门禁

本 ADR 的 Supervisor 状态机、最终停止权、generation 隔离和 Windows backend 已在 Phase 1B 完成实现审查，因此保持 `Accepted`，不抹去现有 Windows 证据。

跨平台总体交付必须同时满足 ADR-0004：WP-1P-04 已在最新 Draft PR HEAD 的 Windows x64、
macOS arm64、Linux x64 原生 CI 完成 POSIX/Job backend 技术门（run `30057738510`、
`30057739993`），因此本 ADR 的进程树 backend 证据扩展为三平台 `CI platform foundation`。
该记录不等价于完整三平台 Shell + Core 生命周期 accepted；WP-1P-06 仍必须完成真实
后代回收、竞态、Tauri 主动退出和锁/数据零残留门禁。后续若真实 Core 接线或 POSIX backend
推翻公共边界，应修改或 Supersede 本 ADR，不得为迁就当前实现降低产品门禁。
