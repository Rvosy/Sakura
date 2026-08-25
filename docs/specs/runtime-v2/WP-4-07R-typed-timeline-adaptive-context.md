---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-26
---

# WP-4-07R：类型化交互时间线与自适应上下文

## 1. 状态、目标与替代范围

本规范定义 Runtime v2 下一阶段的上下文连续性行为。执行状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准；在 WP-4-07R accepted 前，
WP-3-02、WP-4-01 和 WP-4-07 的现有历史/事件行为仍是当前产品行为，不得把本规范写成已经实现。

目标只有五项：

1. 人类发言、assistant 回复、Host 观察和系统事实不再共享错误的 `user/assistant` 存储语义；
2. 一次 assistant generation 作为一个有多个表现 segment 的原子历史条目；
3. 最近历史按真实模型窗口弹性扩展，不再受 24 条、8 条或 4,096 token 的固定总上限支配；
4. Memory 插件可以按角色、按游标补读已提交 Timeline，漏掉 best-effort 完成事件也不永久漏记；
5. 继续使用现有 Context Contributor、ContextPolicy 和 Agent Trace，不建设第二套 Context Runtime。

accepted 后，本规范替代：

- WP-3-02 中 Runtime v2 新聊天继续写 `data/chat_history/<character>.jsonl` 的部分；
- WP-3S-01 中聊天模型槽只有 `profile_id/model` 的窄字段边界，增加可选 `context_window_tokens`；
- WP-4-07 中定时截图全文以 `role=user` 写入历史的部分；
- WP-4-01 中 `sakura.host.chat.completed` 直接携带聊天正文、Memory 直接读取原始 ChatHistoryStore 的部分。

其他终态、取消、截图资源、Plugin Worker、Memory backend、TTS 和 UI 契约不变。

## 2. 不变量与所有权

- Python Core/Host 是 Timeline 唯一写入者。WebView、Rust、Memory 和其他插件不得直接打开或修改 Timeline
  数据库。
- 所有读取和上下文选择都必须绑定当前 `character_id`。跨角色 cursor、entry ID 或 turn ID 明确失败，
  不返回部分结果。
- Timeline 记录已发生的交互事实；Agent Trace 记录模型调用和内部判断。捕获但未提交的截图、候选 Prompt、
  NOOP、重试意图和调试事件不得为了“完整”而写进 Timeline。
- Provider 兼容层可以把当前 observation 装入 API 所要求的 `user` role 容器，但内部 provenance、Trace、
  持久化和历史投影仍必须标为 observation，绝不能表述成“用户说”。
- 当前请求中的图片、tool call/result 和其他 Provider native atom 在完成前保持原结构，不得为了预算或存储
  先转换成普通文本。

## 3. Timeline v1 数据契约

固定路径为：

```text
data/chat_history/timeline.sqlite3
```

第一版业务 schema 只有：

```sql
CREATE TABLE timeline_entries (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id     TEXT NOT NULL UNIQUE,
    turn_id      TEXT NOT NULL,
    character_id TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('human', 'assistant', 'observation', 'system')),
    origin       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX timeline_character_seq
    ON timeline_entries(character_id, seq);
CREATE INDEX timeline_character_turn_seq
    ON timeline_entries(character_id, turn_id, seq);
```

`entry_id/turn_id` 使用不可猜测的稳定 ID；`seq` 只在 Host 内部排序，插件只接收 opaque cursor。`created_at`
是 Host 确认条目的带时区时间。`origin` 是有界、可诊断的来源字符串，第一版至少支持：

```text
chat | manual_screen | scheduled_screen | proactive | host | legacy_chat
```

payload 只允许以下形状：

| kind | payload | 规则 |
|---|---|---|
| `human` | `{ "text": string }` | 仅用户实际提交的文字；Host 引导语不得混入 |
| `assistant` | `{ "segments": Segment[1..N] }` | 一个 generation 一条；Segment 保留 text/translation/tone/portrait/suppressTts |
| `observation` | `{ "text": string, "visual": object? }` | text 是 Host 描述而非用户发言；visual 只含数量、时间、visual ID 等安全 metadata |
| `system` | `{ "text": string, "eventType": string? }` | 仅需要进入未来关系连续性的 Host 已确认事实，不是普通日志 |

