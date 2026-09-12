---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-09-12
---

# Runtime v2 角色工坊

## 产品范围

新角色的确认按钮使用“添加到角色列表”，已在列表中的角色使用“保存”；未安装草稿仍可单独“保存草稿”。
添加成功提示为“角色已添加到列表”，不附加本地保存的解释。内部 publish 事务名称保持不变，不表示在线发布，
也不自动切换桌宠。已有角色放弃操作明确为“放弃修改”；删除新角色草稿仍提示不可恢复。
两者使用工坊内的确认框，取消后保留编辑内容。确认放弃时停止后续自动保存，等待在途草稿写入结束后再删除或恢复，避免旧修改回写。

角色工坊保留 0.9.10 已有的本地编辑能力：新建和编辑角色、草稿自动保存、角色卡、插件表现资源、主题颜色、
GPT-SoVITS 模型、参考语音试听、发布、放弃草稿和 `.char` 导出。本轮不增加角色删除、复制、在线发布或把
未发布草稿直接投影到桌宠。

工坊是 Sakura 主 Tauri 进程中的唯一 `studio` 窗口。Python Core 继续管理草稿、角色包和归档；Rust 只管理
窗口、文件选择、受控请求、临时试听资源、取色覆盖层和发布后的 Core 重建。不得恢复旧 Qt Studio、
`sakura-studio` 子进程或 JSONL stdout marker 协议。这个宿主决定见
[ADR-0044](../../adr/0044-character-studio-same-app-window.md)。

## 窗口与设置

- 设置页可以直接打开所选角色的工坊，无需先保存或应用设置。外观、语音、Memory 等未保存改动，以及
  尚未应用的角色选择都不阻断入口；打开工坊不会自动保存这些改动或切换当前角色。
