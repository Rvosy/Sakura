---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-08-16
---

# ADR-0023：Runtime v2 分离 TTS 合成、播放与语音留存所有权

## Context

Legacy Qt 把服务探测、旧进程接管、合成、临时文件和 Qt 播放器聚合在一个 Provider 中。已存在的
GPT-SoVITS 可能携带旧权重或损坏管道并持续返回 HTTP 400；`data/cache/tts` 启动整目录清理也无法支持
历史回放和收藏。Runtime v2 需要无 Qt Core、三平台默认设备和明确的 generation 清理边界。

## Decision

- Python Core 独占配置、本地 TTS 子进程、合成和持久 recording。bundled GPT-SoVITS 启动前强杀同一
  用户且 Python/work_dir/api_v2.py 精确匹配的旧进程树；未知监听者不杀并 fail closed。
- Rust 独占默认音频设备、播放队列和临时播放文件门禁，每次播放重新打开当前默认设备。WebView 只提交
  已授权的 chat operation/segment identity。
- 持久 recording 位于 `data/voice/recordings/<character>/<recording>/`；每角色保留最近 100 条非收藏
  recording，`favorite=true` 永不自动淘汰。播放使用 generation cache 中的 hardlink/复制副本。
- 不建设设备选择器或通用 resource-token 平台；Core/Rust 间只使用 TTS 专属 opaque descriptor。

## Consequences

旧服务不会再被当作当前 generation 的健康服务，播放失败也不会拖垮聊天。代价是新增 Rust 音频依赖、
语音持久数据和跨层状态机；三平台真实设备仍是 accepted 的人工硬门。回退只关闭能力与临时副本，不删除
持久 recording、收藏、配置或 bundle。