所有字符串和数组必须有界。`payload_json` 不得含图片/音频字节、data URL、base64、绝对路径、临时资源 token、
API key 或 Provider 原始异常。

## 4. 写入、Turn 与展示

- Core 接受一次外部或主动交互时生成一个 `turn_id`。用户文字与手动截图可以是同一 Turn 内的 `human` 和
  `observation` 两个条目；定时截图只有 `observation` 触发条目。
- 只有真正提交给对话模型的 observation 才进入 Timeline。单纯捕获、批次替换、繁忙跳过和提交前取消只
  进入现有日志/Trace。
- human/observation 输入在 Provider 调用前提交。Provider 失败或取消时不伪造 assistant 条目；下一轮投影
  可以看到真实未回答的人类输入，但旧的 observation-only Turn 默认不进入普通聊天历史。
- Provider 最终回复完成解析、segment 校验和授权后，在一个事务中写一条 assistant entry。多个气泡、语气、
  立绘和 TTS 标记全部在 `segments[]`，不得逐 segment 追加历史。
- 工具循环只在当前模型 operation 中保留 Provider native call/result；第一版 Timeline 只保存最终用户可见
  assistant generation，不保存每个内部 Agent step。
- 历史 UI 从同一 Timeline 投影；assistant segments 可以显示为多个气泡，但它们共享一个 entry/turn，删除、
  计数和 Memory 整理不得把它们当作多次回复。
- 明确 NOOP 不写 assistant entry。NOOP 详情只属于 Agent Trace。

## 5. 只读 Timeline Host Service

新增普通 Host Service `sakura.host.timeline`。它不是 Memory 专用协议，只暴露当前绑定角色：

```text
latest_cursor() -> { cursor }
read_recent({ limit }) -> { entries, cursor }
read_since({ cursor, limit }) -> { entries, nextCursor, hasMore }
```

- `limit` 必须为 `1..500`；返回正文继续受通用 Bridge 大小上限约束。
- cursor 是版本化 opaque string，绑定角色和数据库 lineage；插件不得解析、拼接或持久化为整数。
- 数据被用户清除、数据库更换或 cursor 不属于当前角色时返回稳定 `TIMELINE_CURSOR_INVALID`。消费者可以按
  自己的 backfill 配置重新调用 `read_recent`，Host 不自动猜测恢复位置。
- Service 只读；不提供 append/update/delete/search、订阅管理、Episode 或 Observation 专用方法。

完成事实调整为：

```text
sakura.host.chat.completed {
  characterId,
  turnId,
  cursor
}
```

事件必须在 assistant entry 事务提交后发送，仍为 best-effort，且不携带聊天正文。Memory 保存成功消费的
cursor；插件 setup 和每次后续完成事件都从该 cursor 补读，因此一次事件投递失败不会永久缺失。重复读取由
`entry_id` 幂等，不建设 outbox、ack、lease、自动重试线程或第二份原始事件表。

## 6. 轻量 Turn 投影

上下文构建读取当前角色候选条目后，通过无状态函数按 `turn_id` 分组并按 `seq` 排序。它遵守：

- `human` 投影为真实 user history；`assistant` 的 segments 按顺序合成一个历史 assistant message；
- 当前 observation 可以按 Provider 约束使用 user-role 容器，但必须携带内部 observation provenance；历史
  observation 以 Host runtime fact/安全描述投影，绝不写成用户说过的话；
- 旧的 observation-only、system-only、空内容和损坏 Turn 不进入普通对话历史；drop reason 进入 Trace；
- 选中 Turn 最终按旧到新输出，不颠倒真实会话顺序；
- 不创建有状态 TurnAssembler、Turn cache 或 Turn lifecycle。投影失败只影响对应候选，不修改 Timeline。

