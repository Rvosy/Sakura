# ADR-0001：Tauri 桌面技术门的受限验收

- 状态：已接受（带进入 Task 6 前置条件）
- 日期：2026-07-14
- 适用阶段：Sakura Tauri Assistant 第一阶段 Task 1

## 背景

Task 1 要求在迁移主界面前验证透明窗口、IME、多显示器、DPI、拖动、托盘、音频和截图。本次实施机器的 Windows 显示拓扑只有：

```text
DISPLAY1
2560 × 1440
96 DPI（100%）
单显示器
```

因此当前环境无法真实覆盖 125%、150%、200% 缩放以及混合 DPI 多显示器移动。Windows 自动化可以检查窗口渲染和控件调用，但其注入式 drag 操作没有观察到窗口原点变化，不能替代用户使用物理鼠标的拖动验收。

## 已验证事实

- Tauri debug crate 可以完成 `cargo fmt --check`、`cargo test` 和 `cargo build`。
- 主窗口为透明、无边框、无阴影、置顶、跳过任务栏的 WebView 窗口；透明区域可以正确显示后方桌面或应用，没有观察到黑色矩形背景。
- 输入框显示中文与日文文本，`compositionstart` / `compositionend` 路径实际更新了界面状态，Enter 在组合期间由前端守卫。
- Rust 音频原型成功打开默认输出设备并完成短提示音请求。
- Rust 截图原型成功捕获主显示器，返回 `2560 × 1440`、`14,745,600` 字节的 RGBA 数据，不向前端暴露任意文件路径。
- 鼠标穿透可以开启；第二次启动由 single-instance callback 聚焦现有窗口并恢复鼠标交互。
- 隐藏主窗口后再次启动只恢复同一实例，没有创建第二个可见窗口。
- Rust tray、single-instance、窗口、安全 capability 和截图/音频帮助逻辑具有自动测试。
- crate 未引入 `tauri-plugin-shell` 或 `tauri-plugin-fs`，capability 未开放 Shell 或任意文件系统权限。

## 未完成的物理验收

- 使用物理鼠标从状态条和立绘区域拖动窗口。
- 在 125%、150%、200% DPI 下检查尺寸、文本、点击区域和透明边缘。
- 在混合 DPI 多显示器之间移动并检查底边位置、尺寸和截图坐标。
- 从 Windows 托盘实际点击显示、隐藏、恢复交互和退出菜单。
- 由人耳确认提示音确实从预期输出设备播放，而不仅是播放 API 成功返回。

## 决策

1. Task 1 的代码和自动测试可以提交，Task 2–5 的 IPC、Brain Host、无 Qt 服务和 Host 监管基础设施可以继续实施。
2. 在上述物理验收完成前，不进入 Task 6 的完整桌宠主界面迁移，也不宣称 Task 1 exit gate 已完全通过。
3. 窗口拖动同时保留两条受控路径：前端 drag region 和固定 Rust `start_dragging` command；不得为解决拖动而开放通用窗口、Shell 或脚本能力。
4. Task 13 的干净 Windows x64 验收必须覆盖本 ADR 中所有未完成项；若出现 DPI 或多屏缺陷，回到 `desktop/src-tauri/src/windows.rs` 修正几何策略后再切换生产入口。

## 后果

- 当前分支可以继续完成与显示硬件无关的架构工作，避免把单机硬件条件变成 IPC/Brain Host 工作的阻塞项。
- Task 6 形成明确的人工门槛，不能把自动测试或单屏 100% DPI 结果外推为多屏兼容结论。
