---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-18
---

# WP-3-02：无 UI 的真实聊天 Core 垂直链

## 当前执行入口

桌面 IPC、Mobile 与无界面调用共用 RealChatBoundary。Core 固定当前角色、会话、取消和数据所有权，
再调用普通 `sakura.assistant` 服务；模型策略运行于插件进程。Core 不导入默认 Agent、Prompt 或 ChatPipeline，
服务停用后不回落到本地实现。服务与生命周期见 [Assistant 插件边界](assistant-plugin-boundary.md)。
旧 chat_executor 字段被忽略，不建立执行器登记表或隐藏路由。

```text
桌面 Rust Gateway / Mobile / 无界面入口
  → RealChatBoundary 受理、取消与 operation registry
  → 固定角色并写入 Timeline 输入
  → BoundAssistant.begin / poll / result
  → Assistant 插件的模型、上下文和工具策略
  → Core 验证结果、固定实例与取消，提交 Timeline
  → release 插件操作，发布唯一终态并释放受理槽
```

## 受理与所有权

同进程调用传入 ChatTurnInput 并返回 ChatOutcome，不构造 generation credential 或协议 envelope，
也不用临时终态监听器还原业务结果。IPC 适配器只编码 started 和终态事件。Router 的受理与放弃回调
由装配点显式传入，不从 bound method 反射推导。

Core 保持一个活动对话槽。Mobile begin 返回 job ID 前同步受理并固定角色，后台 worker 只执行已受理轮次。
角色预检查之后发生切换时拒绝旧角色请求；取消与 scope 撤销即使早于 worker 开始也能取消该轮。
worker 启动失败必须释放未执行槽位。Mobile 图片归本轮所有，不占桌面待发送附件槽。

chat.started 发布后必须恰有一个终态。chat.send 的 accepted 响应不等待模型、截图上下文或插件整个轮次；
实际执行在 generation 拥有的后台任务中进行，不能占用请求响应 deadline。普通业务异常只使该次请求失败；
传输写失败、坏帧和 EOF 由连接 owner 收尾，不自动重放请求或有副作用的工具调用。

ControlDispatcher/ReadinessController 暴露当前 session，不拥有模型客户端或上下文预算。
Session 只绑定一次 Assistant scope；Provider reload、disable、exit 或替换都使旧绑定失效。
后续显式重新准备可以使用新实例，旧 operation 不会被新实例继承。

## 输入、历史与结果

公开聊天请求仅接受已定义的消息、来源和附件字段。调用方不能提供 system/tool role、任意历史数组、
模型凭据或内部会话。Core 从当前角色和 Timeline 构造输入，将 artifact 与快照授权交给固定 Assistant。
插件通过完整轮分页获取历史，选择与预算由插件负责；Core 不扫描并投影全部历史。

[Timeline 合同](WP-4-07R-typed-timeline-adaptive-context.md) 是历史与上下文来源的现行规范。
输入持久化失败时不启动 Assistant。成功结果在最终身份与取消仲裁内提交；写入失败报告
TIMELINE_WRITE_FAILED，不伪造完成、播放或助手历史。取消和失败不生成假的助手回复。
历史旧 JSONL 兼容由存储层负责，聊天执行不建立第二份历史真相源。

Assistant 返回的 reply/actions/visual_observation 先经 Core 验证。表现 control 按当前 visual binding
解析；桌面 reply 只投影 segments，每段公开 text、translation、tone、portrait、suppressTts 与可选 control。
control 的 state/actions 是表现插件数据，不公开 Python 对象、工具 continuation、Prompt、模型设置或凭据。
本轮可见更新、表现派发和 TTS 必须使用最终提交的有效结果。

## 终态、取消与释放

显式取消或 generation 失效先于尚未提交的结果；确定的 Provider/领域错误映射为 chat.failed；只有结果形状
有效、固定 Assistant scope 仍存在、Timeline 写入成功且终态仲裁胜出后才能发布 chat.completed。
取消胜出后晚到结果不得产生第二终态。重复 chat.cancel 返回 accepted=false，未知或旧 operation
不得影响当前请求。

