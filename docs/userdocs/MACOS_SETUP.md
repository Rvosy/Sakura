---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-26
---

# 在 macOS 上使用 Sakura

先在“关于本机”确认处理器架构：Apple Silicon 使用 `arm64`，Intel Mac 使用 `x86_64`。应用、bundled Python 和原生依赖必须使用同一架构；混用 Rosetta 与 arm64 文件通常会在导入原生模块时失败。

## 使用发布包

Releases 提供对应架构的完整包时，下载后解压并启动应用。首次运行若被 Gatekeeper 阻止，在“系统设置 → 隐私与安全性”中确认来源后选择打开。

完整包包含 Python Runtime 和已经构建的 Tauri Shell。源码压缩包不包含这些内容。

## 从源码运行

准备与机器架构一致的 `runtime/`，放到仓库根目录，然后运行：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

`scripts/start.sh` 会增量编译 debug 开发版，为二进制创建最小 `.app` 包装，再启动 Tauri Shell。这个包装让 macOS 按应用身份管理窗口和权限。release 构建只用于正式发行布局，不由该开发入口启动。

如果 bundled Python 访问 HTTPS 时提示证书错误，安装当前 Python 发行版附带的证书，或确认 Runtime 的 CA 配置。不要通过关闭 TLS 校验解决。

## 系统权限

截图和主动屏幕感知需要“屏幕与系统音频录制”权限。macOS 首次请求时会弹出系统对话框；授权后通常需要退出并重新启动 Sakura。

桌面 MCP 还可能需要“辅助功能”权限。只在确实需要桌面控制时开启，并确认 MCP Server 来源可信。

权限失效时：

1. 退出 Sakura；
2. 在“系统设置 → 隐私与安全性”中找到对应项目；
3. 重新授权后启动；
4. 仍失败时查看运行日志中的 `Screen` 或 MCP 原因码。

## 窗口和外观

macOS 使用 AppKit 管理透明桌宠窗口、点击穿透和原生输入栏材质。窗口只在立绘和可见控件上接收鼠标；透明区域会把点击交给下方应用。

“设置 → 外观”会列出当前系统支持的材质。Liquid Glass 需要系统提供对应的 `NSGlassEffectView`；不支持时选项会置灰。普通原生材质使用 `NSVisualEffectView`。

调整立绘大小时，窗口在手势期间使用稳定包络，松手后按最终可见区域收紧。多显示器和 Retina 缩放由原生坐标换算处理。若命中位置不对，记录每块屏幕的排列和缩放比例，参阅[窗口交互](RUNTIME_V2_WINDOW_INTERACTION.md)。

## 语音

语音可以关闭。关闭后 Sakura 只显示字幕，不启动合成任务。

macOS 可以连接外置 GPT-SoVITS 或 Genie 服务：

1. 在本机或其他主机启动语音服务；
2. 打开“设置 → 语音”；
3. 选择引擎并填写服务地址；
4. 点击“测试语音”，成功后保存。

Apple Silicon 上的本地语音服务应尽量使用 arm64 Python 和原生依赖。Sakura 自身与语音服务可以使用不同 Python 环境，只要通过 HTTP 接口通信。

## MCP、插件和角色工作室

macOS 可以在“设置 → 工具”开启桌面 MCP。保存后 Core 会重建，设置窗口会自动恢复连接。普通 MCP Server 和 Python 插件的使用方式与其他平台相同。

角色工作室由 `tools/studio-tauri/` 构建。发布包提供工作室时，可从“设置 → 角色与布局 → 修改角色”打开；源码环境需要单独构建：

```bash
cargo build --manifest-path tools/studio-tauri/src-tauri/Cargo.toml
```

## 常见问题

- `Bad CPU type` 或原生库架构错误：检查应用、Runtime 和依赖是否同为 arm64 或 x86_64。
- 桌宠启动但不显示：完成角色与供应商设置，并在日志中检查 `CORE_CONFIG_SETUP_REQUIRED`。
- 截图返回权限错误：重新授予屏幕录制权限并重启。
- 透明区域挡住点击：确认系统合成效果正常，重启后再测试。
- TTS 连接失败：用浏览器或命令行先验证服务地址，再检查防火墙和代理。

诊断文件位于 `data/logs/sakura-runtime.log`。公开日志前先删除本机路径、账号信息和其他隐私内容。
