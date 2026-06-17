# 桌宠状态 Pet State 开发文档

本文记录 Sakura 桌宠状态模块的当前 MVP 实现、开发边界和后续路线。当前实现已经从“依赖模型主动调用 `pet_state_update` 工具”调整为“结构化回复每次携带 `pet_state_delta`，本地自动校验、落盘并同步 UI”。

## 目标

为 Sakura 增加一个模型可见、宿主可校验、前端可同步的跨轮次状态层。

核心原则：

- `ChatSegment.tone` / `ChatSegment.portrait` 继续表示本轮每段回复的即时表现。
- `pet_state` 表示跨轮次稳定状态，不直接替代分段语气或立绘。
- 模型可以提出状态变化，但最终写入必须经过本地 schema、范围、长度和只读字段校验。
- 前端只展示本地确认后的状态快照，不直接信任模型原始输出。
- 角色特异状态机和复杂 harness 放到后续阶段，不阻塞基础链路。

## 当前 MVP 链路

当前主路径不再依赖模型主动 tool call：

```text
PetStateStore.snapshot()
  -> PetWindow 注入 pet_state system context / event context
  -> 模型最终回复 JSON 同时输出 segments + pet_state_delta
  -> ChatReply 解析并保留 pet_state_delta
  -> AgentRuntime / API 层发现缺失 pet_state_delta 时触发一次结构修复
  -> PetWindow 收到 AgentResult 后应用 reply.pet_state_delta
  -> PetStateStore.update_from_tool() 校验、钳制、审计、落盘
  -> state_changed signal 更新右键状态气泡
```

工具路径仍然保留：

- `pet_state_get`: 读取当前状态和最近审计。
- `pet_state_update`: 兼容旧工具调用、调试或模型主动修正。

但普通回复的状态更新以顶层 `pet_state_delta` 为准。

## 回复 JSON 合约

启用情绪模块后，最终回复 JSON 必须在 `segments` 同级包含 `pet_state_delta`。

示例：

```json
{
  "segments": [
    {
      "ja": "うん、今は落ち着いてるよ。",
      "zh": "嗯，我现在挺平静的。",
      "tone": "中性",
      "portrait": "站立待机"
    }
  ],
  "pet_state_delta": {
    "mood": "neutral",
    "affect": {
      "valence": 0.1,
      "arousal": 0.2,
      "confidence": 0.8
    },
    "evidence": {
      "last_user_signal": "用户直接询问当前心情",
      "last_trigger": "assistant_reply",
      "reason": "回复中表达当前状态平静，没有明显情绪波动"
    }
  }
}
```

边界：

- `segments[].tone` 控制当前段落语气和 TTS 参考。
- `segments[].portrait` 控制当前段落显示立绘。
- `pet_state_delta.mood` / `affect` 控制跨轮次整体状态。
- `pet_state_delta.evidence` 记录这次状态判断依据。
- `pet_state_delta` 只允许 `mood`、`affect`、`evidence`。
- `display` 只能由宿主派生，模型不能写。

如果模型漏掉 `pet_state_delta`：

- `AgentRuntime._parse_final_reply_with_retry()` 会把它视为结构缺失并修复一次。
- `OpenAICompatibleClient.chat()` 的工具总结路径也会尝试补齐一次。
- 修复失败时保留原回复，不阻断对话，但不会产生状态更新。

## 数据结构

状态持久化记录包含三部分：

```json
{
  "state": {
    "mood": "neutral",
    "affect": {
      "valence": 0.0,
      "arousal": 0.2,
      "confidence": 0.7
    },
    "evidence": {
      "last_user_signal": "",
      "last_trigger": "startup",
      "reason": "默认初始状态。"
    },
    "display": {
      "label": "平静",
      "idle_expression_hint": "站立待机"
    },
    "updated_at": "2026-06-18T12:00:00+08:00"
  },
  "last_model_delta": null,
  "last_harness_decision": null
}
```

字段约束：

| 字段 | 类型 | 约束 |
|---|---|---|
| `mood` | string | `neutral`, `happy`, `sad`, `angry`, `shy`, `anxious`, `curious`, `tired` |
| `affect.valence` | number | `-1.0` 到 `1.0` |
| `affect.arousal` | number | `0.0` 到 `1.0` |
| `affect.confidence` | number | `0.0` 到 `1.0` |
| `evidence.last_user_signal` | string | 最长 120 字符 |
| `evidence.last_trigger` | string | 常用值：`startup`, `user_message`, `assistant_reply`, `runtime_event`, `tool_result`, `harness` |
| `evidence.reason` | string | 最长 240 字符 |
| `display.label` | string | 只读派生字段 |
| `display.idle_expression_hint` | string | 只读派生字段 |
| `updated_at` | string | 本地时区 ISO 时间 |

审计字段：

```json
{
  "last_model_delta": {
    "submitted_at": "2026-06-18T12:00:00+08:00",
    "delta": {},
    "forced": false,
    "force_fields": []
  },
  "last_harness_decision": {
    "status": "applied",
    "reason": "Phase 1 已通过 schema 校验并应用。",
    "revised_fields": [],
    "rejected_fields": []
  }
}
```

