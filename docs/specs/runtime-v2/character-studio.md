---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-09-04
---

# Runtime v2 角色工坊

## 产品范围

角色工坊保留 0.9.10 已有的本地编辑能力：新建和编辑角色、草稿自动保存、角色卡、立绘与表情、主题颜色、
GPT-SoVITS 模型、参考语音试听、发布、放弃草稿和 `.char` 导出。本轮不增加角色删除、复制、在线发布或把
未发布草稿直接投影到桌宠。

工坊是 Sakura 主 Tauri 进程中的唯一 `studio` 窗口。Python Core 继续管理草稿、角色包和归档；Rust 只管理
窗口、文件选择、受控请求、临时试听资源、取色覆盖层和发布后的 Core 重建。不得恢复旧 Qt Studio、
`sakura-studio` 子进程或 JSONL stdout marker 协议。这个宿主决定见
[ADR-0044](../../adr/0044-character-studio-same-app-window.md)。

## 窗口与设置

- 设置页只能在角色选择已提交，且外观、语音、Memory 没有角色级草稿时打开工坊。Provider、Tools、Plugin
  等全局设置草稿不阻断。
- `open_character_studio({ characterId })` 创建或聚焦唯一 `studio` 窗口。窗口初始化完成前保持隐藏。
- 打开后隐藏设置窗口，并把桌宠实际置顶设为关闭，但不改写用户的置顶偏好。工坊销毁后恢复设置窗口、
  桌宠置顶和角色列表。
- 原生关闭事件只发出 `sakura://studio-close-requested`。前端完成待处理的草稿保存后，才调用关闭命令；
  保存失败时窗口保持打开。
- 应用退出同样先经过工坊保存，再进入设置窗口原有的退出确认。工坊关闭不单独结束主程序。

## Core Host 协议

协议版本是 schema v1。`studio_request` 只接受 `studio` 窗口调用，并只转发以下方法：

```text
studio.bootstrap
studio.character.open
studio.character.create
studio.character.publish
studio.draft.save
studio.draft.discard
studio.workspace.release
studio.asset.import
studio.reference.preview
studio.archive.export
studio.operation.cancel
```

请求与响应字段使用 camelCase，未知字段返回 `STUDIO_REQUEST_INVALID`。公开 DTO 不含 `packageDir`、角色绝对
路径和音频 `data:` 内容。工作区用 `workspaceId` 标识；资源使用角色包内逻辑路径。Rust 可以接收 Core 私有的
试听源描述，但向 WebView 返回的只有 `previewUrl`、MIME 和字节数。

每个请求仍绑定当前 Core generation ID 和 credential。Core 重建会清空全部试听注册；旧 generation 的 URL
返回过期，不读取文件。

## 草稿与资源

草稿目录保持为 `user_root/data/character_studio/drafts`，备份目录保持为相邻的 `backups`。schema v1 的
0.9.10 草稿直接恢复，不改变 `character.json`、`card.md`、`portraits/` 和 `voice/` 的包内结构。

表单修改先保存到草稿。切换角色、新建角色和关闭窗口前必须等待自动保存完成。角色 ID、工作区、包内资源
路径和导入源都要检查路径穿越；角色包或草稿资源不能经过符号链接。尾点角色 ID 继续通过可移植目录名保存，
保证 Windows 与旧草稿兼容。

导入类型固定为 `portrait`、`portraitFolder`、`gptModel`、`sovitsModel`、`referenceAudio` 和
`referenceAudioFolder`。大文件使用分块复制和同目录 `.partial` 文件，取消后删除临时文件。

## 发布、导出与取消

发布顺序固定为：

```text
完整校验
-> characters/ 同目录 staging
-> 写 publish-journal.json
-> 正式目录改名为 rollback
-> staging 改名为正式目录
-> rollback 移入 backups
-> 删除 journal
```

启动 `CharacterStudioService` 时如果发现 journal，恢复旧角色并清理 staging。新角色发布中断时删除未完成的
正式目录。已经成功删除 journal 的发布不因后续 Core 重载失败而回滚。

导入、发布和含语音导出使用单操作 ID。`studio.operation.cancel` 在复制或校验阶段设置取消标记；进入目录
切换或导出文件替换阶段后返回 `finalizing`，界面显示“正在完成保存”。导出先写同目录临时 `.char`，提交前
收到取消请求时删除临时文件。

## 运行态与临时资源

发布非当前角色返回 `changePlan: unchanged`，不能切换桌宠。发布当前角色返回
`changePlan: core_restart_required`；Rust 只发起一次现有 Core restart，并关闭旧音频状态。保存已经成功而
restart 请求失败时返回 `runtimeReload: failed`。restart 已接受但新 generation 未在时限内就绪时，通过
`sakura://studio-runtime-reload` 显示“角色已保存，运行态重载失败”。

每次发布发送 `sakura://character-catalog-changed`，设置页重新读取角色列表。当前角色生效仍以现有 generation
变更为准。

参考语音最大 20 MiB。Rust 为已验证的工作区音频注册五分钟有效的不透明 URL；URL 绑定 generation，响应
禁用缓存。音频字节不经过 8 MiB Core 帧。屏幕取色由 Rust 为每台显示器创建透明覆盖层，在点击后由原生
屏幕捕获后端读取一个像素并返回 `#RRGGBB`；截图不进入 Core，也不写入用户数据。

## 验证

自动入口是 `python -m harness run journey-character-studio`，覆盖 Core schema、旧草稿恢复、资源导入、试听
描述、发布恢复、导出、取消清理、Rust 临时资源和取色会话，以及前端 DTO/角色模型。

发布前还要通过 `journey-character-switch`、`runtime-v2-shell` 和 `release-distribution`。Windows x64 与 macOS
arm64 必须实机检查当前/非当前角色发布、跨重启草稿、带语音导出、取消大文件、多显示器取色和安装包入口。
Linux 只要求编译通过。没有实机记录时，本能力最多标记为 `implemented` 或 `stabilizing`。
