# WP-1C-03：协议协商、stderr 排水和故障 transport

> 状态：accepted
> 日期：2026-07-24
> 前置：WP-1P-06 accepted，提交 `ca8fea3`
> 规范来源：ADR-0001、ADR-0002、ADR-0003、ADR-0004、WP-1C-01/02、WP-1P-01 至 06

## 1. 结果边界

本 Work Package 在既有 4-byte big-endian framing、严格 JSON Envelope、单请求 lifecycle 和
`ManagedProcessTreeBackend` 上冻结 Desktop/Core transport 边界。三平台共用完全相同的版本、
capability、generation credential、stderr 和故障语义；平台 backend 只负责 pipe、进程树和原生
错误转换。transport fatal 交回既有 Supervisor generation stop/finalize 路径，不建立第二状态机。

允许目录：`app/core_host/`、`desktop/src-tauri/src/core_host_protocol.rs`、
`core_host_runtime.rs`、Phase 1C acceptance 接线、`tests/unit/test_core_host_*`、
`tests/integration/test_core_host_lifecycle.py`、`tests/fixtures/runtime_v2/wp_1c_03/`、
`desktop/tests/` 的隔离验收、platform foundation workflow、ADR-0002、本文和 Work Package 总计划。

明确非目标：不改变 CoreSupervisor 状态机、generation/restart budget、Snapshot schema、共享用户
数据 schema 或用户可见产品语义；不实现 Phase 2 pending/event Router、Operation/cancel；不接入
Assistant、聊天、Memory、插件、MCP、Tools、TTS、浏览器、截图或主动互动；不恢复系统 Python、
PATH 扫描或硬编码 `runtime/python.exe`；不把 Win32 handle、POSIX fd/signal/PID/PGID 放进公共 DTO。

## 2. 协议协商

公共协议当前版本为 `2.1`，Desktop 与 Core 支持范围均为 major `2`、minor `0..1`。Envelope 保留
`protocolMajor`/`protocolMinor`；启动后的第一条且唯一一条握手必须是 `system.hello`：

```json
{
  "protocol": {"major": 2, "minMinor": 0, "maxMinor": 1},
  "requiredCapabilities": ["system.hello", "system.health", "system.shutdown", "core.initialize", "core.snapshot"],
  "optionalCapabilities": []
}
```

Core 返回同结构的支持范围、按 Core 冻结顺序排列的 `capabilities`，以及确定的协商结果：
`major=2`、`minor=min(desktop.maxMinor, core.maxMinor)`、`capabilities` 为双方声明集合的交集并按
Core 顺序输出。required/optional 列表必须是非空、无首尾空白、无重复的字符串数组，且两列表
不得相交。未知 optional capability 被忽略；重复、非法 DTO、major 不同、minor 范围无交集或缺少
required capability 均 fail closed。major 不兼容为 `PROTOCOL_MAJOR_MISMATCH`，能力缺失为
`CAPABILITY_NEGOTIATION_FAILED`，其余非法协商 DTO 为 `INVALID_NEGOTIATION`。

hello 前除 `system.hello` 外的消息返回 `HANDSHAKE_REQUIRED`；成功后重复 hello 返回
`HANDSHAKE_ALREADY_COMPLETE`。协商失败后 generation 被标为 handshake failed，后续 initialize
返回 `HANDSHAKE_FAILED`，不能继续初始化。公共协商规则不按 OS 分叉。

## 3. Generation credential

Rust 在每次 `CoreHostRuntime::launch` 内从 OS 随机源生成独立 128-bit credential，使用 32 个小写
十六进制字符表示。spawn 建立独占 stdin pipe 后，Rust 在任何公共 frame 前先写入原始 16-byte
credential bootstrap；Core 必须精确读满后才进入 framing loop。credential 不进入 argv、环境、
Snapshot、diagnostics、日志、Debug 或用户错误；bootstrap 缺失/半写立即拒绝启动。该私有启动前缀
不是公共协议消息，第一条公共消息仍必须是 `system.hello`。