取消 checker 贯穿默认插件的 AgentRuntime、上下文读取、Provider retry/sleep/HTTP 读取与工具步骤。
Core 接受取消不意味着后台已退出；确认 worker 终态或精确实例停止之前保留输入和历史授权。
begin ACK 丢失不重放 begin；cancel、poll 或 release 传输失败按 Assistant 合同回收原 scope，
不杀死同 ID 的新实例。工具已经产生的外部副作用不保证可撤销，也不自动执行第二次。

插件 completed 不是 Core completed。RPC 结果返回后还要在 commit_bound_service 临界区检查服务身份，
与 disable/reload/exit 的失效进行仲裁；失效结果报告 ASSISTANT_BINDING_EXPIRED，不写历史或播放。

Timeline 提交、插件通知与 release/Trace 收尾结束后，Core 在同一临界区发布终态并释放受理槽。
Shell 收到终态后立即续发，必须等待协议写入确认与该槽释放，不因旧轮清理间隙错误拒绝下一条消息。
写入失败也要释放登记，并由 Router 报告 transport failure。终态裁决与执行槽释放是两个不同阶段。

## 故障与诊断

chat.failed.error 使用稳定 code、公共 message、retryable 和有界 details。Provider 网络、timeout、HTTP
或格式错误只终止本 operation，不能伪装为 Core crash。默认插件保留原始异常链作为脱敏运行诊断；
配置与协议错误不自动重试，429/5xx 等是否可由用户重试按错误分类呈现。

Core stdout 只承载协议。普通运行日志经统一 Host 日志链路；Agent Trace 在默认插件中写入 Host 授权目录，
失败不改变聊天结果。Trace、输入 artifact 或日志收尾错误不覆盖最初的执行/取消错误。

默认插件模型请求继续使用公开 HTTP SDK 的系统代理规则：每次新尝试重新读取当前代理，回环地址直连，
远端连接和重定向遵守相应网络边界。已打开的流继续使用原连接，不因后续设置变化混用认证信息。

shutdown、EOF、Core generation 失效先 signal 全部 operation，再按现有有界 deadline 收尾。
不合作插件由 Generic Runtime 局部回收；Core 整树最终停止仍由 Rust ManagedProcessTree 负责。
health、cancel 与 shutdown 不等待模型锁；无关插件不得因一次 Assistant 操作失败自动重载。

## 验证

| 风险 | 主要证据 |
|---|---|
| 真实聊天链 | 本地 Provider + 默认 Assistant 子进程 + Core 受理/Timeline/唯一终态 |
| 可替换性 | 第三方普通 sakura.assistant fixture，默认插件停用后无 Core fallback |
| 取消与未知 ACK | 事件控制 worker 退出、begin/cancel/poll/release 故障、原异常保留 |
| 实例失效 | result 返回后重载、旧 scope 不影响新进程、最终提交拒绝旧结果 |
| 历史与大输入 | 完整轮分页、固定快照、artifact 尺寸与接收授权、输入或结果写入失败 |
| 控制面与资源 | 慢模型下 accepted/health/cancel、shutdown/EOF、进程树和输入 owner 清理 |
| 用户可见兼容 | 分段回复、视觉解析、TTS、更新提示、Mobile 与主动输入走同一边界 |

验证使用隔离临时根，不读取真实用户历史或凭据。按受影响风险选择 Core、插件与原生平台测试，
本地通过不冒充跨平台或真实窗口结果。

## 历史依据

本 WP 于 2026-07-26 首次验收，当时在 Core 内直接调用 Pipeline，并以 JSONL best-effort 写入历史。
该构造图与写入策略已分别由 Assistant 插件边界和 Timeline 合同取代。早期验收的实现提交
b835ef2ca66a33f98eb0b4339c1ccb51abcd5e91、平台 run 30200669759 与 Test run 30200669763
只记录当时证据，不证明当前分支已完成相同平台验证，也不授权恢复 fixture-only 产品入口。
