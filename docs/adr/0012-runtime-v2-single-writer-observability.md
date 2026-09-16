---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-10
---

# ADR-0012：Runtime v2 使用 Rust 单写者统一运行日志

## 背景

Legacy Python 已有结构化 JSONL、脱敏和 10 MiB/5 备份轮转，但 Runtime v2 Shell 主要依赖零散
`eprintln!`。Core stderr 虽持续排水并保留 64 KiB 脱敏尾部，正常生产生命周期通常不会持久化；Memory
初始化和 interaction latency 又各自打开专用 JSONL。后续 MCP、插件、TTS 和平台能力迁移若继续各写
文件，会产生并发轮转、顺序、退出、隐私和故障隔离上的多个真相源。

## 候选方案

1. 让 Python Core 继续写既有文件，Rust/WebView 通过 IPC 转发。共享锁已由 Rust 获取，而 Core 会跨
   generation 重建；该方案无法在 Core 启动前和崩溃后覆盖 Shell，也会让多个 generation 竞争轮转。
2. Python、Rust 和 WebView 各写自己的文件。实现直接，但跨层关联、轮转和支持取证都不稳定。
3. 引入远程 telemetry 或完整 tracing collector。超出本地桌面迁移需要，并改变隐私与部署边界。
4. Rust 在共享应用锁成功后启动唯一文件写入服务，Python 和 WebView 只提交受控记录。

## 决策

采用方案 4。

- Rust 是 Runtime v2 中唯一可打开、追加、轮转和刷新 `data/logs/sakura-runtime.log` 的组件。日志服务
  必须晚于共享应用锁成功启动；冲突实例不得创建目录、文件或备份。
- 服务使用 1024 条有界、非阻塞内存队列和单一 worker。warning/error 在拥塞时可淘汰较低级记录；
  所有丢弃通过后续聚合记录报告。日志不可用、队列满或 worker 退出均不得阻塞 UI、Core health 或退出。
- Python Core 使用独立 256 条有界 stderr bridge，输出
  `SAKURA_RUNTIME_LOG_V1\t<json>`；stdout 仍只承载 framed IPC。Rust drainer 校验并补齐当前 run、
  generation 和 Core PID。普通 stderr 只产生首个告警与退出摘要，原 64 KiB 脱敏尾缓存继续用于故障诊断。
- WebView 只能调用严格 `deny_unknown_fields` 的批量诊断 command。Rust 注入真实窗口身份，前端不得传递
  invoke 参数、返回值、聊天正文、console 内容、异常原文或任意 attributes。
- 所有来源在持久层前经过同一 schema、等级、大小和脱敏门。环境变量
  `SAKURA_RUNTIME_V2_LOG_LEVEL=error|warn|info|debug|trace` 只改变密度，不改变隐私规则。
- 旧 `memory-initialization.jsonl` 保留但停止追加；interaction latency 继续受 debug feature 控制，但
  改为提交统一日志事件。

## 统一日志扩展（2026-09-06）

普通运行日志统一由宿主 `RuntimeLogService` 管理。插件依赖日志服务，因此日志服务在插件加载前可用，
在插件关闭后仍能刷新；将它注册为普通插件会额外需要启动和故障时的备用 writer，不采用该方案。

此前的“单文件”扩展为“同一服务按来源分文件”：宿主/Core/WebView 写 `sakura-runtime.log`，
插件主动提交写 `sakura-plugins.log`。共用同一有界队列、全局序号、写入线程、格式化器、轮转实现和
查看器缓冲，仅文件句柄、大小及失败状态独立。既有单实例锁、generation 校验、背压及退出期限不变。

此前只允许固定消息的普通日志事件增加可选自定义内容；Rust、Python、前端和插件通过薄接入层使用
同一事件模型，保留固定目录以维持原有业务显示。文件行形状统一为 `[时间] [频道] [等级] 消息 │ 字段`，
清洗、限长与字段预算在出进程前及最终投影前执行，UI 不接收原始对象。旧 Python 文件回退、GUI 缓冲及
Mem0 初始化 JSONL writer 删除，原日志文件不迁移、不覆盖。

查看器 DTO 升为 v3，在同一快照增加可信来源、插件身份及文件失败状态；插件页只做筛选。
Agent Trace 的正文记录、独立存储与生命周期不参与本次改造。接口和限额以
[插件运行时规范](../specs/runtime-v2/sakura-plugin-runtime-v4.md#41-统一宿主日志)为准。

## 后果

单文件可以按 run、generation、PID、request 和 operation 串联迁移故障，并保持旧无版本 JSONL 与 v2
记录共存。代价是 Rust 日志服务成为需要严格测试的基础设施，且高压时允许有据可查地丢弃低级事件。

本决策不提供日志读取 API、查看器、设置、导出、上传、遥测或 Repair。它们仍由 WP-5-06 单独设计，
且不得通过回读当前 writer 绕过权限与隐私审查。
