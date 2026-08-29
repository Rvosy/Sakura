---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-29
---

# WP-4L-01 Runtime v2 迁移可观测性规范

## 1. 范围与非目标

本规范冻结 Runtime v2 后续迁移共用的本地可观测性基础：Shell、bundled Python Core 和受控 WebView
诊断统一追加到 `data/logs/sakura-runtime.log`，默认保存 info 及以上事件，始终脱敏且不发送遥测。
执行状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

本 WP 本身不提供日志窗口、搜索、读取/导出 command、用户设置、上传、自动修复或 Runtime Repair；后续
本次运行内存查看器由 WP-5-06 独立规范，其余能力仍属于 WP-5-06。Runtime v2 不读取 Legacy
`debug.file_enabled`，不删除、截断或重写既有日志及
`memory-initialization.jsonl`。

## 2. 文件、所有权与生命周期

- 共享应用锁成功前不得创建 `data/logs`、打开日志文件或轮转；第二实例冲突路径对日志零写入。
- 锁成功后，Rust 为本次进程创建不可复用的 `run_id`，启动唯一 writer，并立即记录 Shell start。
- 活跃文件为 `data/logs/sakura-runtime.log`。在追加会使文件超过 10 MiB 时，依次保留 `.1` 至 `.5`；
  不解析、迁移或重写轮转前的旧 JSONL。
- 只有 writer worker 可以持有文件 handle。Memory、interaction latency、Core drainer、Gateway 和 Tauri
  commands 只能向服务提交事件。
- warning/error 每条刷新；其他记录最迟 250 ms 刷新。正常退出停止接收新记录并最多等待 500 ms；
  超时后放弃剩余记录而继续退出。文件打开、写入、刷新或轮转失败不得改变产品控制流。

## 3. v2 JSONL 契约

每个新记录为单行 UTF-8 JSON，包含：

| 字段 | 契约 |
|---|---|
| `schema_version` | 固定整数 `1` |
| `timestamp` | UTC RFC3339，至少毫秒精度 |
| `run_id` | 当前 Rust 进程生成的有界 opaque ID |
| `sequence` | writer 接受顺序内单调递增的正整数 |
| `source` | `rust`、`core` 或 `webview` |
| `pid` | 真实来源 PID；WebView 使用 Shell PID |
| `severity` | `error`、`warning`、`info`、`debug`、`trace` |
| `verbosity` | 事件密度等级，使用 `error|warn|info|debug|trace` |
| `channel`、`event` | 受控 ASCII 点分标识 |
| `message` | 由 Rust/Core 注册表选择的固定说明，不接受异常正文 |

可选关联字段为 `generation_id`、`generation_number`、`core_pid`、`request_id`、`operation_id`、
`action_id` 和 `trace_id`。`attributes` 只能包含该事件批准的标量、计数、类型、长度或枚举；单条编码后
连换行最多 4096 bytes。超限时先删除 attributes，再以固定 `record_truncated` 属性落盘；无法安全收敛
则丢弃并计入聚合计数。只接受当前 v1 记录，不解析或升级其他格式。

## 4. 等级、队列与故障隔离

- `SAKURA_RUNTIME_V2_LOG_LEVEL` 只接受 `error|warn|info|debug|trace`，缺失或非法时为 `info`。
- Rust 队列容量固定 1024，提交永不等待文件 I/O。满载时 warning/error 可淘汰最早的 info/debug/trace；
  低级事件直接丢弃；若队列全是 warning/error，新高级事件也可丢弃，但必须累计来源与等级计数。
- worker 恢复容量后写一条 `runtime.log.records_dropped` 聚合事件，不为每次丢弃递归写日志。
- Python bridge 容量固定 256，生产线程只做有界校验和非阻塞入队；独立线程写 stderr。拥塞、broken
  pipe 或 shutdown 都不回传到聊天、Tools、Memory 或 health。
- Rust writer 故障只允许安全的 stderr 首次提示；不得重试 flood、弹窗、崩溃或阻止共享锁释放。

