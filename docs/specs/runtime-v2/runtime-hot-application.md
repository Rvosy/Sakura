---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-24
---

# Runtime v2 热应用规范

## 不变量

- 普通设置保存返回 `applied`，不得改变 Core generation 或 Plugin Worker PID/token。
- 聊天、Agent 轮次和 TTS 合成在开始时取得配置快照；进行中的操作不得混用新旧配置。
- 保存发生在操作进行中时只保留该域最新待应用值，并在下一次操作被接受前应用。
- `setup_required/ready/degraded` 可在同 generation 内转换；状态转换递增 Core snapshot revision，不重载桌宠
  WebView。
- Core/Worker 整体替换只允许用于 crash、协议损坏、调用/cleanup/reconcile 超时或用户显式重试。

## 域契约

- Provider/模型：一次 `settings.provider_model.save` 完成 Provider、Core 模型槽和当前 Worker 插件模型槽
  保存。有效 Session 调用 client `update_settings()`；配置变为无效时只退休 Session，恢复有效时在同
  generation 创建 Session 并绑定既有 Plugin Worker。
- Tools：保存后更新 `AgentRuntime` 的 loop settings；当前 Agent 轮使用其既有快照。
- MCP：关闭旧 Provider、注销其工具并在原 ToolRegistry 创建新 Provider；不得影响内置和插件工具。
- Agent Trace：新开关只控制新 trace operation；已开始 operation 必须继续记录并完成 staging commit。
  同一设置通过 Host Event 同步给 Memory 插件 recorder。
- 插件：Core 持久化 desired state 后发送完整 inventory 的 `lifecycle.reconcile`。只 dispose 目标、依赖或
  Service 冲突涉及插件及传递消费者；无关 scope、Memory owner 和 TTS Provider 保持不动。
- GPT-SoVITS/Genie：timeout、参考目录等请求参数原位更新；managed runtime 身份变化只关闭自己的
  子进程并在下次合成懒启动；custom endpoint 仅重置探测状态。
- Playwright：配置变化只关闭浏览器实例并替换 config loader。

## 验证

自动测试至少固定同 generation/Worker identity、活动操作配置隔离、Snapshot revision、MCP 工具局部替换、
插件传递消费者 reload、局部失败保持 Worker 健康、故障时一次性 Worker 恢复，以及 Memory/TTS 重资源
在无关保存后持续可用。
