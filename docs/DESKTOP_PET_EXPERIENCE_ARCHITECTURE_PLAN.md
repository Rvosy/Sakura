# Sakura 桌宠体验架构改造计划

## 1. 目标

本计划从桌宠长期陪伴体验出发，借鉴 Claude Code 在上下文、事件流、记忆、会话与权限方面的架构思路，但不照搬代码 Agent 的具体实现。

优先改善用户每天可以直接感受到的体验：

1. 角色人格和表达更加稳定。
2. 重启应用或长时间交互后仍能延续话题。
3. 只回忆真正相关且可信的长期记忆。
4. 更快显示第一段回复并开始播放语音。
5. 主动搭话更加克制、自然，减少重复打扰。
6. 桌面操作过程透明，确认频率与风险匹配。

## 2. 核心判断

Claude Code 最值得 Sakura 学习的不是多 Agent、代码工具或终端 UI，而是以下架构思想：

- 上下文按需选择，而不是全部塞进 Prompt。
- 一次交互是持续事件流，而不是等待一个最终结果。
- 长期记忆、短期状态和会话摘要分层管理。
- 主动行为先经过确定性策略，再交给模型生成表达。
- 工具权限、进度和取消属于执行协议，不是 UI 临时补丁。

Sakura 当前已经有 PromptBlock、插件动态上下文、mem0、JSONL 历史、Backchannel、主动屏幕观察和工具确认等基础能力。主要问题是这些能力尚未形成统一的运行时协议，复杂度仍集中在 `AgentRuntime` 和 `PetWindow`。

## 3. 用户体验优先级

| 改动 | 用户体验收益 | 优先级 |
| --- | --- | --- |
| Context Orchestrator | 角色更稳定，回答更贴合当前场景 | P0 |
| 最近会话延续 | 重启后仍记得刚才聊到哪里 | P0 |
| 相关记忆召回 | 少说错话，减少生硬和被监视感 | P0 |
| 分段流式回复和 TTS | 更快开口，不再长时间沉默 | P0 |
| 主动互动决策器 | 少打扰、少重复、搭话更自然 | P1 |
| 工具状态与风险策略 | 用户知道桌宠正在做什么，更敢授权 | P1 |
| 场景 Skills | 学习、工作、陪伴等模式更鲜明 | P2 |

## 4. 目标架构

```text
PetWindow
  -> ConversationController / ProactiveController / TTSController
  -> ChatPipeline
  -> AgentRunEngine
       -> ContextOrchestrator
            -> ContextCollector
            -> ContextPolicy
            -> ContextSnapshot
       -> PromptRuntime
            -> PromptRecipe
            -> PromptRenderer
       -> MemoryRecallService
       -> SessionStateStore
       -> ToolExecutor
            -> PermissionBroker
            -> ToolCatalog
  -> RunEvent
       -> Qt Signal
       -> Subtitle / TTS
       -> Session Journal
       -> Metrics
```

核心原则：先收集结构化事实，再做优先级和预算决策，最后渲染 Prompt。业务模块不直接拼接完整提示词。

## 5. Context Orchestrator

### 5.1 当前问题

目前插件动态上下文调用 `build_context({})`，没有真实的本轮、会话、事件和能力状态。`AgentRuntime._build_tool_system_prompt()` 仍通过大型字符串拼接角色、记忆、时间、工具规则与回复协议。

这会带来以下问题：

- 动态上下文提供者无法根据本轮请求作出选择。
- 各类内容缺少统一优先级和预算。
- 静态规则和实时事实容易互相污染。
- 无法解释最终 Prompt 由哪些来源组成。

### 5.2 ContextSnapshot

建议定义结构化 `ContextSnapshot`，至少包含：

- 当前用户输入、交互来源和运行模式。
- 最近会话摘要与最近若干轮原文。
- 与本轮问题相关的长期记忆。
- 当前时间、安静时段和用户活跃状态。
- 最新屏幕变化摘要，而不是历史截图堆积。
- 临时陪伴状态，例如忙碌、聊天中、刚拒绝主动搭话。
- 当前可用工具、服务健康状态和降级原因。
- 主动事件类型、触发原因和打扰预算。

每个上下文片段都携带以下元数据：

```text
source
trust
priority
freshness
token_budget
sensitivity
```

### 5.3 静态与动态边界

静态 Prompt 只负责：

- 角色核心人格与不可变边界。
- 基本表达风格。
- 回复协议。
- 安全原则和工具使用原则。

动态 Prompt 只负责：

- 当前事实。
- 最近会话状态。
- 相关记忆。
- 当前屏幕、时间和运行能力。
- 当前场景或 Skill 的附加规则。

### 5.4 Prompt Inspector

为调试模式提供 Prompt Inspector，展示：

- Section ID、来源和信任级别。
- 字符数和估算 token。
- 是否被截断或因预算被丢弃。
- 静态 section hash。
- 脱敏后的最终组装结果。

