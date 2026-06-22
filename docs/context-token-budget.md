# 上下文构成现状分析 & Token 预算设计

> 目的：盘清「一轮请求里到底有哪些东西、各占多少」，并给出按 token（而非字符）统一预算的设计，
> 作为后续「显示预计 token」功能的参考底稿。
>
> 状态：设计/参考文档，非已落地实现。日期：2026-06-20。

---

## 1. 现状：一轮请求里到底发了什么

发给模型的 prompt 由 5 块拼成，但**当前只有一部分被纳入了"预算"统计**。

```
最终 HTTP body.messages（日志实测，N.A.V.I. 角色，新会话首轮）:
  index 0  system  人格/recipe 静态段            ~4265 chars   ← 静态(可缓存)
  index 1  system  运行时系统事件(app.started 等)  ~319 chars    ← 动态(临时注入)
  index 2  user    用户本轮输入                                  ← 动态
  index 3  system  runtime_context               ~1805 chars   ← 动态(末尾追加)
           （= 时间 + agent 步数 + 记忆召回 + session_state digest）
  ＋ tools: 20+ 个工具定义的 JSON（日志实测请求体 ~23KB）        ← 基本静态
  ＋ 可能的图片(视觉上下文)                                      ← 动态、单独成本模型
```

### 1.1 数据怎么流进来的（会话内）

```
self.messages（内存,append-only,只存"干净文本"）
   ├─ user 文本           app/ui/pet_window.py:3253(约)
   └─ assistant reply.text app/ui/pet_window.py:3290(约)   ← 工具调用轮次不进这里
        │
   发送前（pet_window 发送路径 ~3124-3137）:
   [*self.messages, 新user]
     → _add_visual_context_to_messages   （临时，不写回 self.messages）
     → _add_runtime_event_context_to_messages（临时）
     → trim_messages_for_model            （裁剪）
        │
   api_client 再把 runtime_context 作为最后一条 system 追加
     app/llm/api_client.py:693  _messages_with_runtime_context  → [*messages, {role, content}]
```

两个要点：
- **工具调用/结果不进 `self.messages`**：它们只活在 runtime 内部的 `working_messages`，一轮只留下最终 `reply.text`。
  好处是对话窗口干净；代价是第 N 轮模型看不到第 1 轮工具返回了什么（除非被写进回复文本或长期记忆）。
- **持久层 `chat_history.jsonl` 是 append-only JSONL**（`app/storage/chat_history.py:33`），
  与 Claude Code 的 transcript 模型同源。会话窗口（`self.messages`）每次启动为空，跨会话靠
  记忆召回 + `session_state` digest 续接（见 `app/agent/session_state_context.py`）。

### 1.2 静态 / 动态分层（与缓存相关）

`PromptRuntime.build`（`app/llm/prompts/runtime.py:196`）把 prompt 拆成两条独立产物：
- `system_prompt`：只来自 `recipe.blocks`（人格、tools.rules 等），段落带 `cache_scope="static"` + `static_hash`。
- `runtime_context`：只来自 `ContextSnapshot`（动态片段），追加在消息末尾。

对预算/显示功能的意义：**静态块的 token 可以只算一次并缓存**，每轮真正变化的只有
对话消息 + runtime_context（+ 偶发图片）。

---

## 2. 现状：裁剪与预算的局限

当前裁剪在 `app/llm/context_trimming.py:10`：

```python
MAX_MODEL_CONTEXT_MESSAGES = 24
MAX_MODEL_CONTEXT_CHARS = 40_000

def trim_messages_for_model(messages):
    recent = messages[-MAX_MODEL_CONTEXT_MESSAGES:]
    while len(recent) > 1 and _estimate_messages_chars(recent) > MAX_MODEL_CONTEXT_CHARS:
        recent.pop(0)          # 纯 FIFO 砍头
    return recent
```

三个局限：
1. **按字符不按 token**：CJK ≈ 1 token/字，ASCII ≈ 4 字/token，字符数与真实 token 偏差大且不稳定。
2. **只统计了对话消息**：system 人格(~4k)、工具定义(~23KB)、runtime_context(~1.8k) 都没算进这 40k 闸门，
   真实占用远大于"40k 字符"给人的印象。
3. **没有总窗口概念、没有 headroom、没有溢出兜底**：单条超大消息（大段 OCR/粘贴）仍可能把请求冲到模型上限 → API 报错。

