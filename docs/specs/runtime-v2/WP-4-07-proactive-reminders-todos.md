---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-09-19
---

# WP-4-07 定时截图与主动请求规范

主动屏幕感知由普通插件 `sakura.screen_awareness` 提供。插件拥有设置、采样时钟、冷却、批次选择和
专属提示词；宿主拥有截图权限、受控图像资源、角色会话、聊天受理、历史提交与实际音画播放。
请求与历史遵循 [Typed Timeline](WP-4-07R-typed-timeline-adaptive-context.md)。

## 产品行为

- 缺失配置默认启用、20 分钟截图、10 分钟冷却、最多 6 张、全屏分辨率。范围分别为 1–120 分钟、
  1–120 分钟、1–20 张；分辨率只接受 `fullscreen | 720p | 1080p | 2160p`。
- 插件每 10 秒以单调时钟检查一次事实。距最近输入活动、距上一张截图均达到截图间隔且 UI 空闲时才截图；
  忙时跳过，休眠后不补跑。UI 空闲涵盖聊天、等待动画、打字机、输入法组合、手动截图或附件、ASR、
  更新播报和执行中的插件表现控制。主窗口只报告活动版本和空闲事实，不执行采样策略。
- 每次捕获鼠标所在显示器，按设置等比缩小且不放大，JPEG quality 70。第一张截图开始冷却；冷却到期后，
  插件将最新最多 N 张按时间顺序交给统一聊天入口，随后清空批次。
- 主动请求生成期间保持原画面，不显示思考占位符或等待动画。完整回复沿用现有分段打字、角色表现和 TTS
  消费流程；手动聊天仍显示正常思考状态。取消、重复终态和过期 generation 复用同一聊天边界。
- 受理新的聊天、设置变化、会话变化、停用、窗口关闭和退出会回收批次。关闭功能开关或停用插件也取消
  尚未完成的主动请求。截图或发送失败结束本轮，从当前时刻重新开始普通周期，不重试当前请求。
- 主动屏幕感知设置由插件贡献到“交互”页，保存经过通用 Plugin Settings。关闭或卸载本插件不影响手动截图。

## 配置兼容

插件 `config` 是生产保存入口，字段为 `enabled/checkIntervalMinutes/cooldownMinutes/batchLimit/resolution`。
首次加载时，宿主读取旧 `screen_awareness` 配置，把 `enabled && screen_context_enabled` 合并为开关，
只补齐插件配置中缺少的字段。已有插件配置优先；旧 YAML 保持可读，不回写、不双向同步，不需要重填设置。

## 受控截图接口

普通插件通过 `sakura.host.screen.capture({sessionId, resolution})` 请求截图，返回
`{resourceId, sessionId, width, height, capturedAt, screenName}`。调用者由 Plugin Runtime 的实例 scope
确定，不能通过参数冒充。`release(resourceId)` 回收未消费的图片。

资源句柄属于当前调用实例与角色会话，不暴露文件路径、像素或原生 token。宿主每个实例最多保留 20 个句柄、
128 MiB 编码内容，并只允许一个在途截图请求。插件在达到设置张数上限前先释放最旧帧。
切换角色、实例退出和 generation 关闭均撤销句柄；切回同名角色不能恢复旧会话。

Rust 只执行原生截图、编码和临时资源发布，不维护主动截图批次。每帧复用
[受控图像资源](WP-4-06-screen-capture-controlled-image-resource.md) 的 generation 私有临时文件和单次消费校验。
Core 消费原生文件后保存内存句柄；迟到结果、取消和传输失败也必须删除原生文件，不把资源留到下一轮。
手动多截图继续使用独立的 `screen.attach/remove/release` 和手动附件组。

## 通用主动聊天接口

`sakura.host.chat.current()` 返回 `{sessionId, characterId, idle, activityRevision, interactionRevision}`。
其中 `interactionRevision` 在受理任意来源的新聊天时递增，即使随后取消且没有写入历史也保持递增。
插件以此清理过时截图，不依赖前端区分功能名。
`submit({sessionId, message, resources})` 接受插件组织的文本和图片句柄，返回
`{accepted: true, operationId}`，或 `{accepted: false, reasonCode}`。`cancel(operationId)` 只能取消
当前实例自己的操作。会话、资源所有权和 UI 活动均在受理时重新校验；忙碌时直接拒绝，不增加排队系统。

来源实例退出时，Host 向 RealChat 请求取消，不自行发布业务终态。回复写入 Timeline 并认领完成后，
取消返回 `accepted: false`；已经向桌面发布 `started` 的操作仍交付 RealChat 决定的唯一终态，
与历史和 Trace 保持一致。尚未发布 `started` 就被撤销的操作不再向桌面交付事件。

宿主把请求送入既有 RealChat 排他和取消边界。默认 Assistant 不识别屏幕感知功能名，也不替换它的提示词。
来源 `sourcePluginId` 由宿主绑定，记录为 `origin: host` 的 `OBSERVATION`，不发布人类消息、不写 `HUMAN`。
Timeline 保存中性观察及实际回复，不保存原图、base64、路径、资源句柄或插件业务提示词。空回复是正常完成。

桌面订阅接收 `host.chat.started/completed/failed/cancelled`，原生层投影为普通聊天事件和 `silent` 表现方式，
继续复用现有 WebView reducer、取消句柄、字幕、表现与语音链。主窗口销毁时，宿主撤销 UI 空闲事实、图片和
在途主动聊天；重新建立连接后才可重新受理。无需持久化任务、第二套播放状态机或专属屏幕聊天命令。

## 失败与验证

- 权限拒绝、无显示器、编码、Core、模型提供者或发送失败都结束本轮并回收资源，普通聊天保持可用。
- `journey-screen-capture` 覆盖原生资源单次消费、历史隐私、尺寸、不放大、会话/实例回收、假时钟策略、
  配置交接、停用和迟到回包；前端验证订阅注册竞态、静默展示、取消与过期 generation 隔离。
- 自动化证据不替代 Windows/macOS/Linux 实机权限、多显示器和桌面播放验证。

2026-08-25 的旧实现验证记录保留于
[`WP-4-07-AUTOMATED-VALIDATION.md`](../../records/audits/WP-4-07-AUTOMATED-VALIDATION.md) 和
[`WP-4-07-OWNER-ACCEPTANCE.md`](../../records/audits/WP-4-07-OWNER-ACCEPTANCE.md)。这些历史记录不代表本次插件化的
实机验证结果。执行状态见 [`work-packages.md`](../../plans/runtime-v2/work-packages.md)。

## 非目标

CAP-017 提醒与待办仍未排期。本次只迁移已有主动屏幕策略，不新增调度平台、磁盘批次、持久任务队列、补跑或自愈。