每个 request/response Envelope 必须含 `generationCredential`。Core 在任何 dispatch 前使用常量
时间比较验证当前 credential；缺失、错误、旧 generation 和重放均返回或触发
`GENERATION_CREDENTIAL_MISMATCH`，错误 DTO 不回显任何 credential。Rust 在交付 response 前同时
验证 generation ID、credential、request ID/name 和 negotiated version；不匹配即 transport fatal，
终止并回收当前完整进程树。新 generation 创建新的 pipe、credential、stderr reader 和 Snapshot
cache；旧 stdout/stderr/reader 回调只持有旧 generation 私有资源，join 完成前不得发布新 generation。

## 4. stderr 持续排水与脱敏

spawn 返回三条 pipe 后立即把 stderr 所有权移入专用 reader thread，持续读取 4096-byte chunks，
支持无换行长输出、任意 UTF-8 分片、非法 UTF-8、二进制污染和 EOF。reader 不等待行边界，使用
lossy UTF-8 仅生成受控诊断记录；原始字节不进入用户可见面。

每 generation 只保留最近 64 KiB 脱敏文本，单条记录最多 4096 bytes；超过上限删除最旧记录并
累计 `droppedBytes`/`droppedRecords`，单条超限累计 `truncatedRecords`。统计使用饱和整数，重复
finish/close 幂等。日志值先按 ASCII 大小写不敏感规则脱敏 credential、`token`、
`Authorization`、`cookie`、常见 key/secret/password 字段和当前进程环境变量的非空值；聊天/
prompt/message/content 字段整体替换为 `[REDACTED]`。输出只含 generation ID、PID、稳定计数和
脱敏片段，不含裸环境变量或平台资源标识。

reader 在正常退出、Core crash、spawn 后初始化失败、protocol fatal、deadline 强杀和 Tauri
shutdown 后都必须由 runtime 显式 join；pipe read failure 记录 `STDERR_READ_FAILED` 后仍进入同一
cleanup。stderr EOF 是可观测终止事实，不单独改变 Supervisor 状态。

## 5. 故障矩阵

| 场景 | 稳定码 | 结果 |
|---|---|---|
| major 不兼容 | `PROTOCOL_MAJOR_MISMATCH` | handshake fatal，不 initialize，不自动无限重启 |
| capability/minor 无交集 | `CAPABILITY_NEGOTIATION_FAILED` | handshake fatal |
| credential 缺失/错误/旧/replay | `GENERATION_CREDENTIAL_MISMATCH` | generation fatal，完整树回收 |
| stdout 前缀/后缀污染 | `STDOUT_FRAMING_POLLUTION` | transport fatal |
| 非法 header/payload、空帧 | `INVALID_FRAME` | transport fatal |
| 非法 UTF-8/JSON | `INVALID_UTF8` / `INVALID_JSON` | transport fatal |
| 超大帧 | `FRAME_TOO_LARGE` | transport fatal，8 MiB 上限 |
| 半 header/payload EOF | `INCOMPLETE_FRAME` | transport fatal |
| clean stdout/stderr EOF | `STDOUT_EOF` / `STDERR_EOF` | stdout 未满足响应时 fatal；stderr 只记录 |
| request deadline | `REQUEST_DEADLINE_EXCEEDED` | 原 deadline 起算，强制完整树回收 |
| Core crash | `CORE_CRASHED` | 交回 Supervisor 当前 generation failure |
| pipe read/write failure | `TRANSPORT_READ_FAILED` / `TRANSPORT_WRITE_FAILED` | transport fatal |
| writer queue closed | `WRITER_QUEUE_CLOSED` | Core fatal |
| shutdown during handshake/initialize | `SHUTDOWN_DURING_HANDSHAKE` / `SHUTDOWN_DURING_INITIALIZE` | 同一 stop/finalize 路径 |

平台原生错误只在 platform backend 映射为既有稳定 `PlatformError`；公共 IPC DTO 不暴露 native
handle/fd/signal/PID/PGID。错误 message、details、Debug 和测试断言均不得包含 credential 或 secret。

