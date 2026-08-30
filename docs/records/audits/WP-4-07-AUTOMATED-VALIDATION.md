---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-25
---

# WP-4-07 定时截图与主动请求自动验证记录

## 候选与证据边界

本记录保存 WP-4-07 当前工作树的自动验证结果。测试使用隔离 app root、fixture JPEG、模拟时钟和系统临时
资源，没有读取或保存真实用户屏幕内容。自动证据验证协议、资源清理、定时语义和 UI 契约；项目负责人
给出的最终产品结论另见 [`WP-4-07-OWNER-ACCEPTANCE.md`](WP-4-07-OWNER-ACCEPTANCE.md)。

## 最终结果

- `journey-screen-capture`：3/3 通过，报告
  `temp/harness/20260824T215543.584829Z-journey-screen-capture.json`。
  - Python：12 passed、1 skipped、20 deselected；覆盖设置兼容、批量附件、JPEG containment、单次消费、
    多图 Provider 输入和历史 marker。
  - Rust：7 passed；覆盖鼠标所在显示器选择、分辨率、不放大、有界内存批次、时间顺序和 generation 清理。
  - WebView：69 passed；覆盖定时器、忙时跳过、休眠不补跑、失败释放、设置热生效以及设置控件对齐。
- 设置保存响应修正后的 Python focused tests：4 passed、23 deselected；Core 的 get/save 返回同一 snapshot
  结构，不再附加前端不接受的额外字段。
- 未保存状态和布局 focused tests：50/50 通过；覆盖 Tauri JSON 字段重排后不误报 dirty，以及四个屏幕感知
  控件共享同一输入列。
- 实施期间完整 Rust 测试 331 passed、18 ignored，完整前端测试 246 passed；最终相关改动又由上述 focused
  tests 和 journey 复核。
- `git diff --check` 通过；仅输出工作树既有的 LF/CRLF 转换提示。

自动证据支持 WP-4-07 的实现和回归边界，但不补写未提供的设备型号、权限操作或三平台人工步骤。
