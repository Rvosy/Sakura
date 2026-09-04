---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-09-05
---

# WP-5-03 安全角色切换、Session 与历史分页

## 1. 产品边界

第一版只在设置页开放角色选择。下拉选择只更新设置窗口内的临时草稿，用户可以在提交前反复选择；只有点击
“应用”或“保存并关闭”才保存最终目标并触发切换，放弃设置则恢复已提交角色。角色 ID 变化不是同一
Assistant Session 的热配置：它必须保存目标角色，受控停止旧 Core generation，等待旧进程树和
generation 私有资源完成清理，再启动完整的新 generation。
切换会立即终止旧角色正在进行的回复、TTS、Memory 整理、插件任务和异步回调；不提供桌宠右键或托盘入口。

同角色选择是无写入、无重启的 `unchanged`。导入角色包只有在首次导入并自动成为当前角色时要求重启；
导入非当前角色不重启。

设置页可以给当前已提交角色导入 `.voice`，也可以导出完整角色包、单角色包或语音包。给当前角色导入语音后必须
受控重启 Core，使新的模型和参考语音进入下一 generation；给非当前角色导入时只更新角色包。导出只读取
已保存的角色数据，不改变当前角色，也不触发重启。完整角色包和语音包要求 GPT 与 SoVITS 模型文件都存在；
模型不完整时仍可导出不含语音的单角色包。

## 2. 配置提交与 restart 协议

Python `characters.settings.select/import/import_voice` 在校验归档或目标角色后保存数据，返回固定 envelope：

```json
{
  "schemaVersion": 1,
  "snapshot": {},
  "changePlan": "unchanged | core_restart_required"
}
```

Rust 必须在同一设置窗口和当前 Core identity 下校验完整响应。`core_restart_required` 只派发一次受控
restart，并向设置页返回已提交目标、前一 generation 和 `restartState=requested`；`unchanged` 返回
`restartState=not_required`。配置保存失败不得 restart；restart 派发失败返回
`CHARACTER_RESTART_REQUEST_FAILED`。配置可能已经提交，因此失败后禁止第二次写入、自动回滚、自动重试或回退
旧角色，用户只能按明确错误人工恢复。

`characters.settings.export` 接收角色 ID、导出类型和 Rust 文件对话框选出的绝对路径，成功时只返回
`schemaVersion`、`outputPath` 和用户提示。Python Core 负责校验角色及语音模型，并通过临时文件替换目标归档；
Rust 和 WebView 不直接读取角色目录。

设置页的角色下拉不得直接调用 `characters.settings.select`。它可以通过独立的只读视觉预览命令加载目标角色
已保存的主题、默认立绘和初始问候语，但不得改变 active character、Core generation、Chat reducer、Memory/Timeline、TTS
或插件 identity。统一保存流程先提交当前 generation 的其他设置，
最后只对最终角色草稿调用一次 `select`；否则 restart 会使同一批保存请求使用的旧 settings transport 失效。
选择又回到已提交角色时清除角色 dirty 状态，不写配置、不读取 lifecycle，也不重启。暂存目标期间仍只展示和
  编辑当前正式角色的数据，不预加载目标角色的外观、语音、Memory 或历史。为了避免下拉已显示目标角色时造成
  所属角色误判，暂存期间锁定外观、语音和 Memory 页面，只保留角色下拉以及全局设置可编辑。

角色切换完成必须同时满足：

1. Supervisor generation number 严格增加，generation ID 与前一 generation 不同；
2. Core Snapshot 的 generation ID 与 Supervisor 一致；
3. Snapshot readiness 为 `ready` 或 `degraded`；
4. Character Presentation 的 generation ID 一致且 `characterId` 等于已提交目标。

任一条件缺失都不能显示新角色历史或宣告切换成功。目标 generation 到达 `failed` 或
`setup_required` 时直接报告初始化失败；不自动恢复。

## 3. generation 冻结与数据隔离

