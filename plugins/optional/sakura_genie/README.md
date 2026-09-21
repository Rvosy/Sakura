# Genie 语音合成

将 Genie 语音合成接入 Sakura，支持已有服务和插件管理的本地运行资源。

## 使用

需要支持 Plugin API v4 的 [Sakura](https://github.com/Rvosy/Sakura)。下载本仓库 ZIP，在 Sakura 的“插件 → 更多 → 从 ZIP 安装”中导入，然后启用插件。

在语音设置中选择 Genie，配置服务来源、地址与角色语音；使用本地模式前需先准备对应运行资源。

首次安装会按 `requirements.txt` 准备插件依赖，需要网络。本仓库不包含 Python 运行环境、模型权重或用户数据；下载源码不等于已准备好完整离线环境。

从内置版本迁出的用户，由支持迁移的新版 Sakura 保留原插件 ID、设置和资源路径。仍内置该插件的旧版 Sakura 不支持安装同 ID 外部副本。

## 开源说明

插件沿用 Sakura 的 MIT 许可；第三方代码、依赖与模型遵循各自许可。
