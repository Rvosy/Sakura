---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-09-18
---

# WP-4-07 定时截图与主动请求规范

请求与历史遵循 [Typed Timeline](WP-4-07R-typed-timeline-adaptive-context.md)：主动观察作为
`OBSERVATION` 保存，不能伪装成人类消息。本文定义采集策略和原生资源边界。

## 产品行为

- Runtime v2 读取现有 `screen_awareness` 设置：启用、截图间隔、主动发言冷却、单次最多截图和截图
  分辨率。读取时 `enabled && screen_context_enabled` 合并为一个开关，保存时两个旧字段写成同一值。
- 缺失配置默认启用、20 分钟截图、10 分钟冷却、最多 6 张、全屏分辨率。范围分别为 1–120 分钟、
  1–120 分钟、1–20 张；分辨率只接受 `fullscreen | 720p | 1080p | 2160p`。
- 主窗口每 10 秒上报一次 UI 空闲和输入活动事实；Core 使用单调时钟决定采集和主动请求。只有 Core
  ready，距最近输入或手动发送、距上一张截图都达到截图间隔，且 UI 空闲时才截图；忙时跳过，休眠后不补跑。
  UI 空闲事实涵盖聊天、等待动画、打字机、输入法组合、手动截图或附件、ASR 和更新播报。
- 每次捕获鼠标所在显示器，按设置等比缩小且不放大，JPEG quality 70。第一张截图开始冷却；冷却到期
  后将最新最多 N 张按时间顺序作为显式 `screen_observation` 输入交给同一个聊天用例，然后清空批次。
- 主动请求生成期间主界面保持原有画面，不显示思考占位符或等待动画；完整回复到达后才直接进入现有的
  分段打字、角色表现和 TTS 流程。手动聊天仍显示正常思考状态。
- 手动发送、设置变化、generation 变化、禁用或退出立即清空批次。截图或发送失败不自动重试；清理后
  从当前时刻重新开始普通周期。

## 所有权与资源边界

- Core 拥有设置、采样时钟、冷却、批次数量及触发决策。WebView 只拥有事实轮询、原生执行和晚到结果
  隔离；它不保存业务 Prompt、截图间隔或冷却时钟，也不接收路径、resource token、base64 或图像字节。
- 默认 Assistant 根据显式来源构造多模态输入和主动接话 Prompt；Core 只传中性的屏幕事实和受控附件。
- Rust `CaptureManager` 使用 `VecDeque` 保存 JPEG bytes，同时受设置张数和 64 MiB 总量限制；超限删除
  最旧帧。原图不提前落盘，也不投影给 WebView。
- 清空原生批次同时递增资源 revision；清空之前开始、之后才完成的截图不能再次加入队列。主窗口销毁
  和设置保存成功都在原生层清空，不依赖前端接到事件。Core 在设置变化后的下一次事实请求也返回清空，
  防止客户端断开再连接时复用旧批次。
- 发送时 Rust 才创建 generation 私有临时资源并调用 `screen.attachBatch`。Core 单次消费后立即删除；
  成功、拒绝和中途失败都清理剩余资源。手动多截图使用 `screen.attach` 维护最多 6 项的待发送组；主动
  截图仍通过独立的 `screen.attachBatch` 一次性建立批次，不与手动组混合。
- Core 一个 attachment ID 可对应一至多张图片。自动批次不生成 `VisualObservationJob`，不写
  `visual_observations.jsonl`，也不进入 legacy `screen_awareness_check` 事件系统。

## 请求与历史

请求采用 `chat.send { operationId, event: { type: "screen_observation" }, attachmentId }`。
必须同时具备主动批次附件和显式来源；普通 `message` 文本不能赋予主动观察身份。该输入不发布人类
`message.user` 事件，不写入 `HUMAN` 历史。Timeline 只保存中性 `OBSERVATION` 及实际产生的回复，
不得保存图像、base64、路径、resource token 或 Assistant 的业务 Prompt。空回复是正常 no-op，
不得归为更新播报失败。请求继续复用 `chat.send` 的排他、取消、回复事件、角色表现和 TTS 链。

## 接口

- Core：`screen_awareness.settings.get`、`screen_awareness.settings.save`、`screen_awareness.step`、
  `screen.attachBatch { resources: ScreenResourceDescriptor[1..20], sessionId }`。
- `screen_awareness.step` 接收 `sessionId, idle, activity, reset`，采集完成时另外接收 `revision, count`；
  返回 `none | clear | capture | submit` 决策及当前 revision。`capture` 附带分辨率和批次上限，`submit`
  附带实际批次数量。会话或设置变化使旧 revision 失效；主窗口只执行结果，不重新推导策略。
- 每帧采集前读取 `screen.session`，原生内存批次保留该会话标记；发送时仅物化当前会话的帧。
  Core 读取资源前及接纳批次前检查 `sessionId`。前端切换角色或清空批次后，旧采集和附件请求的迟到结果不得触发发送。
- `screen.attachBatch` 返回 `{ attached: true, attachmentId, count }`。
- Tauri：`settings_screen_awareness_get`、`settings_screen_awareness_save`、
  `screen_awareness_step`、`capture_screen_awareness_frame`、`attach_screen_awareness_batch`、
  `clear_screen_awareness_batch`、`chat_screen_observation`。原生层只转发事实、注入当前角色会话标记并执行截图。
- 设置保存成功后发布一次 `sakura://screen-awareness-settings`。事件失败不重试；持久化值在下次启动生效。
- 主动屏幕感知设置归入“交互”页，不再单列“隐私”导航；设置 capability 在 `interaction` section
  暴露 `privacy.screen_awareness = available`。保留既有配置键、`chat.send`、聊天事件和 TTS 接口。

## 失败与验收

- 权限拒绝、无显示器、编码、Core、Provider 或发送失败都必须显式结束本轮并清理资源，不得破坏普通聊天。
- 自动门覆盖设置兼容与原子保存、批量 JPEG 单次消费、历史隐私、分辨率和不放大、最新 N 张、64 MiB、
  generation 清理、Core 假时钟策略、UI 晚到结果隔离、忙时跳过、休眠不补跑和发送失败释放。
- 扩展既有 `journey-screen-capture`，不新增 Harness profile。
- WP-4-07 只有自动门、Windows/macOS/Linux 实机行为和负责人验收全部通过后才能 accepted。

2026-08-25 的自动验证与负责人验收记录分别见
[`WP-4-07-AUTOMATED-VALIDATION.md`](../../records/audits/WP-4-07-AUTOMATED-VALIDATION.md) 和
[`WP-4-07-OWNER-ACCEPTANCE.md`](../../records/audits/WP-4-07-OWNER-ACCEPTANCE.md)。当前执行状态以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

## 非目标

CAP-017 提醒与待办不属于本 WP，保持未排期。本 WP 不实现 Scheduler、提醒、待办、视觉摘要、磁盘批次、
额外 Worker、自动恢复、自愈、任务图、lease、outbox、ack、补跑或通用主动事件协议，也不为这些能力预留接口。
