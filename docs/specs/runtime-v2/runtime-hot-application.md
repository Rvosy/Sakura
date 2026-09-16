---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-09
---

# Runtime v2 热应用规范

## 不变量

- 普通设置保存返回 `applied`，不得改变 Core generation、目标插件 PID 或无关插件 PID/scope。
- 聊天、Agent 轮次和 TTS 合成在开始时取得配置快照；进行中的操作不得混用新旧配置。
- 保存发生在操作进行中时只保留该域最新待应用值，并在下一次操作被接受前应用。
- 某域应用失败时，本次操作不被接受；保留失败域和尚未应用域的最新值，供下一次操作边界应用。
  已成功应用的域从待办中移除。再次保存同一域时替换旧待办，不重放已经成功的更新。
- `setup_required/ready/degraded` 可在同 generation 内转换；状态转换递增 Core snapshot revision，不重载桌宠
  WebView。
- Core 整体替换只用于 Core crash 或协议损坏。插件调用、cleanup 或进程失败只影响目标插件及硬依赖
  consumer，恢复由用户 reload、重新安装或新 generation 显式触发。

## 域契约

- Provider/模型：一次 `settings.provider_model.save` 完成 Provider、Core 模型槽和当前 PluginApplication 插件模型槽
  保存。有效 Session 调用 client `update_settings()`；配置变为无效时只退休 Session，恢复有效时在同
  generation 创建 Session 并绑定既有 PluginApplication。
- Tools：保存后更新 `AgentRuntime` 的 loop settings；当前 Agent 轮使用其既有快照。
- MCP：Application 持有 Provider 和工具注册，Session 退休与重建只借用该实例。当前只提供状态读取，
  `mcp.yaml` 的修改在新 Core generation 启动时读取，不提供设置保存或热替换接口。
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
MCP 实例和状态不变、插件硬依赖 consumer reload、局部失败不影响无关插件、故障不自动恢复，以及 Memory/TTS 重资源在
无关保存后持续可用。聊天边界还需覆盖跨域应用失败后的待办保留，以及同域重复保存只应用最新值。
