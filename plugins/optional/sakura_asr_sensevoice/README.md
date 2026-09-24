# SenseVoice 语音识别

使用 SenseVoice 与 sherpa-onnx 在本地识别语音，为 Sakura 提供语音输入。

## 使用

在支持插件市场的 [Sakura](https://github.com/Rvosy/Sakura) 中，打开“设置 → 插件 → 市场”，搜索 SenseVoice 并安装。安装后到“已安装”中启用插件。

也可以下载 [Sakura-SenseVoice 独立仓库](https://github.com/Rvosy/Sakura-SenseVoice)的源码 ZIP，通过“设置 → 插件 → 更多 → 从 ZIP 安装…”导入。插件使用 Plugin API v4，具体宿主服务要求见 `plugin.yaml`；旧版 Sakura 可能不支持这些接口。

在插件设置中下载识别模型与语音活动检测（VAD）资源，再在语音输入设置中选择 SenseVoice。点击聊天输入栏的麦克风开始录音，再次点击后停止并识别；识别结果写入草稿，确认后再发送。资源就绪后，识别在本地完成。

首次安装需要联网下载 `requirements.txt` 中的 Python 依赖。插件包不包含 Python 运行环境、模型权重或用户数据；下载源码后仍需在 Sakura 中安装，并准备所需资源。

从内置版本升级时，支持迁移的 Sakura 会保留原插件 ID、设置和资源路径。旧版仍内置此插件时，不能再安装同 ID 的外部副本。迁移失败的处理见 [Sakura 插件升级指南](https://github.com/Rvosy/Sakura/blob/main/docs/userdocs/RUNTIME_V2_PLUGINS.md#升级已有安装)。

## 开源说明

插件沿用 Sakura 的 MIT 许可；第三方代码、依赖与模型遵循各自许可。

依赖及模型来源见 [SOURCES.md](SOURCES.md)。
