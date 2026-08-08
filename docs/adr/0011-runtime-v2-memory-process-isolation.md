---
kind: adr
status: proposed
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-09
---

# ADR-0011：Runtime v2 Memory 运行时使用 generation 私有子进程

本 ADR 只替代 ADR-0005 中“2026-08-04：Memory embedding 隔离澄清”的窄 embedding 子进程
边界；ADR-0005 的无 Qt Assistant Adapter、唯一 Core Host 和单一生命周期根决策继续有效。

## 背景

WP-4-01 最初把固定 HuggingFace embedding 模型放入 Core 管理的子进程，而 mem0 导入、Qdrant、
SQLite 和业务适配仍留在 Core。真实 Windows 冷启动日志随后证明：即使尚未创建 embedding 子进程，
`from mem0 import Memory` 本身也会连续约二十秒占用 Core 解释器，超过 Supervisor 健康检查预算。
Supervisor 因而反复替换仍在初始化的 Core generation，设置页在多个加载文案间轮换，最后收到
`REQUEST_DEADLINE_EXCEEDED`；增加页面重试或只延长一次请求 deadline 都不能恢复控制面响应。

## 候选方案

### 方案 A：继续只隔离 embedding，并延长 Supervisor deadline

该方案改动最小，但已经被实机日志证伪：阻塞发生在 embedding 创建之前的 mem0 冷导入。扩大健康预算
还会延迟真正失活 Core 的恢复，不能保证聊天、设置和退出在初始化期间持续响应。

### 方案 B：在 Core 线程中导入完整 mem0

线程可以组织状态和取消，但不能抢占持有 GIL 的原生或导入工作，仍会阻塞 Router 与健康检查。

### 方案 C：把完整本地 Memory 运行时放入 generation 私有子进程

Core 保留 `MemoryStore` 的业务策略、scope、公开 DTO、状态投影和生命周期所有权，只通过私有、有界
Pipe 调用子进程。子进程独占 mem0、Qdrant、SQLite、SentenceTransformer/PyTorch 及其本地句柄。

### 方案 D：建立可独立恢复的常驻 Memory 服务

该方案需要第二套寻址、认证、版本协商、数据锁、恢复和升级机制，超过当前缺陷的最小边界，并会形成
ADR-0001 禁止的第二生命周期根。

## 决策

采用方案 C：

- 每个 Core generation 最多创建一个由 `MemoryStore` 拥有的 Memory 子进程；它继承 Supervisor 管理的
  进程树，不可独立寻址、breakaway、跨 generation 复用或自行重启。
- Core 不再导入 mem0、Qdrant、SentenceTransformer 或 PyTorch。固定模型已安装时，Core owner 仍在
  创建后立即非阻塞预热，但 loader 线程只等待 Pipe，不承担重型 import 或 native 初始化。
- 子进程按顺序创建 embedding、Qdrant、LLM 和 SQLite，并独占相关文件句柄。Core 代理只开放
  `get_all/search/add/get/update/delete`、整理缓存清理和 LLM 热重载；不提供任意反射、文件或 SQL RPC。
- 一个代理连接串行化请求并设置启动、业务请求和关闭预算。连接中断、超时或子进程退出只让当前
  Memory 能力降级；Core Router、普通聊天、设置关闭和 `system.shutdown` 必须继续响应。
- API 设置热重载在既有子进程内替换 LLM，避免第二个进程同时打开同一 Qdrant/SQLite。需要完整重建时，
  旧 owner 必须先失效并释放句柄，不能并发拥有同一存储。
- 关闭先发送私有 close，请求未在预算内完成时只终止该 generation 明确拥有的 Memory 子进程；不得
  扫描或结束系统中其他 Python 进程。
- 子进程启动阶段通过现有 `memory-initialization.jsonl` 记录固定事件和错误类别。日志不得包含 Memory
  正文、query、路径、配置值、密钥或异常原文，也不改变公开协议。
- 本决策不迁移、不修复也不改写 `data/memory/`、旧 `memory.json`、模型缓存或公共 Memory DTO。

## 后果

收益：mem0 与 PyTorch 的冷导入不再占用 Core GIL；Supervisor 能准确判断 Core 存活，设置页可在初始化
期间关闭和重开。Qdrant、SQLite 与 embedding 位于同一进程，文件句柄和推理资源有唯一所有者。

代价：Memory 调用多一次本机 Pipe 序列化，进程启动与错误映射需要额外测试；Core 崩溃时未完成的单次
Memory 请求会随 generation 丢弃。私有代理必须显式维护允许的方法和返回值兼容性，不能依赖 mem0 对象
属性穿透。

## 回退与后续变更

整体回退本 ADR 时应先正常关闭应用，再恢复 ADR-0005 的窄 embedding 代理；回退不得删除 Memory 数据、
锁文件、模型缓存或日志。若未来需要跨 Core generation 的常驻 Memory 服务、并行写入者或公共 Memory
IPC，应新增 ADR 替代本决策，而不是扩张当前私有 Pipe。

Work Package 状态和验收结论只以
[`work-packages.md`](../plans/runtime-v2/work-packages.md) 为准。
