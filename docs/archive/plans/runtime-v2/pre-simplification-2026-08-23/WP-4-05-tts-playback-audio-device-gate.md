---
kind: plan
status: archived
audience: maintainer
source_of_truth: self
status_source: self
updated: 2026-08-23
---

# WP-4-05 TTS、播放与音频设备门禁实施计划

## 实施顺序

1. 以回归测试冻结旧 GPT-SoVITS 精确识别/强杀、未知端口保护、recording 原子提交、每角色 100 条和收藏
   豁免；建立稳定 history entry identity。
2. 提取无 Qt TTS synthesis boundary，接入 Core capability、段授权、设置、整合包任务和 generation 清理。
3. 建立 Rust opaque audio gate 与 rodio 默认设备播放，接入停止、故障恢复、日志和插件 TTS 事件。
4. WebView 接入多段预生成、播放/字幕双门和 voice 设置；失败统一释放字幕门。
5. 纠正 Runtime v2/Legacy TTS 监听器竞争；增加 session-ready 后台预热、严格 status v1、统一运行日志和
   Rust 播放源头日志；voice 设置恢复既有紧凑 `resource-card`，并允许关闭状态配置、安装与测试。
6. 运行 `docs`、`smoke`、`core-host`、`runtime-v2-shell`、`journey-tts`、`journey-observability`、
   `python-full`、locked Rust、frontend 全量和同 SHA 三平台 workflow。

## 候选与回退

自动门全绿后记录实际证据并进入 stabilizing；不得代填真实设备验收。回退先关闭 voice feature/capability，
取消 synthesis/bundle、停止播放和当前子进程，再逆序回退产品提交；持久 recording 和用户数据保持原样。
