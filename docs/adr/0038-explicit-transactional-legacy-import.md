---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0035-clean-runtime-v2-layout-and-explicit-legacy-import (legacy import only)
updated: 2026-08-30
---

# ADR-0038：首次启动提供显式、事务化的 0.9.x 数据导入

## 背景

Runtime v2 已有独立的用户布局、类型化 Timeline 和 Plugin Runtime v4，不能在正常启动链中继续读取旧格式。
但 0.9.x 用户拥有角色、聊天、长期记忆、TTS 模型和本机 Provider 配置；完全放弃这些数据会让升级无法接续。

ADR-0035 正确禁止了自动扫描、原地升级、双读和旧运行时复活，但其“不提供旧数据导入”不再满足产品升级需求。

## 决策

- 只在首次设置未完成且 v2 用户数据语义为空时开放导入，不提供合并。
- 用户必须通过原生目录选择器显式选择 0.9.x 根目录；正常启动仍不扫描或猜测旧目录。
- Core 在首次导航期间保持 paused。导入由当前发行 Python 的独立进程离线执行，不导入或运行旧源码。
- 旧目录只读；完整 v2 payload 在同卷 staging 中构建并校验，再以 rollback journal 提交。Core 后置校验成功才删除备份，失败则停 Core并恢复。
- 历史 JSONL 转为 Host Timeline；兼容的 Qdrant/SQLite 原样复制；角色和 TTS 配置转换为当前扩展与插件配置。
- 不兼容插件代码和私有用户数据进入隔离区且不执行；日志、缓存、锁和可重建数据不迁移。
- API Key 可以进入当前正式配置，但任何进度事件、日志和报告都不得包含凭据、正文、记忆、绝对源路径。
- 首版只支持 Windows 0.9.x 到 Windows v2。

## 与既有决策的关系

本 ADR 仅 supersede ADR-0035、ADR-0034 和 `runtime-v2-only-boundary` 中“完全不提供旧数据 parser/importer”的部分。
以下约束继续有效：v2 正常启动只认识 v2 schema；没有双读、双写、fallback、旧插件宿主或 Legacy Qt 运行链；
源数据不得原地修改。

## 后果

升级可以保留对话连续性和长期记忆，同时旧格式复杂度被限制在一个首次启动、离线、可删除的边界中。代价是需要
维护有限的 0.9.x fixture、磁盘空间预检、事务故障注入和真实 Windows 样本验收。
