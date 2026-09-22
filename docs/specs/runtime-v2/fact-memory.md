---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-16
---

# 便签记忆

## 能力与数据归属

可选插件 `fact_memory`（版本 `0.1.1`）使用 Plugin API v4，在自己的设置窗口提供便签 Collection，
并通过 Context schema 2 和只读工具增强正常 Assistant。它是第二种记忆实现的实际消费者，不改变主设置、
模型配置、默认聊天实现或产品模式。

插件只依赖公共 SDK 和 Python 标准库，不导入 `app.*`。数据库使用 `context.data_path("facts.sqlite3")`，
按当前角色 ID 隔离条目。插件拥有记录格式、查询及保存；不读取或修改 Mem0 数据库，不消费 Timeline 自动整理，
不调用 embedding 或额外模型。安装包不携带用户数据。

当前角色从 `sakura.host.character` 取得，不接收用户填写的角色 ID。Collection 和工具在调用时取得当前角色，
设置贡献的注册不依赖模型就绪。没有可用角色时沿用 Host 的明确错误，不把记录写入共享或空角色分区。
Context 请求缺少角色身份或与 Host 当前角色不一致时返回空贡献，不使用请求中的其他角色 ID 查询数据库。

## 设置与 Collection

插件、设置区块与 Context Provider ID 均为 `fact_memory`。设置区块显示为“便签记忆”，Collection ID 为 `facts`。
它使用现有 `sakura.host.settings` 与 `sakura.host.settings.collection-v0`，不注册 surface，不增加主设置页。
当前源码显式声明 `scope: character`；已分发的旧 `0.1.1` ZIP 未声明时由宿主按 character 兼容，无需为此重装。

| 字段 | 规则 |
|---|---|
| `content` | 必填非空字符串，最多 1000 字符 |
| `keywords` | 可选字符串，最多 200 字符；缺省为空 |
| `updatedAt` | 插件保存的更新时间，只读显示 |

Collection 沿用公共 query/create/update/delete 合同，条目形状为 `{itemId, values: {content, keywords, updatedAt}}`。
查询支持按内容或关键词进行字面子串搜索及分页；当前角色之外的条目不得被查询、更新或删除。
新增、更新、删除在 Collection 操作成功时提交 SQLite；关闭设置窗口和主设置的“应用”不再重复提交这些操作。
删除前明确告知无法恢复，确认后删除该条目，不自动建立回收站或备份副本。
失败必须明确返回，不能显示为已保存。重启后保留成功提交的内容与角色归属。

## Context 与搜索工具

Context 以 `scope: turn`、`failurePolicy: skip` 登记，使用当前输入和角色匹配便签；失败不会阻断普通聊天。
有命中时贡献一个 `facts` 文本片段，带“当前角色的便签记忆”标识及所选条目；无命中不贡献片段。
便签是可选上下文，默认对话仍执行其模型窗口预算，插件不要求这些内容绕过消费方预算。

自动 Context 的关键词按英文或中文逗号、顿号、分号、换行分隔；任一关键词在输入中命中即可入选，匹配忽略大小写。
只有未填写关键词的便签才使用轻量文本匹配：中文二元片段或英文完整单词有交集即命中。
先排关键词命中，再按命中数量、更新时间倒序及 ID 稳定排序。

每次最多 5 条，所选正文总计不超过 2000 字符；包装标题和条目标识不计入正文额度。
逐条判断预算，整条装不下就跳过并继续尝试后续条目，不截断便签。该限制由插件业务自行决定，
不增加 Host 的记忆类别、统一检索策略或新的 Context 裁剪规则。

只读工具 `fact_memory_search` 用于显式查询。`query` 去除两端空白并用 `casefold()` 归一后，
在同样归一的 `content` 或 `keywords` 中做字面子串匹配。是否填写关键词不限制正文搜索；
工具不使用 Context 的中文二元片段、英文分词或关键词优先规则，也不做模糊、语义匹配。
匹配结果沿用 Collection 的更新时间倒序、ID 稳定排序。工具与 Context 共用角色隔离和上述条数、正文预算：

| 参数/结果 | 合同 |
|---|---|
| `query` | 必填非空字符串，最多 200 字符 |
| `limit` | 可选整数，范围 1–5，默认 5 |
| 成功结果 | `{facts: [{id, content, keywords, updatedAt}]}` |

工具只查询当前角色，不自动写入、整理或删除。模型是否调用由现有 Assistant 的工具策略决定。
工具结果为空与执行失败分别表达。

## 生命周期与兼容

更新或删除影响之后的查询与 Context 采集，不重写已开始操作的上下文。停用、Worker 退出和 generation 关闭时，
沿用 SDK/Host 撤销 Context、工具、设置及 Collection 的机制，进程清理后不留下可调用的旧贡献。
停用保留插件私有数据，重新启用后可继续使用；角色切换遵守现有 Core generation 隔离。

`0.1.1` 仅修正显式搜索的匹配行为。原 `0.1.0` 私有数据库和条目直接可读，不迁移或重建数据库。
现有安装器拒绝覆盖相同插件 ID；升级使用“卸载插件”保留私有数据后再安装新版 ZIP，随后重新启用。
卸载不保留插件启用状态；重新安装后的条目 ID、内容和角色分区沿用原数据库。

本插件不转换已有记忆格式，也不删除 Timeline 或其他插件中的相同事实。删除便签或停用插件不保证模型忘记
聊天历史中已出现的文字；验收以当轮 `fact_memory` Context 和工具结果为主要证据，再分别检查其他输入来源。

## 验证

`tests/integration/test_fact_memory_core_protocol.py` 使用隔离用户根、真实 Core/插件进程和受控模型端点，
验证 Collection 保存的内容进入正常聊天请求，修改后重启保留，删除与停用后撤回贡献，以及只读工具查询和无模型管理。
插件聚焦测试覆盖角色分区、字段边界、Context 的关键词与无关键词分支、显式搜索已设关键词便签的正文、
条数/正文预算，以及无效写入保留已有数据。
这些是验收入口和范围，实际结果随本次运行记录；不据本文推断测试或原生窗口验收已经通过。

日常操作见[插件 README](../../../plugins/optional/fact_memory/README.md)，已有数据启动和回归范围见[生态验收指南](../../devdocs/PLUGIN_ECOSYSTEM_ACCEPTANCE.md)。

相关公共合同见 [Plugin Runtime v4](sakura-plugin-runtime-v4.md)、[Plugin SDK](../../devdocs/SAKURA_PLUGIN_SDK.md)
和[薄宿主与插件自有策略](../../archive/adr/0053-thin-host-and-plugin-owned-policies.md)。
