---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-26
---

# WP-4-07 定时截图与主动请求规范

> 计划中的 [WP-4-07R](WP-4-07R-typed-timeline-adaptive-context.md) accepted 后会替代本规范的请求/历史
> 角色投影；在此之前，下述 user-role 请求和 JSONL 历史仍是当前 accepted 行为。

## 产品行为

- Runtime v2 读取现有 `screen_awareness` 设置：启用、截图间隔、主动发言冷却、单次最多截图和截图
  分辨率。读取时 `enabled && screen_context_enabled` 合并为一个开关，保存时两个旧字段写成同一值。
- 缺失配置默认启用、20 分钟截图、10 分钟冷却、最多 6 张、全屏分辨率。范围分别为 1–120 分钟、
  1–120 分钟、1–20 张；分辨率只接受 `fullscreen | 720p | 1080p | 2160p`。
- 主窗口使用 10 秒普通轮询。只有 Core ready，距最近输入或手动发送、距上一张截图都达到截图间隔，
  且聊天、等待动画、打字机/TTS、手动截图或附件均空闲时才截图；忙时跳过，休眠后不补跑。
- 每次捕获鼠标所在显示器，按设置等比缩小且不放大，JPEG quality 70。第一张截图开始冷却；冷却到期
  后将最新最多 N 张按时间顺序作为一次普通聊天请求发送，然后清空批次。
- 手动发送、设置变化、generation 变化、禁用或退出立即清空批次。截图或发送失败不自动重试；清理后
  从当前时刻重新开始普通周期。

## 所有权与资源边界

- WebView 只拥有设置、普通 timer、当前批次数量和 opaque attachment ID，不接收路径、resource token、
  base64 或图像字节。
- Rust `CaptureManager` 使用 `VecDeque` 保存 JPEG bytes，同时受设置张数和 64 MiB 总量限制；超限删除
  最旧帧。原图不提前落盘，也不投影给 WebView。
- 发送时 Rust 才创建 generation 私有临时资源并调用 `screen.attachBatch`。Core 单次消费后立即删除；
  成功、拒绝和中途失败都清理剩余资源。现有单图 `screen.attach` 行为不变。
- Core 一个 attachment ID 可对应一至多张图片。自动批次不生成 `VisualObservationJob`，不写
  `visual_observations.jsonl`，也不进入 legacy `screen_awareness_check` 事件系统。

## 请求与历史

固定请求全文为：

> 这是一次由 Sakura 定时截图触发的主动屏幕观察。以下截图按时间顺序展示我最近正在做的事情。请结合最近聊天历史和这些截图，以当前角色的语气自然接话：可以评论变化、接续任务、询问卡点或提供轻量帮助。不要逐张复述，也不要因为时间或久坐机械地提醒休息；如果没有明显变化，就简短说出你能确认的具体内容。

历史保存该全文并追加 `[已附加 N 张定时屏幕截图]`。历史不得保存图片、base64、路径或 resource token。
请求继续复用 `chat.send`、现有回复事件、角色表现、TTS 和历史链。

## 接口

- Core：`screen_awareness.settings.get`、`screen_awareness.settings.save`、
  `screen.attachBatch { resources: ScreenResourceDescriptor[1..20] }`。
- `screen.attachBatch` 返回 `{ attached: true, attachmentId, count }`。
- Tauri：`settings_screen_awareness_get`、`settings_screen_awareness_save`、
  `capture_screen_awareness_frame`、`attach_screen_awareness_batch`、`clear_screen_awareness_batch`。
- 设置保存成功后发布一次 `sakura://screen-awareness-settings`。事件失败不重试；持久化值在下次启动生效。
- 设置 capability 为 `privacy.screen_awareness = available`。不修改 `chat.send`、聊天事件、TTS 或手动
  截图公开结构。

## 失败与验收

- 权限拒绝、无显示器、编码、Core、Provider 或发送失败都必须显式结束本轮并清理资源，不得破坏普通聊天。
- 自动门覆盖设置兼容与原子保存、批量 JPEG 单次消费、历史隐私、分辨率和不放大、最新 N 张、64 MiB、
  generation 清理、前端假时钟、忙时跳过、休眠不补跑和发送失败释放。
- 扩展既有 `journey-screen-capture`，不新增 Harness profile。
- WP-4-07 只有自动门、Windows/macOS/Linux 实机行为和负责人验收全部通过后才能 accepted。

2026-08-25 的自动验证与负责人验收记录分别见
[`WP-4-07-AUTOMATED-VALIDATION.md`](../../records/audits/WP-4-07-AUTOMATED-VALIDATION.md) 和
[`WP-4-07-OWNER-ACCEPTANCE.md`](../../records/audits/WP-4-07-OWNER-ACCEPTANCE.md)。当前执行状态以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

## 非目标

CAP-017 提醒与待办不属于本 WP，保持未排期。本 WP 不实现 Scheduler、提醒、待办、视觉摘要、磁盘批次、
额外 Worker、自动恢复、自愈、任务图、lease、outbox、ack、补跑或通用主动事件协议，也不为这些能力预留接口。
