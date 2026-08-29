---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-29
---

# WP-4-06 手动截图、受控图像资源与平台权限规范

## 产品行为

- Runtime v2 输入栏左侧显示 `+`。点击后加号旋转 45°，附件菜单从加号右侧以工具栏内浮层展开；它不
  参与输入栏高度测量，也不得移动气泡或改变原生命中矩形。当前唯一可用项为“截图”。菜单支持鼠标、
  Enter/Space、Escape 和外部点击关闭，不能破坏草稿、输入焦点、IME、发送/取消或桌宠拖动语义；
  `prefers-reduced-motion` 下立即切换最终状态。
- 点击“截图”后进入框选模式。Windows、macOS、Linux 的每块显示器各有一个覆盖本显示器的框选层；一次
  选择只属于一块显示器，避免用单一跨屏 WebView 的 scale factor 换算混合 DPI 坐标。拖动不足 8 个逻辑
  像素、右键或 Escape 均为取消，不产生附件。
- 框选成功后把截图追加到当前手动附件组，单条消息最多 6 张；每次框选后返回输入栏，用户可再次从
  `+` 菜单继续截图。第 7 张明确拒绝且不改变已有附件。截图可随文本发送，也可独立发送；草稿为空且
  存在截图时，WebView 使用“请根据我框选的截图继续对话。”作为用户消息。发送接受后整体清除；发送
  拒绝时保留以便重试。截图不能绕过现有单活动聊天与 generation 门禁。
- 只要存在手动截图，输入栏就复用既有展开态：上行是文本，下行依次为 `+`、可横向滚动的截图条和
  发送按钮。截图条只显示顺序、尺寸和逐项删除按钮，不向 WebView 投影像素；删除最后一张后，若文本
  本身不需要展开，输入栏恢复单行。数量达到 6 时只禁用截图入口，不禁用菜单和删除。
- 捕获、权限拒绝、portal 取消、显示器消失和编码失败均为可恢复错误；聊天和 Core 生命周期保持可用，
  UI 显示稳定中文错误，不投影路径、平台异常原文或图像内容。

## 所有权与资源生命周期

- WebView 只拥有菜单状态、组级 `attachmentId`、逐项 `itemId`、尺寸和顺序；不得接收文件路径、resource
  token、base64 或图像字节。Rust 拥有显示器枚举、覆盖层、实际捕获、压缩、临时文件和 resource token
  注册表。
- 每个捕获资源写入系统临时目录下当前 Core generation 的私有目录。跨 Rust/Python IPC 只传
  `generationId + resourceToken + mimeType + width + height + byteLength + capturedAt + screenName`；路径由
  两端在同一固定根下独立解析，不能进入 DTO、事件、日志或 WebView。
- token 使用不可预测随机值，只允许当前 generation 单次消费，TTL 为 120 秒。Core 必须重新校验 token
  形状、generation、路径 containment、常规文件、大小、MIME、JPEG 结构/SOF 尺寸和图片上限，读取后立即删除。
  Rust 在成功、拒绝、取消、过期、generation 变化和应用退出时均清理剩余文件。
- Core 只在内存中保留一个待发送手动附件组；组内含一至六个有序 `ScreenObservation`。`screen.attach`
  追加一项并保持组级 `attachmentId` 稳定，同时返回该项的 `itemId`；`screen.remove` 删除指定项，最后
  一项删除后销毁整个组。`screen.release` 和接受的 `chat.send` 整体单次消费它。旧 generation、重复 ID
  和伪造 ID 必须拒绝或返回未接受，不能读取资源。
- 聊天历史只保存手动截图 marker 和可追问视觉记录，不保存原图、base64、resource token 或
  `attachmentId`。Pipeline 使用现有多模态消息和视觉摘要链。

## 接口与平台门

- Core capability 为 `assistant.screen-capture-v2`，allowlist 增加 `screen.attach`、`screen.remove`、
  `screen.release` 和带可选组级 `attachmentId` 的 `chat.send`。`screen.attach` 返回
  `{ attached, attachmentId, itemId, width, height, count }`；`screen.remove` 接受
  `{ attachmentId, itemId }` 并返回 `{ accepted, attachmentId, itemId, count }`。`itemId` 为不可预测的
  `shot-<32 hex>`。Rust commands 只允许 `main` 或当前捕获覆盖层调用；覆盖层会话 ID、窗口 label、
  显示器 ID 与 generation 必须一致。
- Rust 记录 `screen.capture.started/attached/cancelled/failed`，只包含 generation 关联、显示器数量、尺寸、
  字节数、耗时、结果和稳定错误码，不记录图像、路径、token、用户消息或平台异常原文。
- 自动验证覆盖资源 escape/symlink、过期、重复读、旧 generation、错误窗口、尺寸/MIME/解码不一致、
  多显示器负坐标、DPI 换算、取消、六张上限、逐项删除和聊天单次消费。真实应用门覆盖 Windows
  100%/150% 混合 DPI、macOS
  Retina 与屏幕录制权限拒绝/恢复、Linux X11 以及 Wayland portal 选择/取消。
- 本 WP 不开放自动观察、主动互动、提醒或调度；它们属于 WP-4-07。平台实机门未完成前 CAP-015 不得
  标记 `parity-accepted`。
