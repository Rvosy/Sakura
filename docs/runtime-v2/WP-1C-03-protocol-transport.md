# WP-1C-03：协议协商、stderr 排水和故障 transport

> 状态：active
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
  "requiredCapabilities": ["core.initialize", "core.snapshot"],
  "optionalCapabilities": ["system.health", "system.shutdown"]
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
十六进制字符表示。credential 只通过受控启动环境 `SAKURA_CORE_GENERATION_CREDENTIAL` 传递，
不进入 argv、Snapshot、diagnostics、日志、Debug 或用户错误。Core 启动时读取并立即从自身环境
删除该变量；缺失或非法值拒绝启动。

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
