---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-18
---

# WP-3-01：无 Qt Assistant Adapter 与真实 Readiness

## 当前职责

AssistantAdapter 将 Core 配置、当前角色和普通 `sakura.assistant` 服务接到既有 readiness owner。
它不构造模型客户端、AgentRuntime 或 ChatPipeline。默认实现与替代实现在独立插件进程中运行，
服务协议、Host 能力和失效处理见 [Assistant 插件边界](assistant-plugin-boundary.md)。

初始化继续使用既有 Core worker、generation、cancellation 和 Snapshot，不另建生命周期根。
`core.initialize` 接受空的生产 payload 并快速返回，在后台完成读取和 prepare；health、握手与 shutdown
不等待模型或其他领域工作。生产 payload 不能指定模拟 ready、failed 或 hang 状态。

```text
Core initialize → 既有初始化 worker
  → CoreConfigReader / CharacterRegistry → 当前 visual 服务及硬依赖
  → 发布 CharacterPresentation，Assistant 仍 initializing
  → sakura.assistant 服务及硬依赖 → 固定 providerId + scopeId
  → AssistantSession.descriptor() → 插件 prepare() → 发布聊天 readiness
  → 完成其余可选插件启动，发布各插件局部结果
```

早期 CharacterPresentation 不携带 CurrentCharacterSummary，不使聊天提前就绪。可选插件启动失败不覆盖
已发布的角色或聊天状态；角色可在 Assistant 仍 initializing、需要设置或失败时显示。所有阶段共用同一个
受管 worker，关闭先停止 Application 拥有的启动中或已启动进程，再按原有期限等待 worker 退出。

AssistantSession 保存 CharacterProfile、BoundAssistant、loopSettings、model_slots、appVersion 和可空 visual binding。
`descriptor()` 只输出本轮需要的角色说明、回复语气、循环设置、模型快照、版本与表现合同；插件进程由
PluginRuntimeApplication 拥有，退休 readiness session 不关闭无关插件，也不恢复本地 Agent。

## 读取、准备与发布

1. Core 使用 RuntimeLocator 已批准的 distribution root 与 user root，不猜测其他用户目录。
2. CoreConfigReader 只投影当前配置，不迁移、备份或 normalize-and-save。支持的 config_version 为非 bool
   整数 1；缺少 system_config.yaml 使用当前版本默认值，不为准备会话写文件。
3. CharacterRegistry 确定用户明确选择的角色。缺失选择或对应包不存在时返回 CHARACTER_REQUIRED，
   不选择首个角色或隐藏默认角色。可选角色包失败使用脱敏 issue sink。
4. Adapter 固定当前 `sakura.assistant` 实例，调用 prepare(session)。缺少服务报告
   ASSISTANT_PROVIDER_REQUIRED，不在 Core 内构造模型。模型是否必需和是否配置完成由 Provider 决定。
5. 默认 Assistant 的 prepare 只验证 chat 配置形状。回环 endpoint 可以没有 API key，实际请求不发送
   Authorization；远端模型需要完整配置。准备阶段不做 DNS、HTTP、认证、模型列表或模型请求。
6. 取消和关闭在读、绑定与 prepare 返回边界检查。readiness owner 只发布仍属于当前 generation 的结果；
   已取消或已关闭 worker 的晚到 session 不增加 revision，也不覆盖新会话。

## Readiness 合同

结果包含 `state/code/message/retryable/currentCharacterSummary` 与可空 CharacterPresentation。
`ready/degraded` 才携带可用 session；message 是公共脱敏文案。Provider 的四种终态与公开 code 遵循
[Assistant 插件合同](assistant-plugin-boundary.md)，下表列出内置结果，并非第三方 code 白名单。
retryable 是 boolean 元数据，初始化结果不触发自动 Core 重启或隐式重试。

