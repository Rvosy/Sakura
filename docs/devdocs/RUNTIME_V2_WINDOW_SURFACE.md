---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-08-26
---

# 桌宠窗口表面

桌宠表面由可见边界和命中区域组成。可见边界决定原生窗口大小与位置，命中区域决定哪些像素接收鼠标。两者不能用同一个外接矩形代替。

## 坐标模型

前端在 900×996 规范坐标系中测量气泡、输入栏和菜单，并提交 revision。Rust 读取可信角色 PNG 的 alpha，合成立绘与控件的 `active_bounds`，再按内容缩放和显示器比例换算成物理坐标。

锚点是立绘底部中心。窗口原点加窗口内锚点始终等于同一物理锚点，因此反复缩放或切换表情不会积累舍入误差。左上边界向下取整，右下边界向上取整；全局坐标必须保留符号以支持位于主屏左侧或上方的显示器。

第一次放置会把可见表面限制在工作区。用户拖动后，保存的锚点优先，不因表情、控件或缩放变化再次夹取。

## 提交顺序

前端每次几何提交都带 revision。Rust 只应用当前 revision；旧请求返回空结果，不能回写页面。

真实 bounds 变化前，Rust 先把新的舞台 offset、pointer offset 和 revision 提交给 WebView，再更新原生窗口。原生回调可能同步触发 moved/resize 事件，窗口事务持锁期间只能使用 `try_lock`，不能重入同一几何锁。

提交失败时保留上一份有效 snapshot。第一次提交失败时窗口继续隐藏。

## 点击、拖动与选择

前端把区域分成 `interactive`、`drag`、`neutral` 和 `transparent`。立绘固定是第一项 drag region，Rust 会用 alpha mask 二次判断；气泡空白可以拖动，回复文字、输入框、按钮和滚动条必须覆盖为 interactive。

DOM `pointer-events` 只能解决 WebView 内部路由，不能实现跨进程点击穿透。最终命中区域由平台后端应用。

表情交叉淡入期间使用新旧 mask 的并集，动画结束后收紧到新 mask。全透明立绘产生空 alpha 区域，仍可通过气泡、输入栏或菜单交互。

## 连续预览

立绘倍率和布局滑块使用显式 gesture guard。pointerdown 或方向键按下时开始，pointerup、pointercancel、失焦或按键抬起后结束。新手势在上一轮 drain 完成前到达时复用同一 guard，最后一轮结束后才收口。

缩放帧使用 latest-wins 单槽队列。前端可以先显示最新 transform，Rust 只处理当前需要的命中更新。最终完整提交才是可靠状态；轻量帧失败不能弹出保存错误。

## 平台后端

### Windows

Windows 用 `SetWindowRgn` 应用可见与输入区域，alpha 行段通过 `ExtCreateRegion` 生成。窗口常驻允许的最大立绘包络，静止态用精确 region 裁出实际轮廓。缩放预览开始时放宽一次 region，结束时恢复最终精确区域；中间帧不 resize 或 reposition HWND。

输入栏合成效果位于独立原生管线。窗口 region、视觉效果和 WebView transform 的 revision 必须一致。

### macOS

macOS 在 AppKit 主线程维护精确矩形快照，根据鼠标位置切换 `NSWindow.ignoresMouseEvents`。local/global event monitor 提供有界采样兜底，拖动期间锁定路由。

手势开始时窗口扩到“当前控件 + 最大倍率立绘”的包络，中间只更新 transform 和命中矩形，结束时通过一次 `setFrame:display:NO` 收紧。不要拆成异步 size/position 两步，也不要设置 `display:YES`。

### Linux

Linux 用 `cairo::Region` 和 GTK `input_shape_combine_region` 应用输入区域。物理像素写入 cairo 前要按 GTK scale 向外取整。

X11/XWayland 可以在手势首尾 `move_resize`；原生 Wayland 只请求 resize，窗口位置由 compositor 决定。该降级必须出现在诊断中，不能宣称绝对定位成功。

## 设置项

`controlPanelWidth`、`bubbleMaxHeight`、`controlPanelVerticalOffset` 和 `inputBarOffset` 走独立布局预览通道。`bubbleMaxHeight` 在运行时表示固定气泡高度；回复长度只改变内部滚动。

立绘图片保持在合成层，避免第一次缩放才提升图层。布局预览开始时关闭 CSS 过渡，结束或失败都要清除预览状态。

## 验证

下面使用 macOS/Linux 路径；Windows 使用 `.\runtime\python.exe`。

```bash
npm test --prefix desktop/frontend
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml -- --test-threads=1
./runtime/bin/python3 -m harness run runtime-v2-window-surface
```

Windows 还要运行 `runtime-v2-windows-interaction`。macOS/Linux 的模型测试不能替代真实桌面验证。实机记录至少包含候选 SHA、系统版本、桌面会话、显示器排列、缩放比例和 surface 诊断快照。
