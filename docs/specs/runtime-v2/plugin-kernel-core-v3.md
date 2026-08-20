---
kind: spec
status: draft
audience: maintainer
source_of_truth: self
updated: 2026-08-21
---

# Plugin Kernel Core v3

本规范只冻结 Worker 内的组合内核：Service、Event、Transform、Effect、Config 与 Session Effect。
进程 Bridge、安装管理和领域 Host Service 分别由独立规范负责。

一个 Core generation 拥有一个 `PluginApplicationHost` 和至多一个 Worker。Application Service 在
Assistant Session 创建、失败、切换或结束时保持；Session 只能消费 Application Service，不能 provide、
覆盖 Service 或访问插件 Config。

插件激活顺序固定为 `setup → root staged commit → callback 激活 → Application Service 发布 → active`。
`ctx.on()`、`ctx.on_transform()`、`ctx.on_session()` 和 Host callback 注册都必须随 root scope staging；
setup 内直接或间接 emit 看不到尚未提交的 Handler。失败时先使 callback 不可调用，再逆序完整 dispose root
scope。这里的原子性承诺是 setup 阶段不可调用、失败后无持久残留以及 commit rollback-complete，不承诺
跨进程分布式原子事务。

`ctx.on_session(setup)` 的 `setup` 接收只读 `PluginSessionContext`：`session_id`、`character_id`、
`get/inject/on/on_transform/effect`。每次绑定为每个订阅创建 child EffectScope；unbind、插件停用或 Worker
结束时幂等逆序清理。单插件 setup 失败只把该插件置为
`failed / PLUGIN_SESSION_SETUP_FAILED`。Host 对每次真实绑定各发送一次
`sakura.host.session.started/ended`，Application 启动使用 `sakura.host.app.started`，两者不得混用。

Plugin ID 上限为 64 个 ASCII 标识字符；Service/Event/Transform key 上限独立为 200。Config 默认文件或
用户 `config.json` 无法解析时返回 `PLUGIN_CONFIG_INVALID`，保留原文件并拒绝覆盖；修复文件后 reload
可以恢复。
