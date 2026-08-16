---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-16
---

# WP-4-05 TTS、播放与音频设备门禁规范

## 产品行为

- `assistant.tts-v1` 只为已完成聊天中的 `operationId + segmentIndex` 合成，`suppressTts` 和语言守卫必须
  fail closed。WebView 不得提交文本、路径、generation 或音频描述符。
- 音频实际开始后字幕才开始；字幕和音频均终止后推进下一段。当前段开始后预生成下一段；任何合成、
  设备或播放失败立即降级为字幕且不改变聊天终态。历史导航不自动重播。
- 输出始终使用播放时的系统默认设备；不提供设备选择器。设备断开只结束当前项，下一次播放重新探测。
- 已保存且启用的 TTS 在 Assistant session 发布后由 generation boundary 后台预热；预热失败只更新 TTS
  状态并降级字幕，不阻塞 Core readiness。设置页读取状态不得触发服务启动、旧进程清理或全 Provider 探测。
- `availability` 只表示 bundled Provider 已安装或 external Provider 已配置；它不表示进程当前可达。启用开关
  与已选 Provider 分别保存，关闭时仍可切换 Provider、安装整合包和播放固定测试音。

## 进程与数据

- bundled GPT-SoVITS 启动前终止同一用户且精确匹配当前配置的旧进程树，等待端口释放后创建当前 Core
  generation 的受控子进程。未知端口占用者不得终止。自定义/远程 Provider 永不自动杀进程。
- 成功的聊天合成原子写入 recording；测试音和失败/跳过请求不留存。每角色最多 100 条非收藏 recording，
  收藏不计入上限。损坏或未来 schema 只隔离对应记录。
- 持久 recording 与 generation 临时播放副本分离；启动清理只触碰临时目录。跨边界 DTO 不含裸路径。

## 接口、故障与回退

Core 开放 TTS synthesis/settings/status/bundle allowlist；`tts.status.get` 返回 schema v1 的全部 Provider
availability、bundle、当前 runtime 状态和活动任务，不含音频路径、正文、凭据或异常原文。runtime state 只允许
`disabled | waiting_for_session | starting | ready | failed | stopping`。Core 发布 synthesis 唯一终态；Rust 开放准备、播放、停止和
设置 commands，并发布 playback 唯一终态。旧 generation、重复消费、逃逸/symlink、超大或无效 WAV 必须
拒绝。回退关闭 capability、停止服务和播放，但不得删除 recording、收藏、配置、bundle 或下载分片。

设置页必须由 Runtime v2 voice controller 独占 TTS 控件，使用既有 `resource-card__*`、badge、progress、meta
和 actions 结构。启动、安装或测试进行中才允许 1 秒状态轮询，终态停止；失败诊断只复制稳定错误码、Provider、
状态和时间。
设置测试由 Rust 消费 Core descriptor 并等待音频线程真实的 `finished | stopped | failed` 终态；WebView 只接收
Provider、测试状态和可选稳定错误码，不接收 descriptor 或路径。

Python startup/process cleanup/settings/bundle/test/synthesis/recording 与 Rust playback 的真实终态都写入统一
`data/logs/sakura-runtime.log`。日志只允许稳定标识、Provider、端口、状态/阶段、进度、字节数、HTTP 状态、
耗时和重试次数；不得记录文本、凭据、音频路径或完整 Provider 响应。Rust 必须先在音频回调源头落日志，Core
observe 或插件发布失败不得吞掉播放证据。

自动验证覆盖 Python/Rust/WebView 纵向链；Windows WASAPI、macOS CoreAudio 和 Linux PipeWire/Pulse/ALSA
真实默认设备及旧 GPT-SoVITS 清理是项目负责人验收前的硬门。
