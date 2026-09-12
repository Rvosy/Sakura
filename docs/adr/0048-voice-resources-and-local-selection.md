---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-12
---

# ADR-0048：语音资源与应用运行选择分离

## 背景

角色工坊曾用一个开关同时管理语音资源、启用状态与 GPT-SoVITS 选择，关闭并保存会移除资源关联。
应用语音设置也把选择写入角色包，导致分享资源携带本机运行偏好，编辑资源又会改变运行状态。

## 决策

角色包只提供模型、参考语音、文本、标签、语言等资源配置。工坊移除启用开关，独立编辑和保存资源，
允许分步准备尚不能合成的资源。完整性错误在相应编辑或合成边界报告。

语音 Hub 使用现有插件配置存储，将每个角色的 `{enabled, provider}` 保存在应用用户根
`data/plugins/sakura.tts/config.json` 的 `selections` 中。设置页、预热和合成共用它；关闭语音保留引擎选择
和资源关联。未设置时禁用，不自动选择引擎。

不为旧 `extensions.sakura.tts` 建立迁移、双写或回退逻辑。旧字段在运行时忽略，在工坊保存和包导入导出时
移除；语音资源字段继续可读，不要求重新导入模型或参考音频。旧版整目录导入也不凭资源存在自动开启语音。
此决策取代旧工坊保存语音时同步 Hub 启用与引擎选择的规则，接口见
[语音合同](../specs/runtime-v2/WP-4-05-tts-playback-audio-device-gate.md)和
[工坊合同](../specs/runtime-v2/character-studio.md)。

## 影响

同一角色包在不同安装中可以使用不同的语音选择。首次使用这套设置存储时，用户在语音设置中选择引擎并启用；
角色资源编辑、语音关闭和包分享彼此独立。Hub 继续通过描述符调用 Provider，Core 不增加引擎分支。
