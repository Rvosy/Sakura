---
kind: userdoc
status: current
audience: user
source_of_truth: ../specs/runtime-v2/release-distribution-and-storage.md
updated: 2026-08-26
---

# 数据与存储

Windows 安装版和 Portable 把配置、角色、聊天数据和 TTS 保存在 Sakura 目录中。macOS 将这些内容保存在：

```text
~/Library/Application Support/Sakura
```

可以在“设置 → 数据与存储”查看并打开数据目录。macOS 更新会替换 `.app`，不会删除 Application Support
中的内容。

TTS 默认位于数据目录下的 `tts`。可以在设置中选择一个已经存在且可写的外置硬盘目录。更改位置不会自动
移动旧文件；外置盘断开时 Sakura 不会改用默认目录，文字聊天和设置仍可使用，TTS 会明确显示存储不可用。
