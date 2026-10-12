# Genie 语音合成

将 Genie 语音合成接入 Sakura，支持已有服务和插件管理的本地运行资源。

## 使用

在支持插件市场的 [Sakura](https://github.com/Rvosy/Sakura) 中，打开“设置 → 插件 → 市场”，搜索 Genie 并安装。安装后到“已安装”中启用插件。

也可以下载 [Sakura-Genie 独立仓库](https://github.com/Rvosy/Sakura-Genie)的源码 ZIP，通过“设置 → 插件 → 更多 → 从 ZIP 安装…”导入。插件使用 Plugin API v4，所需宿主服务见 `plugin.yaml`。

在语音设置中选择 Genie，配置服务来源、地址与角色语音；使用本地模式前需先准备对应运行资源。

首次安装需要联网下载 `requirements.txt` 中的 Python 依赖。插件包不包含 Python 运行环境、模型权重或用户数据；下载源码后仍需在 Sakura 中安装，并准备所需资源。

更新保留已有设置、资源和启停选择，操作方法见 [Sakura 插件升级指南](https://github.com/Rvosy/Sakura/blob/main/docs/guide/plugins.md#更新与修复)。

## 开源说明

插件沿用 Sakura 的 MIT 许可；第三方代码、依赖与模型遵循各自许可。
