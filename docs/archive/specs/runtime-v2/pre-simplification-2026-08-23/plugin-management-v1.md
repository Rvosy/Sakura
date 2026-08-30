---
kind: spec
status: archived
audience: maintainer
source_of_truth: self
updated: 2026-08-23
---

# Plugin Management v1

Core inventory 将 bundled 根下含 `plugin.yaml` 的目录视为安装记录；该根同时是 Python package，未声明
manifest 的辅助目录、缓存和升级残留不属于插件安装。user 根下每个非隐藏目录都是用户安装状态，缺
manifest 也必须可见并可卸载。已声明安装中的损坏 YAML、缺 ID/entry、API 不支持、链接目录和重复 ID
都必须作为 `InstalledPluginRecord` 可见，但不得进入 Runtime、依赖或 Service 图。每条记录使用来源与
相对目录名生成稳定 opaque `installId`；公开 DTO 不暴露目录名或本地路径。

重复 ID 规则：一个 bundled 与任意 user 副本并存时只运行 bundled，user 副本显示
`PLUGIN_ID_CONFLICT` 且可卸载；多个 bundled 或多个 user 同 ID 时该同源组全部不运行。只有 bundled
manifest 能声明 `required`。

公开记录包含 `installId`、nullable `pluginId`、元数据、source、canUninstall、desired enabled、required、
supported、state/reason，以及 `provides/requires/optional/missingServices/conflicts`。管理命令为：

```text
plugins.enabled.set {revision, installId, enabled}
plugins.uninstall {revision, installId}
plugins.settings.save {pluginId, sectionId, values}
```

`PluginDesiredStateStore` 是 `plugins.yaml` 唯一写入者，canonical 内容只有 `{id, enabled}`。旧 priority、
required 和未知字段只读忽略，并在下一次管理写入原子清理。revision 同时包含 desired 文件与 inventory
manifest 摘要。

安装先保存 `enabled=false` 再发布代码；卸载按 `installId` 进入 quarantine 后更新 desired state。失败保持
既有回滚和 fail-closed 语义，永不删除插件私有数据。启停结果区分
`applied / READY`、`recovered / DESIRED_SAVED_RUNTIME_RECOVERED` 和
`degraded / DESIRED_SAVED_RUNTIME_DEGRADED`；后两者都明确 desired state 已保存。