## 6. Timeout、资源上限与三平台责任

- hello 3 秒；initialize 接受 5 秒；readiness watchdog 30 秒；shutdown 3 秒；完整树停止 5 秒。
- frame payload 最大 8 MiB；writer queue 32；stderr read chunk/record 4096 bytes；缓存 64 KiB。
- Windows x64、macOS arm64、Linux x64 在同一最新 HEAD 分别运行 native Rust/Python lifecycle，
  bundled Python、shared lock、RuntimeLocator、ManagedProcessTree、协商、credential、initialize/
  readiness、health、Snapshot、stderr、shutdown、强制整树回收、锁立即重获和隔离清单门禁。
- workflow 的 apt/download/build/test 保持有界 timeout、retry、cancel-in-progress；Linux Xvfb 只算
  CI 环境，不描述为真实 X11/Wayland 设备验收。

## 7. 退出条件与独立回退

退出条件：本文列出的协商、credential、stderr、framing/EOF/deadline/crash/queue/shutdown 竞态
测试全部通过；三平台最新实现 HEAD 与 accepted 文档 HEAD 的 Unit/UI、platform foundation 全绿；
完整树、pipe、fd/handle、reader/writer/init thread、锁和临时目录零残留；真实 `data/`、`runtime/`
内容清单前后相同；P0/P1 为 0；PR 保持 Draft，WP-1C-04 保持 planned。

独立回退：依次 revert accepted 文档、实现/修正提交和 activation 提交，恢复 WP-1P-06 的兼容
最小握手路径；保留基础 framing、Snapshot、RuntimeLocator、三平台进程树/共享锁/窗口 backend，
不删除或改写真实 `data/`、`runtime/`、普通 POSIX lock 或用户资源。

## 8. Accepted 记录（2026-07-24）

提交边界：activation `07f7afc`；协议/credential `079280f`；stderr 门禁 `f36f04d`；分片脱敏
修正 `a1c07ca`；握手/EOF/后缀污染分类 `b5ab652`；Linux Cargo 下载重试与 step timeout
`af79255`。Draft PR #147 在最新实现 HEAD
`af7925572a53d693cf12ab02c93f6a6bfab0cf9f` 保持 Draft。

最新实现 HEAD 的权威证据：

