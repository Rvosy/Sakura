---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-19
---

# ADR-0061：模型提供方与插件主动互动

## 背景

默认 Assistant 已经是普通插件，但仍直接构造远程模型客户端。主动屏幕感知的调度和专属设置分布在 Core、Shell 与前端，
其他插件也缺少提交表现控制的公开入口。新增模型来源或互动功能因此仍需改动宿主。

## 决策

沿用 Plugin Runtime v4 的 Service、固定实例绑定、配置和生命周期。新增模型服务合同，引用采用
`{serviceKey, profileId, modelId}`。提供方拥有连接、凭据、模型目录和能力描述，消费者通过公开 SDK 调用
`catalog/describe/begin/poll/result/cancel/release`。不增加模型注册器进程或另一套能力总线。

默认仍随包启用远程 `sakura.model.openai_compatible`。OpenAI SDK、HTTP 传输、连接探测和协议兼容归该插件；
Assistant 保留 Prompt、上下文和工具循环，Memory 保留整理策略、游标及本地向量存储。没有模型服务时，
宿主仍可启动、显示角色并管理配置；Memory 的手动管理和本地召回不因此停用。本地推理、权重下载和 Steam 离线安装留待后续。

配置所有者各自持久化。Core 只保存默认槽位引用；旧配置交接不改写源文件，也不覆盖已存在的新配置。
提供方冻结当前有效配置，保存与应用有明确区别。Assistant Session 记录模型服务的 `providerId/scopeId`，
重载后旧 Session 不能重新绑定同名新实例。恢复来自显式操作；结果未知的任务不重放。

大输入和结果使用现有 Artifact。显式交付同时绑定发送方、接收方 scope 和 operationId，任一生命周期结束都回收该任务文件；
普通工具图片继续遵守原有接收者持有规则。RPC 截止时间贯穿消费者、Router 和目标实例；任务取消与资源回收共用有界预算。

主动屏幕感知作为普通功能插件，持有采样、批次、冷却、设置和提示词。宿主只提供当前会话状态、受控截图及主动聊天受理。
插件受理的互动复用既有 `RealChatBoundary`、Timeline、`ChatBridge` 和分段音画呈现，不单独保存聊天或维护播放链。
窗口失效、角色切换、取消和插件退出使旧资源及控制失效。

`sakura.host.visual` 允许插件读取当前表现合同、提交控制并查询播放回执，也允许选择角色已有资源。目标包含角色、绑定和资源 ID。
提供方解释控制，宿主仲裁受理，原 RendererHost 执行。选择仍复用角色设置事务；保存成功但应用失败必须明确返回，不能声称已经显示。

插件页沿用角色与领域两个维度，系统组件默认折叠但保留错误数量。搜索和依赖跳转可以展开对应分组。
角色表现使用独立领域分类；普通合同本身不作为可启停插件展示。

## 取舍与验证

提供方不能只贡献一个 URL，否则消费者仍要掌握认证、协议和配置。也不把 Assistant 的工具循环搬到模型提供方，避免模型实现承担对话策略。
这项边界允许未来接入本地实现，但本轮不实现没有消费者的调度平台或模型下载框架。

验证覆盖真实插件进程间的大消息、两个消费者的取消隔离、过期绑定、保存未应用、未知启动确认，以及桌面事件的顺序和单一互动槽。
Core 在无远程实现或缺少模型依赖时仍须可管理。真实设备上的截图、音频和窗口体验以实际执行结果为准，自动测试不代替设备证据。

公共合同见[模型服务](../specs/runtime-v2/model-services.md)、[主动屏幕感知](../specs/runtime-v2/WP-4-07-proactive-reminders-todos.md)
与[角色表现](../specs/runtime-v2/visual-plugin-boundary.md)。本决策更新 ADR-0060 中远程客户端仍由默认 Assistant 持有的阶段性安排。