generation 创建时冻结 `active_character_id`。角色、Assistant Session、Plugin Runtime、Memory、TTS、
Timeline Host Service 和所有 generation 私有资源由该冻结值构建；旧 generation 在停机窗口内即使看到配置
文件已经改变，也只能访问旧角色。

| 领域 | 强制隔离行为 |
|---|---|
| Memory/Mem0 | 查询、写入、core profile、整理游标和 Curator 使用冻结角色；新角色 Memory 不可用时返回空的 degraded 结果，不读取旧角色或默认角色 |
| Timeline/历史 | 所有请求携带 generation 与角色 ID；角色变化立即清空窗口内容和分页游标；旧游标和迟到分页结果失效 |
| TTS/音频 | 合成、播放、参考资源和回调由 generation identity 约束；restart 先取消旧 generation 工作 |
| 截图/插件 artifact | opaque 资源只在创建它的 generation 内有效；restart 时失效并按既有 cleanup 顺序回收 |
| 设置 transport/异步事件 | 旧 transport 在 stop 开始时移除；旧 generation response/event 不得重新水合新角色页面 |

旧 generation cleanup blocked 时 Supervisor 不得启动新角色。普通同角色 Core restart 继续使用既有
generation 隔离，但不视为角色变化。

## 4. 设置页与桌宠表现

- 当前角色有未保存的外观、语音或 Memory 新建/编辑草稿时，选择另一角色必须恢复到前一个下拉值并提示先
  保存或放弃。下拉草稿本身可反复改选；选回已提交角色即取消待切换状态。
- 待应用角色只改变下拉显示、dirty/提示状态，以及桌宠的目标主题、默认立绘和气泡问候语预览；不得改变名字、
  输入提示、Chat reducer、回复历史、语音、Memory 或 Timeline。预览资源使用单独的只读 resource slot，不能替换 generation 的 active
  Character Presentation。放弃或选回已提交角色时恢复正式角色主题、当前回复对应立绘和精确点击区域。
  气泡同时恢复 reducer 当前应显示的内容；预览期间到达的回复只更新 reducer 状态，不覆盖预览问候语。
- 只有后端返回已提交的 restart receipt 后才进入 switching。Memory 草稿检测必须同时覆盖原生编辑器与插件
  collection editor；切换清理会关闭 editor portal、失效在途 collection 查询并清空其分页状态。
- 收到已提交的 restart receipt 后进入 switching，立即清空并隐藏旧角色 Memory 列表、编辑器、插件
  collection 页面状态和历史内容；切换期间禁用角色导入/选择及角色相关操作，不整页 reload。
- 当前角色有未保存的外观、语音或 Memory 改动时禁止导入语音，避免受控重启丢失草稿。导出仍可读取已保存
  的角色包；有待应用的角色切换时，导入语音和导出都保持禁用。
- Provider、Tools、Plugin 和 Screen Awareness 等全局设置草稿保留，并在新 generation 就绪后重新绑定；
  外观、语音和 Memory 草稿不得迁移到另一角色。
- 主桌宠只有在角色 ID 变化时替换 Chat Presentation reducer，清除旧回复浏览/打字/TTS 状态并显示新角色
  初始消息。同角色普通 restart 保留已经稳定显示的画面。
- 已打开的历史窗口收到角色 reset 事件后先清空旧页面和 cursor，再读取新角色；reset 前已发出的读取结果
  即使迟到也不得提交到 DOM。

## 5. 验收与回退

自动验收使用 Harness `journey-character-switch`，所有角色包、配置、Memory 和 Timeline 数据只创建在 Harness
临时根目录。journey 覆盖 A→B→A、generation 冻结、Memory degraded、整理游标、历史分页失效、前端草稿
阻断/reducer 重建/迟到页以及受控 restart cleanup。

回退只能关闭设置页角色选择入口并移除新命令接线；不得改写用户已保存的目标角色、删除 Memory/Timeline，
也不得恢复同 generation 的 Assistant Session 热替换路径。本 WP 沿用既有受控 Core restart 决策，不新增 ADR。
