# SakuraTTS

离线角色语音插件。空闲后由 SakuraTTS 释放推理进程；对话开始时可利用等待大模型回复的时间提前加载模型。

从[魔搭](https://modelscope.cn/models/SuzushimaArisu/SakuraTTS)下载对应平台的整合包。安装插件后，在插件设置中选择“导入整合包”，导入与本机平台匹配、包含准备组件的完整包。Windows 使用 x64 统一包，macOS 使用 Apple silicon 包。角色的 GPT、SoVITS 权重和参考音频沿用角色资源，在语音页选择 SakuraTTS。

“自动”在 Windows 优先检查 NVIDIA CUDA，检查失败则使用 CPU；macOS 使用 MLX。手动选择的后端失败时会报告错误。DirectML 需要手动选择。设置页面和应用启动不会加载模型。

再次导入即可更新整合包。新包检查通过后才切换，失败或取消保留原有安装；模型缓存、角色资源和插件设置保留在版本目录之外。当前尚未提供在线下载、检查更新和一键回退。

运行日志保存在插件数据目录的 `runtime-data/server.log`；错误详情保存在 `last-error.log`。支持范围和整合包构建方法见 [SakuraTTS](https://github.com/Rvosy/SakuraTTS)。
