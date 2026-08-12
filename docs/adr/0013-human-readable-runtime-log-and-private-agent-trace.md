---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-12
---

# ADR-0013：分离人类可读运行日志与私密 Agent Trace

## 背景

ADR-0012 建立了 Rust 单写者、跨层事件汇聚、背压和故障隔离，但把面向人的运行日志编码为 JSONL。
实际诊断时，固定 schema、关联字段和被摘要的正文挤占主要阅读空间；Prompt 优化又需要另一类信息：最终
Provider payload 的消息顺序、历史与记忆正文、工具定义、模型原始结构化回复，以及解析或修复造成的
最终结果变化。把两种目标继续塞进一个文件，会同时破坏运行故障阅读和 Prompt 隐私边界。

## 候选方案

1. 继续扩展 Runtime JSONL，并用查看器渲染。它引入当前范围明确排除的读取 UI，原始文件仍不便直接读，
   而且运行日志的严格脱敏与 Prompt Trace 的正文保留目标互相冲突。
2. 只恢复 Legacy Python 文件日志。它失去 Shell/Core/WebView 的单写者顺序，并重新引入跨进程轮转竞争。
3. 保留 Rust 单写者事件管线，但把运行日志编码改为固定中文文本；另由 Python Core 维护本地私密 Agent
   Trace，按 operation staging 后成块提交。
4. 引入 OpenTelemetry collector 或远程平台。它改变部署、隐私和联网边界，超出本地桌面需求。

## 决策

采用方案 3。

- ADR-0012 关于共享锁后启动、Rust 唯一 writer、有界非阻塞队列、Core stderr bridge、WebView 边界、
  等级和故障隔离的决策继续有效；本 ADR 只替代其“v2 JSONL 持久格式”和“旧 JSONL 可混写”结论。
- `data/logs/sakura-runtime.log` 是面向人的 UTF-8 文本，每行使用
  `[HH:MM:SS] [频道] 固定中文消息 │ key=value`。Rust 仍是该文件唯一 writer，属性只允许稳定、短小、
  已脱敏的诊断值。首次使用新格式前把旧 `sakura-runtime.log*` 原样归档，禁止格式混写。
- `data/logs/sakura-agent-trace.log` 是仅存本机的私密诊断流。它不属于 Runtime writer，不经 Core stderr
  bridge；`AgentTraceRecorder` 在 Python Core 内记录模型边界。每个 request/reply 是一个由 `====` 包围、
  字段对齐、section 分隔的人类可读文本块；结构化模型值在块内以缩进 JSON 展开，但整个文件不声明为
  JSON、JSONL 或回放协议。
- Trace 先写 operation 私有 staging，终态后在进程内提交锁下把整个 operation 作为一个块追加。崩溃后
  下次启动恢复 staging，并给恢复记录添加 `status: interrupted`。staging 继续使用内部紧凑 JSON，活动
  文件不新增结构化 sidecar。这样并发 operation 不会交错，也不让单次模型调用阻塞等待全局日志 I/O。
- Trace 默认开启。普通用户正文、历史、动态上下文、记忆、工具参数/结果和模型输出按实际发送/收到内容
  保留；静态 system/persona 只留 section ID 与字符数。凭据、URL userinfo 和二进制正文无条件删除。
- 两种日志的任何 open/write/flush/rotate/recovery 失败都必须吞掉，不得改变 chat、Tools、Core health、
  cancellation 或 shutdown 的产品结果。

## 后果

运行故障可以直接用文本工具阅读，Prompt 优化可以准确看到一次模型调用的上下文构成和成本来源；两者的
读者、保留策略和隐私语义不再互相妥协。代价是本地会存在一份明确包含对话与记忆正文的高敏感文件，设置
页必须清楚说明风险，测试也必须同时证明“普通正文存在”和“凭据/二进制正文不存在”。

本决策不增加日志查看器、目录按钮、清除按钮、读取/导出 API、远程 telemetry、结构化 Runtime sidecar、
聊天历史恢复或请求回放能力。