## 7. 自适应 Token Budget

### 7.1 窗口解析

聊天模型槽增加可选 `model_slots.chat.context_window_tokens`，范围 `4,096..2,000,000`。窗口来源优先级为：

1. 用户为当前聊天模型显式保存的值；
2. Provider adapter 返回的精确模型 metadata；
3. 完全未知时使用 `32,768` fallback。

不得按模型名称子串猜测 128K。用户明确配置 128K 后必须按 128K 计算。Trace 记录 resolved value 和
`user/provider/fallback` 来源。

### 7.2 预算计算

默认输入目标为模型窗口的 75%，同时必须留出输出和安全空间：

```text
output_reserve = configured max_tokens
                 或 min(8192, max(2048, context_window / 8))
safety_margin  = max(1024, context_window * 5%)
input_target   = min(context_window * 75%,
                     context_window - output_reserve - safety_margin)
context_budget = input_target
                 - static_prompt
                 - tool_schemas
                 - current_required_atoms
```

百分比和 fallback 是首版产品参数，可依据评测在 Spec 内调整，不是插件 API 或存储契约。优先使用 Provider
tokenizer；不可用时使用现有保守估算器，并在 Trace 标明 estimator。

若静态 Prompt、工具 schema、当前输入、当前图片/tool atom 和输出预留本身超过窗口，调用明确失败为
`CONTEXT_WINDOW_EXCEEDED`，不得静默截断当前输入、图片或工具结果，也不得自动改成另一个模型。

### 7.3 选择规则

`ContextPolicy` 在同一个预算账本中处理历史 Turn 和 Context Fragment：

1. 保留必需 Host facts 和当前 Turn；
2. 在能完整容纳时优先保护最近 8 个 eligible 完整 Turn；8 是保护尾部，不是历史上限；
3. 按既有 required/priority/freshness 选择 session 与插件 Fragment；同一 Contributor 的额度按
   `plugin_id/source` 聚合，不能拆 Fragment 绕过限制；
4. 用剩余预算继续从近到远选择更早的完整 Turn；
5. 输出前恢复为旧到新，并由 Provider adapter 进行最终 role/placement 兼容。

旧 Turn 超大时只允许类型化降级：使用已经存在的安全摘要/引用，或丢弃整个 Turn 并记录原因；不得在请求
路径临时调用另一个 LLM 总结，也不得截出破坏 role/tool 原子性的半个 Turn。

WP-4-07R 的 Runtime v2 路径不得继续把以下值作为总上限：

```text
24 messages / 40,000 chars
8 messages / 1,000 chars each
4,096 dynamic-context tokens
1,024 tokens per every fragment
```

Legacy Qt 可以暂时保留旧限制，但不得影响 Runtime v2 resolved budget。

## 8. Memory 与其他插件

- `sakura.memory.mem0` 仍是普通插件，继续自行拥有 core profile、semantic、episodic、procedural、session、
  Qdrant/SQLite、整理触发和召回策略。Core 不理解或调度这些 layer。
- Memory 通过 `sakura.host.timeline` 消费完整条目，通过现有 `sakura.host.context` 贡献少量文本 Fragment；
  停用、失败或超时不阻断聊天。
- 新提炼记录至少保存一个 `source_entry_ids` 集合，以便检查误记和幂等；不要求图、信任传播或完整数据血缘。
- 当前 `triggerTurns/backfillLimit` 可以继续使用。空闲阈值、topic segmentation、EpisodeBuilder 和新的整理
  生命周期不是本 WP 的验收前置。
- 召回允许 0 条。FTS、RRF、固定 top-30/max-8、图数据库、连续情绪状态机和双时间事实更新不在本 WP
  冻结；它们必须在 Timeline/预算基线后由实际 Precision、错误注入或时间更新失败样本驱动。
