---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-08-20
---

# ADR-0028：模型页使用动态、owner-scoped 的配置槽位

## 背景

模型页原先写死 Core 对话、视觉和记忆整理用途。继续为每个插件增加 `model.<用途>_slot` 会让 Core、Rust
和 WebView 预先理解插件能力，也会把插件私有配置错误地搬进全局 `api.yaml`。另一方面，embedding、TTS
权重等本地资源具有下载、缓存和独立 Runtime 生命周期，不适合与远程 Provider 模型选择混为一谈。

## 候选方案

1. 继续扩展硬编码槽位。实现简单，但每个新能力都要求修改宿主并产生 feature 特判。
2. 把所有槽位统一保存到 `api.yaml`。页面统一，但破坏插件配置所有权，插件卸载/回退也更难兼容。
3. 由 Core 和 active 插件动态贡献槽位，宿主只统一验证、呈现和保存编排，各 owner 继续保存自己的选择。

## 决策

采用方案 3。新增 `sakura.host.model_slots` Host Service。Core 保留 `core:chat`、`core:vision_chat`；插件
identity 使用 `plugin:<plugin_id>:<slotId>`。首期只支持 `chat_completion`，接口不暴露 API key，也不建设
通用推理代理。注册和 callback 绑定插件 root Effect，因此插件停用、失败、reload 或 generation 失效时槽位
立即消失；插件私有选择保留，重新启用时恢复并对缺失引用显式报不可用。

Provider/model snapshot 升级为 schema v2 动态列表。保存前校验 Provider、Core 与全部 active 插件槽位；
任何非法引用都不开始写入。写入顺序为：Provider/Core owner、必要的 Core restart 与 generation 重绑、稳定
identity 顺序的插件 callback、真实快照刷新。跨 owner 不承诺事务；后序失败返回 partial、已保存 identity
和失败 identity。

`model.slots` 是唯一通用 capability。未来新增 Chat Completion 用途直接注册槽位；新的调用类型需要另行
扩展 `modelKind`。embedding、TTS 权重和其他需要下载或独立 Runtime 的本地资源不进入该服务。

## 后果

- 模型页随 active 插件真实需求自动增减，宿主不再枚举插件用途。
- Core 与插件配置所有权保持不变，禁用插件不会破坏其选择。
- 多 owner 保存可能部分成功，前端必须明确提示并刷新，不能宣称全局事务。
- Host Service 成为新的公开插件契约，descriptor、Effect 撤销、敏感字段隔离和 generation 重绑必须有
  回归测试。