不得默认持久化完整 Prompt、截图、密钥或用户敏感内容。

## 6. 最近会话延续

### 6.1 桌宠需求

桌宠不需要完整复刻代码 Agent 的 transcript 图，但需要在应用重启后知道：

- 刚才正在聊什么。
- 用户当前在做什么。
- 哪些问题尚未完成。
- Sakura 已经给过什么建议。

### 6.2 SessionStateStore

建议增加轻量 `SessionStateStore`：

- 每 6 至 10 轮生成一次结构化 session summary。
- 应用关闭或角色切换前刷新摘要。
- 启动后注入摘要和最近若干轮，不恢复已完成工具调用。
- 新话题出现后逐步降低旧摘要权重。
- 保存 `session_id`、`turn_id` 和摘要版本，便于迁移和排错。

建议的摘要结构：

```json
{
  "current_topics": [],
  "user_activity": "",
  "open_loops": [],
  "recent_decisions": [],
  "sakura_already_said": [],
  "updated_at": ""
}
```

聊天历史继续作为用户可查看的展示记录；SessionState 作为模型连续性上下文，两者职责分离。

## 7. 相关记忆召回

### 7.1 当前问题

当前 `memory.summary()` 会在每轮注入最多 12 条长期记忆。无关、过时或低置信度信息可能影响回复，让桌宠显得生硬或错误地声称了解用户。

### 7.2 MemoryRecallService

保留现有 mem0 存储，增加独立召回层：

- 使用本轮用户输入和 session topic 做语义查询。
- 每轮最多选择 3 至 5 条。
- 设置最低相关度，没有合适记忆时不注入。
- 对重复、冲突和过时记忆做过滤。
- 记录召回原因和最后使用时间。

长期记忆建议增加：

```text
source: explicit | inferred | imported
confidence
scope: user | character | device
created_at
updated_at
last_used_at
expires_at
sensitivity
```

用户明确要求记住的信息优先级最高。系统推断的信息需要更高召回阈值，并允许用户查看、纠正和删除。

“用户现在很忙”“用户刚才心情不好”属于短期 CompanionState，不应写成永久偏好。

## 8. 分段流式回复与 TTS

### 8.1 目标

将体验指标从整轮完成时间改为：

- 首次视觉反馈时间。
- 第一段字幕出现时间。
- 第一段语音开始时间。
- 用户取消到真正停止的时间。

### 8.2 RunEvent

建议建立统一事件协议：

```text
run_started
model_started
model_delta
backchannel_ready
tool_queued
permission_requested
tool_started
tool_progress
tool_finished
reply_segment_validated
tts_segment_ready
run_finished
run_failed
run_cancelled
```

UI、日志、TTS 和会话状态只消费事件，不直接依赖 AgentRuntime 内部实现。

### 8.3 分段输出策略

- 模型回复继续使用 segments 协议。
- 流式解析器检测到一个完整 segment 后立即校验。
- 只有通过字段和语言校验的 segment 才能进入字幕与 TTS。
- 后续整体 JSON 失败时，只修复尚未发布的部分，避免重复播放。
- Backchannel 根据真实运行阶段调度，第一段及时到达时立即取消。

## 9. 主动互动决策器

### 9.1 设计原则

是否打扰用户不能完全交给 Prompt 或模型。应先由本地 `EngagementPolicy` 做确定性决策，再让模型生成具体表达。

决策结果：

```text
silent
subtle_reaction
start_conversation
offer_help
```

### 9.2 决策输入

- 安静时段和用户设置。
- 最近键鼠或输入活跃度。
- 全屏应用或会议状态。
- 距离上次用户互动和主动搭话的时间。
- 屏幕内容是否发生有意义变化。
- 最近主动搭话是否被忽略或拒绝。
- 最近对话情绪和挫败信号。
- 当前话题是否已经重复提醒。

### 9.3 代码收敛

把当前屏幕感知与主动关怀的相似调度链合并到 `ProactiveController + EngagementPolicy`：

- Controller 管理定时、截图任务和事件生命周期。
- Policy 只做纯函数决策，便于测试。
- 模型只负责生成符合角色的内容。

## 10. 桌宠式工具信任模型

工具按用户感知的副作用分级：

| 类型 | 示例 | 策略 |
| --- | --- | --- |
| 观察类 | 截图、读取页面、查询时间 | 默认自动执行 |
| 低风险操作 | 打开页面、切换窗口 | 执行时显示状态，可按策略确认 |
| 外部副作用 | 发送消息、提交表单、删除文件、付款、输入凭据 | 必须确认 |

确认范围建议支持：

- 仅这一次。
- 本次会话允许相同精确操作。
- 总是允许该工具的匹配规则。

完整访问不应默认开启。运行期间通过 RunEvent 向用户展示“正在查看网页”“准备操作窗口”“等待确认”等简短状态，并提供可靠取消入口。

## 11. 场景 Skills

