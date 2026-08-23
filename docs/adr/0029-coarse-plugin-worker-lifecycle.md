---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0027-dynamic-lifecycle-parts
updated: 2026-08-23
---

# ADR-0029：Plugin Worker 使用粗粒度生命周期

## 背景

早期 Plugin v3 为局部启停、热 reload、Session 绑定、依赖失效传播和超时恢复建立了 reconcile、sticky
failure、child scope 与多套 IPC。当前 Sakura 只有一个 Core generation、一个 Worker 和六个 bundled
插件；这些机制增加了状态组合，却没有独立消费者。

## 决策

- Worker 启动时扫描一次，以 `provides/requires` 做确定性拓扑加载。
- 插件只有 `disabled/active/failed`；一个 root LIFO cleanup 栈覆盖 setup 失败与 Worker close。
- Context 只保留 `get/provide/on/effect/config/data_path`。
- 启停、reload、安装、卸载以及 `restart_required` 配置统一重建整个 Worker。
- Handler 异常记录后继续；Service 异常返回调用者。两者不改变插件状态。
- 调用超时不重放，杀死 Worker；同一 token 后台最多重建一次，失败后等待人工 reload 或 Core 重启。

进程隔离、强杀、IPC 限额、原子配置写入和安装路径安全继续保留。

## 后果

新增插件能力只需要 Service、Host event 或已有 Host descriptor，不需要增加生命周期状态。局部管理变更会
丢失 Worker 内存状态，因此需保留的内容必须进入 config 或插件 data directory。整 Worker 重建的短暂成本
可以接受，换取单一、可定位的加载路径。

本 ADR 只 supersede ADR-0027 中 transform、Session scope、动态 reconcile、sticky/conflict 传播和局部
reload 决策；ADR-0027 关于开放 Service 组合与 generation 私有 Worker 的边界继续有效。
