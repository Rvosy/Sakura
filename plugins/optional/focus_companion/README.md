# 专注陪伴

一个离线互动插件。选择它后，可以在原来的对话输入框里开启专注计时，无需配置模型或 API Key。

安装并启用插件后，在设置中把互动方式切换为“专注陪伴”，然后发送：

```text
专注 10 秒 整理桌面
专注 1 分钟 阅读
```

计时期间显示剩余秒数，完成后在原对话中回复。点击停止会中断实际计时；插件停用或退出时也会停止计时并回收线程。
发送其他文字会收到用法提示。支持 1 秒至 120 分钟，每次处理一项专注事项。

切回默认互动方式后，可以继续原来的模型对话。专注陪伴不读取历史来生成内容，也不调用工具或在线服务。

从仓库打包：

```powershell
runtime\python.exe tools\release\package_optional_plugin.py --source plugins\optional\focus_companion --output dist\focus_companion-0.1.0.sakplugin.zip
```

在插件管理中安装生成的 ZIP。插件声明 `focus_companion.executor` Service，通过 `sakura.host.executors` 登记到互动方式列表；
使用公开的 `describe/begin/read/cancel` 合同。宿主负责原有输入、操作状态、历史与输出，计时和进度内容由插件实现。

执行方法只接受宿主的真实调用身份。受理时保存不可变的操作信息，后台线程不依赖 RPC 的调用者上下文。
结果在进程内保留最近 32 项；保留期间重复受理相同操作不会重启计时，重复读取终态不会改变结果。插件退出后不恢复计时。