Prompt 架构稳定后，增加不执行 Python 或 Shell 的声明式 Skills：

- 专注学习。
- 编程陪伴。
- 日语练习。
- 睡前低打扰。
- 会议静音。
- 情绪安抚。
- 定时休息。

Skill 建议包含：

```text
id
name
description
triggers
allowed_tools
prompt_sections
priority
character_scope
max_context_tokens
```

Skill 根据场景按需激活，不能全部常驻 Prompt。可执行扩展继续使用插件系统，Skills 只负责行为知识和场景规则。

## 12. 实施顺序

### 阶段 1：上下文与 Prompt

1. 让现有 `PromptContext` 真正进入主聊天构建链。
2. 引入 `ContextRequest`、`ContextFragment` 和 `ContextSnapshot`。
3. 把 `_build_tool_system_prompt()` 拆成稳定 Prompt sections。
4. 为插件 ContextProvider 传入受限、真实的本轮上下文。
5. 增加 section 预算、信任级别与 Prompt Inspector。

### 阶段 2：会话和记忆

1. 增加 SessionStateStore 和结构化 session summary。
2. 启动时恢复最近会话摘要。
3. 增加 MemoryRecallService，停止每轮注入全量摘要。
4. 区分长期记忆和短期 CompanionState。

### 阶段 3：交互事件流

1. 定义 RunEvent 和 AgentRunEngine 边界。
2. ChatPipeline 把 RunEvent 转为 Qt signals。
3. API Client 增加流式响应适配。
4. 实现 segment 校验后立即显示和播放。
5. Backchannel、工具状态和取消统一接入事件流。

### 阶段 4：主动互动

1. 合并屏幕感知和主动关怀调度。
2. 实现 EngagementPolicy 纯函数。
3. 增加屏幕变化摘要和重复话题抑制。
4. 记录主动互动的接受、忽略和拒绝反馈。

### 阶段 5：信任与场景扩展

1. 重构工具风险和确认策略。
2. 增加会话级精确授权。
3. 增加声明式 Skills。
4. 为 Skills、插件和 MCP 上下文增加信任边界与预算。

## 13. 验收指标

### 角色与上下文

- Prompt Inspector 能解释每个 section 的来源和成本。
- 动态上下文不会覆盖角色核心与回复协议。
- 重启后可以自然延续最近话题。

### 记忆

- 每轮自动注入长期记忆不超过 5 条。
- 无相关记忆时不注入长期记忆正文。
- 用户可以查看记忆来源并纠正、删除。

### 响应速度

- 记录第一段字幕和第一段 TTS 的延迟。
- 第一段有效 segment 无需等待整轮回复完成。
- 用户取消后模型、工具、字幕和 TTS 都能停止。

### 主动互动

- 安静时段、全屏和高活跃输入时不会主动打扰。
- 相同话题不会在短时间内重复出现。
- 用户多次忽略或拒绝后自动降低主动频率。

### 工具信任

- 外部副作用工具始终进入确认流程。
- 用户可以看到当前工具阶段和取消状态。
- 记忆、TTS 或 MCP 不可用时，文本聊天仍可正常降级。

## 14. 不建议照搬的能力

- 完整 swarm、mailbox 和 teammate 系统。
- 面向代码仓库的 project memory 和文件索引结构。
- 终端消息虚拟化与 TUI 组件体系。
- Anthropic 专用 Prompt 缓存协议。
- 为代码执行设计的全应用 Sandbox。
- 完整恢复所有历史工具链和侧链会话图。

这些能力复杂度高，对桌宠核心体验收益有限。Sakura 应借鉴其边界和协议设计，而不是复制实现规模。

## 15. 参考位置

Sakura 当前实现：

- [`app/agent/runtime.py`](../app/agent/runtime.py)
- [`app/llm/prompts/`](../app/llm/prompts/)
- [`app/agent/memory.py`](../app/agent/memory.py)
- [`app/storage/chat_history.py`](../app/storage/chat_history.py)
- [`app/ui/pet_window.py`](../app/ui/pet_window.py)

Claude Code 分析材料：

- [`analysis/04g-prompt-management.md`](../../claude-code-analysis/analysis/04g-prompt-management.md)
- [`analysis/04-agent-memory.md`](../../claude-code-analysis/analysis/04-agent-memory.md)
- [`analysis/04f-context-management.md`](../../claude-code-analysis/analysis/04f-context-management.md)
- [`analysis/04i-session-storage-resume.md`](../../claude-code-analysis/analysis/04i-session-storage-resume.md)
- [`analysis/04b-tool-call-implementation.md`](../../claude-code-analysis/analysis/04b-tool-call-implementation.md)
- [`analysis/06b-negative-keyword-analysis.md`](../../claude-code-analysis/analysis/06b-negative-keyword-analysis.md)

参考仓库声明其材料仅供研究并禁止商业使用。Sakura 应以 clean-room 方式借鉴架构思想，不复制其中的泄露源码、提示词或实现文本。
