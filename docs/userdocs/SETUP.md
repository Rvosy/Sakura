---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-09-22
---

# 安装与首次配置

想直接使用 Sakura，请从 [Releases](https://github.com/Rvosy/sakura/releases) 下载完整包。GitHub 自动生成的源码压缩包不包含 bundled Python Runtime，也没有已经构建好的桌面程序。

## 下载和安装

### Windows

1. 下载 Windows x64 Setup 或 Portable ZIP。
2. Setup 直接运行安装器；Portable ZIP 解压到普通目录后运行 `sakura.exe`。
3. 使用 `0.9.x` 旧版包时，按对应 Release 页面和包内说明使用随包提供的旧入口；这些入口不属于当前 Runtime v2 源码布局。

Runtime v2 正式包已经包含冻结的 Python Core 依赖，用户安装时不再运行 pip。

### macOS

优先使用 Releases 中与处理器架构匹配的完整包。没有对应包时，请按 [macOS 使用说明](MACOS_SETUP.md) 从源码构建。

### Linux

Linux x64 目前从源码运行。安装 WebKitGTK 4.1 开发包后执行 `bash scripts/install.sh`，脚本会下载冻结的 Python Runtime 并安装依赖。窗口、截图和 Wayland 限制见 [Linux 使用说明](LINUX_SETUP.md)。

## 从 1.1.0 升级

旧角色包和聊天历史继续可用。原来的立绘会显示为“立绘1”，在“设置 → 角色”的“显示方式”中选择。
立绘需要“立绘”插件保持启用；增加其他形态需要安装对应插件，并准备该形态的资源。
工坊会恢复升级前尚未发布的草稿，包括修改过的默认图片和表情标签。

升级时会把本机已有角色的旧语音开关和引擎选择补入本机配置，已经设置过的新选择不会被覆盖，原本关闭的
语音也不会被打开。模型、参考音频和引擎配置仍保留，无需重新导入；已停用的引擎插件仍保持停用。

新版支持导入旧版 `.char` 角色包，保留立绘、人设和语音资源。新导入角色不继承包内的语音开关和引擎选择，
请在本机语音设置中选择引擎并启用。新的语音选择只保存在本机，分享角色包不会带走这些选择。

旧 MCP 配置不再恢复；联网工具通过插件页管理。

## 从源码运行

源码检出默认不包含 `runtime/`。安装脚本会在缺少 Runtime 时按当前平台清单下载冻结的 CPython；也可以从 Releases 拷贝对应平台的 Runtime。不要使用系统 Python 替代。

Windows：

```powershell
.\scripts\install.bat
.\scripts\start.bat
```

macOS / Linux：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

`scripts/start.bat` 和 `scripts/start.sh` 都会增量编译并启动 debug 开发版。

源码安装脚本和 Windows 本地打包默认使用阿里云 PyPI 镜像。可通过 `PIP_INDEX_URL` 指定其他源；插件依赖
安装也支持 `UV_DEFAULT_INDEX`。这些设置只影响本次启动的安装进程，不写入系统 pip 配置。

### Windows 本地生成安装包

仓库已准备好 `runtime/`、Rust 和 Node.js 后，在根目录执行：

```powershell
.\scripts\package.bat
```

脚本生成 Windows Setup、Portable ZIP 和单独的 Playwright 插件 ZIP 到
`artifacts/local/`。冻结 Python 归档、pip wheel 和 uv wheel 缓存位于 `temp/release-cache/`；后续构建会先
核对 Python 归档的固定大小并确认归档可解析，再复用缓存。pip/uv 锁文件中的上游包哈希仍按安装工具的要求校验。缓存失效时只重建对应缓存，不会把开发用 `runtime/` 中已经
安装的包直接复制到发行包。

## 第一次启动

首次启动需要完成两件事：添加角色、配置模型服务。设置窗口会自动打开。

### 添加角色

在“角色与布局”页导入 `.char` 文件。角色包可以包含角色卡、立绘、主题和语音参考资源。完成导入后，从“当前角色”选择要显示的角色。展开「当前角色」后，每一行可以删除该角色；页面下方也可以删除当前选中的角色包。删除前会确认。角色包从本机移除后无法恢复，聊天记录和记忆会保留。重新导入相同 ID 的角色包可以继续使用这些记录。

从源码运行时，也可以把现成的角色目录放到仓库根下的 `base_characters/`（历史拼写 `base_charaters/` 同样有效）。Core 会扫描其中直接含有 `.char` / `.card.char` 的文件夹，包括像 `角色包/` 这样的嵌套目录。0.9.5 之后立绘和语音是分开的：同一角色若同时存在旧的整包 `.char` 和 `.card.char` + `.voice`，只导入后一种。已有逻辑 ID 不会重复导入。发行包仍然不附带默认角色。

![导入角色包](assets/setup_01.webp)

应用内角色工作室可以新建或修改角色。入口位于“设置 → 角色与布局 → 修改角色”，不需要单独构建或启动工作室程序。

### 配置模型服务和模型

1. 在“模型服务”页填写 API 地址、API Key，并保存。
2. 在“模型”页选择对话模型和视觉对话模型。
3. 发送一条普通消息验证聊天；需要截图功能时，再发送一张图片验证视觉模型。

详细字段和错误说明见 [API 模型服务与模型](API_CONFIG.md)。

![配置模型](assets/setup_02.webp)

## 设置页面

设置按用途分成四组：

| 分组 | 页面 | 内容 |
|---|---|---|
| 角色 | 角色与布局、外观 | 角色包、立绘、气泡、输入栏、字体和主题 |
| 智能 | 模型服务、模型、语音、记忆 | 模型连接、TTS、长期记忆和本地资源 |
| 行为 | 交互、隐私、工具 | 回复表现、主动屏幕感知与工具 |
| 系统 | 插件、系统 | 插件管理、日志等级、Agent Trace 和开机自启动 |

大部分设置在保存后立即生效。需要重建 Core 或重新加载目标插件时，设置窗口会自动连接当前实例。保存失败会保留页面中的草稿，并显示稳定原因码。

## 聊天与截图

在桌宠输入栏输入文字即可聊天。输入栏左侧 `+` 提供截图和插件动作。截图会随下一条消息发送，框选取消后不会留下附件。

“设置 → 隐私”可以开启主动屏幕感知，并调整检查间隔、冷却时间、批次数和分辨率。忙时跳过，不会补跑积压截图。完整说明见 [聊天、截图与屏幕感知](CHAT_SCREEN_AND_CONTEXT.md)。

回复保存在本地 Timeline。气泡顶部的左右按钮可以翻阅当前运行会话中已经完成的回复；输入框草稿不会因 Core 重启被自动发送。

## 语音

语音是可选功能。关闭“启用角色语音”后，Sakura 不提交 TTS 合成任务，聊天和字幕照常工作。

先从插件市场安装 GPT-SoVITS 或 Genie，并启用对应插件。安装方式见[插件指南](RUNTIME_V2_PLUGINS.md)。配置步骤：

1. 打开“设置 → 语音”。
2. 开启当前角色语音并选择语音引擎。
3. 填写该引擎需要的地址、路径或运行参数。
4. 点击“测试语音”，成功后保存。

GPT-SoVITS 可以使用 Sakura 管理的本地服务，也可以填写自定义服务地址，不需要聊天用的 API Key。Genie 使用对应的服务接口。配置保存后若页面提示 `restart_required`，点击该引擎区块的重新加载动作。

![配置语音](assets/setup_03.webp)

![配置外置语音服务](assets/setup_04.webp)

## 长期记忆

长期记忆由可选的 Mem0 插件提供，先从插件市场安装并启用。Mem0 使用本地 ONNX 向量模型查找相关记忆；第一次使用时，在该插件设置中安装模型。下载和初始化期间，普通聊天仍可使用。

记忆页支持搜索、新增、编辑和删除。编辑内容必须点击页面内的保存按钮才会提交。自动整理使用“模型”页中 Memory 插件注册的模型用途；没有选择整理模型时，手工管理和本地召回仍然可用。

![安装本地记忆模型](assets/setup_05.webp)

Memory 状态长期停在初始化时，先查看用户数据目录中的 `data/logs/sakura-runtime.log`。不要删除 `data/memory/` 中的 Qdrant、SQLite 或锁文件；正常退出、备份整个用户数据目录后再排查。

## MCP 和插件

MCP 基础组件为其他插件提供连接能力，具体功能和配置由对应插件提供。详见 [MCP 基础组件](RUNTIME_V2_MCP.md)。

本地插件通过“设置 → 插件”安装。插件与 Sakura 具有相同的本机权限，只安装可信代码。详见 [Python 插件](RUNTIME_V2_PLUGINS.md)。

## 更新

Windows Setup 与 macOS 安装版通过签名的 Tauri Updater 更新。安装器替换发行资源并保留用户数据；升级后的首次启动可能执行插件或配置兼容迁移，见[插件升级说明](RUNTIME_V2_PLUGINS.md#升级已有安装)。Windows Portable 不自替换，也不会启动 NSIS；发现新版本后下载或打开新版 Portable ZIP，由用户退出 Sakura 后解压到目标目录。

当前 Runtime v2 正常启动只读取 v2 数据契约，不会自动扫描旧目录。Windows 用户可在首次设置尚未完成、v2
用户数据为空时，通过“迁移旧版本”显式选择 0.9.x 目录；迁移会在 Core 启动前离线转换并校验数据，不支持与
已有 v2 数据合并。

`tools/cleanup.py` 默认只预览可清理内容。确认列表后才使用 `--apply`。不要对不确定的目录执行清理。

## 获取帮助

先看 [运行日志与故障排查](RUNTIME_LOG_TROUBLESHOOTING.md)。提交 Issue 时写明系统、Sakura 版本、复现步骤和已经尝试的处理；日志片段要先检查隐私和密钥。