- 既有 legacy `VisualObservationStore` 不因本 WP 自动删除，但本 WP 不读取、不扩张也不为它增加新的
  retention/search/lifecycle；其存废由现有真实消费者另行处理。

`Companion Context` 可以作为推荐插件组合或 UI 名称，但不得成为拥有 Timeline 和最终 Prompt 的必装大插件。

## 9. 旧数据导入与回退

- 首次切换在持有单应用锁、没有活动聊天时读取 `data/chat_history/*.jsonl` 及既有 archive。每条合法 user
  记录映射为 `human/legacy_chat`；assistant 记录映射为一个含单 segment 的 `assistant/legacy_chat`。
- 导入使用确定性 entry ID，在一个 SQLite 事务中完成；重复执行不得产生重复条目。损坏或不完整源文件使
  整次切换失败，现有文件和当前运行路径保持不变，不做自动修复或部分启用。
- 导入后逐角色比对合法记录数、顺序、正文和 assistant 展示字段。校验成功才启用 SQLite 单写；旧 JSONL、
  archive、`.corrupt-*.bak` 和 legacy 根文件原字节保留。
- 切换后不长期双写 JSONL。回退代码不得删除、重建或降级 Timeline SQLite；旧版本暂时不可见的新记录
  必须被报告为兼容限制，而不是被导出覆盖到原文件。

## 10. Agent Trace 与可解释性

复用 WP-4L-02，不新增 ContextTrace Store。每次 Provider request 的现有 Trace summary 增加：

```text
context_window_tokens / window_source / estimator
input_target / output_reserve / safety_margin
required_tokens / history_candidate_turns / history_selected_turns
context_selected_tokens / dropped_turns / dropped_context reasons
```

Trace 的最终 prompt 仍必须对应实际 Provider payload。运行日志只记录数量、预算、来源和稳定 drop/error code，
不记录聊天正文。Trace 失败继续不得改变聊天结果。

## 11. 验收条件

自动测试和 Harness 必须至少证明：

- typed human/assistant/observation/system 写入、同 Turn 分组、一次 generation 一条 assistant entry 和 segment
  展示等价；
- 手动截图与定时截图不再成为“用户说”，NOOP 不产生 assistant 历史，当前图片仍以 Provider native atom
  发送；
- Provider 失败、取消、进程重启和损坏 payload 不产生伪回复或跨角色读取；
- `read_since` 在漏掉一次完成事件后由下一事件或插件重启补读，重复消费不重复提炼；
- 128K 显式配置能够在预算允许时选择超过旧 24-message/4,096-token 上限的完整历史；未知模型按 32K
  fallback，有界且 Trace 可解释；
- 当前输入或当前 tool result 超窗时明确失败，旧 Turn 丢弃不破坏原子顺序；
- JSONL 导入顺序、正文、角色、segment 字段、幂等、失败不切换和旧文件原字节保留；
- Memory 停用/失败、双 Context Contributor、角色切换和历史清除不会串角色或阻断普通聊天；
- Agent Trace 记录真实预算与 selected/dropped 结果，不泄漏图片、资源 token 或凭据。

质量基线至少比较当前实现、只启用 Typed Timeline、Timeline + 自适应历史、Timeline + 现有 Memory 四组，记录
多会话抽取、时间更新、拒答、错误记忆注入、token、首 token 延迟和人工连续性感受。基线建立前不把固定
召回权重或绝对分数写成验收阈值。

## 12. 非目标

本 WP 不建设 ObservationStore、EpisodeStore、EpisodeBuilder、独立 ContextBudgetPlanner、Context Runtime、
Provenance Graph、知识图谱、图数据库、多 Agent Memory Manager、实时主题边界检测、RL reflection、自动重试、
outbox/ack、长期双写或新 Prompt Trace 文件。提示词的角色措辞和自然引用在结构与基线稳定后单独 A/B，
不以文案调整代替 Timeline 修复。

架构取舍见
[`ADR-0033`](../../adr/0033-host-typed-timeline-and-lightweight-context-building.md)。
