# WP-3-01 Task 5：共享关闭期限与隔离 Assistant 根设计

> 状态：方案 C（失败返还 recovery owner）已批准，书面修订待负责人复核
> 日期：2026-07-25
> 分支：`refactor/tauri-runtime-v2`
> 基线：`f5b5e49509239c920cc7dcc054c4ebfa5a6cffbd`
> 上位约束：ADR-0001、ADR-0004、`WP-3-01-qt-free-assistant-adapter-readiness.md`

## 1. 背景与问题

WP-3-01 Task 5 已在 `f5b5e49` 提交可独立保留的安全子集：Rust 只从
`RuntimeLayout` 注入 `--app-root`，严格校验公开角色摘要、敏感字段与 Assistant
readiness 分类。该提交明确没有实现共享关闭期限。

冻结契约要求：从 Rust 成功写入并 flush `system.shutdown` frame 的时刻开始，只允许一个
5000ms absolute deadline。协议优雅期最多占其中 3000ms；剩余预算覆盖关闭 stdin、等待根、
终止完整树、验证树为空、回收 POSIX guardian 或 Windows Job、排空和关闭 pipe、结束 reader
thread、释放 fd/handle，以及清理当前 generation 拥有的临时资源。成功返回时这些资源必须全部
归零，不得在 Drop 或错误路径再启动一段完整 timeout。

现有实现无法满足该契约：

- `ManagedProcessTree` 只接受相对 `Duration`，`release_exited(self)` 没有 deadline。
- POSIX guardian、`release_exited` 和 `NativeTree::Drop` 各自包含额外或无界等待。
- Windows Drop 关闭 kill-on-close Job 后不等待 Job accounting 归零。
- stdout response 使用临时 blocking reader thread；错误路径可以丢弃 `JoinHandle`。
- stderr `finish`/Drop 和 trailing stdout `read_to_end` 均无界。
- RuntimeLocator 当前把 Python code root、working directory 和 Assistant 配置根合并为同一路径，
  导致真实三平台验收无法安全注入隔离 ready fixture。

因此 Task 5 必须窄扩平台契约、进程树 backend、Windows Job 实现和 RuntimeLocator；只在
`core_host_runtime.rs` 内重算 timeout 不能证明 deadline 与 resource-zero 同时成立。

### 1.1 方案 C 修订背景

Task 5C 的严格 RED 证明：当 POSIX finalizer 在进入时 caller deadline 已经过期，`SIGKILL` 只会
异步投递；guardian 可以在函数必须返回之后才进入 waitable 状态。原规范同时要求 error 也消费
唯一 `Child` owner、deadline 后不得等待、不得创建第二预算或转交 reaper，并要求 guardian 同步
resource-zero。这组条件在 POSIX 上不可同时满足。

负责人批准方案 C：保留 hard deadline、单 owner 和“未归零不得进入下一 generation”三项核心
安全边界；finalizer 只在成功时消费并释放 owner，失败时把同一个 recovery owner 返还给调用者。
失败返回本身不自动开始第二次 finalization。后续恢复是一个由 Rust owner 明确发起、具有新 absolute
deadline 的独立 operation；恢复成功前 generation 保持 `failed/stopping`，不得报告 `stopped`，也
不得创建下一 generation。

## 2. 设计目标

1. 一个 shutdown intent 只创建一个 `Instant` deadline，并把同一值传到所有后续资源 owner。
2. 进程树 finalizer 成功时消费所有权；失败时返还唯一 recovery owner。成功只表示 root、后代、
   guardian/Job 和平台句柄均已归零。
3. stdout/stderr 读取可被 deadline 或 cancellation 唤醒，不产生 detached thread。
4. 任一协议、读取或平台错误都汇合到同一 cleanup path；cleanup 尽可能继续并在最后聚合错误。
5. Python code root 与 Assistant 配置/角色 root 明确分离，二者都由 RuntimeLocator 显式批准。
6. 不修改 Supervisor restart 语义，不提前实现 Router，不增加依赖或第二个进程终止 owner。
7. Windows x64、macOS arm64、Linux x64 使用同一可观察契约和同一故障矩阵。