## 5. 脱敏与来源边界

进入持久层前必须删除凭据、generation credential、环境值、绝对路径、Prompt、对话正文、记忆正文、
工具参数/结果、HTTP body/header 和异常原文。字段名包含 `body/content/input/output/payload/arguments`，或
`authorization/cookie/token/secret/password/api_key/credential` 时，不得保留原值；只允许保存类型、
字符/字节数、元素数和获准键名计数。即使 trace 等级也不得放宽。

字符串需移除 ANSI、URL userinfo、Windows/UNC/POSIX 绝对路径和 secret-shaped 片段；未知 attributes
键在 Rust 最终门丢弃。测试 sentinel 必须跨 Python、Rust、WebView 和普通 stderr 路径扫描为零命中。

## 6. Python Core bridge

- Core 进程初始化最早阶段安装 bridge，并把 `app.*` 标准 logging 与现有 `log_event` 镜像为
  `SAKURA_RUNTIME_LOG_V1\t<json>`。Core 模式下 `log_event` 不直接打开 Runtime 文件。
- bridge JSON 只含已验证的 `severity/verbosity/channel/event/message`、可选关联 ID 和安全 attributes；
  每行不超过 4096 bytes。它只写 `sys.__stderr__.buffer`，不写 stdout。
- 聊天 `operationId` 必须在执行线程进入现有 interaction context，使 Agent、模型、Tools、Memory 和未来
  领域 `log_event` 自动携带 `operation_id`；终态后清理 context。
- 未捕获 Python 异常只记录稳定异常类型和边界 code，不记录 `str(exception)` 或 traceback 正文。

## 7. Rust Core stderr drainer

- drainer 对任意字节分片按行重组。仅精确前缀和合法 bridge schema进入统一 writer；非法或超限 prefixed
  行按普通 stderr 处理，绝不反序列化为任意 attributes。
- Rust 覆盖记录中的来源身份，注入当前 `run_id`、generation ID/number、真实 Core PID；旧 generation
  drainer 晚到记录保留其原 generation，不得被标成当前新 generation。
- 普通 stderr 继续进入现有 64 KiB 脱敏尾缓存。每个 generation 只写首个
  `core.stderr.detected` warning，退出时写一条包含总行数、总字节数和截断标记的摘要，避免 flood。

## 8. WebView diagnostics

批量 command 每批 1–64 条，只接受 `level/event/command/outcome/code/elapsedMs/operationId/revision`
以及规范列出的少量标量字段，拒绝额外字段、非法枚举、非有限时长和超长 ID。Rust 从 Tauri Window 注入
真实 `window_label` 与 Shell PID。前端包装器只观察 invoke 的开始、成功、稳定失败 code 与耗时，并原样
保留 Promise 返回值和拒绝语义；日志发送失败必须被吞掉。

禁止发送 invoke 参数、返回值、聊天/设置文本、异常对象或 message、console 内容、DOM 内容及任意
attributes。批量器在页面卸载时只做 best-effort，不延迟窗口关闭。

## 9. 首批事件与验收

首批至少覆盖 Shell start/ready/stop、Core spawn/hello/initialize/readiness/restart/stop、IPC request
成功/失败/取消、设置窗口 open/close、聊天 send/terminal、Memory、Tools 以及 Python/Rust/WebView 未捕获
错误。事件密度可逐步扩展，但新增字段必须先满足本规范的固定注册和脱敏规则。

自动测试必须覆盖当前格式、并发 sequence、轮转、过载、写失败、退出刷新、字段拒绝、任意分片、非法
bridge、stderr flood、stdout 零污染、generation 重建/晚到、operation 关联和前端语义透明。真实 Windows
验收使用隔离 assistant root 完成启动、聊天、设置、Tools、Core crash/recovery、退出和第二实例冲突；
扫描 sentinel、正文、工具参数、路径及 generation credential 为零，真实 `data/**` 清单不变且无残留。