> 注：会话内的"硬遗忘"（FIFO 砍掉的旧消息）一部分被 `memory_curation`（每 3 轮全量读历史并固化要点）
> 通过记忆召回补回，所以**不建议**为会话内溢出再上一套 LLM 摘要——长期记忆系统已覆盖大半。

---

## 3. 已有可复用资产（不要重造）

| 资产 | 位置 | 作用 |
|---|---|---|
| `estimate_prompt_tokens(text)` | `app/llm/prompts/runtime.py:71` | 保守估 token：非 ASCII 每字符 1，连续 ASCII 约 4 字符 1 token |
| `truncate_to_token_budget(text, budget)` | `app/llm/prompts/runtime.py:89` | 按 token 预算截断文本，返回 (文本, 是否被截断) |
| `ContextPolicy` 预算 | `app/llm/prompts/runtime.py:23-26,119` | runtime_context 已按 token 预算选择片段（total 4096 / plugin 2048 / memory 1024） |
| `PromptInspection.estimated_tokens` | `app/llm/prompts/types.py:129-142` | **已逐段 + 汇总**了 system_prompt 与 runtime_context 的估算 token（日志里被 redacted，但值已算出） |
| `get_last_prompt_inspection()` | `app/agent/runtime.py:151` | 取最近一次 prompt 构建的脱敏检查（含 estimated_tokens） |

**缺口**：`PromptInspection` 只覆盖 system_prompt + runtime_context，**没算**对话消息和工具定义；
`api_client` **没有解析响应里的 `usage`**（无真实 token 对账，目前只能估）。

---

## 4. Token 预算设计思路

### 4.1 真实 prompt 的 token 构成（要全量统计）

```
total_prompt_tokens ≈
    T(system_prompt)          # 已在 PromptInspection 里
  + T(tools_json)             # 缺：把工具定义序列化后估算
  + T(conversation_messages)  # 缺：对 trim 后的 request_messages 估算
  + T(runtime_context)        # 已在 PromptInspection 里
  + Σ image_cost              # 缺：每图按固定/按尺寸的成本模型估
```

建议抽象一个统一记账结构（设计草图，非最终实现）：

```python
@dataclass(frozen=True)
class PromptTokenEstimate:
    system_prompt: int       # 静态，可缓存只算一次
    tools: int               # 基本静态，工具集变化时才重算
    messages: int            # 动态
    runtime_context: int     # 动态
    images: int              # 动态
    @property
    def total(self) -> int: ...
    # 相对窗口
    context_window: int      # 按模型查
    reserved_output: int     # 给输出预留的 headroom
    @property
    def effective_window(self) -> int:   # = context_window - reserved_output
        ...
    @property
    def usage_ratio(self) -> float:      # total / effective_window
        ...
```

### 4.2 模型窗口 + 预留 headroom（借鉴 Claude Code）

当前 Sakura 没有"模型上下文窗口"的配置（`api_client` 只有 model 名 + timeout）。需要补一个
`model → context_window` 查表（带默认值），并预留输出空间：

```
effective_window = context_window(model) - reserved_for_output
```

参考量级（Claude Code，`claude-code-analysis/analysis/04f-context-management.md`）：
- 默认窗口 200k；带 `[1m]` 的模型用百万级。
- 预留 `MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000`（给压缩/输出留头）。
- 触发缩减的缓冲 `AUTOCOMPACT_BUFFER_TOKENS = 13_000`。

Sakura 用的 `gemini-3.5-flash` 窗口很大，**日常聊天几乎压不满**，所以 headroom/缩减阈值取保守值即可，
重点是"有这个概念"而不是精调。

### 4.3 预算化裁剪（替换/补充现有 char 裁剪）

把 `trim_messages_for_model` 从"按字符"升级为"按 token，且对账整体预算"：

```
budget_for_messages = effective_window
                      - T(system_prompt) - T(tools) - T(runtime_context)
                      - safety_margin
# 然后对 request_messages 做 FIFO，直到 T(messages) <= budget_for_messages
# 仍保留至少 1 条；可保留最新 user 消息优先级最高
```

### 4.4 溢出兜底（最小版，不必抄整套熔断）

- 单条消息硬上限：超长则用 `truncate_to_token_budget` 截断（已有函数）。
- 一次降级重试：整体仍超限时，砍掉最旧一批后重试一次；再失败才报错给用户。
- 不需要 Claude Code 的连续失败熔断器 / PTL 剥洋葱那种重机械（那是为编码 Agent 的极端大输入设计的）。