## 3. 非目标

- 不修改 IPC envelope、Snapshot schema、readiness code 或 generation credential 边界。
- 不实现聊天、pending request map、通用 reader/writer Router、Operation 或 Gateway。
- 不迁移 `CoreSupervisor`、Fake Core 或 Phase 1B 到新的 transport abstraction。
- 不改变 Windows suspended spawn、Job assignment-before-resume 或 POSIX setsid/guardian containment。
- 不恢复 fake `ready`/`hang` 协议字段，不通过 cwd、环境变量、仓库根或用户目录隐式寻找配置。
- 不修改 Cargo/Node/Python manifest 或 lockfile；当前 Windows crate feature 已包含 Pipes、
  JobObjects 和 Threading。
- 不读取、写入、复制到、删除、截断或清理仓库真实 `data/`、`characters/`、`runtime/`。

## 4. 公共所有权契约

### 4.1 Absolute deadline

`CoreHostRuntime::shutdown` 不再接收两个可叠加的相对 deadline。生产入口改为无 deadline
参数的 `shutdown(self)`，并使用 Rust 内部冻结的 3000ms graceful/5000ms total policy；WebView、
Python 和调用者不能覆盖。仅 `#[cfg(test)]` helper 可以注入同比缩短的 policy 来做确定性单元测试，
真实进程和 acceptance 仍使用正式数值。进入 shutdown 后立即冻结以下时刻：

```text
shutdown frame write + flush succeeds at t0
absolute deadline = t0 + 5000ms
graceful deadline = min(t0 + 3000ms, absolute deadline)
```

所有等待只计算 `absolute_deadline.saturating_duration_since(Instant::now())`。任何 helper
不得把 remaining duration 再解释为一段新的完整 timeout。`Instant` 只在 Rust 进程内传递，
不进入协议、WebView、日志或序列化结构。

在 shutdown frame 未成功写入时，启动/transport 失败仍必须走同一个显式 finalization helper；
它以调用处创建的单一 5000ms recovery deadline 为上限，但不得伪装成“successful shutdown”。

### 4.2 Success-consuming process-tree finalizer 与 recovery owner

在 `platform/contracts.rs` 保留既有 wait/terminate/verify 方法以兼容已验收消费者，并增加仅供
需要完整后置条件的 success-consuming 终结操作。冻结语义等价于：

```rust
pub struct ProcessTreeFinalizationFailure {
    error: PlatformError,
    recovery: Box<dyn ManagedProcessTree>,
}

pub type ProcessTreeFinalizationResult =
    Result<ProcessTreeFinalization, ProcessTreeFinalizationFailure>;

fn finalize_until(
    self: Box<Self>,
    deadline: Instant,
    reason_code: u32,
) -> ProcessTreeFinalizationResult;
```

failure 类型只公开 `new(error, recovery)`、`error(&self)` 和
`into_parts(self) -> (PlatformError, Box<dyn ManagedProcessTree>)`。如测试需要 `Debug`，实现只输出
脱敏 error 和 `has_recovery_owner=true`，不得格式化 native owner、PID/PGID、handle 或路径；不得为
该类型实现 `Clone`、`Copy`、`Serialize` 或 `Deserialize`。trait 不提供生产默认
`finalize_until`，所有 backend 和 test double 必须在编译期显式选择成功或返还 owner。

`ProcessTreeFinalization` 至少记录 root status 和是否执行强制终止；它不携带平台 handle、路径、
credential 或原始错误。finalizer 必须：

1. 首先把 absolute deadline 写入内部 cleanup state，使后续错误和 Drop 都不能获得新预算。
2. 观察 root 和 tree；root 已退出但后代存活仍必须进入整树终止。
3. 在 graceful deadline 已耗尽或调用者要求收束时终止完整树。
4. 使用剩余预算等待 root status、验证 tree empty，并回收 guardian/Job。
5. 只有 tree empty 且所有 owned process handle/fd 已释放时返回成功。
6. 单项失败不应提前跳过仍可执行的 cleanup；最终返回稳定、脱敏的错误和原唯一 owner。

