---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-10-03
---

# 在 Linux 上使用 Sakura

1.3.1 正式 [Releases](https://github.com/Rvosy/Sakura/releases) 已提供 Linux x64 AppImage 和 DEB。普通使用优先下载完整包；只有开发或需要自行构建时，才准备冻结的 Python Runtime 和 Tauri 编译环境。最低环境是 glibc 2.39 以上的 x86_64，构建基线是 Ubuntu 24.04、GTK 3.24 与 WebKitGTK 4.1。

X11 与 XWayland 支持桌宠绝对定位。纯 Wayland 会话里，Sakura 会优先切到 X11 后端；若桌面环境不允许绝对定位，窗口仍可缩放和点击穿透，但最终位置由合成器决定。

## 安装正式包

- **AppImage**：下载文件后，在文件属性中允许作为程序执行，再打开。终端也可运行 `chmod +x Sakura-*.AppImage` 后执行对应文件。
- **DEB**：Ubuntu/Debian 用户可通过系统软件安装器打开，也可在下载目录执行 `sudo apt install ./Sakura-*-linux-x64.deb`，由包管理器处理依赖。

首次设置见[安装与首次配置](SETUP.md#第一次启动)。完整包包含 Python Runtime，不需要运行源码的安装脚本。

## 源码构建依赖

在 Ubuntu 24.04 上安装编译 Tauri Shell 所需的开发包：

```bash
sudo apt-get update
sudo apt-get install -y build-essential pkg-config clang libclang-dev libssl-dev \
  libasound2-dev libgbm-dev libpipewire-0.3-dev libwebkit2gtk-4.1-dev \
  libayatana-appindicator3-dev libxdo-dev librsvg2-dev
pkg-config --exists webkit2gtk-4.1 gtk+-3.0 && echo OK
```

确认 `pkg-config --exists webkit2gtk-4.1 gtk+-3.0` 成功。若编译仍提示找不到 `libclang`，再执行 `export LIBCLANG_PATH=/usr/lib/llvm-18/lib`。Fedora、Arch 等发行版需要自行安装对应的 GTK 3、WebKitGTK 4.1、ALSA、PipeWire 和 AppIndicator 开发包。Rust 使用仓库 `desktop/rust-toolchain.toml` 固定的 1.96.0，首次编译会由 rustup 安装。

## 从源码运行

仓库源码不包含 `runtime/`。在 Ubuntu/Debian x86_64 上可以直接执行：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

`scripts/install.sh` 会按 `desktop/src-tauri/runtime-layouts/linux-x64/runtime-manifest.json` 下载并解压冻结的 CPython 3.12.8，再安装 Core 与官方插件依赖。不要用系统 `/usr/bin/python3` 替代这份 Runtime。

`scripts/start.sh` 增量编译 debug Shell 并启动。release 构建只用于完整发行布局，不由该开发入口启动。

用户数据默认位于：

```text
${XDG_DATA_HOME:-$HOME/.local/share}/Sakura Development
```

正式发行布局使用 `.../Sakura`。日志在该目录下的 `data/logs/sakura-runtime.log`。

## 窗口、截图和外观

Linux 使用 GTK 输入区域实现点击穿透：只有立绘和可见控件接收鼠标，透明区域交给下方窗口。拖动、多屏和缩放行为见[窗口交互](RUNTIME_V2_WINDOW_INTERACTION.md)。

框选截图和主动屏幕感知依赖桌面的屏幕共享权限。部分 Wayland 合成器会在首次截图时弹出系统对话框；拒绝后日志中的 `Screen` 原因码可帮助排查。

“设置 → 外观”在 Linux 上提供纯色，以及 GTK/桌面环境能够提供的效果。Windows 液态玻璃和 macOS 原生材质在此平台会置灰。

## 语音

语音可以关闭。关闭后只显示字幕，不启动合成任务。先从插件市场安装 GPT-SoVITS 或 Genie 并启用，见[插件指南](RUNTIME_V2_PLUGINS.md)。Linux 可运行 `bash scripts/install_gpt_sovits_linux.sh` 安装本地 GPT-SoVITS（只创建名为 `sovits` 的 Python 3.10 conda 环境，其余用 pip；文件默认放在仓库根目录的 `envs/sovits` 与 `tts/`），也可以连接本机或局域网上已有的 GPT-SoVITS、Genie 服务：

1. 安装本地组件，或先启动已有语音服务；
2. 打开“设置 → 语音”；
3. 选择引擎；使用已有服务时填写地址；
4. 确认服务和角色语音资源就绪后，开启“开口说话”并应用。

GPT-SoVITS 本身不需要聊天用的 API Key。它通过 HTTP 调用本机或局域网里的合成服务（默认 `http://127.0.0.1:9880/tts`）。没有本地服务时，关掉“开口说话”即可，聊天不受影响。对话模型的连接信息仍需在“模型服务”页配置，见 [API 配置](API_CONFIG.md)。

本地引擎与 Sakura 可以使用不同的 Python 环境，只要通过 HTTP 通信。

## 常见问题

- `未找到 WebKitGTK 4.1 / GTK 3 开发文件`：先安装上一节的系统包，再重新运行 `scripts/install.sh`。
- `Unable to find libclang`：安装 `clang` 和 `libclang-dev`，或把 `LIBCLANG_PATH` 指到 `libclang.so` 所在目录后重新编译。
- `BOOTSTRAP_RUNTIME_UNSUPPORTED`：当前只支持 x86_64 GNU/Linux，不支持 ARM 或其他 libc。
- 桌宠启动但不显示：完成角色与模型服务设置，并在日志中检查 `CORE_CONFIG_SETUP_REQUIRED`。
- 窗口不能拖到指定位置：确认会话是 X11 或 XWayland；纯 Wayland 可能由合成器接管位置。
- 透明区域挡住点击：重启合成器或 Sakura，确认没有关闭窗口透明效果。Linux 上点击穿透使用立绘可见区域的外接矩形，发丝等镂空处仍可能挡住下方窗口。
- 选择夜乃桜后程序退出：Linux 在设置里改选角色时不会加载新立绘，点“应用”后才切换。需要包含该修复的新构建。若点应用后仍退出，请从终端启动并保留日志。
- 右键菜单时角色跳到右侧：菜单放大窗口时必须保持当前左上角；旧构建会把缓存坐标再次 `move_resize`。

诊断文件位于用户数据目录的 `data/logs/sakura-runtime.log`。公开日志前先删除本机路径、账号信息和其他隐私内容。
