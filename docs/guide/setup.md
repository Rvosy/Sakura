# 安装与首次配置

从 [Releases](https://github.com/Rvosy/Sakura/releases) 下载对应系统和处理器的完整包。完整包自带 Python 运行环境；GitHub 的源码压缩包需要自行构建。

## Windows

安装版运行 Setup；免安装版解压 Portable ZIP 后运行 `sakura.exe`。免安装版更新时先退出 Sakura，再解压新版，保留原用户数据目录。

## macOS

Apple Silicon 使用 arm64 包，Intel Mac 使用 x86_64 包（以当次 Release 提供的文件为准）。应用与所带原生依赖应使用同一架构。

首次打开被系统阻止时，先尝试启动一次，再到“系统设置 → 隐私与安全性”确认应用名称并选择“仍要打开”。详见 [Apple 官方说明](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac)。发布页和应用 ZIP 中也提供离线打开说明。

截图需要屏幕录制权限。授权后若仍不可用，退出并重新启动 Sakura。HTTPS 证书错误应修复 Runtime 的 CA 配置。

## Linux

Linux x64 以 Ubuntu 24.04、glibc 2.39 和 WebKitGTK 4.1 为运行基线。发布包按 Release 中提供的 AppImage 或 deb 使用，也可以从源码运行。

源码构建所需系统依赖：

```bash
sudo apt-get install -y build-essential pkg-config clang libclang-dev libssl-dev \
  libasound2-dev libgbm-dev libpipewire-0.3-dev libwebkit2gtk-4.1-dev \
  libayatana-appindicator3-dev libxdo-dev librsvg2-dev
```

X11/XWayland 支持桌宠定位；纯 Wayland 的窗口位置和截图授权由桌面环境决定。Linux 点击穿透按角色可见区域的外接矩形处理，图片内部镂空可能仍接收鼠标。

## 首次启动

1. 在“角色与布局”导入 `.char` 角色包并选择当前角色。应用内的角色工坊可以新建或编辑角色。
2. 在“模型服务”添加连接，再在“模型”中选择对话模型；需要图片聊天时选择支持图片的模型。见[模型配置](models.md)。
3. 发送一条消息。需要截图时，再发送图片确认视觉能力。
4. 按需设置语音、语音输入或记忆，见[插件与资源](plugins.md)。

发行包不附带默认角色。设置保存失败时保留草稿；“已保存”与“已生效”分别按实际结果显示。模型服务应用需要等待当前对话结束时，可以先保存。

## 从源码运行

准备 Rust 和 Node.js，在仓库根目录执行安装与启动脚本。安装脚本在缺少 `runtime/` 时下载项目指定的 Python，启动脚本增量编译并启动开发版。

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

开发、验证和本地打包见[贡献指南](../../.github/CONTRIBUTING.md)。

## 更新与迁移

启动时的更新检查只提示版本，不自行下载或安装。支持应用内更新的安装版会在安装前验证更新签名，并保留用户数据。Portable 使用手动更新。

从旧版迁移、数据目录与备份见[数据管理](data.md)；插件更新与修复见[插件指南](plugins.md#更新与修复)。启动或安装失败时，按[故障排查](diagnostics.md)定位失败步骤。
