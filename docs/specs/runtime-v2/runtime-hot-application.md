---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-18
---

# Runtime v2 热应用规范

## 不变量

- 普通配置保存不得改变 Core generation、目标插件 PID 或无关插件 PID/scope。
- 聊天、Agent 轮次和 TTS 合成在开始时取得配置快照；进行中的操作不得混用新旧配置。
- Provider/Tools 保存时完成写盘与同步应用。当前对话或角色切换尚未结束时，应用明确返回失败；
  响应说明设置已保存但尚未应用，用户可在空闲时重新保存或重启应用。已经发布的配置继续服务后续聊天。
- 不存储设置应用回调，不在聊天受理时执行、修复或重试上次保存。普通应用异常保留原始原因；
  保存成功与运行态应用成功是两个事实，不能把后者失败描述为磁盘保存失败。
- `setup_required/ready/degraded` 可在同 generation 内转换；状态转换递增 Core snapshot revision，不重载桌宠
  WebView。
- Core 整体替换只用于 Core crash 或协议损坏。插件调用、cleanup 或进程失败只影响目标插件及硬依赖
  consumer，恢复由用户 reload、重新安装或新 generation 显式触发。

## 域契约

- Provider/模型：一次 `settings.provider_model.save` 完成 Provider、Core 模型槽和当前 PluginApplication 插件模型槽
  保存。默认 Assistant 的有效 Session 调用 client `update_settings()`；其模型配置变为无效时只退休该 Session，
  恢复有效时在同 generation 创建 Session 并绑定既有 PluginApplication。
- 正常聊天固定使用默认 Assistant。`f9fde091` 中的互动方式设置已撤回，`settings.executor.get/save` 不再提供，
  历史 `chat_executor` 字段被忽略，不作为隐藏选择。插件开关只管理插件生命周期，不切换聊天实现。
  未使用的执行器实验已清理；当前边界见 [Plugin Runtime](sakura-plugin-runtime-v4.md#65-正常对话与插件服务的边界)。
- Tools：空闲时保存并更新 `AgentRuntime` 的 loop settings；进行中的轮次继续使用既有快照，
  此时保存返回 `CONFIG_APPLY_FAILED`，不会在下一次聊天自动发布设置。
- MCP：`sakura.mcp` 只提供 Service，服务器配置与变更由消费插件处理；不读取旧 `mcp.yaml`。
- Agent Trace：新开关只控制新 trace operation；已开始 operation 必须继续记录并完成 staging commit。
  同一设置通过 Host Event 同步给 Memory 插件 recorder。
- 插件：enable、disable、install、uninstall、reload 和 `restart_required` 都是当前用户操作内的同步步骤。
  只停止目标、硬依赖 consumer 或 Service 冲突参与者；无关 scope、Memory owner 和 TTS Provider 保持不动。
  不发送完整 inventory，不运行后台 reconcile，也不自动恢复或重放调用。
  开关已持久化但启停失败时，管理响应仍返回最新列表及 `revision`，以 `desiredSaved: true`、
  `applicationState: error` 和 `applicationReasonCode` 区分保存结果与运行结果。桌面层接受此响应，
  设置页更新状态并显示失败，保留未保存的其他设置。版本冲突或响应丢失后重新读取列表，不自动重试启停；
  保存前发起的列表读取不得覆盖保存结果。
  插件清单 revision 直接比较安装记录与结构化开关配置，变化时分配随机 token，同一 Core 的不同
  Inventory 实例共享该状态。等价文件格式变化不更新 token；恢复旧状态也不复用旧 token。
  installId 使用来源与目录名的可逆编码，既有开关仍按 pluginId 保存。依赖就绪保留已安装 marker、
  声明类型和 Python ABI 判断，忽略旧 fingerprint，不因声明内容变化重算摘要或自动安装。
- GPT-SoVITS/Genie：timeout、参考目录等请求参数原位更新；managed runtime 身份变化只关闭自己的
  子进程并在下次合成懒启动；custom endpoint 仅重置探测状态。
- Playwright：配置变化只关闭浏览器实例并替换 config loader。

## 验证

自动测试至少固定同 generation、无关插件 PID/scope、活动操作配置隔离、Snapshot revision、Session 重建时
插件硬依赖 consumer reload、局部失败不影响无关插件、故障不自动恢复，以及 Memory/TTS 重资源在
无关保存后持续可用。聊天边界需覆盖已保存但未应用的配置不会被下一次聊天隐式发布，
以及应用异常不被聊天重试、用户显式重新保存后才发布新配置。
