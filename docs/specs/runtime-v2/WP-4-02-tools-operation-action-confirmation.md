---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-14
---

# WP-4-02 内置 Tools、Operation 与 Action ID 确认规范

## 1. 范围与目标

> 当前产品覆盖：[`ADR-0020`](../../adr/0020-assistant-direct-tool-execution.md) 已替代本文关于“助手阶段
> 激活二次确认”的结论。当前所有助手工具在校验后直接执行，`PendingToolAction`、Action ID 和原生确认
> 仅作为未来 Agent 插件权限设计的未启用基础设施保留。本文其余 Action ID 章节记录该基础设施的安全
> 契约，不代表当前产品会创建确认租约。

本规范冻结 CAP-009/010 的第一条 Runtime v2 纵向链：真实聊天可以发现并调用已批准的内置工具，
有副作用的调用由 Core 保存不可变参数并以一次性 Action ID 请求确认，Tauri 使用应用拥有的原生提示
取得用户决定。工具成功、失败、拒绝、超时、取消和 generation 失效均收敛到当前聊天 Operation 的
唯一终态，WebView 不能提交工具名、工具参数、generation、deadline 或确认后的替代参数。

执行状态只以 [`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。本文定义行为和数据契约，
不复制 `active`、`stabilizing` 或 `accepted` 状态。

## 2. 真实消费者与允许工具

本 WP 的真实消费者是 WP-3-04 已接入的真实 Assistant 聊天，工具提供者是 WP-3-01 的无 Qt
Assistant Session。只开放以下工具：

| 工具 | 行为 | 副作用与确认 |
|---|---|---|
| `get_current_time` | 返回 Core 所在本机的带时区时间 | 无副作用，不确认 |
| `memory_search` | 调用 WP-4-01 当前角色 Memory boundary | 只读，不确认 |
| `memory_remember` | 在当前角色 scope 保存长期记忆 | 当前助手直接执行 |
| `memory_update` | 按当前角色 scope 更新既有记忆 | 当前助手直接执行 |
| `memory_forget` | 按 ID 删除当前角色记忆 | 当前助手直接执行 |

Memory 工具必须复用 WP-4-01 已验收的 generation 私有 owner、初始化状态、外部存储降级、写入队列和
错误分类，不得另建 `MemoryStore`、Qdrant client、FastEmbed/ONNX 进程或数据 schema。工具结果只返回
模型继续推理所需的公开 DTO；内部路径、provider credential、原始 exception、embedding/vector、
generation credential 和 continuation context 不进入 response、event、Snapshot 或日志。

以下能力明确不属于本 WP：

- Todo、提醒、任务与调度，归 WP-4-07；
- 屏幕观察、截图和图像资源，归 WP-4-06；
- MCP、Python 插件及其工具贡献，归 WP-4-03/04；
- 浏览器自动化、桌面控制、`open_url`、`open_local_folder` 和受控外部进程，归对应平台/浏览器 WP；
- TTS、音频、Studio、导入导出、resource token、通用任务图和 worker process 平台。

## 3. Operation 与 Action ID

### 3.1 Operation 边界

`chat.send` 产生的 `operationId` 仍是一次用户聊天交互的唯一 Operation。模型在该交互内发起的工具
调用是它的子步骤，不另造可独立恢复的顶层 Operation，也不新增通用 `operation.*` 协议事件。

聊天与 Tools 已证明的共性只冻结为下列最小规则：

- identity 归当前 Core generation；generation 改变后旧 identity 全部失效；
- `chat.cancel`、Core shutdown/EOF/crash、窗口退出和确认超时必须能取消正在规划、等待确认或执行工具的
  当前 Operation；
- `chat.started` 后最终仍只能发布一个 `chat.completed`、`chat.failed` 或 `chat.cancelled`；
- 工具子步骤的成功或失败是模型输入，不自动成为第二个聊天终态；只有边界级不可恢复错误终止聊天；
- registry、等待确认项、worker、timer、request、临时对象和 Memory 调用在终态或 generation 关闭后归零。

这实现 ADR-0002 的“出现第二个真实消费者后只提取已证明共性”要求，不冻结 progress 百分比、三级业务
优先级、跨域公平性、资源 token、断点恢复或未来 MCP/导入任务模型。

### 3.2 Action ID

当 ToolRegistry 判定需要确认时，Core 必须：

1. 在完成工具存在性、能力、参数 schema、当前角色 scope 和确认策略校验后，创建至少 128-bit 随机
   `actionId`；不得沿用模型 `tool_call_id`、request ID 或可预测计数器。
2. 在 generation 私有 pending-action store 中保存完整 `PendingToolAction`，并绑定
   `generationId + operationId + actionId`、单调时钟到期点和 `pending` 状态。
3. 只向 Rust 发布原生提示所需的公开确认 DTO：`actionId`、稳定 `title`、脱敏 `summary`、风险级别和
   到期时间。完整工具参数、continuation messages、tool call context 和 credential 不进入 WebView。
4. 只接受 Rust 内部路径提交的 `{actionId}` 决定。协议 payload 不允许工具名、arguments、risk、
   operationId、generation 或 deadline；额外字段一律拒绝。
5. 以 compare-and-consume 方式一次性取出保存对象后执行或拒绝；重复、未知、旧 generation、错误
   operation、已过期和已消费 ID 均失败安全，且绝不执行工具。

Action ID 是授权一次既存参数快照的租约，不是用户数据 ID、Operation ID、通用 capability token 或可
跨 generation 恢复的凭据。日志只记录截断后的 Action ID、工具名、风险和状态，不记录完整参数。

## 4. 原生确认与焦点

- 确认提示由 Rust/Tauri 应用根拥有并调用平台原生对话能力；主 WebView 不渲染确认按钮，也不注册
  `confirm_action(arguments)` 一类可伪造参数的 command。
- 提示必须显示动作类型、脱敏目标摘要、风险和“执行/取消”。Memory 内容只显示有界摘要；不得显示
  continuation context、模型原始 JSON 或 secret-shaped 字段。
- 同时最多存在一个前台确认。提示出现时主窗口保持当前聊天 Operation，不允许发送第二条消息；取消
  按钮仍可取消整个 Operation。
- 对话框关闭、显式取消、应用失焦后关闭、窗口退出均视为拒绝，不得视为确认。平台无法创建原生提示时
  返回稳定失败并保持工具未执行。
- 默认确认租约为 60 秒；到期后 Core 原子消费为 `expired`，Rust 的晚确认只得到失败安全结果。超时后
  Agent 以“未获确认”工具结果继续一次收尾推理，不能自动再次弹窗。
- Core crash/restart 后 Rust 必须关闭旧提示；旧对话框的晚结果不能进入新 generation。

## 5. 工具执行与错误语义

- ToolRegistry 是当前 Assistant Session 的 generation 私有对象；只由 Adapter 显式注册第 2 节清单，
  不得先创建全部 Legacy built-ins 再做删除过滤。
- 参数在准备确认前和实际执行前各校验一次。Action ID 消费后若工具定义、角色 scope 或 generation 已
  改变，执行失败而不是使用旧引用。
- Memory 工具调用必须轮询现有 cancel checker；`loading`、`degraded`、不存在 ID、只读/未来 schema、
  外部存储失败和取消返回稳定公开结果，不泄漏内部异常。
- 只读工具可以同步完成；写工具必须沿用 Memory boundary 的有界执行与关闭语义。本 WP 不承诺任意
  Python handler 的通用强杀能力，也不允许无界 daemon thread。
- 单步/整轮工具数继续由现有 `RuntimeLoopSettings` 约束。超过上限、相同参数重复失败或确认被拒绝后，
  Agent 必须收敛回复，不能无限循环或反复请求同一确认。

## 6. 协议与公开 DTO

协议 minor/capability 升级必须显式协商 `tools_v1`。未协商时 Core 不公开工具、Rust 不接受工具事件，
聊天继续按无 Tools 路径工作。

最小新增协议语义：

```text
event tool.confirmation.requested
  id = chat request/operation identity
  payload = { actionId, title, summary, risk, expiresAt }

request tool.confirm
  payload = { actionId }

request tool.reject
  payload = { actionId }
```

`tool.confirm`/`tool.reject` 是 control 类请求，不能排在正在等待确认的聊天 worker 后；response 只公开
`accepted`、`actionId` 和稳定拒绝码。工具开始/完成的用户可见进度若需要，只作为当前聊天的受控展示
状态，不冻结通用 `tool.result` 或 `operation.progress` event。

Rust Gateway 必须校验事件来自当前 generation/credential、关联当前 operation、字段集合和长度；未知、
重复、旧 generation 或第二个并发确认事件全部关闭对应聊天 Operation并产生稳定错误，不能弹提示。

## 7. Tools 设置切片

本 WP 的当前产品只开放一个 feature：

- `tools.runtime_limits`：`max_agent_steps_per_turn`、`max_tool_calls_per_step`、
  `max_tool_calls_per_turn`；继续使用既有有界归一化，整轮上限不得小于单步上限。
- `tools.confirmation_policy` 当前为 `unavailable`。`risk_based` / `confirm_writes` 仅作为旧配置兼容值读取和
  保存，不影响助手工具执行，也不在设置页展示。

兼容映射继续写入 `data/config/system_config.yaml` 的既有 `tool_loop.*` 和
`ui.free_access_enabled` 字段：`risk_based=true`、`confirm_writes=false`。Python Core 是校验和原子保存
所有者；WebView 只持有草稿，Rust 注入 settings window generation 和 request identity。保存必须保留
未知字段，损坏 YAML 或 `config_version > 4` 只读，临时写/flush/replace 失败时旧文件和当前运行值不变。

保存成功返回 `core_restart_required`，由既有 Supervisor 受控重建 Core；设置窗口按 WP-4-01A 已验收的
原位 generation 重绑定保留草稿、焦点和 IME。`windowsMcp` 控件继续 `unavailable`，由 WP-4-03 迁移。

## 8. 故障矩阵

| 场景 | 必须结果 |
|---|---|
| 未知工具、非法/超限参数、未协商 capability | 工具不执行；稳定失败；聊天可收尾或唯一失败 |
| 模型伪造 action/request/generation 字段 | 准备阶段拒绝；不创建 pending action |
| WebView 调用不存在的确认 command 或尝试附带参数 | 无授权路径；Core 保存参数不变；工具不执行 |
| 重复 confirm/reject、confirm 与 reject 竞态 | 恰有一个 consume 成功；至多执行一次 |
| 确认超时、对话框关闭、应用退出 | 视为拒绝/取消；pending store、dialog、timer 归零 |
| `chat.cancel` 与确认/执行竞态 | 当前聊天只有一个终态；确认晚结果和工具晚结果丢弃 |
| Core crash/restart、旧 generation event/response | 旧 Action ID 和提示失效；新 generation 不执行旧参数 |
| Memory loading/degraded/只读/不存在 ID/外部存储失败 | 公开稳定工具结果；聊天和 control 保持可用 |
| settings 非法值、损坏/未来 YAML、权限/replace 失败 | 零部分写；旧运行态与旧文件保持；重新打开一致 |
| shutdown/EOF/窗口关闭期间正在规划、确认或执行 | health/shutdown 不被阻塞；线程、请求、store、dialog、timer、进程归零 |

## 9. Journey 与退出条件

`journey-tools` 必须使用 deterministic/local Provider 走真实 Tauri Gateway + bundled Core 路径，至少覆盖：

1. `get_current_time` 与 `memory_search` 无确认完成，工具结果回填模型并产生唯一聊天终态；
2. `memory_remember`、`memory_update` 与 `memory_forget` 在兼容配置取任意旧值时均直接执行；
3. 当前助手入口不创建 Action ID；保留的底层 coordinator 对篡改、重复、过期和旧 generation ID 仍失败安全；
4. 拒绝、对话框关闭、超时、`chat.cancel`、Core crash/restart 和退出资源归零；
5. Tools 设置读取、非法保存、原子失败、成功保存、Core restart 和窗口原位重绑定；
6. 真实 Windows 候选上工具无确认直接执行、设置保存/重开和正常退出；公共代码在同一
   SHA 取得 Windows/macOS/Linux 自动门。

Journey 使用专用 fixture 和定向 Python/Rust/frontend cases，不重复收集 `python-full`。退出前还必须
满足：CAP-009/010 行为存在真实纵向证据；P0/P1、参数伪造、重复执行、credential/路径泄漏、用户数据
损坏、线程/请求/提示/进程残留为零。

## 10. 回退

先把 `tools.runtime_limits`、`tools.confirmation_policy` 与 `tools_v1` capability 恢复为不可用，阻止新
工具调用并拒绝所有 pending Action ID；取消/排水当前聊天 Operation，关闭原生提示并确认 Core/Router/
writer/thread/timer/pending store/Memory worker 和进程树归零，再回退 Gateway、Core、设置和前端接线。

回退不删除、恢复或重写用户现有 Memory 与 `system_config.yaml`。旧版本继续读取既有
`tool_loop.*`/`ui.free_access_enabled`；若发现写入安全缺陷，Runtime v2 对 Tools 设置退回只读。
