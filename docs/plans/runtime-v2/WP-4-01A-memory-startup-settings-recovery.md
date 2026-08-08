---
kind: plan
status: active
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-09
---

# WP-4-01A Memory 启动预热与设置窗口恢复纠正计划

## 范围

本纠正包只恢复 WP-4-01 已冻结但真实产品未满足的两项生命周期语义：当前 Core generation 创建
Memory owner 后立即非阻塞预热已安装的固定 embedding 模型；设置窗口在 Memory 首读加载、超时或关闭
期间始终可以有界关闭并重新创建。它不改变 Memory 数据、协议 DTO、模型、配置 schema 或下载策略。

## 实施顺序

1. 用 Python 单元测试先证明 Memory owner 构造后、任何 `status/search/settings.get` 之前已经调用一次
   `preload(wait=False)`；模型未安装时继续稳定降级且不得隐式联网。
2. 把首个预热触发从 `MemoryBoundary.status()` 移到 owner 创建路径；状态读取只观察，不启动新的加载根。
3. 让设置页首次 Memory 快照对 generation/transport/deadline 瞬时错误执行可取消的有界重试，使用稳定
   中文加载状态，不把 Router 原始错误直接显示给用户，也不在页签切换间建立两个轮询器。
4. 关闭设置时停止页面重试并使旧 window generation 失效；原生窗口若正在销毁，把一次立即重开请求
   排队到 `Destroyed` 清场后，用新的单调 generation 创建窗口。
5. 运行定向 Python、前端和 Rust 测试，再运行 task required profiles；自动事实写入 audit record。

## 故障矩阵与退出条件

- 已安装模型、模型缺失、预热失败、Core 暂不可用、首读 deadline、generation 更换。
- 记忆页加载中切换页签、点击刷新、关闭设置、立即重开、连续重复打开及应用退出。
- 旧窗口的迟到成功/失败不得更新新窗口；同一窗口最多一个首读重试与一个列表轮询。
- 正常聊天不等待 Memory；不隐式联网，不修改或修复 `data/memory/**`、旧 `memory.json` 或模型缓存。
- 最新候选的 docs、smoke、core-host、runtime-v2-shell 全绿，并另跑 python-full 扩大回归；Windows
  实机确认页面不再
  反复切换文案、不出现 deadline 原文，关闭后可以立即重新打开且退出无相关进程残留。

## 回退

停止新设置动作并正常退出应用，整体回退本 WP 的启动预热、首读重试和窗口重开协调。回退不得删除、
恢复、迁移或修复任何用户 Memory、embedding 缓存、配置或聊天历史；WP-4-01 已接受的数据契约保持不变。
