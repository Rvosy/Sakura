---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-28
---

# ADR-0023：Runtime v2 分离 TTS 合成、播放与语音留存所有权

## Context

Legacy Qt 把服务探测、旧进程接管、合成、临时文件和 Qt 播放器聚合在一个 Provider 中。已存在的
GPT-SoVITS 可能携带旧权重或损坏管道并持续返回 HTTP 400；`data/cache/tts` 启动整目录清理也无法支持
历史回放和收藏。Runtime v2 需要无 Qt Core、三平台默认设备和明确的 generation 清理边界。

## Decision

- Provider 插件独占自己的配置、合成和 Managed Runtime；Python Core 负责段落授权、接收合成产物和持久
  recording。Managed GPT-SoVITS 遇到已有端口监听者时明确报错，只启动和回收自己创建的受控进程。
- Rust 独占默认音频设备、播放队列和临时播放文件门禁，每次播放重新打开当前默认设备。WebView 只提交
  已授权的 chat operation/segment identity。
- 持久 recording 位于 `data/voice/recordings/<character>/<recording>/`；每角色保留最近 100 条非收藏
  recording，`favorite=true` 永不自动淘汰。播放使用 generation cache 中的 hardlink/复制副本。
- 不建设设备选择器或通用 resource-token 平台；Core/Rust 间只使用 TTS 专属 opaque descriptor。

## Consequences

旧服务不会再被当作当前 generation 的健康服务，播放失败也不会拖垮聊天。代价是 Rust 音频依赖、
语音持久数据和跨层状态机；三平台真实设备仍是 accepted 的人工硬门。回退只关闭能力与临时副本，不删除
持久 recording、收藏、配置或 bundle。

## Plugin Runtime v4 所有权

按 ADR-0037，Hub 和各 Provider 分别运行在独立插件进程中，通过 Service descriptor 和 `jobId` 协作。
Core 只调用 `sakura.tts`，不保留 Provider Registry、合成队列或进程 supervisor 的备用实现。
Core TTS boundary 独占 segment authorization、artifact 消费、recording 和 opaque playback descriptor；
Rust 拥有默认设备与进程树的最终回收权。Provider 子进程不得脱离 Rust generation process group。

## 插件输入与远程播放

[讨论 #227](https://github.com/Rvosy/Sakura/issues/227) 暴露了旧手机入口与桌面聊天事件链分离的问题。
[PR #230](https://github.com/Rvosy/Sakura/pull/230) 将插件用户输入接入统一 conversation 入口；
字幕栏和远程端还需要知道具体播放哪条回复，以及如何取得可释放的音频。

Core 提供按历史段落准备音频的 `sakura.host.speech`，沿用既有 TTS 授权和录音所有权。
远程插件取得 scope 内的音频副本并负责传输与本端播放；Core 回收 Provider artifact，插件释放交付副本，
Rust 继续独占桌面播放。这样远程端无需枚举缓存、读取 Provider 配置或接管桌面播放队列。

播放事实事件携带来自录音元数据的历史条目和原始段落下标，字幕据此定位内容；仅有播放开始次数不足以
区分静音段、历史回放和中断。接口合同见 [Plugin Runtime](../specs/runtime-v2/sakura-plugin-runtime-v4.md#已保存回复的语音与播放身份)。