`last_harness_decision.status` 可取：

- `applied`: 完全接受。
- `revised`: 接受但修正部分字段，例如数值钳制。
- `rejected`: 预留，当前 MVP 尚未做复杂拒绝。
- `model_forced`: 模型使用 `forced` 请求，当前 MVP 只记录且仍校验。
- `noop`: delta 没有造成状态变化。

## 模块职责

### `app/pet_state/models.py`

- 定义 `PetState`、`PetAffect`、`PetStateEvidence`、`PetStateDisplay`、`PetStateRecord`。
- 实现 `apply_pet_state_delta()`。
- 负责 mood 枚举、数值范围、文本长度、只读字段边界。
- 根据 mood 派生 `display.label` 和 `display.idle_expression_hint`。

### `app/pet_state/store.py`

- `PetStateStore(QObject)` 是本地状态权威。
- `snapshot()` 返回当前完整记录。
- `update_from_tool(arguments)` 接受 `{"delta": ...}`，调用模型层校验后落盘。
- 写入成功后发出 `state_changed` signal。
- 读取失败或文件损坏时回退默认状态。

### `app/pet_state/tools.py`

- 注册工具组 `pet_state`。
- `pet_state_get` 读取当前快照。
- `pet_state_update` 保留为兼容和调试入口。

### `app/pet_state/prompting.py`

- `build_pet_state_context_message(snapshot)` 构造本轮 system context。
- 上下文明确说明：
  - `pet_state` 是跨轮次状态。
  - `tone` / `portrait` 是当前回复段表现。
  - 最终 JSON 必须包含 `pet_state_delta`。
  - `pet_state_delta` 不允许写 `display`。

### `app/llm/chat_reply.py`

- `ChatReply` 新增 `pet_state_delta` 字段。
- `parse_chat_reply_result()` 保留顶层 `pet_state_delta`。
- `sanitize_reply_tones()` 修正 tone 时保留 `pet_state_delta`。

### `app/llm/api_client.py`

- `OpenAICompatibleClient.chat(..., require_pet_state_delta=True)` 在工具总结路径要求补齐状态 delta。
- 如果首次回复缺少 `pet_state_delta`，会以低温度请求模型修复一次 JSON。

### `app/agent/runtime.py`

- 常规 tool loop 最终回复走 `_parse_final_reply_with_retry()`。
- 当 working messages 中包含 `pet_state_delta` 契约时，缺失 delta 会触发一次结构修复。
- `_build_tool_system_prompt()` 仍保留 `pet_state_get/update` 工具说明，方便模型读取状态或调试。
- 主动事件的 `event_messages` 也会检测 `pet_state_delta` 契约。

### `app/ui/pet_window.py`

- 用户消息路径：
  - `_add_pet_state_context_to_messages()` 将 store snapshot 注入 request messages。
- 主动事件路径：
  - `_event_with_pet_state_context()` 将状态快照和回复契约放入 event payload。
- 回复消费路径：
  - `_apply_reply_pet_state_delta()` 在记录历史和显示前应用 `reply.pet_state_delta`。
  - 应用失败只写 debug log，不阻断回复展示。
- UI：
  - 右键菜单“桌宠状态”为 checkable action。
  - `ui.pet_state_popup_pinned` 控制状态气泡常显。
  - 状态气泡可拖动，使用对话气泡同款圆角样式。
  - 状态气泡置顶状态跟随主窗口 `always_on_top_enabled`。

### `app/core/bootstrap.py` / `app/core/app_context.py`

- 启动时创建 `PetStateStore`。
- 将 store 放入 `AppContext`。
- 内置工具注册时传入 store。

### `app/storage/paths.py`

- `data/pet_state/<character_id>.json` 是每个角色独立的状态文件。
- `StoragePaths.ensure_dirs()` 会创建 `data/pet_state/`。

## UI 行为

右键菜单：

- “桌宠状态”是可勾选项。
- 勾选：显示状态气泡，并保存 `ui.pet_state_popup_pinned = true`。
- 取消勾选：隐藏状态气泡，并保存 `ui.pet_state_popup_pinned = false`。
- 隐藏到托盘时状态气泡会一起隐藏。
- 桌宠恢复显示后，如果配置仍为勾选，会自动恢复状态气泡。

状态气泡：

- 独立顶层工具窗口。
- 可拖动。
- 使用 `#petStatePopupBubble` QSS，样式与 `#speechBubble` 保持一致。
- 展示中文键值，而不是原始 JSON。
- 只显示本地确认后的状态、审计和 harness 决策。
- 置顶状态跟随主窗口；主窗口不置顶时，状态气泡也不额外置顶。

显示字段：

- 心情
- 愉悦度
- 活跃度
- 置信度
- 判断信号
- 触发来源
- 判断依据
- 待机表情
- 更新时间
- 本地裁决
- 最近提交 / forced 审计