成功 finalization 消费 tree 后，`CoreHostRuntime` 不再有可重复释放的第二 owner。失败时
`ProcessTreeFinalizationFailure` 必须满足以下合同：

- `recovery` 是原 owner 的继续，不是 clone、新 backend 或从 PID/PGID 重建的 owner；它保留 POSIX
  `Child`/冻结 PGID 或 Windows process/Job handle，足以再次调用 `finalize_until`。
- failure 和 recovery owner 不可复制、不可序列化，不进入 Snapshot、WebView、日志或 evidence；
  对外诊断只使用 `error` 的稳定 category/operation/message。
- 当前调用可立即 TERM/KILL、关闭不再需要的控制 writer，但不能把 guardian/Job owner 标为
  `released`，也不能在返回 failure 前丢弃它。
- 当前 shutdown intent 不得自动消费 failure 再创建一个 timeout。只有明确持有 recovery owner 的
  Rust 调用者可以在后续独立 recovery operation 中提供新的 absolute deadline。

Drop 只作保险：可以立即关闭控制 fd、发出 kill 或关闭 kill-on-close Job，但不得 sleep、无界 wait
或创建新 deadline，也不得宣称 resource-zero。显式 finalizer 成功结果是唯一可以宣称
resource-zero 的路径。

### 4.3 Deadline-aware pipe reader

平台 `ManagedProcessPipes` 的 stdin 仍可保留唯一 `File` owner；stdout/stderr 不再以裸 blocking
`File` 交给 Core Host，而是使用 deadline-aware reader。公共语义至少包括：

- `read_until(buffer, deadline, cancellation)`：返回 bytes、EOF、cancelled 或 timed out。
- `drain_until(limit, deadline, cancellation)`：有界读取尾随数据，超过 limit 按污染失败。
- 每次平台阻塞最多一个冻结的短 poll quantum；必须定期观察 cancellation 和 deadline。
- reader Drop 关闭自身唯一 fd/handle，但不负责终止进程树。

POSIX 使用 nonblocking fd + `poll`；Windows 使用匿名 pipe 支持的 `PeekNamedPipe`/有界读取或
等价 evented 原语。不得依赖“从另一线程 drop 同一个 File 会取消同步 read”，也不得在 timeout
后遗失 `JoinHandle`。

stdout frame 读取在调用线程上直接使用 deadline-aware reader，因此删除 thread-per-response。
stderr 仍可有一个持续 drainer 以避免子进程因 pipe backpressure 阻塞，但必须持有 cancellation、
completion channel 和唯一 `JoinHandle`。`finish_until` 先等待 tree finalization 造成的 EOF；需要
强制停止时设置 cancellation。只有 completion 已确认后才 join，且 join 使用同一 absolute
deadline 的剩余预算。正常 cleanup 后 Drop 观察到的 handle 必须为空；保险 Drop 若仍持有 handle，
先设置 cancellation，再依赖 reader 的单个有限 poll quantum 完成并 join。它不得创建新的完整
timeout，也不能把该保险路径记为成功终结。reader panic、redaction failure 或 backlog 超限都
不能跳过 tree cleanup。

## 5. 平台终结语义

### 5.1 Windows

