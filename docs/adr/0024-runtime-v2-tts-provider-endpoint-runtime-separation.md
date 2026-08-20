---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-08-20
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

- Runtime v2 只公开 `gpt-sovits` 和 `genie-tts` 两个 Provider。Provider Registry 负责从 Provider ID
  创建合成协议实现和 Endpoint Resolver；聊天、录音与播放边界不按具体引擎分支。
- GPT-SoVITS 的部署方式只由 `custom_base_url` 推导：`null` 使用 Sakura Managed Runtime，非空值使用
  用户管理的 Custom Endpoint。不持久化第二个 mode 字段。
- Managed Runtime 独占安装、进程、端口、健康检查和模型切换。Custom Endpoint 只允许连接探测与合成；
  即使地址是 loopback，Sakura 也不得启动、接管、重启、切模型或停止该服务。
- GPT-SoVITS Endpoint 持久化为 `custom_base_url + tts_path`。远程参考音频采用显式根目录映射：
  `<remote_reference_root>/<character_id>/<角色包内相对路径>`。远程根目录缺失或参考文件逃逸角色包时
  返回 `REFERENCE_AUDIO_UNAVAILABLE`。本次不增加上传或资源同步协议。
- 配置 v5 将旧 custom/external Provider 归一化为 `gpt-sovits` 并拆分 Endpoint；旧 flat Runtime 路径移入
  `managed_runtime`。Legacy Qt 通过只读兼容投影继续进入原 custom 分支，但不会把旧 Provider ID 写回磁盘。
- 统一公开错误码，不把 urllib、操作系统错误或完整响应直接投影到 UI；日志记录 Provider、Endpoint 类型、
  地址、文本长度、耗时和音频字节数，不记录完整对话文本或请求 payload。

## Consequences

增加新 Provider 时仍需注册实现、配置和 UI，但不再修改聊天、录音或 Rust 播放主链。自定义 Endpoint 的
生命周期边界可由单元测试证明，且远程参考音频失败是显式的。代价是配置需要 v5 迁移，并要求远程服务运营者
预先镜像角色参考音频；上传和远程模型管理留待独立协议。

本决策补充 ADR-0023，不改变其 Python 合成/录音与 Rust 默认设备播放所有权。

ADR-0027 的后续切换保留本 ADR 的 Provider/Endpoint/Managed Runtime 分离，但用普通 `sakura.tts` Hub
和 Provider 插件替代 Core Provider Registry。具体 Provider ID、配置、安装和进程生命周期不再由 Runtime v2
Core/Rust/Voice shell 枚举；本 ADR 中关于固定公开 Provider 和“新增实现需修改 Core”的描述只保留为切换前
历史背景。
