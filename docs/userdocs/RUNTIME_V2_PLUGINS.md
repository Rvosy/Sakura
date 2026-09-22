---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-09-22
---

# Python 插件

Sakura 使用 Plugin API v4。每个启用插件运行在独立进程和独立 Python dependency root 中；一个插件失败或
卡死时，桌宠窗口、Core 和无关插件仍可响应。

插件进程不是安全沙箱。插件代码仍以当前用户权限访问文件和网络，只安装你信任的来源。

## 安装和卸载

1. 打开“设置 → 插件”。
2. 从插件市场选择插件并安装；已有插件包时选择“安装 ZIP”或“安装文件夹”。
3. 检查插件名称、作者、说明和声明的服务。
4. 打开启用开关并保存。

GPT-SoVITS、Genie、Mem0、SenseVoice 和手机网页端等是可选插件，新安装按需添加。源码中的
`plugins/optional/` 是开发与打包来源，不会在普通启动时自动安装。

Sakura 只安装 `api: 4` 插件。安装包不能包含符号链接、路径逃逸、特殊文件或跨平台非法文件名。安装操作会把
插件声明的 Python 依赖解析到该插件自己的目录；失败不会改写 Core Runtime 或其他插件环境。普通启动不会
联网安装或修复依赖。

未指定软件包源时，Python 依赖默认从阿里云 PyPI 镜像下载，并复用已有下载缓存；用户或插件指定的源优先。
镜像缺少某个版本时，可在启动 Sakura 前设置 `UV_DEFAULT_INDEX=https://pypi.org/simple`，再重试安装。
市场目录、说明和安装包按“设置 → 下载源”中的顺序下载，默认先尝试两条国内镜像，再访问 GitHub。
插件自行下载的模型或整合包使用各插件的来源配置，不随“设置 → 下载源”调整；具体见[下载插件资源](#下载插件资源)。

```text
plugins/user/<plugin_id>/                              用户插件代码
data/plugins/<plugin_id>/                              插件配置和数据
data/plugin-runtime/dependencies/<plugin_id>/           用户插件 Python 依赖
```

卸载会移除用户插件代码和 dependency root，但保留插件数据。随 Sakura 分发的 bundled 插件不能卸载，但默认
领域实现可以停用并由第三方插件替换。

## 升级已有安装

从曾内置这些能力的版本升级时，Sakura 会利用本地文件和发行包中的兼容材料，将退役内置插件迁入用户插件目录，
保留配置、数据和启停选择。兼容材料随目标版本提供，支持跳过中间版本直接升级；新安装不会因此启用这些可选插件。
迁移失败时，可以进入市场安装对应插件；已存在同版本时选择“重新安装”。重新安装会替换代码和 Python 依赖，保留配置、模型、数据和启停选择。安装过程中等待结果，不要删除原配置、模型或迁移备份。
如果旧版因迁移失败无法打开设置，先升级到修复了启动隔离的版本，再从市场修复。回退旧版后再次升级仍会检查未完成的迁移，不会自动降级已有的其他版本用户插件。

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

SenseVoice 主模型和词表从 ModelScope 下载，VAD 默认按 gitproxy.mrhjx.cn、ghproxy.vip、GitHub 官方的顺序下载同一文件。
GPT-SoVITS 的 macOS 安装使用相同顺序下载 Miniforge；源码先通过 gitproxy.mrhjx.cn 获取，失败后访问 GitHub 官方。
模型默认从 ModelScope 下载，Python 包默认使用阿里云 PyPI，Conda 使用清华镜像。
上游脚本单独指定的 PyTorch、TorchCodec 仍使用 PyTorch 官方源。镜像切换不改变固定的模型、安装器版本或源码提交。
显式设置的 `GPT_SOVITS_REPO`、`GPT_SOVITS_MINIFORGE_URL` 使用指定地址；模型源、PyPI 和 Conda 的覆盖方式见
[macOS 安装脚本](../../plugins/optional/sakura_gpt_sovits/install_gpt_sovits_macos.sh)。

Intel Mac 使用当前上游脚本安装时，PyTorch 官方源没有适用于 Python 3.10、macOS x86_64 的 TorchCodec 包，
会在依赖安装阶段失败；更换下载镜像无法解决。此时可连接已部署的 GPT-SoVITS 服务。

网络失败按顺序切换来源，每个来源只尝试一次；磁盘写入、文件大小或安装器格式错误直接报错。全部来源失败时保留原始原因。
GPT-SoVITS 安装失败会记录退出码和有长度限制的输出末尾，经过日志脱敏；开启远程诊断时沿用现有错误上报链路。

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
