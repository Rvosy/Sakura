---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-19
---

# ADR-0060：共享对话入口与普通 Assistant 插件

## 背景

桌面、Mobile、默认 Agent、Rust Gateway 与 ChatBridge 曾分别维护同一次聊天的部分状态。默认 Assistant 又直接依赖 Core 私有实现，
使插件替换需要改变宿主调用链。设置延迟到下次聊天应用、语音提交前探测以及同步观察通知，也让一次普通请求承担了其他资源的恢复工作。

[ADR-0054](0054-retire-executor-experiment-and-scope-collections.md) 删除了无实际消费者的备用执行器，并要求未来通过真实公开边界替换默认路径。
本决策落实这项替换，取代其中“默认消费者仍在 Core、正常聊天调用 ChatPipeline”的阶段性安排；Collection 归属及其他数据保护决策继续适用。

## 决策

Core 的 `RealChatBoundary` 作为唯一聊天用例，负责受理、当前角色、取消、Timeline 与最终提交。桌面协议和 Mobile 分别解析各自输入后调用它。
Mobile 在返回 job 前完成同步受理，避免取消早于 worker 启动时被忽略。

默认 Assistant 提供普通 `sakura.assistant` Service，使用现有插件进程和依赖隔离。模型循环、上下文选择、Prompt、回复协议和 Trace 归插件；
宿主暴露中性的会话、完整轮次历史页、工具和 Context 合同。Core 不保留备用 Agent 或失败后回退实现。

启动由同一个 Core 初始化 worker 分阶段完成：先启动当前角色表现所需的服务和硬依赖并发布角色，再准备 Assistant 并发布聊天状态，
最后启动其余已启用插件。尚未准备好聊天时可以显示角色，但不能受理聊天。各阶段仍按完整插件图检查冲突与依赖，
失败的插件不自动重试；可选插件失败不覆盖已经发布的角色或聊天状态。关闭先回收正在启动或已发布的 Application，再等待 worker 结束。

跨进程任务采用短调用 `begin/poll/result/cancel/release`，始终关联同一 `operationId` 和固定的 `providerId + scopeId`。
大输入、工具图片、历史页与结果使用 Host Artifact，保留既有帧上限。最终结果提交与服务失效共用实例绑定锁；
取消与完成也在 Core 中仲裁。启动确认丢失时不重放，无法确认 worker 已停止时由现有 Manager 回收精确实例。

Rust 删除 Gateway 的重复聊天状态，`ChatBridge` 保留一个界面投影，每轮用 Tauri Channel 传递操作事件。
Channel 解决订阅与路由，不代表跨进程消息具有 exactly-once 保证；早到终态、迟到回复与取消传输失败仍有明确处理。

模型连接采用官方 OpenAI Python SDK，关闭网络自动重试，保留有界的第三方参数兼容与默认助手回复修复。
同一轮固定配置，实际网络尝试可追踪。Core 的模型设置探测只依赖独立探测能力，不导入默认 Agent。

Core 的正文提交不等待语音健康探测、播放或普通观察通知。桌面分段呈现仍须同步：有语音时等该段音频准备完成，
在播放开始时一起切换立绘与字幕，字幕和音频都结束后才进入下一段。语音失败时按纯文字回退，继续保护角色、
音频实例、取消与录音互斥。2026-09-19 更正：先前“语音独立排队”的描述误将 Core 提交与桌面呈现混为一谈，
不符合原有分段同步要求；具体行为见 [TTS 播放规范](../specs/runtime-v2/WP-4-05-tts-playback-audio-device-gate.md)。
Service 调用返回结果，普通观察通知使用有界队列，生命周期撤销仍由资源拥有者等待完成。
设置操作当场报告保存和应用结果，下一次聊天不执行遗留回调。

## 考虑过的方案

- 引入完整 Agent 框架：会同时引入会话、历史、工具与任务状态，重复现有产品责任；本次只复用模型 SDK。
- 保留 Core Agent 作为回退：会形成两条生产路径，插件失败还可能隐式重复模型或工具调用。
- 将长任务直接塞进一个短 RPC：普通超时无法说明 worker 是否退出，也难以保留取消通道。
- 删除进程隔离或所有迟到检查：会失去依赖隔离与可终止性，旧任务仍可能影响新角色或新插件实例。

## 后果与兼容

替换 Assistant 只需提供同一服务合同，宿主和 UI 无须增加模型实现分支。官方默认插件与第三方插件走同一 Runner。
发行包和开发依赖准备必须包含默认 Assistant 及其私有依赖；缺少插件时明确显示不可用，不静默退回 Core 实现。

Timeline、角色包与旧版显式导入继续沿用现有格式。历史预算移入默认 Assistant，并以完整轮次分页读取；读取授权固定角色与快照，
不另建历史数据库，也不依靠扫描用户目录或内容摘要证明正确性。

停用或重载插件后的旧结果会失败，不能冒充新实例的回复。用户可以显式重新发送；有副作用的工具和结果未知的请求不自动重放。
真实音频设备、WebView 和多显示器体验仍需对应平台验证，单元测试与本地服务集成不能替代这些证据。

当前代码地图见[技术架构](../devdocs/TECHNICAL_README.md)，公共边界见 [Plugin Runtime](../specs/runtime-v2/sakura-plugin-runtime-v4.md)。