---

## 5. 「显示预计 token」功能落地参考

### 5.1 单一计算点
所有组成在**构建请求、调用 `api_client` 之前**那一刻全部已知（system_prompt、tools、trim 后的
request_messages、runtime_context）。在这里算一份 `PromptTokenEstimate` 是最干净的：
- runtime 侧：`AgentRuntime` 构建 snapshot/prompt 处（已有 `PromptInspection`，扩展它即可）。
- 或 UI 侧：`pet_window` 发送前（它持有 `self.messages`，能在"发送前/输入时"先估）。

### 5.2 利用静态/动态分层做增量更新
- `system_prompt`、`tools` 基本不变 → **算一次缓存**，角色切换 / 工具集变化时才失效重算。
- 用户**正在输入**时的"预计 token" = 缓存的(system+tools) + 当前 self.messages + 草稿输入 + 预计 runtime_context。
  这样可以做到随输入实时更新而几乎零成本。

### 5.3 估算 vs 真实（建议补 usage 对账）
- 现在只有估算。若想显示"真实用量"，在 `api_client` 解析响应的 `usage`
  （`prompt_tokens` / `completion_tokens` / 可能的 `cached_tokens`）即可拿到 ground truth，
  可用于：① 显示真实值；② 反过来校准 `estimate_prompt_tokens` 的系数（CJK/ASCII 比例、工具 JSON 膨胀、每图成本）。
- 注意 provider 差异：`usage` 字段名/是否返回 cached_tokens 因服务商而异，做容错。

### 5.4 UI 展示建议
- 主数字：`total / effective_window`（如 `3.2k / 1M`，或百分比环）。
- 分块明细（可折叠）：人格 / 工具 / 对话 / 动态上下文（记忆+session_state）/ 图片 —— 数据正好对应 §4.1 五块。
- 接近 headroom 时变色提示。
- 若已接 usage：并排显示"预计 vs 实际"，顺带体现缓存命中（cached_tokens）带来的省量。

---

## 6. 估算精度与校准要点
- **CJK vs ASCII**：`estimate_prompt_tokens` 已分别处理；中文为主时偏保守（略高估），可接受。
- **工具定义**：JSON 结构（括号、键名、type 字段）token 密度和自然语言不同，建议单独用 usage 校准一个膨胀系数。
- **图片**：视觉上下文按图片 token 成本模型估（多数 provider 按分辨率/patch 计），先用一个保守常数占位。
- **runtime_context**：已被 `ContextPolicy` 按 token 预算裁过，估算与实际较接近。

---

## 7. 关键文件索引
- 裁剪：`app/llm/context_trimming.py:10`
- token 估算/截断：`app/llm/prompts/runtime.py:71`（estimate）、`:89`（truncate）
- runtime_context 预算与选择：`app/llm/prompts/runtime.py:23-26,119`（ContextPolicy）
- prompt 构建（静态/动态分层 + inspection）：`app/llm/prompts/runtime.py:196`
- 检查结构（含 estimated_tokens）：`app/llm/prompts/types.py:114-142`
- 取最近一次检查：`app/agent/runtime.py:151`
- runtime_context 追加进消息：`app/llm/api_client.py:693`
- 发送路径（视觉/事件注入 + 裁剪）：`app/ui/pet_window.py:3124-3137`
- 持久化 transcript：`app/storage/chat_history.py:33`
- 跨会话注入片段：`app/agent/session_state_context.py`

## 8. 借鉴 / 不借鉴 Claude Code
（参考 `claude-code-analysis/analysis/04f-context-management.md`、`04i-session-storage-resume.md`）

**借鉴（便宜高价值）**：按 token 的整体预算、模型窗口 + 预留 headroom、溢出一次性降级、append-only transcript（已是）。

**不借鉴（对桌宠过度工程）**：auto-compact 全套（含连续失败熔断、PTL 剥洋葱、压缩后状态重注入）、
`/resume` 恢复流水线（JSONL→图重建→snip/parallel-tool-result 修复→运行时接管）、sidechain / remote ingress。
原因：桌宠故意每次空窗口启动，靠长期记忆 + session_state digest 续接；没有 subagent/跨进程协作；
所用模型窗口很大，日常聊天压不满。
