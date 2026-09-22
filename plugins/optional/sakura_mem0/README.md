# Mem0 长期记忆

为 Sakura 提供长期记忆的整理、检索和管理，让角色在对话中回想已有信息。

## 使用

需要支持 Plugin API v4 的 [Sakura](https://github.com/Rvosy/Sakura)。下载本仓库 ZIP，在 Sakura 的“插件 → 更多 → 从 ZIP 安装”中导入，然后启用插件。

在记忆设置中配置所需的模型服务和资源。插件包含本地记忆存储与向量检索；是否需要联网取决于所选模型服务。

首次安装会按 `requirements.txt` 准备插件依赖，需要网络。本仓库不包含 Python 运行环境、模型权重或用户数据；下载源码不等于已准备好完整离线环境。

从内置版本迁出的用户，由支持迁移的新版 Sakura 保留原插件 ID、设置和资源路径。仍内置该插件的旧版 Sakura 不支持安装同 ID 外部副本。

## 开源说明

插件沿用 Sakura 的 MIT 许可；第三方代码、依赖与模型遵循各自许可。

`mem0/` 中的第三方代码保留 Apache-2.0 许可，见 [LICENSE](LICENSE)；Sakura 插件适配代码的许可见 [LICENSE-SAKURA](LICENSE-SAKURA)。
