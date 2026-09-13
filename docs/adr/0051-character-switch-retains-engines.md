---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-14
---

# ADR-0051：切换角色保留可复用插件与推理进程

本决策取代 [ADR-0050](0050-current-character-local-refresh.md) 中“跨角色 ID 切换必须重启 Core”的部分。
GPT-SoVITS 已支持在同一推理服务内切换权重，但整核重启会先关闭该服务，导致每次切换角色都重复冷启动。

复用现有 AssistantAdapter 的会话退役、初始化，以及 PluginApplicationHost 的解绑、绑定接口。
切换先拒绝新聊天并等待旧回复取消，撤销旧语音授权、取消 ASR，再提交角色选择和重建会话。
Core generation、MCP、工具注册表、表现 Provider 和 TTS 引擎保持运行。历史、记忆和迟到结果仍按角色隔离。

旧插件可能在 setup 时缓存当前角色。对依赖角色或 Timeline 服务的这类插件，使用已有的局部停止、恢复流程；
Mem0 目前属于这一类。表现 Provider 和 TTS Provider 接收明确角色参数，因此保留进程。此回退只影响相关插件及其硬依赖方，
不为了统一生命周期关闭所有服务。

语音文件更新增加一对可选的 Provider 操作，用来暂停任务和恢复任务。GPT-SoVITS 复用现有串行队列与取消标记，
等待文件读取任务结束后允许替换文件；恢复时清除权重缓存，下一次请求调用已有换权重接口。
没有实现该能力的 Provider 仍局部重载。更新不修改用户插件启用配置，也不把运行态恢复失败混同为文件保存失败。

首次导入并建立角色会话保留旧启动路径；显式重启、插件代码更新和推理环境变更仍可关闭对应进程。
切换角色时不改写用户历史或记忆，不保留旧角色可执行的授权，也不自动把清理失败转换为整核重启。
