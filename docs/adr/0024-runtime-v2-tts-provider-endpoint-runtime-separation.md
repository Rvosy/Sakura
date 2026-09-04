---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-05
---

# ADR-0024：Runtime v2 分离 TTS Provider、Endpoint 与 Managed Runtime

## Context

Runtime v2 曾把 Sakura 内置 GPT-SoVITS 和用户提供的 GPT-SoVITS HTTP 服务暴露为两个 Provider。
二者使用相同的合成协议，区别只是 Sakura 是否拥有服务进程的生命周期。继续沿用这一划分会让每个新引擎
都复制 Provider 选择、URL、进程和 UI 分支，也会让自定义 localhost 或远程服务被误认为可由 Sakura
启动、切模型或停止。

GPT-SoVITS 的 `ref_audio_path` 还带有部署边界：远程服务无法读取客户端角色包中的本地路径。模型权重路径
也只对 Sakura 管理的 Runtime 有意义。

## Decision

- `sakura.tts` Hub 根据 Provider 提交的 descriptor 选择服务，通过 `begin/poll/cancel` 合成。
  各 Provider 插件拥有合成协议和 Endpoint Resolver；聊天、录音与播放边界不按具体引擎分支。
- GPT-SoVITS Provider 显式持久化 `endpointMode=managed|custom`，让设置页可以把
  “Sakura 内置”与“连接已有服务”作为独立选择呈现；`customBaseUrl` 作为非活动参数保留，切回已有服务时
  无需重新填写。缺少 `endpointMode` 的旧配置继续由 `customBaseUrl` 是否为空推导，保持升级兼容。
- 旧配置的 `custom_base_url` 由配置兼容层读取；迁入 Provider 插件时补出 `endpointMode`。
  Core、Rust 和 TTS Hub 不读取这一 Provider 私有字段，也不实例化旧 Provider Registry 或合成服务。
- Managed Runtime 独占安装、进程、端口、健康检查和模型切换。Custom Endpoint 只允许连接探测与合成；
  即使地址是 loopback，Sakura 也不得启动、接管、重启、切模型或停止该服务。
- GPT-SoVITS 插件以 `customBaseUrl + ttsPath` 表达已有服务的 Endpoint。远程参考音频采用显式根目录映射：
  `<remote_reference_root>/<character_id>/<角色包内相对路径>`。远程根目录缺失或参考文件逃逸角色包时
  返回 `TTS_REFERENCE_AUDIO_UNAVAILABLE`。不提供上传或资源同步协议。
- 旧配置 v5 的迁移继续将 custom/external Provider 归一化为 `gpt-sovits` 并拆分 Endpoint，将 flat Runtime
  路径移入 `managed_runtime`。兼容层只负责读取和迁移数据，不保留旧运行时或 Qt 播放路径。
- 公开错误使用稳定错误码，不把 urllib、操作系统错误或完整响应直接投影到 UI。Provider 通过 Host
  diagnostics 上报阶段、状态、错误类型和耗时等有界字段，不记录地址、路径、对话正文或请求 payload。

## Consequences

增加新 Provider 只需提供插件服务和设置贡献，无需修改聊天、录音或 Rust 播放主链。自定义 Endpoint 的
生命周期边界可由单元测试证明，且远程参考音频失败是显式的。代价是配置需要 v5 迁移，并要求远程服务运营者
预先镜像角色参考音频；上传和远程模型管理留待独立协议。

Voice 设置面使用声明式字段的 `placement=advanced` 收起运行目录、Python、推理配置、请求路径和超时等
技术参数；页面只显示当前选中 Provider 所属插件的区块，不枚举具体 Provider ID。

本决策补充 ADR-0023。ADR-0027 引入普通 `sakura.tts` Hub 和 Provider 插件，ADR-0037 将它们隔离为逐插件
进程；Provider/Endpoint/Managed Runtime 的职责划分继续有效。具体 Provider ID、配置、安装和进程生命周期
由插件拥有，Core/Rust/Voice shell 通过能力契约消费。
