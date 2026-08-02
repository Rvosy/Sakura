---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-02
---

# Sakura 安装与配置指南

> 快速开始请看 [项目 README](../../README.md)；API 配置教程请看 [API_CONFIG.md](API_CONFIG.md)；macOS 专项问题请看 [MACOS_SETUP.md](MACOS_SETUP.md)。

---

## 第一步：下载发布包

打开 [Releases 页面](https://github.com/Rvosy/sakura/releases)，下载最新的构建包。

| 文件名 | 是什么 | 适合谁下载 |
|:-:|---|---|
| `sakura-v0.9.8-windows-x64.zip` | Windows 完整包，包含项目文件和 `runtime` | Windows 新手推荐 |
| `runtime-windows-x64.zip` | 只有 Windows 预置 Python 运行环境 | 拉源码、缺 `runtime` 的用户 |
| `sakura.char` | 默认 Sakura 角色包（含语音权重） | 想使用默认角色的用户 |
| `models--sentence-transformers--all-MiniLM-L6-v2.zip` | 长期记忆所需的本地向量模型 | 首次启动自动下载失败时手动导入 |

> 如果你只是想运行桌宠，下载 `sakura-v0.9.8-windows-x64.zip` 这个**完整包**。`runtime` 包不是完整程序，单独下载后不能直接启动。

---

## 第二步：安装依赖

解压完整包后，进入解压出来的软件目录。

- **Windows 用户：** 双击 `install.bat`，等待完成（约 5-15 分钟）。
- **Mac 用户：** 可尝试双击 `install.command`，或在终端进入项目目录后运行 `bash scripts/install.sh`。从源码运行、依赖问题、Apple Silicon/Rosetta 架构问题以及 GPT-SoVITS 语音搭建，详见 **[MACOS_SETUP.md](MACOS_SETUP.md)**（已在 Apple Silicon 实机测试）。
- **Linux 用户：** 当前没有正式发布包；如果从源码运行，进入项目目录后运行 `bash scripts/install.sh`。

> 如果是直接拉取的源码，需要先从 Release 页面下载对应平台的预编译依赖包（`sakura-runtime-*.zip`），把里面的 `runtime` 文件夹放到项目根目录，再运行安装脚本。不管下载的是 Release 完整包还是 GitHub 源码，这一步都要做。装完命令行窗口会自动关闭。

---

## 第三步：启动

- **Windows 用户：** 双击 `start.bat`
- **Mac 用户：** 双击 `start.command`，或在终端运行 `bash scripts/start.sh`
- **Linux 用户：** 在终端运行 `bash scripts/start.sh`

首次启动会先让你选择或导入角色，再配置 API 供应商和模型。已有角色与 API 配置的用户不会重复进入引导。

---

## 首次配置

### 导入角色包

从 [Releases 页面](https://github.com/Rvosy/sakura/releases) 下载角色包。Release 附件中，大小约 300MB、以 `.char` 结尾的文件即为包含语音的完整角色包。

下载后在软件设置中点击**导入 .char**，选择文件完成导入。

![导入角色包](assets/setup_01.webp)

---

### 配置模型

进入**模型**页面，填写 `Base URL`、`API Key` 和模型名称。新手或第一次配置中转站的用户，按 **[API 配置教程](API_CONFIG.md)** 操作。

> 必须选择支持多模态（图像识别）的模型，否则屏幕观察等功能会报错。推荐 Gemini Flash 系列。DeepSeek 系列作为主模型时，截图识别等功能会报错。

填写完成后点击**检测模型**获取可用模型列表，再点击**测试 API** 验证连通性。

![配置模型](https://oss.cialloo.cn/img/setup_02.webp)

### Runtime v2 桌宠聊天

使用 Runtime v2 桌宠窗口时，输入文字后按 Enter 或点击发送按钮即可发起真实聊天；Shift+Enter 仍用于
换行。回复生成期间，发送图标会切换为可点击的环形旋转条，输入框仍可编辑下一轮草稿；点击旋转条会
停止当前生成。等待时气泡会循环显示简短点号，输入框提示“角色名正在思考中…”。系统开启“减少动态
效果”时圆环不会旋转、点号固定为 `...`，但仍可点击取消。界面不提供“立即显示”按钮。

气泡和输入栏会随内容在允许范围内调整高度。输入栏从一行扩展到最多四行或收回时，发送/取消按钮保持
在右下角且完整可见；布局在原生窗口确认后整块更新，不再先移动内部控件。

启动时，角色问候会在桌宠窗口和初始立绘显示完成后逐字出现。模型返回多段回复时，桌宠会按段依次
清屏显示，并在每段开始时同步切换对应立绘。
发送消息后等待模型回复期间会保持当前立绘，不会先切换成固定等待表情。立绘采用重叠淡入淡出，快速
连续切换时只提交最后一张，图片加载失败也会保留当前画面。

在桌宠可见区域点击右键，可以勾选或取消“显示中文字幕”。勾选时优先显示模型返回的中文翻译，缺少
翻译会自动回退到原文；取消勾选后显示日文原文。选择会保存到 Runtime v2 的用户配置中，下次启动继续
使用。切换后当前气泡会立即换成所选语言；若正在逐字显示，当前段会用新语言从头重新显示，已经播完的
段落不会重复。

一次回复显示完成后，可以使用气泡右侧的上、下按钮翻阅当前运行会话中的回复段；文字和该段对应的
立绘会一起切换。生成或逐字显示期间按钮暂时不可用。当前版本不会在应用重启后恢复这组翻阅记录。

设置窗口的“交互 → 字幕与回复”可以调整“字幕逐字间隔”和“回复分段停顿”。保存后从下一条回复开始
生效；保存失败时原值保持不变。为了保持固定桌宠窗口、常驻气泡和常驻输入栏，气泡自动隐藏、自由布局
和快速接话等尚未迁移的控件在 Runtime v2 中会保持禁用。

如果发送按钮一直不可用，请先在“供应商”和“模型”页面完成聊天模型配置并保存，然后等待 Core 重连；
若单次回复失败，可以直接再次发送，应用不会自动重试或重复提交。供应商返回 HTTP 400、429 或 5xx
时，气泡会显示状态码和可安全公开的错误说明；API Key、请求头、地址及原始响应体不会显示。

---

### 配置语音（TTS）

TTS 为可选功能，不配置也可以正常使用，只是没有语音。

软件内提供了一键下载整合包的选项，根据你的设备选择：

| 方案 | 适合谁 |
|---|---|
| 50 系整合包 | RTX 50 系列显卡用户 |
| 通用整合包 | 其他 NVIDIA 显卡用户 |
| CPU 整合包 | 无独显或不支持 CUDA 的用户 |

RTX 50 系显卡必须使用 50 系专用整合包；通用整合包不支持该系列。设置页会优先把 50 系设备的默认路径指向专用包目录，未安装时请先下载对应整合包。

下载完成后在软件内直接启动 TTS 服务即可。

![配置 TTS](https://oss.cialloo.cn/img/setup_03.webp)

#### AMD 显卡 / 外置 GPT-SoVITS

AMD 显卡用户如需 GPU 推理，可在 B 站搜索 GPT-SoVITS 整合包自行安装，然后在软件中选择**外置 GPT-SoVITS** 模式，按下图填写服务地址。

macOS 用户的 GPT-SoVITS 配置方式另见 [MACOS_SETUP.md](MACOS_SETUP.md)。

![外置 GPT-SoVITS 配置](https://oss.cialloo.cn/img/setup_04.webp)

---

### 长期记忆模型

首次启动时，软件会在后台自动下载长期记忆所需的本地向量模型。下载过程中可以正常使用，无需等待。

如果遇到网络问题导致下载失败，可以从 [Releases 页面](https://github.com/Rvosy/sakura/releases) 手动下载 `models--sentence-transformers--all-MiniLM-L6-v2.zip`，然后在软件内导入。

![手动导入记忆模型](https://oss.cialloo.cn/img/setup_05.webp)

---

## 如何更新版本

Windows 的 `0.9.9-dev` 及后续构建包含 `update.bat`。先退出 Sakura，再双击这个脚本。更新器会校验下载文件，保留 `data/`、`characters/`、`runtime/` 和本地插件配置；如果依赖清单有变化，它会继续更新 Python 依赖。

如果当前安装包没有 `update.bat`，按下面的方法手动更新：

1. 关闭正在运行的 Sakura。
2. 备份原目录中的 `data/`；自制角色或插件也建议一并备份。
3. 从 [Releases 页面](https://github.com/Rvosy/sakura/releases) 下载最新完整包。当前稳定包是 `0.9.8`。
4. 把新包内容复制到原 Sakura 目录，遇到同名文件选择**覆盖/替换**。发布包不包含 `data/`，不会覆盖 API 配置、聊天记录、长期记忆和 TTS 数据。
5. 运行一次安装脚本（Windows 为 `install.bat`），再启动 Sakura。配置迁移会自动执行，并把迁移前文件备份到 `data/migration_backup/`。

不要删除原目录里的 `data/`。如果升级后怀疑存在旧版缓存，可先运行 `runtime/python.exe tools/cleanup.py` 预览；确认列表无误后再加 `--apply` 清理。

---

## 角色工作室（Windows / macOS）

Release 完整包会携带 Tauri 角色工作室二进制。在 Sakura 设置页的角色页面打开工作室后，可以新建角色、编辑人格卡和主题、导入立绘与参考音频，最后导出 `.char` 文件，无需自行安装 Rust 或单独编译 Tauri。

从 0.9.9 起，应用内的 Tauri 工作室是唯一受支持入口，不再提供 `start_studio.bat` 和旧 PySide6 独立工作室。Linux 当前没有正式角色工作室发布包，从源码运行时需要自行准备并编译 Tauri 运行环境。
