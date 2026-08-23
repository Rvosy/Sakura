---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0029-coarse-plugin-worker-lifecycle
updated: 2026-08-24
---

# ADR-0032：Runtime 热应用与局部插件生命周期

## 背景

Runtime v2 曾把普通设置保存统一映射为 Core 或 Plugin Worker 重建。这样实现简单，但会让 Memory 的
embedding/Qdrant/SQLite owner、TTS 已加载权重、插件内存状态和 Assistant Session 一起丢失。设置保存
本身并没有改变进程协议或 generation，粗粒度重建造成的重新预热和短时不可用已经成为实际体验问题。

## 决策

- 普通 Provider、Tools、MCP、Agent Trace、TTS 和插件设置在当前 Core generation 内热应用。
- 活动聊天和合成使用操作开始时的旧配置完成；最新待应用值在下一次对应操作开始前生效。
- Provider 设置使用一次 `settings.provider_model.save` 原子保存 Core 与插件模型槽，不再跨 Core 重启分期。
- Plugin Worker 接受私有 `lifecycle.reconcile`，输入完整最新 inventory 和显式 reload ID。Kernel 只按反向
  依赖顺序 dispose 目标、冲突参与者及传递消费者，再以确定性拓扑 setup；无关 root scope 保持不动。
- 插件公开状态仍只有 `disabled/active/failed`。`restart_required` 表示局部 reload 该插件及消费者；成功
  对设置调用者报告 `applied`，失败保留 `failed` 和稳定错误，不回滚已保存配置。
- GPT-SoVITS、Genie 和 Playwright 自己管理其重资源：纯请求参数原位更新；Sakura 管理的 TTS runtime
  身份变化只停止对应子进程并懒启动；外部 endpoint 永不由 Sakura 终止。
- 整 Core/Worker 重建只用于进程崩溃、协议损坏、调用或 cleanup/reconcile 超时以及用户显式重试。

## 后果

Core generation、Worker PID/token、Memory owner 和无关插件 Effect 在普通保存前后稳定。Kernel 多出一个
私有、完整目标态 reconcile，但不扩张公共 Plugin Context，也不恢复 waiting/sticky 等动态治理状态。
局部 cleanup 必须有界；超时仍由进程隔离和一次性 Worker 恢复兜底。
