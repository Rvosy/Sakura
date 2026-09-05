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
- 工坊外壳使用当前已生效的 Runtime 外观主题，与设置页保持一致。角色包配色只在“配色”页编辑和保存，
  不改变工坊窗口本身的颜色。
- 打开后隐藏设置窗口，并把桌宠实际置顶设为关闭，但不改写用户的置顶偏好。工坊销毁后恢复设置窗口和
  桌宠置顶；角色列表由发布事件刷新。
- 原生关闭事件只发出 `sakura://studio-close-requested`。前端完成待处理的草稿保存后，才调用关闭命令；
  有活动操作时先请求取消，再等待保存。保存失败时窗口保持打开；保存成功后的 clean workspace 只尽力释放，
  Core 重建期间释放失败不阻断关窗。
- 应用退出同样先经过工坊保存，再进入设置窗口原有的退出确认。工坊五秒内没有响应时，原生侧销毁窗口并
  继续退出，避免 WebView 或长操作让整个应用卡住。工坊正常关闭不单独结束主程序。

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
0.9.10 草稿直接恢复，不改变 `character.json`、`card.md`、`portraits/` 和 `voice/` 的包内结构。旧版
`runtime/character-studio/workspace/characters` 中未发布的新角色也要迁移；使用原始角色 ID 命名的草稿目录
在启动时迁移到可移植目录名，重复启动不得生成第二份草稿。

表单修改先保存到草稿。切换角色、新建角色和关闭窗口前必须等待自动保存完成。角色 ID、工作区、包内资源
路径和导入源都要检查路径穿越；角色包或草稿资源不能经过符号链接。尾点角色 ID 继续通过可移植目录名保存，
保证 Windows 与旧草稿兼容。

导入类型固定为 `portrait`、`portraitFolder`、`gptModel`、`sovitsModel`、`referenceAudio` 和
`referenceAudioFolder`。大文件复制和重复文件比较都要分块检查取消。文件夹导入在全部文件复制完成后一次
登记；任一文件失败或用户取消时，只删除本批新建的资源，不改动此前已有的同名文件。

Managed Genie 未显式配置的共享语音字段在运行时继承 GPT-SoVITS extension，再兼容旧 `voice`；Studio
不向 Genie 复制模型路径。这样源权重编辑可在下一次 Genie 预热或合成时生效，同时保留用户的 Genie 覆盖值。
独立语音包导入同步替换旧 `voice` 和 GPT-SoVITS 的共享资源字段，保留当前引擎选择、Genie 覆盖值与未知字段。

Studio 只拥有表单明确编辑的 manifest 字段。`renderer`、`backchannel`、未知顶层或嵌套字段和其他插件
extension 必须原样保留。GPT-SoVITS 打开时兼容 legacy `voice` 与 Runtime v2 extension，保存时同步
`voice`、`sakura.tts` 和 `sakura.tts.gpt-sovits`。Genie 等非 Studio 管理的语音 Provider 不能因为普通
主题保存而被切换或禁用。

## 发布、导出与取消

发布顺序固定为：

```text
完整校验
-> characters/.studio-transactions/<uuid>/staging
-> 写 publish-journal.json
-> 当前角色发布时停止旧 generation 的聊天、TTS 和插件读取
-> 正式目录改名为 rollback
-> staging 改名为正式目录
-> rollback 移入 backups
-> 校验正式角色、写入 clean 草稿状态并构造返回数据
-> 删除 journal
```

`CharacterRegistry` 不扫描事务根和旧版严格命名的 staging、rollback、recovery 目录。写 journal 前取消或
失败时直接删除事务目录。启动 `CharacterStudioService` 时如果发现 journal，优先从 rollback 恢复；原角色
已经移入 backups 时，先复制到事务内 recovery，完整校验后再原子替换正式目录。恢复过程再次中断时，下次
启动仍要从完整 backup 重试。新角色发布中断时删除未完成的正式目录。恢复会把草稿重新标记为 dirty。
删除 journal 是提交点；此后的 Core 重载失败不能回滚已经发布的文件。

导入、发布和含语音导出使用单操作 ID。`studio.operation.cancel` 在复制或校验阶段设置取消标记；进入目录
切换、资源登记或导出文件替换阶段后返回 `finalizing`，界面显示“正在完成保存”。最后一次取消检查与进入
提交阶段必须在同一把 operation lock 中完成。导出先写同目录临时 `.char`，ZIP 中的大文件按块压缩；提交前
收到取消请求时删除临时文件。

## 运行态与临时资源

发布非当前角色返回 `changePlan: unchanged`，不能切换桌宠。发布当前角色返回
`changePlan: core_restart_required`。替换目录前，Core 先取消并关闭旧 generation 的聊天、TTS 和插件读取者；
Rust 随后只发起一次现有 Core restart，并关闭旧音频状态。停止这些读取者后发布仍失败时，错误响应带
`generationInvalidated: true`，Rust 仍要重建已经失效的 generation。保存已经成功而 restart 请求失败时返回
`runtimeReload: failed`。restart 已接受但新 generation 未在时限内就绪时，通过
`sakura://studio-runtime-reload` 显示“角色已保存，运行态重载失败”。

发布非当前角色后立即发送 `sakura://character-catalog-changed`，设置页重新读取角色列表。发布当前角色时，
事件必须等新 generation 就绪后发送，并携带新 generation ID；设置页先重绑定运行态控制器，再刷新角色列表。
旧 generation 的迟到事件不能覆盖新状态或显示 `Router closed`。工坊关闭本身不触发目录刷新。

`.char` 导出以原角色 manifest 为基线，只改写已知字段和资源路径。完整包必须携带 legacy `voice` 以及内建
GPT-SoVITS、Genie extension 引用的模型、参考表和参考音频；导入后继续保留 `renderer`、`backchannel`、
`extensions` 和未知字段。

参考语音最大 20 MiB。Rust 为已验证的工作区音频注册五分钟有效的不透明 URL；URL 绑定 generation，响应
禁用缓存。音频字节不经过 8 MiB Core 帧。屏幕取色由 Rust 为每台显示器创建透明覆盖层，在点击后由原生
屏幕捕获后端读取一个像素并返回 `#RRGGBB`；截图不进入 Core，也不写入用户数据。

## 验证

自动入口是 `python -m harness run journey-character-studio`，覆盖 Core schema、旧草稿恢复、资源导入、试听
描述、发布与二次恢复、整批取消清理、归档字段和资源 round-trip、Rust 临时资源和取色会话，以及前端 DTO、
同角色历史和立绘归一化。

发布前还要通过 `journey-character-switch`、`runtime-v2-shell` 和 `release-distribution`。Windows x64 与 macOS
arm64 必须实机检查当前/非当前角色发布、跨重启草稿、带语音导出、取消大文件、多显示器取色和安装包入口。
Linux 只要求编译通过。没有实机记录时，本能力最多标记为 `implemented` 或 `stabilizing`。
