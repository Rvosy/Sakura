---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
status_source: ../plans/runtime-v2/work-packages.md
updated: 2026-08-18
---

# ADR-0026：截图使用 generation 私有 token 与每显示器框选层

## 背景

Legacy Qt 能保留各屏幕 DPR 后裁剪，但 Runtime v2 的旧候选实现把临时图片绝对路径放进 IPC DTO，并用
一个跨越虚拟桌面的 WebView scale factor 将框选坐标换算为物理像素。前者泄漏宿主路径且不能证明
generation 授权，后者在混合 DPI 桌面上没有唯一正确比例。

## 决策

- Rust 为每块显示器创建本地覆盖层，覆盖层只提交本显示器内的逻辑矩形；Rust 使用该窗口当前 scale
  factor 和物理原点换算，禁止一次框选跨越显示器。
- Rust 捕获并压缩图像到系统临时目录下的 generation 私有根，使用随机 opaque token 注册。Python Core
  根据相同固定根、当前 generation 和 token 独立解析并执行 containment、大小、MIME 与 JPEG 结构校验；IPC 不
  传路径，WebView 不接触 token 或图像字节。
- Core 在 `screen.attach` 同步单次读取并删除资源，随后只在内存保存待发送观察；`chat.send` 以
  `attachmentId` 单次消费。这让 Rust 可在收到 attach 响应后无条件释放 registry，同时避免图片进入普通
  8 MiB JSON frame。
- 捕获库只是平台 backend，不拥有会话、Core 生命周期或资源授权；macOS 权限和 Linux portal 的拒绝/
  取消统一映射为稳定可恢复错误。

## 后果

混合 DPI 坐标不再依赖跨屏比例，裸路径不会跨边界，旧 generation 即使留下 token 也无法由新 Core 解析。
代价是单次框选不能横跨两块显示器；用户仍可在任意显示器分别截图。系统临时根是同一用户进程之间的受控
交换区而不是长期存储，必须由两端重复校验并在所有终态清理。
