# 专注陪伴

一个离线执行服务开发样例，用于验证后台计时、进度、取消和进程生命周期。它不需要模型或 API Key。

`f9fde091` 曾把它接到聊天框和主设置的“互动方式”。该产品入口已按用户要求撤回：当前聊天使用默认 Assistant，
安装或启用本插件不会接管聊天，也不能通过旧配置字段选择它。未来入口与界面尚未确定。

开发消费者使用 `focus_companion.executor` Service 的 `describe/begin/read/cancel` 合同。
`begin` 请求的文本可以是：

```text
专注 10 秒 整理桌面
专注 1 分钟 阅读
```

服务实际计时，`read` 返回剩余秒数和最终结果；`cancel` 中断后台计时，线程退出后才报告取消终态。
插件停用或退出时停止计时并回收线程。其他文字返回用法提示；支持 1 秒至 120 分钟，每次处理一项事项。
样例不读取对话历史、不消费 Context，也不调用工具或在线服务。

在隔离环境中运行回归：

```powershell
runtime\python.exe -m pytest -q tests/unit/test_focus_companion_plugin.py
```

需要检查安装包时，在仓库打包：

```powershell
runtime\python.exe tools\release\package_optional_plugin.py --source plugins\optional\focus_companion --output dist\focus_companion-0.1.0.sakplugin.zip
```

插件通过 `sakura.host.executors` 登记服务供开发消费者绑定；登记不增加用户界面。
执行方法只接受宿主的真实调用身份。受理时保存不可变的操作信息，后台线程不依赖 RPC 的调用者上下文。
结果在进程内保留最近 32 项；保留期间重复受理相同操作不会重启计时，重复读取终态不会改变结果。插件退出后不恢复计时。
