# SenseVoice 语音识别

使用 SenseVoice 与 sherpa-onnx 在本地识别语音，为 Sakura 提供语音输入。

## 使用

在支持插件市场的 [Sakura](https://github.com/Rvosy/Sakura) 中，打开“设置 → 插件 → 市场”，搜索 SenseVoice 并安装。安装后到“已安装”中启用插件。

也可以下载 [Sakura-SenseVoice 独立仓库](https://github.com/Rvosy/Sakura-SenseVoice)的源码 ZIP，通过“设置 → 插件 → 更多 → 从 ZIP 安装…”导入。插件使用 Plugin API v4，所需宿主服务见 `plugin.yaml`。

在插件设置中下载识别模型与语音活动检测（VAD）资源，再在“设置 → 语音”的语音输入栏选择 SenseVoice。麦克风选择和识别测试由内置的“语音输入”插件提供，可从其插件设置打开。点击聊天输入栏的麦克风开始录音，再次点击后停止并识别；识别结果写入草稿，确认后再发送。资源就绪后，识别在本地完成。

首次安装需要联网下载 `requirements.txt` 中的 Python 依赖。插件包不包含 Python 运行环境、模型权重或用户数据；下载源码后仍需在 Sakura 中安装，并准备所需资源。

更新保留已有设置、资源和启停选择，操作方法见 [Sakura 插件升级指南](https://github.com/Rvosy/Sakura/blob/main/docs/guide/plugins.md#更新与修复)。

## 开源说明

插件沿用 Sakura 的 MIT 许可；第三方代码、依赖与模型遵循各自许可。

依赖及模型来源见 [SOURCES.md](SOURCES.md)。
