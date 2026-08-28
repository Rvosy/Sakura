---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
partially_superseded_by: 0037-replaceable-default-plugins-and-isolated-python-runtimes
updated: 2026-08-04
---

# ADR-0005：Runtime v2 通过无 Qt 薄 Assistant Adapter 接入既有领域代码

> 状态：Accepted
> 决策日期：2026-07-25
> 适用范围：Python Core Host 对 Sakura Assistant 角色、Provider、`AgentRuntime` 和
> `ChatPipeline` 的初始化、公开状态投影与关闭

## 背景

ADR-0001 和 ADR-0002 已经把 Tauri/Rust 确立为桌面生命周期根，并建立受监管的 Python Core
Host。Runtime v2 随后需要第一个真实 Assistant 消费者，证明该 Core 生命周期能够承载现有领域代码，
而不是长期只由 Fake Core 或测试 fixture 消费。

legacy `build_initial_app_context` / `AppContext` 同时聚合 Qt UI、Memory、Tools、插件、MCP、TTS、
存储和其他服务。直接复用或简单包一层 headless 开关，会把尚未迁移的领域、隐式写入和 Qt 生命周期
带入 Core Host。另建 Assistant sidecar 则会增加第二个 IPC、进程树和生命周期所有者，与既有
Supervisor 边界冲突。

## 候选方案

### 方案 A：懒导入的薄 Assistant Facade 与专用配置投影

在既有 Core Host worker 内构造只包含当前角色、基础 Provider、`AgentRuntime` 和
`ChatPipeline` 的 `AssistantSession`。使用无 Qt、只读的专用配置投影，初始化阶段不运行聊天、
不访问 Provider 网络，也不启动其他领域组件。

### 方案 B：复用或 headless 包装 `build_initial_app_context` / `AppContext`

该方案复用现有聚合入口，但同时导入 Qt，并初始化或写入 Memory、Tools、插件、MCP、TTS 和存储。
它无法保持当前 Work Package 的资源、数据和生命周期边界。

### 方案 C：Assistant sidecar 或第二 Python 进程

该方案把 Assistant 放入 Core Host 之外的新进程，需要新增进程监管、IPC、错误恢复和关闭所有权。
现有资料没有证明它比直接消费受监管 Core Host 更小或更安全。

## 决策

采用方案 A：

- `app/config/core_config_reader.py` 是 Core Host 使用的唯一纯、显式 raw 配置投影；它只读，不执行
  migration、normalize-and-save 或隐式修复。
- `app/core_host/assistant_adapter.py` 是 Core Host 内唯一 Assistant Facade，负责构造和关闭
  `AssistantSession`。
- Adapter 复用既有 `CharacterRegistry`、Provider、`AgentRuntime` 和 `ChatPipeline` 业务语义，
  但只初始化当前切片需要的依赖。
- 所有领域 import 在 initializer worker 内懒加载；hello/control 路径保持无 Qt 和轻量。
- Adapter 只拥有自己实际构造的资源，并按创建反序幂等关闭。Rust Snapshot、generation credential、
  Supervisor 或进程树不属于 Adapter。
- Provider 网络验证、聊天执行、Memory、Tools、MCP、插件和 TTS 由各自真实消费者 Work Package
  独立接入，不通过扩张 Adapter 初始化范围提前实现。
- 不建立第二个 Assistant 进程、第二个 stdout writer 或第二生命周期根。

### 2026-08-04：Memory embedding 隔离澄清

WP-4-01 真实 Windows 冷启动证明，`SentenceTransformer` 导入 PyTorch 时会连续占用 GIL，超过 Core
Router 的设置请求和 Supervisor Snapshot deadline。普通 Python 线程无法满足“Memory 初始化不阻塞聊天
与控制面”的既有契约。因此本 ADR 对“第二 Python 进程”的边界作如下澄清：

- 仍禁止第二 Assistant、第二 Core Host、sidecar 协议和独立生命周期根；角色、Provider、`AgentRuntime`、
  `MemoryStore`、Qdrant、SQLite、配置与业务状态继续由唯一 Core generation 拥有。
- 允许 Core generation 为不可在活动解释器内安全冷加载的本地推理依赖创建窄子进程。本次子进程只拥有
  固定 HuggingFace embedding 模型，使用私有 Pipe 返回向量，不拥有 Memory 数据或公共协议。
- 子进程必须由 Memory owner 创建、取消和回收，继承 Supervisor 管理的进程树；启动、请求、关闭均有界，
  失败降级为空召回。它不是可独立恢复、寻址或长期驻留的服务。

该澄清保留方案 A 的单一 Assistant Facade 和生命周期根，同时避免方案 C 所述的第二套 Assistant IPC、
监管和恢复机制；如果未来把 Memory 数据或 Assistant 会话移入独立服务，仍须新增 ADR 替代本决策。

精确配置输入、readiness code、Snapshot DTO、秘密投影和验收条件继续由
[`WP-3-01` spec](../specs/runtime-v2/WP-3-01-qt-free-assistant-adapter-readiness.md) 约束。

## 后果

收益：

- 第一个真实 Assistant 消费者复用 ADR-0001/0002 已建立的生命周期、generation 和 IPC 边界。
- legacy Qt 与 Runtime v2 可以继续共享领域语义，同时避免把 Qt UI 和未迁移服务带入 Core Host。
- 配置读取、公开投影和资源关闭的所有者明确，初始化失败不会要求第二套恢复机制。

代价：

- 需要维护一个窄配置投影和一个明确的 Adapter，而不能直接复用 legacy 聚合入口。
- 新增可关闭依赖时，必须显式登记到 Adapter 的创建与逆序关闭流程。
- Adapter 不是未来所有 Assistant 能力的通用容器；每个新领域仍需证明真实消费者、数据所有权和
  故障语义。

## 状态与后续变更

WP-3-01 已完成无 Qt Adapter/readiness 验证，WP-3-02 已在同一边界上接入真实聊天，因此本决策为
`Accepted`。执行状态和验收事实只见
[`work-packages.md`](../plans/runtime-v2/work-packages.md) 与 records，不在本 ADR 重复维护。

未来若必须改为独立 Assistant 进程、重新引入聚合 `AppContext`，或改变 Session/配置投影的最终
所有权，应创建新的 ADR 并 `supersedes: ADR-0005`，不得只在 spec 中改写方案理由。