- 工坊发布后，设置页接续新的 Core generation。同一角色只保留相对原基线实际修改的外观和语音字段，
  未修改字段使用新快照；待应用的角色选择（角色仍在目录中时）和记忆编辑草稿也保留。草稿仍由用户
  在设置页保存或放弃。真正切换角色时继续清理旧角色状态，不能把旧角色的草稿带给新角色。
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
studio.visual.catalog
studio.visual.open
studio.visual.create
studio.visual.import
studio.visual.export
```

请求与响应公共字段使用 camelCase，`visuals`、`visualData` 和插件 `data` 的内部键原样传递；未知公共字段返回 `STUDIO_REQUEST_INVALID`。公开 DTO 不含 `packageDir`、角色绝对
路径和音频 `data:` 内容。工作区用 `workspaceId` 标识；资源使用角色包内逻辑路径。Rust 可以接收 Core 私有的
试听源描述，但向 WebView 返回的只有 `previewUrl`、MIME 和字节数。

每个请求仍绑定当前 Core generation ID 和 credential。Core 重建会清空全部试听注册；旧 generation 的 URL
返回过期，不读取文件。

## 草稿与资源

草稿目录保持为 `user_root/data/character_studio/drafts`，备份目录保持为相邻的 `backups`。schema v1 的
0.9.10 草稿直接恢复，不改变 `character.json`、`card.md`、`portraits/` 和 `voice/` 的包内结构。旧版
`runtime/character-studio/workspace/characters` 中未发布的新角色也要迁移；使用原始角色 ID 命名的草稿目录
在启动时迁移到可移植目录名，重复启动不得生成第二份草稿。

恢复 1.1.0 及更早的内联立绘草稿时，封面、插件编辑器、组件导出和发布都以 `draft.json` 中尚未发布的默认图、
表情映射为准，不能使用草稿包 `character.json` 中的旧副本覆盖。公共表单自动保存为 `visuals` 引用后，
只要该引用仍指向内联立绘，就继续保留这些待发布修改；独立表现入口和已有 `visualData` 使用各自的数据。

表单修改先保存到草稿。切换角色、新建角色和关闭窗口前必须等待自动保存完成。角色 ID、工作区、包内资源
路径和导入源都要检查路径穿越；角色包或草稿资源不能经过符号链接。尾点角色 ID 继续通过可移植目录名保存，
保证 Windows 与旧草稿兼容。

每个角色复用一个草稿目录。自动保存只更新 `draft.json`，不复制整个包，也不创建历史备份；已导入且不再被
语音表单引用的导入资源按原有规则清理。表现资源引用由插件解释，普通保存不推测删除其私有文件。未发布修改必须跨重启保留；关闭窗口时可以释放 clean 工作区。

通用表现导入使用 `visual` 或 `visualFolder`，绑定 workspaceId 与 resourceId。立绘标签和说明文件由插件解释；
语音继续使用 `gptModel`、`sovitsModel`、`referenceAudio` 和 `referenceAudioFolder`。大文件复制和重复文件比较都要分块检查取消。文件夹导入在全部文件复制完成后一次
登记；任一文件失败或用户取消时，只删除本批新建的资源，不改动此前已有的同名文件。

Managed Genie 未显式配置的共享语音字段在运行时继承 GPT-SoVITS extension，再兼容旧 `voice`；Studio
不向 Genie 复制模型路径。这样源权重编辑可在下一次 Genie 预热或合成时生效，同时保留用户的 Genie 覆盖值。
独立语音包导入同步替换旧 `voice` 和 GPT-SoVITS 的共享资源字段，保留 Genie 覆盖值与未知字段。

Studio 只拥有表单明确编辑的 manifest 字段。`renderer`、`backchannel`、未知顶层或嵌套字段和其他插件
extension 必须原样保留，已废弃的 `sakura.tts` 运行选择除外。语音资源读取兼容 `voice` 与资源 extension，
保存时同步 `voice` 和 `sakura.tts.gpt-sovits`，不写入引擎选择或启用状态。

工坊不提供语音启用开关。模型、参考音频、文本、标签和语言始终可以编辑；允许先保存模型，稍后再补参考语音。
已有参考语音行必须完整，缺少合成必需资源时由实际语音请求报错。外部语音开关或引擎选择不影响资源展示、编辑、
保存和导出；关闭语音不删除资源配置或引用。启用状态和引擎选择只由应用语音设置管理，见
[语音合同](WP-4-05-tts-playback-audio-device-gate.md)与 [ADR-0048](../../adr/0048-voice-resources-and-local-selection.md)。

“语音模型”页独立列出当前编辑副本中可读取元数据的 `.ckpt`、`.pth` 和 `.onnx` 文件，显示文件名、大小及
角色包内相对路径。列表不依赖语音是否启用，也不要求文件已经登记到 Provider 配置；使用 Genie
或关闭语音时仍可查看。打开、草稿保存和发布响应通过只读 `modelFiles` 返回 `relativePath`、`byteLength`，
切换角色、导入模型和清理草稿资源后同步刷新。枚举只读取文件元数据，不读取模型内容、不跟随符号链接或
目录联接，也不把模型列表写入角色配置。下方编辑区为语音资源配置，查看和编辑文件不会切换 Provider。

## 表现编辑与组件

公共页面管理资源列表、默认项、提供者、添加、移除和保存。专属编辑区加载安装内的插件模块，立绘标签、默认图片、
PNG 导入和标签文件解释由内置立绘插件提供。私有草稿以 `visualData[resourceId]` 保存；资源引用格式及方法签名见
[表现插件合同](visual-plugin-boundary.md)。缺失入口可以由 editorData 的默认配置修复；插件停用、缺失或不兼容时，
公共资料仍可保存并保留原资源。编辑器进程 scope 改变后，模块授权及前端实例均撤销。

“角色形态”页以卡片显示包内形态，提供名称编辑和“添加形态”弹窗。当前编辑项与包默认项分开；设置页的
个人“显示方式”选择也不改包默认。默认项显示带勾选图标的状态样式，其他形态显示“设为默认”操作。立绘编辑器提供缩略图、大图预览、标签、默认图片、替换和移除。
卡片封面由插件的可选静态预览接口提供，打开列表就加载所有已有封面；未选中的形态无需挂载编辑器或渲染器。
立绘使用默认图，模型插件可提供包内截图，没有预览图时显示占位图标。切换编辑器保留同一封面的图片节点，
更换封面先完成新图解码，保存或切换期间不先清空旧图。
自动保存后仍可继续改名；暂时空白或重复的表情标签按完整行保存草稿，切换或重开后继续编辑，不能静默合并或删除。

显示新角色表单时先完整填充参考语音和主题，再打开表现编辑器，不能在半成品表单上自动保存。添加和导入资源期间
禁止切换工作区；回包必须核对工作区和编辑器修订。关闭或替换编辑器中止 signal，旧模块不能继续写新草稿。

完整 `.char` 保持旧 version 1 读取；通用角色包使用 version 2、kind character。单独形态组件使用 `.visual`，为 version 2、kind resource，
由插件投影入口和资产，宿主完成容器、路径、复制、取消和提交。旧版 `.char` 形态组件仍可导入，新导出统一使用 `.visual`。
组件导入生成新资源 ID，仅加入目标草稿并设为默认，
保留其人格、voice、角色 ID 与已安装包；可以放弃草稿撤销。详见表现插件合同的文件交换部分。

基础信息中的“所需插件”根据当前草稿显示形态与 TTS 资源所需的插件，保存草稿后刷新。
每项以紧凑行展示插件名称和状态，区分已启用、未启用、未安装和不可用；异常状态附必要的处理提示。
同一需求存在多个已启用插件时列出这些插件，不表示它们都必须安装或当前正在使用其中某个引擎。“已启用”不等于模型运行验证通过。
缺少插件时仍可保存资源；插件安装建议和实际引擎选择分开。包声明及 Genie 转换兼容规则见
[角色包插件需求](visual-plugin-boundary.md#角色包插件需求)。

## 发布、导出与取消

发布前校验草稿，并与已安装包逐文件比较实际内容。比较包括模型、立绘、未知资源和目录结构，大文件按块
检查取消，不单凭文件大小或修改时间判断相同。两者完全一致时只把草稿标记为 clean，不复制 staging、
不新增备份，也不停止当前 generation 或触发 Core 重建。首次保存旧包时，manifest 的规范化仍可能产生变化。

内容发生变化时，发布顺序固定为：

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
-> 清理该角色超出保留数量的历史备份
```