## 工具接口

### `pet_state_get`

用途：读取当前桌宠状态、最近一次模型提交和 harness 决策。

Schema：

```json
{
  "type": "object",
  "properties": {},
  "required": []
}
```

### `pet_state_update`

用途：兼容工具调用路径，提交状态修改建议。

Schema：

```json
{
  "type": "object",
  "properties": {
    "delta": {
      "type": "object",
      "properties": {
        "mood": {"type": "string"},
        "affect": {"type": "object"},
        "evidence": {"type": "object"}
      }
    },
    "forced": {"type": "boolean"},
    "force_fields": {
      "type": "array",
      "items": {"type": "string"}
    },
    "force_reason": {"type": "string"}
  },
  "required": ["delta"]
}
```

注意：

- 普通回复不依赖这个工具更新状态。
- 工具入口和结构化回复入口最终都复用 `PetStateStore.update_from_tool()`。
- `forced` 只记录请求，不绕过 schema、范围、长度和只读字段校验。

## 插件边界

当前 MVP 是宿主能力，不是外部插件：

- `PetStateStore` 由 `AppContext` 持有。
- 工具注册由内置工具系统完成。
- UI 更新依赖 Qt signal。
- 状态上下文由 `PetWindow` 主动注入模型请求。

未来插件 SDK 可以扩展：

- 新权限：`pet_state`。
- `PluginContext.pet_state` 只暴露受限 facade。
- 第三方插件可读状态或提交 delta，但仍走同一个 store / harness。

## 线程与安全边界

- LLM 请求和工具执行在 worker 线程。
- `PetStateStore` 内部用 `RLock` 保护 `_record`。
- `state_changed` 通过 Qt signal 通知 UI。
- UI 不直接解析模型原始 JSON，只消费本地 store snapshot。
- 状态落盘使用 `atomic_write_text()`。
- 状态 delta 应用失败只记录 debug log，不阻断用户回复。

## 已完成测试覆盖

关键测试：

- `tests/unit/test_pet_state.py`
  - store 更新、钳制、持久化。
  - `display` 只读保护。
  - `pet_state_get/update` 工具路径。
  - pet state context 包含 `pet_state_delta` 契约。
- `tests/unit/test_api_client.py`
  - `ChatReply.pet_state_delta` 解析。
  - tone 清洗保留 pet_state delta。
- `tests/unit/test_agent_runtime.py`
  - 固定工具提示包含 pet_state 路由。
  - 缺少 `pet_state_delta` 时触发最终回复修复。
- `tests/ui/test_pet_window.py`
  - 右键菜单 checkable “桌宠状态”。
  - 状态气泡持久化显示/解除。
  - 状态气泡置顶跟随主窗口。
  - 结构化 `pet_state_delta` 应用到 store。
  - 主动事件注入 `pet_state_context`。
- `tests/unit/test_bootstrap.py`
  - `AppContext` 创建 pet state store。
  - 内置工具注册 `pet_state_get/update`。

常用验证命令：

```bash
.venv/bin/python -m pytest -q tests/unit/test_pet_state.py tests/unit/test_api_client.py tests/unit/test_agent_runtime.py tests/ui/test_pet_window.py
.venv/bin/python -m pytest -q tests/unit/test_prompt_templates.py tests/integration/test_agent_core.py tests/integration/test_chat_worker.py tests/integration/test_chat_pipeline.py tests/integration/test_native_tool_calls.py
.venv/bin/python -m pytest -q
```

当前开发目录验证结果：

```text
1059 passed, 1 warning
```

warning 是既有的 `sdk.tool_registry` 废弃导入提示。

## 后续路线

### Phase 2: 标准 Harness

目标是把当前 MVP 的 schema / 钳制规则扩展为更完整的通用状态裁决。

建议规则：

- 单次 `valence` / `arousal` 变化限幅。
- 证据为空时降低 `confidence`。
- 极端 mood 跳转需要明确 evidence。
- 低质量 reason 降级或标记 revised。
- 对 `forced` 做字段级审计，而不是只记录请求。

### Phase 3: 角色特异状态机

目标是基于角色包、游戏内文本或角色资料生成角色特异 harness，作为标准 harness 之后的增强层。

可能资产：

```json
{
  "pet_state_harness": "pet_state/harness.json"
}
```

角色特异 harness 可以包含：

- 角色状态枚举扩展。
- 游戏内文本证据片段。
- 状态转移图。
- mood 到 display label / idle expression hint 的映射。
- 角色特有的禁跳规则。
- 角色特有的 decay 规则。

角色特异 harness 不应该：

- 绕过 schema。
- 绕过 forced 审计。
- 直接操作 UI。
- 把大段游戏文本注入每轮模型上下文。

## 暂缓项

以下内容不进入当前 MVP：

- 基于游戏内文本自动提炼状态机。
- 状态驱动主动行为。
- 状态衰减和定时恢复。
- 多角色状态迁移策略。
- 第三方插件直接扩展 harness。
- 状态驱动空闲立绘自动切换。
