---
kind: spec
status: superseded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
superseded_by: docs/adr/0035-clean-runtime-v2-layout-and-explicit-legacy-import.md
updated: 2026-08-26
---

# WP-3-06：Runtime v2 数据兼容门禁

## 当前边界

Sakura 只提供 Runtime v2 产品入口。Legacy Qt 进程、双入口共享锁验收和旧桌面回退入口已经按
[ADR-0034](../../adr/0034-retire-legacy-qt-runtime.md) 退役，不再构成源码、测试或发布契约。

本规范的旧 main 数据兼容要求已被 ADR-0035 取代。Runtime v2 正常启动不扫描、读取、迁移或兼容旧 main
目录；将来的迁移由用户主动选择旧目录的独立 importer 完成。以下内容仅保留为历史设计证据，不再约束
当前实现。

## 数据权限与版本门

测试数据只能从 `tests/fixtures/runtime_v2/` 复制到系统临时目录；不得让测试指向真实 `data/**` 或
`characters/**`。fixture 源只读，运行产物写入测试专属临时目录。

全局共享数据版本锚点是 `data/config/system_config.yaml::config_version=4`：

| 数据状态 | Runtime v2 行为 |
| --- | --- |
| 当前版本且结构有效 | 允许按当前 repository 契约读写 |
| 旧版本或缺少版本 | 只允许受支持的 migration；迁移前保留可恢复备份 |
| 未来版本 | 明确拒绝写入，不降级、不覆盖 |
| YAML/JSON/JSONL 损坏或必要资源缺失 | 明确拒绝写入，保留原 bytes，不以默认值修复 |
| v2 私有文件未来 schema | 仅禁用或只读对应域，不回写其他共享文件 |

WebView 只能获得稳定错误码和可操作提示，不得收到绝对路径、凭据、原始文件内容或未清洗异常。

## 兼容写入边界

- 聊天历史沿用当前 JSONL 文件名与字段，追加必须保持完整行；不得静默截断、修复或重放旧记录。
- Provider、模型和设置保存必须原子化，保留未知字段及未修改的 secret bytes，失败时保留旧文件。
- Runtime v2 私有状态只写入其明确拥有的命名空间。
- 角色包、Memory、插件、任务、提醒、外部资源、备份和损坏工件不得因无关迁移发生变化。
- 路径越界、符号链接逃逸、未来 schema 或非隔离测试根必须 fail closed。

## 自动验收

兼容测试必须覆盖：

1. 当前、旧版、缺版本、未来版本和损坏数据的读取/迁移/拒绝路径；
2. 迁移备份、原子替换、失败回滚和重复执行幂等性；
3. 聊天 JSONL、角色配置、Provider/模型配置及 v2 私有设置的实际 parser/repository；
4. 迁移前后文件清单与 SHA-256 对比，证明只有声明路径变化；
5. `data/`、凭据和真实用户内容不进入测试报告或日志。

所需 profile 为 `docs`、`smoke`、`core-host`、`runtime-v2-shell` 和 `python-full`。跨平台不能执行的验证要
明确记录为未验证，不得用已删除的旧进程冒充兼容性证据。

## 回退

回退只针对 migration 或当前 Runtime v2 写入逻辑：停止新写入、退出 Core、恢复迁移前备份或撤销代码。
不得把默认入口切回历史运行时，也不得删除用户在新版中产生的有效数据。