`CharacterRegistry` 不扫描事务根和旧版严格命名的 staging、rollback、recovery 目录。写 journal 前取消或
失败时直接删除事务目录。启动 `CharacterStudioService` 时如果发现 journal，优先从 rollback 恢复；原角色
已经移入 backups 时，先复制到事务内 recovery，完整校验后再原子替换正式目录。恢复过程再次中断时，下次
启动仍要从完整 backup 重试。新角色发布中断时删除未完成的正式目录。恢复会把草稿重新标记为 dirty。
删除 journal 是提交点；此后的 Core 重载失败不能回滚已经发布的文件。

每个角色保留最近两份完整包备份。只在保存成功后清理更早的备份，无变化的重复保存也会执行清理，便于
收敛旧版累积的历史。清理只处理 `backups` 直接子目录中符合工坊时间戳命名、且 manifest ID 与本次角色
一致的备份；其他角色、手动命名、清单无法读取或经过链接的目录不参与。新备份记录备份时间，同一秒内的
保存不按随机后缀决定先后。journal 存在时禁止清理；清理失败保留保存结果，记录日志并提示下次保存重试。

以每版约 300 MB 的角色为例，两份历史备份约占 600 MB，编辑中的草稿另占约 300 MB，发布暂存目录会短暂
增加约 300 MB。真实修改仍使用完整包事务复制，空间按保留版本的实际大小计算，不随成功保存次数持续增长。

导入、发布和含语音导出使用单操作 ID。`studio.operation.cancel` 在复制或校验阶段设置取消标记；进入目录
切换、资源登记或导出文件替换阶段后返回 `finalizing`，界面显示“正在完成保存”。最后一次取消检查与进入
提交阶段必须在同一把 operation lock 中完成。导出先写同目录临时 `.char`，ZIP 中的大文件按块压缩；提交前
收到取消请求时删除临时文件。