| 情况 | state | code |
|---|---|---|
| system_config.yaml 内容损坏、空白或不是 mapping | failed | CONFIG_DATA_INVALID |
| config_version 缺失、类型错误或不受支持 | failed | CONFIG_VERSION_UNSUPPORTED |
| 当前角色未选或不存在 | setup_required | CHARACTER_REQUIRED |
| sakura.assistant 未启用、服务冲突或不可用 | setup_required | ASSISTANT_PROVIDER_REQUIRED |
| 默认 Assistant 缺少可用 chat 配置 | setup_required | PROVIDER_SETUP_REQUIRED |
| Provider 准备成功 | ready | Provider 的稳定 code，默认 READY |
| Provider 返回可用但降级的结果 | degraded | Provider 的稳定 code |
| 读取、绑定或 prepare 出现未分类故障 | failed | ASSISTANT_INITIALIZATION_FAILED |

CoreConfigReader 遇到模型配置不足时可以返回 PROVIDER_SETUP_REQUIRED；Adapter 将模型就绪判断交给
实际 Assistant，不能据此阻止不需要远端模型的替代 Provider。配置结构损坏仍明确失败。
Provider 网络、认证和响应错误只在真实操作中发生，不是启动时自动探测的 readiness 条件。

## Snapshot 与秘密边界

Snapshot 继续由 Python 拥有，revision 单调递增，Rust 只缓存投影。角色摘要通过显式 projector 输出
`id/displayName/initialMessage/replyTones/portraitChoices`；portraitChoices 为空，资源与控制使用
[表现插件合同](visual-plugin-boundary.md) 中的 schemaVersion 2 可空 visual。
公开投影不包含系统说明、模型凭据、角色包路径、内部对象或 generation credential。

ApiSettings.api_key、ProviderSelection.api_settings、CoreConfigReadResult.provider_selection 和
HostConfig.generation_credential 保持 repr 排除。含密 DTO 不交给通用日志序列化器。
模型设置只通过公开 model_slots 的受控 Host 调用，或绑定 Assistant 的会话快照交给插件，用于实际模型请求；不得进入 WebView、
Snapshot、错误或 Trace。generation credential 只允许进入既有 Core framed IPC envelope，不传入插件会话。

## 导入与清理

Core 启动图和初始化都不依赖 Qt、QObject、QThread 或 Qt stub。Generic Plugin Runtime 负责启动已启用插件，
Assistant 的模型与 Prompt 包只在它自己的进程中加载；Core 不以 import 默认实现来判断能力。
普通角色、日志、缓存与设置写入遵循各自所有权，不设用户目录全局只读限制。

Adapter.close 幂等并使未完成初始化失效。PluginRuntimeApplication 关闭服务进程后才清理其仍可读取的
artifact 与历史授权；未知 ACK 和精确 scope 回收规则见 Assistant 合同。`run_host` 的 finally 必须逐项尝试
关闭 dispatcher、领域 owner 与 writer；清理错误保留原始诊断，不覆盖已有主异常，不跳过剩余清理。

Rust ManagedProcessTree 仍是 Core 整树的最终停止所有者。shutdown intent 成功写入 Core stdin 起，使用
共享的端到端 5000 ms deadline；其中 graceful protocol 最多 3000 ms，包含在该总预算内。
剩余预算用于 stdin、进程树、pipe、线程和句柄回收，不能为每个阶段重新开始一段完整预算。
插件进程的局部关闭由 Generic Runtime 执行，不建立第二套 Core supervisor。

## 验证

测试使用隔离配置、角色、公开服务 fixture 与本地 Provider，覆盖配置形状、无默认角色 fallback、
无 Assistant、第三方 Assistant、无网络 prepare、取消与初始化竞争、固定 scope、秘密投影及进程回收。
真实打包 Core 应能完成 hello → initialize → health/snapshot → shutdown；平台进程树风险由对应原生 CI 验证。
本地 unit 通过不能代替三平台或真实窗口证据。

## 历史依据

本 WP 首次于 2026-07-26 验收；早期“在 Core 构造但不运行 Agent/Pipeline”的实施已由当前插件合同取代。
当时取舍见 [ADR-0005](../../adr/0005-runtime-v2-headless-assistant-adapter.md)，实施记录见
[早期计划](../../archive/plans/runtime-v2/2026-07-25-wp-3-01-assistant-adapter-readiness.md)。
这些记录保留历史事实，不定义当前构造图或文件修改范围。
