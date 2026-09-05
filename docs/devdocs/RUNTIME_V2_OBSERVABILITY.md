---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-09-06
---

# 运行日志与 Agent Trace

Sakura 把运行诊断和模型正文分开保存。运行日志适合定位生命周期与失败，Agent Trace 用于检查实际模型输入和回复。

## 运行日志

`desktop/src-tauri/src/runtime_log.rs` 中的 `RuntimeLogService` 统一写入 `sakura-runtime.log` 和
`sakura-plugins.log`，按可信来源分流；两份文件共用队列和写入线程，独立轮转。生产配置为：

- 队列容量 1024；
- 单条记录最多 4 KiB；
- 活动文件最多 10 MiB；
- 保留 5 个轮转文件；
- 250 ms 刷盘间隔，退出最多等待 500 ms。

事件格式为：

```text
[HH:MM:SS] [CHANNEL] 中文消息 │ key=value
```

固定事件字段使用受限标量；自定义消息可以附带有界 JSON 字段。正文、Prompt、工具参数、绝对路径、
generation credential 和 API Key 不得进入运行日志或插件日志。

### Rust 事件

Shell、Gateway、窗口后端、截图、音频和进程监管直接提交固定 event 名与字段。warning/error 优先进入队列；队列拥塞时低级别记录可以丢弃，但必须留下有界的 dropped 诊断。

### Python Core 事件

Core 启动时安装 `RuntimeLoggingBridge`。`app.core.runtime_log.log_event`、`log_message` 和 Core 标准
logger 被转换成带前缀的 stderr 帧：

```text
SAKURA_RUNTIME_LOG_V1\t<json>
```

Rust 验证大小、类型、generation 和字段后再写文件。无法识别的 stderr 保留为受限进程诊断，不能破坏 stdout 协议流。

Plugin Runner 的 stdout 只用于 IPC，插件的普通 stdout 会被重定向到 stderr；插件不能直接打开统一运行日志。
当前 Plugin Runtime 通过公开状态和稳定 `reasonCode` 报告进程、依赖与 Service 失败。

### 插件日志

插件通过 `context.get("sakura.host.logging")` 调用 `debug/info/warning/error(message, *, fields=None)`。
SDK 本地队列后台提交到 Core，再经同一 bridge 进入 Rust 服务；返回值只表示本地接收，日志失败不得改变
业务结果。插件身份由当前 RPC 调用上下文绑定，不能由插件 payload 指定。

插件主动记录进入 `sakura-plugins.log`；宿主报告的插件加载、依赖与进程故障仍进入 `sakura-runtime.log`。
运行日志窗口的“插件”页支持按插件筛选。SDK 和 Core 不打开日志文件，断连也不回退到独立 writer 或 GUI
缓冲；不自动捕获插件的 Python 标准 logger、print、stderr 或外部程序输出。

新插件建议记录初始化结果、实际配置变化、失败和清理异常，高频轮询使用 `debug` 或不记录。只记录稳定
原因码、异常类型、耗时和计数，不传原始异常或业务正文。编写方式见[插件日志指南](SAKURA_PLUGIN_SDK.md#写入插件日志)，
预算、清洗及兼容入口见[统一宿主日志合同](../specs/runtime-v2/sakura-plugin-runtime-v4.md#41-统一宿主日志)。

### WebView 事件

前端的 runtime diagnostics 模块批量提交固定事件。通用 invoke 只记录 command、outcome、稳定 code、耗时、operation ID 和 revision，不检查 args、result 或错误正文。诊断提交失败必须被吞掉，业务 Promise 语义保持不变。

## Agent Trace

`app.agent.trace.AgentTraceRecorder` 写入 `data/logs/sakura-agent-trace.log`。默认设置由 `agent_trace.enabled` 控制。每个 operation 先写 staging，终态后在 commit lock 内追加完整 Request/Reply 文档。

模型客户端在发送最终 payload 前生成平行 provenance，然后删除 message 内部的 `_sakura_trace_provenance`。Trace 顺序必须与真实 Provider payload 一致。

一次 operation 中的模型重试、工具循环、截图请求和回复修复共用 trace ID，`model_call` 单调增加。Memory 自动整理使用独立 `memory-curation-*` operation，不能追加到已经结束的聊天 trace。

活动文件在下一完整 operation 会超过 32 MiB 时轮转。总量上限 512 MiB，保留期 30 天。崩溃遗留 staging 在下次启动写成 `interrupted`。

Trace 会保留普通聊天、Memory 和工具内容。凭据键、已知 secret、URL userinfo、data URL 和 bytes 正文必须移除；超大单值保存大小、SHA-256 及头尾片段。

## 事件设计

新增事件时先回答两个问题：读者能否据此定位故障，字段是否可能泄漏用户内容。能用稳定枚举和数量表达时，不要记录自由文本。

同一交互使用 `operationId` 关联 Rust、Core、WebView 与 Trace。generation、request 和 revision 用于丢弃迟到状态，不应拿完整 credential 做关联字段。

## 验证

下面使用 macOS/Linux 路径；Windows 使用 `.\runtime\python.exe`。

```bash
./runtime/bin/python3 -m harness run journey-observability
./runtime/bin/python3 -m harness run journey-agent-trace
node --test desktop/frontend/tests/runtime-diagnostics.test.js
cargo test --manifest-path desktop/src-tauri/Cargo.toml runtime_log
```

隐私测试应向 API Key、聊天正文、工具参数、绝对路径和 credential 注入独立 sentinel，再分别扫描运行日志与 Trace。运行日志不得命中任何正文 sentinel；Trace 可以包含普通正文，但不能包含凭据 sentinel。
