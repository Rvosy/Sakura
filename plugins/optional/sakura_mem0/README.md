# Mem0 长期记忆

为 Sakura 提供长期记忆的整理、检索和管理，让角色在对话中回想已有信息。

## 使用

在支持插件市场的 [Sakura](https://github.com/Rvosy/Sakura) 中，打开“设置 → 插件 → 市场”，搜索 Mem0 并安装。安装后到“已安装”中启用插件。

也可以下载 [Sakura-Mem0 独立仓库](https://github.com/Rvosy/Sakura-Mem0)的源码 ZIP，通过“设置 → 插件 → 更多 → 从 ZIP 安装…”导入。插件使用 Plugin API v4，具体宿主服务要求见 `plugin.yaml`；旧版 Sakura 可能不支持这些接口。

在插件设置中安装本地向量模型，在“设置 → 模型”中选择记忆整理模型，再到“记忆”页管理内容。记忆存储和向量检索在本地完成；使用远程模型服务整理记忆时需要联网。未选择整理模型时，仍可手工管理和检索已有记忆。

首次安装需要联网下载 `requirements.txt` 中的 Python 依赖。插件包不包含 Python 运行环境、模型权重或用户数据；下载源码后仍需在 Sakura 中安装，并准备所需资源。

从内置版本升级时，支持迁移的 Sakura 会保留原插件 ID、设置和资源路径。旧版仍内置此插件时，不能再安装同 ID 的外部副本。迁移失败的处理见 [Sakura 插件升级指南](https://github.com/Rvosy/Sakura/blob/main/docs/userdocs/RUNTIME_V2_PLUGINS.md#升级已有安装)。

## 开源说明

插件沿用 Sakura 的 MIT 许可；第三方代码、依赖与模型遵循各自许可。

`mem0/` 中的第三方代码保留 Apache-2.0 许可，见 [LICENSE](LICENSE)；Sakura 插件适配代码的许可见 [LICENSE-SAKURA](LICENSE-SAKURA)。