- 继续使用每 generation 独立 Job、suspended spawn、assignment-before-resume 和
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`。
- graceful phase 后若 Job 仍有 active process，调用 `TerminateJobObject`。
- 使用 absolute deadline 的剩余预算等待 root handle 和 Job accounting 变为零。
- 只有 accounting 为零后才释放 process 和 Job handle；关闭 Job 不是 tree-empty 证据。
- deadline/error 返回时 process/Job handle 必须保留在 recovery owner；不得先 `take()` 后只返回
  `PlatformError`。后续 recovery 仍以 Job accounting 零作为成功前置。
- `ManagedProcessTree::Drop` 保留 kill-on-close 保险，但不得把快速关闭 handle 记为成功终结。
- stdout/stderr polling 不新增 crate feature 或依赖。

### 5.2 macOS 与 Linux

- 继续由 guardian 在目标 Core exec 前建立 setsid/process group；parent 持有已验证的 PGID。
- explicit finalizer 关闭/命令 guardian control，并在剩余预算内直接对已验证 group 执行
  TERM、必要时 KILL；它不能只等待 guardian 自己的固定 500ms+5000ms。
- guardian 的正常 cleanup 必须接受父进程给出的剩余预算，或因 parent 已完成 group kill 而立即
  收敛；显式 shutdown 一旦 armed，不允许 guardian 再启动独立完整预算。
- 收到 `TREE_EXITED` 后用有界 `try_wait`/poll 回收 guardian；禁止无界 `Child::wait()`。
- parent-death/guardian EOF 仍是 Tauri crash 保险，可以使用独立 crash-only ceiling；它不能在
  已 armed 的显式 finalizer 错误路径中成为第二预算。
- Drop 最多立即关闭 control、向已验证 PGID 发 KILL、kill guardian 和释放本地 fd；不得 sleep
  或无界 reap。正常与可验收路径必须在消费式 finalizer 内完成 wait/reap。
- deadline/error 返回时不得设置 `released=true` 后丢弃 guardian `Child`；原 `Child`、冻结 PGID 和
  尚需读取的 status owner 必须留在 recovery owner。后续 recovery 可以对已被 kill、已进入 zombie
  的 guardian 执行有界 `try_wait` reap。

## 6. Core Host shutdown 数据流

```text
write + flush system.shutdown
  -> freeze t0 / graceful_deadline / absolute_deadline
  -> read protocol response until graceful_deadline
  -> close stdin owner
  -> wait root only within remaining graceful budget
  -> attempt tree finalization with finalize_until(absolute_deadline)
       -> root-first descendants are still terminated
       -> tree empty verified
       -> guardian/Job reaped and handles released
  -> drain bounded trailing stdout until EOF
  -> finish stderr drainer and join its sole thread
  -> close reader handles and generation-owned temp resources
  -> aggregate protocol + cleanup results
  -> success: return only before absolute_deadline
  -> failure: return typed diagnostic + recovery owner before/at the failed boundary
