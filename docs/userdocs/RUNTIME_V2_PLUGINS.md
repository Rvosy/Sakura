---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-09-06
---

# Python 插件

Sakura 使用 Plugin API v4。每个启用插件运行在独立进程和独立 Python dependency root 中；一个插件失败或
卡死时，桌宠窗口、Core 和无关插件仍可响应。

插件进程不是安全沙箱。插件代码仍以当前用户权限访问文件和网络，只安装你信任的来源。

## 安装和卸载

1. 打开“设置 → 插件”。
2. 选择“安装 ZIP”或“安装文件夹”。
3. 检查插件名称、作者、说明和声明的服务。
4. 打开启用开关并保存。

Sakura 只安装 `api: 4` 插件。安装包不能包含符号链接、路径逃逸、特殊文件或跨平台非法文件名。安装操作会把
插件声明的 Python 依赖解析到该插件自己的目录；失败不会改写 Core Runtime 或其他插件环境。普通启动不会
联网安装或修复依赖。

```text
data/user_plugins/<plugin_id>/                         用户插件代码
data/plugins/<plugin_id>/                              插件配置和数据
data/plugin-runtime/dependencies/<plugin_id>/           用户插件 Python 依赖
```

卸载会移除用户插件代码和 dependency root，但保留插件数据。随 Sakura 分发的 bundled 插件不能卸载，但默认
领域实现可以停用并由第三方插件替换。

## 启停与设置

插件列表按功能扩展、能力提供方和系统组件分组，用浅色标签区分类别。顶部可切换分类，或搜索名称、作者、
ID 和简介。卡片在名称右侧显示运行状态，领域和安装来源在详情中查看。分类和图标由插件声明，不代表权限或运行是否正常。

选中插件后，在详情里启用或停用；有可配置区块时，点击右上角“插件设置”打开独立窗口。插件只能提供
Sakura 支持的字段、状态卡、资源进度和动作，不能加载自己的网页或脚本。

- 编辑字段后点“完成”，再在设置页底栏点“应用”或“保存并关闭”，配置才会提交。
- “取消”、右上角关闭或 Esc 恢复该插件打开窗口时的可编辑值，其他插件草稿保留。
- 测试连接、数据增删和下载等操作点击后就会执行，取消窗口不会撤销这些操作。

语音配置也可从“语音”页进入，两个入口共用同一套控件；记忆内容管理仍在“记忆”页。保存结果分为：

- `applied`：当前插件进程已经应用；
- `restart_required`：配置已保存，Sakura 在本次操作中重新加载目标插件及必要的硬依赖插件；
- `error`：配置已保存，但插件没有应用。

install、enable、disable、reload 和 uninstall 都是明确的用户操作。它们不重启桌面应用，也不重启无关插件。
失败后不会自动重试、自动恢复或重放调用。

## 下载插件资源

需要本地模型或运行组件时，在所属插件的设置窗口中安装、重试或取消。GPT-SoVITS 与 Genie 整合包、
Mem0 向量模型的资源管理入口都在各自的插件设置中。

“关于 → 组件”汇总已启用插件的资源状态，只提供“前往下载设置”；点击后会打开所属插件并定位到资源。
停用插件的资源不会出现在总览里，尚未应用的启停操作不会提前改变总览。“已安装”只表示资源状态，
外部服务是否就绪还要看插件自己的运行状态。

## 状态

- `disabled`：已安装但未启用；
- `active`：插件进程已经发布声明的 Service 和 Contribution；
- `failed`：manifest、依赖、导入、`setup()`、Service 冲突或进程运行失败。

常见原因码包括 `API_VERSION_UNSUPPORTED`、`MISSING_SERVICE`、`SERVICE_CONFLICT`、`DEPENDENCY_CYCLE`、
`PLUGIN_DEPENDENCIES_MISSING`、`PLUGIN_CALL_TIMEOUT`、`PLUGIN_PROCESS_EXITED` 和 `PLUGIN_ID_CONFLICT`。失败时先在
插件页执行 reload 或重试安装；仍然失败再查看[运行日志](RUNTIME_LOG_TROUBLESHOOTING.md)。不要手工移动安装
事务目录或其他插件的 dependency root。

插件接入宿主日志后，可以在运行日志窗口的“插件”页按插件筛选，也可以查看 `data/logs/sakura-plugins.log`。
安装、加载和依赖失败由宿主记录在 `data/logs/sakura-runtime.log`。没有业务日志时，可能是插件尚未接入，
不能据此判断它没有运行。

插件作者请看 [Plugin API v4 开发指南](../devdocs/SAKURA_PLUGIN_SDK.md)，或使用
[插件开发 AI 提示词](../devdocs/SAKURA_PLUGIN_AI_PROMPT.md)开始开发。
