---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-24
---

# Runtime v2 热应用规范

## 不变量

- 普通设置保存返回 `applied`，不得改变 Core generation、目标插件 PID 或无关插件 PID/scope。
- 聊天、Agent 轮次和 TTS 合成在开始时取得配置快照；进行中的操作不得混用新旧配置。
- 保存发生在操作进行中时只保留该域最新待应用值，并在下一次操作被接受前应用。
- `setup_required/ready/degraded` 可在同 generation 内转换；状态转换递增 Core snapshot revision，不重载桌宠
  WebView。
- Core 整体替换只用于 Core crash 或协议损坏。插件调用、cleanup 或进程失败只影响目标插件及硬依赖
  consumer，恢复由用户 reload、重新安装或新 generation 显式触发。

## 域契约

- Provider/模型：一次 `settings.provider_model.save` 完成 Provider、Core 模型槽和当前 PluginApplication 插件模型槽
  保存。有效 Session 调用 client `update_settings()`；配置变为无效时只退休 Session，恢复有效时在同
  generation 创建 Session 并绑定既有 PluginApplication。
- Tools：保存后更新 `AgentRuntime` 的 loop settings；当前 Agent 轮使用其既有快照。
- MCP：关闭旧 Provider、注销其工具并在原 ToolRegistry 创建新 Provider；不得影响内置和插件工具。
- Agent Trace：新开关只控制新 trace operation；已开始 operation 必须继续记录并完成 staging commit。
  同一设置通过 Host Event 同步给 Memory 插件 recorder。
- 插件：enable、disable、install、uninstall、reload 和 `restart_required` 都是当前用户操作内的同步步骤。
  只停止目标、硬依赖 consumer 或 Service 冲突参与者；无关 scope、Memory owner 和 TTS Provider 保持不动。
  不发送完整 inventory，不运行后台 reconcile，也不自动恢复或重放调用。
- GPT-SoVITS/Genie：timeout、参考目录等请求参数原位更新；managed runtime 身份变化只关闭自己的
  子进程并在下次合成懒启动；custom endpoint 仅重置探测状态。
- Playwright：配置变化只关闭浏览器实例并替换 config loader。

## 验证

自动测试至少固定同 generation、无关插件 PID/scope、活动操作配置隔离、Snapshot revision、MCP 工具局部
替换、插件硬依赖 consumer reload、局部失败不影响无关插件、故障不自动恢复，以及 Memory/TTS 重资源在
无关保存后持续可用。
