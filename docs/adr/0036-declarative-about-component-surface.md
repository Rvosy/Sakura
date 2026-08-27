---
kind: adr
status: accepted
date: 2026-08-27
---

# ADR-0036：使用声明式 About Surface 汇总插件组件

## 背景

Mem0、Genie TTS 和 GPT-SoVITS 各自需要本地模型或运行时。下载入口分散在模型页、语音页和插件详情，用户
难以判断整套应用是否就绪。现有 Plugin Settings resource 已能表达状态、进度和 Action。

## 决策

复用 `sakura.host.settings` 的只读 resource section，并通过
`sakura.host.settings.surface-v0.register(sectionId, "about")` 投影到“关于 → 组件”。Host 只负责验证声明、
稳定排序、展示、调用 Action，以及仅在 `queued/running` 时轮询 Snapshot。

下载 URL、目标路径、校验、断点续传、原子替换、配置回写和线程 cleanup 继续由插件拥有。状态读取不得联网，
安装和重试只由用户显式触发。Settings Snapshot 升级为 schema v3，并为 resource 增加
`required/not_required/unsupported` applicability；旧 v3 插件省略时归一化为 `required`。

## 未采用

- 中央下载任务平台：会夺走插件对格式、安装结果和生命周期的所有权，并引入当前不需要的并发治理。
- 为每个领域保留专用下载页：继续造成重复入口和不一致状态。
- 向 UI 暴露 URL 或路径：扩大边界并泄露插件私有实现。

## 后果

About section 必须有 load、只能包含只读 resource、不得包含 save 或 Collection，且 Action 必须被资源字段引用。
已安装、无需安装和不支持一键安装的项目没有操作按钮。本次不提供重装、卸载、自动修复、自动重试或自动下载。
