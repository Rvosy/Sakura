# ADR-0001：Runtime v2 进程监管

> 状态：Proposed
> 日期：2026-07-15
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

实现统一 Rust 接口：

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

### 非 Windows 平台边界

- Phase 1A–3 只以 Windows 为正式目标平台。
- `ManagedProcessTree` 保留跨平台 trait 和安全失败边界。
- Linux/macOS 具体 process group、parent-death signal 和透明窗口行为不作为首轮门禁。
- 对应平台进入交付范围前，单独建立 ADR 和真实故障注入测试。

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

- 意外退出、暂时性启动失败可以在 budget 内重试。
- 协议 major 不兼容、缺失必要 capability、配置 `setup_required` 和明确不可重试错误不得自动循环重启。
- 用户手动重试仍经过同一个串行状态机和旧 generation 清理门禁。

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

- Windows Job Object 需要平台代码和故障注入测试。
- suspended spawn 可能增加 Windows 实现复杂度，因此必须通过技术验证确认是否必要。

## 允许调整的范围

只要以下验收保持成立，可以调整具体 API、crate 或 spawn 方式：

- 无孤儿领域进程。
- Tauri 拥有最终终止权。
- Core 崩溃时 UI 保持可用。
- 关闭和恢复具有确定 deadline。
- Fake Core 故障矩阵通过。

## ADR 状态门禁

本 ADR 在 Phase 1B 的 Windows Job Object、后代回收、竞态和 Fake Core 测试通过后，从 `Proposed` 更新为 `Technically Validated`，经实现审查后更新为 `Accepted`。验证失败时应修改或 Supersede 本 ADR，不得为迁就当前方案降低产品门禁。