## 运行态与临时资源

发布非当前角色或内容未变化时返回 `changePlan: unchanged`，不能切换桌宠。发布当前角色且内容有变化时返回
`changePlan: core_restart_required`。替换目录前，Core 先取消并关闭旧 generation 的聊天、TTS 和插件读取者；
Rust 随后只发起一次现有 Core restart，并关闭旧音频状态。停止这些读取者后发布仍失败时，错误响应带
`generationInvalidated: true`，Rust 仍要重建已经失效的 generation。保存已经成功而 restart 请求失败时返回
`runtimeReload: failed`。restart 已接受但新 generation 未在时限内就绪时，通过
`sakura://studio-runtime-reload` 显示“角色已保存，运行态重载失败”。

发布非当前角色后立即发送 `sakura://character-catalog-changed`，设置页重新读取角色列表。发布当前角色时，
事件必须等新 generation 就绪后发送，并携带新 generation ID；设置页先重绑定运行态控制器，再刷新角色列表。
旧 generation 的迟到事件不能覆盖新状态或显示 `Router closed`。工坊关闭本身不触发目录刷新。
工坊保存前冻结旧表现编辑器并保留静态内容；当前角色需要重启时，先等待 `sakura://studio-runtime-reload` 的结果，
再刷新表现插件目录，待新编辑器就绪后替换。保存回包之前到达的就绪事件也有效。失败时保留已保存的数据并提示重启。
图片与模型文件在打开形态时一次授权资源根，随后由原生协议直接读取，不经 Core 逐文件请求或草稿写锁。
重复点击当前形态保留编辑器；临时插件状态查询失败不能销毁编辑区。

`.char` 角色包和 `.voice` 独立语音包导入允许单个文件最大 8 GiB、解压后总量最大 32 GiB，
大小按 ZIP 中的未压缩字节数计算，上限值本身允许导入。仍限制 ZIP 成员不超过 4096 个，
大于 1 MiB 的文件压缩比不超过 200，并检查目标磁盘空间、路径穿越和符号链接。
这些大小限制仅适用于角色与语音归档，不改变其他归档的默认限制。

`.char` 导出以原角色 manifest 为基线，只改写已知字段和资源路径。完整包必须携带 legacy `voice` 以及内建
GPT-SoVITS、Genie extension 引用的模型、参考表和参考音频；导入后继续保留 `renderer`、`backchannel`、
`extensions` 和未知字段。

参考语音最大 20 MiB。Rust 为已验证的工作区音频注册五分钟有效的不透明 URL；URL 绑定 generation，响应
禁用缓存。音频字节不经过 8 MiB Core 帧。屏幕取色由 Rust 为每台显示器创建透明覆盖层，在点击后由原生
屏幕捕获后端读取一个像素并返回 `#RRGGBB`；截图不进入 Core，也不写入用户数据。

## 验证

自动入口是 `python -m harness run journey-character-studio`，覆盖 Core schema、旧草稿恢复、资源导入、试听
描述、发布与二次恢复、整批取消清理、归档字段和资源 round-trip、Rust 临时资源和取色会话，以及前端 DTO、
同角色文字历史；过期表现控制只保留为数据，不重新执行。
`journey-visuals-browser` 另以正式工坊和插件模块验证形态卡片、名称、添加、默认项、草稿恢复与窄窗口布局。
其中保存回归使用正式桌面 CSP 和真实有界 Core 路由，覆盖多图预览及重启期间的编辑器挂载顺序。

发布前还要通过 `journey-character-switch`、`runtime-v2-shell` 和 `release-distribution`。Windows x64 与 macOS
arm64 必须实机检查当前/非当前角色发布、跨重启草稿、带语音导出、取消大文件、多显示器取色和安装包入口。
Linux 只要求编译通过。没有实机记录时，本能力最多标记为 `implemented` 或 `stabilizing`。
