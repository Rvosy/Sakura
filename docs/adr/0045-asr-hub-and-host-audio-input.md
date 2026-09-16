---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-09
---

# ADR-0045：ASR Hub、可替换识别引擎与宿主录音

## 背景

Sakura 需要点击开始、再次点击结束的语音输入，识别结果进入输入栏，由用户编辑和发送。同时需要一个统一
管理入口，让官方和第三方识别插件接入不同本地模型或云服务，用户可以选择引擎。

Plugin Runtime v4 已有逐插件进程、独立 dependency root、ServiceProxy 和唯一 Service 提供者规则。TTS
已经使用独立 Hub 与 Provider，通过 JSON descriptor 和 jobId 协作。现有 artifact 主要支持插件产出文件供
Core 使用，Host 录音向插件输入的资源授权仍需补齐。

本 ADR 定义职责划分，当前实现与验证边界见 [ASR 语音输入](../specs/runtime-v2/asr-voice-input.md)。

## 决策

ASR 使用一个可替换的官方 Hub 插件，提供 `sakura.asr`。各识别引擎是独立插件，提供各自的 Service key，
向 Hub 登记 descriptor。Hub 持有应用级当前引擎选择，通过 `status/warmup/begin/poll/cancel` 调用 Provider。
识别语言、支持的语言选项、模型资源、推理和 VAD 由引擎拥有。Hub 只做登记、选择、路由与任务状态，不成为模型运行平台。

默认引擎另做官方 SenseVoice Provider，使用 sherpa-onnx、SenseVoiceSmall INT8 和 Silero VAD，优先 CPU
本地识别。它与第三方遵循同一协议，可关闭和替换。新增 Provider 不要求修改 Core、UI 或 Hub 的模型名单。

麦克风属于 Rust/Tauri 宿主。Core 负责语音输入协调和 Host 管理的临时音频授权，Provider 只读取授予本次任务
的录音。普通插件 Service 只交换 JSON、资源 descriptor 与任务 ID，不跨插件传路径、音频数组或 callback。
通用 Runtime 不理解 ASR Provider，也不参与模型选择。

语音输入的配置只在独立插件设置中展示。Hub 选择引擎，Provider 管理自己的语言和模型资源。
设备列表和麦克风选择由宿主管理，在 Provider 插件设置中提供。插件设置中的输入测试
复用正式采集与识别链路，绑定设置窗口；临时试用的引擎和设备不改变全局选择，文字只展示在测试结果中。

输入栏麦克风位于发送按钮左侧。点击后进入录音，原麦克风变为停止按钮；再次点击停止并识别。录音区域显示
取消和音量波形，不显示时长与上限。取消复用左侧扩展按钮旋转后的「×」。准备与识别状态位于整条输入栏
中央，暂时隐藏草稿与附件并保持单行高度。识别完成后恢复文字输入并回填草稿，发送必须由用户另行触发。
ASR Hub 关闭时隐藏麦克风并恢复原有布局；显隐不依赖 TTS Hub 或模型就绪状态。停止按钮的小方块跟随主题色。

波形由宿主从 PCM 提取的短时音量驱动，WebView 绘制固定容量的历史竖条。该反馈不依赖 ASR、VAD、频谱或
声纹身份模型。音量帧可以合并和丢弃，不能阻塞采集。平台绘制问题可以降级到简单音量指示与状态文字。

第一版同一时间只有一次语音输入，不做常驻监听或 partial transcript。当前引擎失败时明确提示，不自动选择
其他引擎；活动录音不能因设置变化被发送到新的 Provider。录音、推理和回填分别处理取消，保证旧结果不会改写
新草稿，音频在读取者退出后回收。

## 备选方案与原因

让 SenseVoice 插件直接提供唯一 `sakura.asr`，可以少一层路由，但用户只能通过停用旧插件来切换识别实现。
当前已经明确需要多个引擎的统一管理与选择，因此采用与 TTS 一致的 Hub/Provider 结构。

把所有识别模型放进同一个 Hub，可以内部切换模型，但会让第三方共享依赖和推理故障，也让 Hub 持续增长模型
适配代码。独立 Provider 能直接使用已有的进程与依赖隔离。

让 Provider 自行打开麦克风，能更快运行上游示例，但录音权限、设备释放、UI 波形和切换引擎都要重复实现。
由宿主采集后交给 Provider，可以让输入行为不随引擎变化。

直接使用 WebView 录音也可实现短录音，但跨平台权限、编码格式和窗口关闭后的设备生命周期仍需统一处理。
当前选择 Rust/Tauri 拥有采集，WebView 只接收状态和音量摘要。

## 后果与关系

增加 Hub 插件会多一个进程和一次任务路由，但统一管理是当前产品需求，且可以复用现有 Service 模式。后续
引擎作者只需实现有限的识别契约，不必重建录音或聊天入口。

主要新增工程边界是系统音频采集、Host 输入资源授权和草稿生命周期。Windows/macOS/Linux 的麦克风权限、
bundled Python wheel 与实际推理性能需要分别验证；依赖隔离不能消除平台兼容问题。

本决策延续 [ADR-0037](0037-replaceable-default-plugins-and-isolated-python-runtimes.md)，不取代其唯一 Service
规则：唯一的是 Hub 的 `sakura.asr`，多个引擎使用独立 Service。它也不改变
[ADR-0023](0023-runtime-v2-tts-audio-ownership.md) 的 TTS 音频所有权；输入录音单独作为短期资源，默认不留存。
录音期间暂停应用发声属于新增语音输入行为，具体边界由上述 Spec 定义。
