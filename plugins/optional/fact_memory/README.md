# 便签记忆

把希望角色记住的事实写成便签，聊天时按关键词召回。各角色分别保存，内容由你增删和修改；不需要额外模型，也不读取或改写其他记忆插件的数据。插件 ID 为 `fact_memory`，使用 Plugin API v4 和 Context schema 2。

## 使用

1. 在插件页从 ZIP 或文件夹安装本插件，启用并应用设置。
2. 打开插件设置，新增便签，填写内容和可选关键词。
3. 回到聊天窗口发送相关问题。保存的便签从下一轮对话参与匹配。

例如，内容写“项目交付时间是周五下午三点”，关键词写“项目交付”，然后询问“项目交付安排是什么？”

内容最多 1000 字符，关键词最多 200 字符，多个关键词可用逗号、顿号、分号或换行分隔。创建、修改和删除立即保存，不需要再按主设置窗口的“应用”。列表支持按内容或关键词搜索。

有关键词时按关键词匹配，没有关键词时按文字匹配。模型也可按需调用只读工具搜索正文或关键词；聊天不会自动创建便签。每次召回最多 5 条，正文合计不超过 2000 字符。

## 数据与更新

便签保存在插件私有数据目录的 `facts.sqlite3`。切换角色后只能查看和使用该角色的便签。停用或卸载保留数据，删除单条便签无法恢复。更新方法见[插件指南](../../../docs/guide/plugins.md#更新与修复)。

已经开始的回复使用本轮采集的内容。删除便签后，聊天历史或其他记忆插件仍可能包含同一事实；排查时在 Prompt Trace 中查看 `fact_memory` 提供的上下文。

## 开发

在仓库根目录打包，安装包只含插件文件，不含用户便签：

```powershell
runtime\python.exe tools\release\package_optional_plugin.py --source plugins\optional\fact_memory --output dist\fact_memory.sakplugin.zip
```

macOS/Linux 使用 `runtime/bin/python`。真实对话回归见 `tests/integration/test_fact_memory_core_protocol.py`，同类插件的接口见[插件能力接入](../../../docs/development/plugin-recipes.md)。