- Unit/UI run [`30074854468`](https://github.com/Rvosy/Sakura/actions/runs/30074854468) 全绿；
- pull_request platform run [`30074854406`](https://github.com/Rvosy/Sakura/actions/runs/30074854406)
  的 Windows x64、macOS arm64、Linux x64 全绿；
- push platform run [`30074851836`](https://github.com/Rvosy/Sakura/actions/runs/30074851836)
  的 Windows x64、macOS arm64、Linux x64 全绿。

协议证据：Python/Rust 公共版本固定为 `2.1`，支持 `2.0..2.1`；测试覆盖完全匹配、minor 降到
`2.0`、major 不兼容、minor 无交集、缺失 required、未知 optional、重复/非法 capability DTO、
hello 前消息、重复 hello、协商失败后禁止 initialize，以及 hello/initialize 中 shutdown。required
capability 按 Core 冻结顺序确定返回，不按 OS 分叉。

credential 证据：每次 Rust launch 从 Windows `RtlGenRandom` 或 POSIX `/dev/urandom` 生成独立
128-bit 值，在公共 frame 前经当前 stdin pipe 私有 bootstrap 传递；每个 response 在交付前验证
generation ID、credential、request identity 和协商版本。缺失、错误、旧 generation、stale response
和 replay 均拒绝；跨 generation uniqueness、旧 response 强制整树回收，以及 Debug、错误、Snapshot、
stderr 和测试输出不含 credential 均有可执行断言。

stderr 证据：reader 在 bootstrap 前启动，以 4096-byte chunk 持续排水；覆盖普通/多行、逐字节
分片 UTF-8、非法 UTF-8、NUL/二进制、无换行超长输出、超过 1 MiB flood、EOF、read failure、
重复 finish、正常退出和 Core crash。每 generation 只保留 64 KiB 脱敏尾缓存；超长记录 fail-closed
丢弃原文并稳定累计 `droppedBytes`、`droppedRecords`、`truncatedRecords`。credential、token、
Authorization、cookie、key/secret/password、prompt/message/content、聊天内容和非空环境值在完整
有界行上脱敏；跨 read chunk secret 回归已关闭。

transport 证据：双端 codec 覆盖任意 frame 分片/合并、空帧、8 MiB 上限、非法 UTF-8/JSON、
半 header/payload EOF、pipe read/write failure 和 writer queue closed；真实 fixtures 覆盖 stdout 前缀/
后缀污染、旧 credential response、request deadline、Core crash、stdin/stdout/stderr EOF 顺序、
忽略 shutdown 与强制整树回收。稳定分类包括 `PROTOCOL_MAJOR_MISMATCH`、
`CAPABILITY_NEGOTIATION_FAILED`、`GENERATION_CREDENTIAL_MISMATCH`、
`STDOUT_FRAMING_POLLUTION`、`INVALID_FRAME`、`INVALID_UTF8`、`INVALID_JSON`、
`FRAME_TOO_LARGE`、`INCOMPLETE_FRAME`、`STDOUT_EOF`、`REQUEST_DEADLINE_EXCEEDED`、
`CORE_CRASHED`、`TRANSPORT_READ_FAILED`、`TRANSPORT_WRITE_FAILED`、`WRITER_QUEUE_CLOSED`、
`SHUTDOWN_DURING_HANDSHAKE` 和 `SHUTDOWN_DURING_INITIALIZE`。

本机 Windows 证据：Python Core Host 定向最终 44 passed；Rust `core_host_` 最终 22 passed；Rust
完整 108 passed / 14 ignored fixture；UI 379 passed；fmt、Debug/Release locked build、py_compile、
YAML safe-load 和 diff check 通过。完整本机 Unit 为 958 passed / 6 skipped / 6 failed / 12 errors；
失败精确来自环境残留 `F:\Projects\Sakura`/`.pytest-basetemp` 和无权限 `D:\`，本次 diff 未触及
对应 backchannel/storage/TTS 模块；最新 HEAD GitHub Unit job 为权威全绿证据。

真实 Windows Shell + bundled Python Core 完成 normal、Shell crash 和 lock reacquire 三轮，保护范围
为仓库根 `data/` 与 `runtime/` 的全部递归 path/type/file length/content SHA-256；before/after canonical
manifest 均为 `b4d51258a488d00413689c8c963a1210f243825f3444dafb633a406819596b2d`。CI 每个平台还通过
shared lock、RuntimeLocator、正式 ManagedProcessTree、hello/initialize/readiness/health/Snapshot、
stderr fixtures、protocol shutdown、根/后代/pipe/fd/handle/reader/writer/init thread/临时目录清理，
以及锁释放后立即重获。`ca8fea3..af79255` tracked diff 不含 `data/`、`runtime/` 或 `.superpowers/`。

CI 暴露并关闭一个外部下载问题：push run `30074514479` 的 Linux runner 在 crates.io `bytes`
下载收到 curl 18 partial file；没有以同 SHA 的 PR 绿灯代替失败。workflow 现使用单次 180 秒、最多
3 次的 Linux Cargo build retry，并为 build/archive/format/platform/shared-lock/process-tree/locator
步骤设置分钟级 timeout；最新 push/PR run 均证明修正有效，concurrency 继续 cancel-in-progress。

审查确认公共 IPC/Snapshot/Assistant 层没有 Win32 handle、POSIX fd/signal/PID/PGID；平台差异仍只
位于 RuntimeLocator、ManagedProcessTree、共享锁和随机源边界。没有修改 CoreSupervisor 状态机、
generation/restart budget、Snapshot/shared-data schema、用户可见功能、Assistant、聊天、Memory、
插件、MCP、Tools、TTS、浏览器、截图或主动互动。P0/P1 为 0，WP-1C-04 具备独立激活前置但仍为
`planned`。