```

如果 protocol response 无效、stdout frame 失败、root wait 失败或 tree finalization 报错，控制流
仍进入同一 cleanup tail。错误优先级为：原始协议/transport 错误为 primary；process-tree、pipe、
thread、temp cleanup 按发生顺序作为固定类型/operation notes。诊断不得包含路径、原始 stderr、
credential、API key、prompt、endpoint 或异常 repr。

成功的 `CoreHostExit` 必须同时证明：root status 已知或稳定标为 unknown、tree empty、reader
completion 已确认、pipe owner 已关闭、平台 tree owner 已消费。若任一后置条件未证明，返回失败，
并禁止调用者把 generation 视为 stopped 或启动下一 generation。

`CoreHostRuntime` 不得把 tree failure 立即格式化为 `String` 后丢弃 owner。Task 5D 使用统一 typed
`CoreHostLifecycleFailure` 作为 `launch` 与 `shutdown` 的错误边界：spawn 前的校验/定位失败携带
`recovery=None`；spawn 后若 tree 未归零则携带不可复制的 recovery capsule。诊断始终脱敏，
`Display`/`Debug` 不格式化 native owner。第一次 cleanup 不自动重试；调用者要么继续持有 capsule，
要么显式以新的 recovery operation 调用其 tree owner 的 `finalize_until`。只有 recovery 成功并完成
剩余 owner cleanup 后，capsule 才能产生 stopped/resource-zero 结果。WP-3-01 不借此修改 Supervisor
restart 语义。

Core Host 当前没有生产 filesystem temp owner；因此正常 Task 5 路径的 temp 集合为空。若测试或
后续调用为 generation 注册临时资源，它必须在同一 cleanup tail 中使用 remaining budget 清除。
Phase 1C harness 自己拥有的验收目录不冒充 Core 资源，由 Task 6 外层门禁独立删除并验证零残留。

## 7. Assistant root 与 Python resource root 分离

现有 runtime manifest 的 `*ApplicationRootRelativePath` 实际定位 bundled/development Python code。
为避免继续复用含糊名称，Locator 内部把该路径解析为 `resource_root`，并新增显式
`assistant_root`：

- `RuntimeLocationRequest` 在 development 与 packaged 两种 mode 都新增必填、绝对的
  `assistant_root`。
- `RuntimeLayout` 用 canonical `assistant_root` 替换含糊的兼容字段 `application_root`，并继续
  保存 `resource_root` 和 `working_directory`；runtime manifest 字段名保持不变，避免扩大打包
  schema。
- `resource_root` 继续受 runtime root containment 约束，供 `sys.path`、Core module 和 cwd 使用。
- `assistant_root` 必须存在、是 canonical directory，并且只能来自 request；允许位于 runtime
  root 之外，以支持 Tauri app-data 和隔离 fixture。
- `core_host_process_request` 只把 `assistant_root` 作为唯一 `--app-root` 参数。
- Python 不增加 cwd/repository/home/env fallback；缺配置仍由真实 Adapter 返回稳定
  `setup_required`，而不是由 Locator 猜测路径。

Phase 1C acceptance 在已校验的系统临时目录内复制只读 Assistant fixture，拒绝 symlink 和路径
逃逸，然后把 canonical fixture root 显式交给 RuntimeLocator。Python code 仍从仓库或 staged
runtime 的 `resource_root` 导入。这样 `ready`、`setup_required`、`degraded` 和 `failed` 都走真实
Adapter，不修改仓库 `data/`/`characters/`，也不增加 test-only protocol 字段。

## 8. 故障处理与安全不变量

- root 正常退出不代表 tree empty；后代存在时仍强制整树收束。
- tree、stdout、stderr 任一 owner 不允许 `mem::forget`、detach 或把 cleanup 转交给不再 join 的线程。
- timeout 不允许伪造 resource-zero：成功结果仍要求全部归零；失败结果必须返还 recovery owner，
  generation 保持 `failed/stopping`。
- writer/reader/platform failure 不触发新的 Supervisor restart path；WP-3-01 readiness code 继续
  全部 `retryable=false`。
- Windows handle 与 POSIX fd 均只有一个 owner；转换为 reader 后原始 `File` 不再另行保留。
- deadline/credential 不序列化到 Snapshot、WebView、runtime-layout evidence 或 stderr。
- cleanup failure 后旧 generation 状态必须失效；完整树未证明为空前不得创建下一 generation。
- recovery failure 不允许隐式 Drop 后继续：生产调用链必须继续持有 typed recovery capsule，或在明确
  的上层终止路径中执行 Drop 保险并保持 generation 非 stopped。
- fixture 和保护目录验证使用 path、length、mtime、SHA-256；不得以测试 cleanup 修改真实现场。

## 9. TDD 与验收矩阵

### 9.1 Contract/unit RED

- fake tree 记录收到的 `Instant`，证明 graceful、forced、verify 和 release 使用同一值。
- graceful 消耗接近 3000ms 后，finalizer 只获得原 5000ms 的剩余量。
- stdout/stderr timeout、cancel、panic、EOF、backlog 和 pollution 均不遗失 reader completion。
- root-first-exit + surviving descendants 必须触发 terminate，而不是只等待后报错。
- wait-root、terminate、verify、guardian reap/Job accounting、reader 和 release 注入失败后，Drop
  不创建第二预算。
- already-expired finalizer 必须 RED 证明：第一次调用稳定返回 `TimedOut + recovery owner`，不遗失
  POSIX guardian/Windows Job owner；对同一 owner 的后续显式 recovery 使用新 absolute deadline，
  成功后 guardian PID/PGID/fd 或 Windows Job/process handle 归零。
- fake owner 证明第一次 failure 不会自动触发第二次 finalization，且 recovery 成功前 generation
  transition 不能进入 stopped 或启动下一 generation。
- RuntimeLocator 拒绝相对、缺失、非 canonical assistant root 和隐式 fallback；接受与 code root
  分离的显式 isolated root。

### 9.2 Real platform tests

- cooperative shutdown、接近 3000ms graceful、ignore shutdown、close-block。
- root + 一个/多个后代，后代忽略 TERM；root crash 和 external kill。
- Windows Job accounting 归零后 release；macOS/Linux guardian 在同一 deadline 内退出并 reap。
- 每个场景从 successful shutdown write 起测量，小于单一 5000ms 门禁允许的测试抖动上限，且
  PID/group/Job、pipe、fd/handle、reader thread 和 generation temp 立即归零。
- cleanup 后共享锁立即可重新获取，连续 generation 不接收旧 reader/Snapshot 状态。
- timeout/fault 行先证明 recovery capsule 仍拥有同一 native identity，再执行测试明确授权的 recovery
  operation 清零；测试不得把 recovery deadline 混入原 5000ms shutdown elapsed 结果。

### 9.3 Real Adapter acceptance

- isolated ready fixture 最终得到 `ready/READY/retryable=false` 和精确五字段 summary。
- setup_required/degraded/failed 均由真实配置 fixture 产生，不使用 fake mode。
- 初始化期间 repeated health 响应；正常、close-block、强杀后均满足同一资源门禁。
- fixture 和仓库真实 `data/`、`characters/`、`runtime/` 前后只读摘要符合冻结基线；fixture
  无 `.bak`、log、cache、`__pycache__` 或 symlink。
- Windows x64、macOS arm64、Linux x64 workflow 执行相同 Rust/Core Host 测试，不以单平台替代。

## 10. 精确实施范围

批准的最小生产扩域：

- `desktop/src-tauri/src/platform/contracts.rs`
- `desktop/src-tauri/src/platform/process_tree_backend.rs`
- `desktop/src-tauri/src/platform/runtime_locator.rs`
- `desktop/src-tauri/src/managed_process_tree.rs`
- `desktop/src-tauri/src/core_host_runtime.rs`
- `desktop/src-tauri/src/phase_1c_core_host_acceptance.rs`

允许修改上述模块的 inline tests，并在 `tests/fixtures/runtime_v2/wp_3_01/**` 增加隔离 shutdown/
descendant fixture。Task 6 原定的 workflow 和 Python integration files 仍由 Task 6 独立提交，
不得借 Task 5 扩入聊天、产品 UI、Provider 网络、用户配置写入或通用 transport platform。

若 TDD RED 证明必须调整 `main.rs` 的 POSIX guardian composition entry，只允许传递已存在的
guardian mode，不改变产品启动或 Supervisor 状态机；实施前必须把该证据写入 Task 5 报告。
`Cargo.toml`、`Cargo.lock`、`core_supervisor.rs` 和 manifest 默认不在 Task 5 范围。

## 11. 回退

回退前先停止当前 generation，并用平台身份和共享锁证明 root、后代、guardian/Job、pipe、reader
和 temp 均已归零。随后按单一目的提交逆序 revert：先回退 Task 5 deadline/root-separation 实现，
再按需回退 `f5b5e49` 的安全子集。回退恢复 WP-1C-04 fake-readiness 生命周期，不删除或恢复
仓库 `data/`、`characters/`、`runtime/`、日志、cache、migration backup 或用户数据。

## 12. 已拒绝方案

- 只把每段 timeout 改成 `remaining()`：仍有 POSIX/reader 无界等待。
- 依赖 tree empty 后最终 EOF：不能证明 detached reader、guardian handle 和用户态 drain 完成。
- timeout 后丢弃 `JoinHandle` 或 tree：满足返回延迟但泄漏 thread/fd/process owner。
- timeout 后继续消费 tree 并只返回 `PlatformError`：POSIX already-expired deadline 下无法同时保证
  hard deadline 与 guardian reap；改为方案 C 的 typed recovery owner。
- timeout 后自动启动第二个 recovery deadline：掩盖原 shutdown deadline 失败；recovery 必须由
  持有 typed owner 的上层显式发起，并保持 generation 非 stopped。
- 仅关闭 Windows Job：可以触发 kill-on-close，但没有 Job accounting 零证据。
- test-only env/cwd app-root override：绕过 RuntimeLocator 批准链，不能代表生产路径。
- 平台统一 `ManagedProcessSession` 或单 lifecycle actor：所有权更集中，但改动面更大，并会提前
  侵入 WP-2-01 Router；本次保留为未来重构候选，不作为 Task 5 实现。
