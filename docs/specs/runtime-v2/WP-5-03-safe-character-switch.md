---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-09-16
---

# WP-5-03 安全角色切换、Session 与历史分页

## 1. 产品边界

第一版只在设置页开放角色选择。下拉选择只更新设置窗口内的临时草稿，用户可以在提交前反复选择；只有点击
“应用”或“保存并关闭”才保存最终目标并触发切换，放弃设置则恢复已提交角色。角色 ID 变化不是同一
Assistant Session 的热配置：切换先阻止新聊天、取消并等待旧回复结束，撤销旧语音授权并取消录音，
再保存目标角色并重建 Assistant Session。Core、MCP、工具注册表以及可复用的表现和 TTS 插件进程保留。
依赖当前角色且缓存角色信息的旧插件，在切换范围内暂停并恢复；Memory 整理及其旧回调随该插件作用域回收。
不提供桌宠右键或托盘入口。

同角色且显示方式不变时是无写入、无重启的 `unchanged`。仅改变当前角色显示方式时返回 `visual_rebind`，
只更新表现绑定和控制说明，原生回执为 `restartState: not_required`，不重启 Core 或其他插件。
导入角色包只有在首次导入并自动成为当前角色时要求重启；
导入非当前角色不重启。

设置页可以给当前已提交角色导入 `.voice`，也可以导出完整角色包、单角色包或语音包。给当前角色导入语音返回
`character_refresh`：支持资源更新协议的语音服务暂停任务并失效权重缓存，保留推理进程；其他语音服务局部重载。
Core generation 保持不变。
语音包已导入但运行态更新失败时返回 `CHARACTER_VOICE_APPLY_FAILED`，不得宣称文件未保存或自动重启。
给非当前角色导入时只更新角色包。导出只读取
已保存的角色数据，不改变当前角色，也不触发重启。完整角色包和语音包要求 GPT 与 SoVITS 模型文件都存在；
模型不完整时仍可导出不含语音的单角色包。

## 2. 配置提交与 restart 协议

Python `characters.settings.select/import/import_voice` 在校验归档或目标角色后保存数据，返回固定 envelope：

```json
{
  "schemaVersion": 1,
  "snapshot": {},
  "changePlan": "unchanged | visual_rebind | character_refresh | character_switch | core_restart_required"
}
```

Rust 必须在同一设置窗口和当前 Core identity 下校验完整响应。`core_restart_required` 只派发一次受控
restart，并向设置页返回已提交目标、前一 generation 和 `restartState=requested`；其余计划返回
`restartState=not_required`。配置保存失败不得 restart；restart 派发失败返回
`CHARACTER_RESTART_REQUEST_FAILED`。配置可能已经提交，因此失败后禁止自动重复写入、自动回滚、自动重试或回退
旧角色，用户只能按明确错误人工恢复。

正常角色切换返回 `character_switch`，原生回执增加 `characterChanged: true`，同时保持
`restartState: not_required`。前端据此清空旧角色页面并等待目标表现就绪，不要求 generation 增加。
准备失败返回 `CHARACTER_SWITCH_PREPARE_FAILED` 且不保存目标；保存后应用失败返回
`CHARACTER_SWITCH_APPLY_FAILED`，明确说明选择已经保存，不自动重启。
Core 记录尚未成功应用的选择；用户再次提交同一目标时重新执行切换，只有会话应用和相关插件恢复都成功后，
后续相同选择才返回 `unchanged`。初始化失败发布 `failed` 状态，设置与工坊仍可访问，不把已保存误报为已应用。

`characters.settings.export` 接收角色 ID、导出类型和 Rust 文件对话框选出的绝对路径，成功时只返回
`schemaVersion`、`outputPath` 和用户提示。Python Core 负责校验角色及语音模型，并通过临时文件替换目标归档；
Rust 和 WebView 不直接读取角色目录。

设置页的角色下拉不得直接调用 `characters.settings.select`。它可以通过独立的只读视觉预览命令加载目标角色
已保存的主题、默认立绘和初始问候语，但不得改变 active character、Core generation、Chat reducer、Memory/Timeline、TTS
或插件 identity。统一保存流程先提交当前 generation 的其他设置，
最后只对最终角色草稿调用一次 `select`，避免角色相关设置写入不同目标。
选择又回到已提交角色时清除角色 dirty 状态，不写配置、不读取 lifecycle，也不重启。暂存目标期间仍只展示和
  编辑当前正式角色的数据，不预加载目标角色的外观、语音、Memory 或历史。为了避免下拉已显示目标角色时造成
  所属角色误判，暂存期间锁定外观和语音页面，并暂停角色 Collection 的查询和写入；角色下拉、普通全局字段和全局 Collection 仍可编辑。

角色切换完成必须同时满足：

1. `characterChanged` 回执允许保持原 generation；旧版 restart 回执仍要求 generation number 增加；
2. Core Snapshot 的 generation ID 与 Supervisor 一致；
3. Snapshot readiness 为 `ready`、`degraded`，或带有效目标表现的 `setup_required`；
4. Character Presentation 的 generation ID 一致且 `characterId` 等于已提交目标。

任一条件缺失都不能显示新角色历史或宣告切换成功。目标到达 `failed` 时报告初始化失败，不自动恢复。

切换准备期间，Snapshot 保留上一份完整角色信息；新 Session 和最终表现绑定完成后，再一起发布目标角色摘要、
表现和递增 revision。不得组合旧角色摘要与新角色表现，也不得发布随后立即被替换的中间绑定。
切换前发起的表现查询不能覆盖已经提交的新 Session。桌面端拒绝快照时，日志必须记录具体读取或校验错误。
同角色保存名称或开场白也须先准备新摘要与新表现，再一次持锁发布，不能依赖下一次快照读取补齐状态。

## 3. 会话与数据隔离

Core generation 表示进程寿命，角色 ID 表示会话归属。切换阻止新聊天，等待旧聊天取消完成后才提交配置；
旧 Assistant Session 被退役，新 Session 按目标角色构造。插件角色服务的 current 值在此边界更新，
不根据磁盘变化提前切换。Timeline Host Service 从当前 Session 获取角色。
声明依赖 `sakura.host.character` 或 `sakura.host.timeline` 的活动插件默认局部重载，包括其硬依赖方；
表现 Provider、TTS Provider 和 TTS Hub 使用明确的角色参数处理资源，保留进程。旧插件 callback 随其作用域失效。

| 领域 | 强制隔离行为 |
|---|---|
| Memory/Mem0 | 查询、写入、core profile、整理游标和 Curator 使用冻结角色；新角色 Memory 不可用时返回空的 degraded 结果，不读取旧角色或默认角色 |
| Timeline/历史 | 所有请求携带 generation 与角色 ID；角色变化立即清空窗口内容和分页游标；旧游标和迟到分页结果失效 |
| TTS/音频 | 切换撤销旧段落授权并取消合成；迟到合成不能发布 ready，原生停止旧播放，TTS 引擎保留并切换权重 |
| 截图/插件 artifact | 切换清空待发送截图并取消 ASR；旧任务由取消标记和明确的角色归属隔离，进程退出时仍按 generation 回收资源 |
| 设置 transport/异步事件 | 保留 transport；清空角色页面、失效旧集合查询和编辑器后重新读取，旧结果不得提交到新页面 |

旧聊天取消超时或角色插件无法停止时不提交新角色。普通同角色 Core restart 继续使用既有 generation 隔离，
但不视为角色变化。首次导入并建立角色会话仍使用原有受控重启路径。

## 4. 设置页与桌宠表现

表现资源使用[表现插件合同](visual-plugin-boundary.md)的 schemaVersion 2。切换、插件重载或停用撤销旧 bindingId、
控制与资产授权；同角色重启保留文字历史，不根据历史中的 portrait 或 control 重放画面。目标角色预览使用其自己的缩放值。

桌宠按 generation 和角色 ID 判断是否需要重新绑定。同一 generation 内切换角色时，也要等待绑定完成后再派发
可聊天状态，使新 reducer 能显示初始问候语；等待期间不接受发送。角色变化同时清理录音、待发送截图和旧语音状态。


- 当前角色有未保存的外观、语音或 character Collection 修改时，选择另一角色恢复原下拉值并提示先保存或放弃。
  打开编辑器不算修改；global Collection 草稿不阻止角色选择。归属与旧包兼容见 [Collection 合同](sakura-plugin-runtime-v4.md#10-插件管理与设置窗口)。
- 待应用角色只改变下拉显示、dirty/提示状态，以及桌宠的目标主题、默认立绘和气泡问候语预览；不得改变名字、
  输入提示、Chat reducer、回复历史、语音、Memory 或 Timeline。预览资源使用单独的只读 resource slot，不能替换 generation 的 active
  Character Presentation。放弃或选回已提交角色时恢复正式角色主题、当前回复对应立绘和精确点击区域。
  气泡同时恢复 reducer 当前应显示的内容；预览期间到达的回复只更新 reducer 状态，不覆盖预览问候语。
  暂存期间暂停角色 Collection 请求，global Collection 仍可使用。
- 已提交的角色切换回执才进入 switching；`characterChanged` 即使不伴随 Core 重启也必须等待目标角色及表现绑定。角色切换或 Core 转场期间所有 Collection 暂停请求，删除确认返回后复核编辑器与实例。
  本地切换与外部重绑定各自完成后释放锁，过期 generation 和普通目录通知不能提前解除另一操作的锁。
- 实际角色变化清空角色集合的页面、编辑器、筛选、游标和在途请求；global 草稿保留。同角色局部刷新或 Core 重启保留仍存在集合的草稿，并使刷新前的回执失效。
  新快照绑定新的请求状态，旧查询、错误或写入回执不回填，也不自动重放写入；结束后恢复当前搜索。
- 当前角色仍有外观、语音或角色集合修改时禁止导入语音。全局草稿不属于此门禁。
  导出读取已保存的角色包；有待应用角色时，导入语音和导出仍禁用。
- 全局字段与 global Collection 草稿可在“应用”后保留，角色变更最后提交；“保存并关闭”必须先处理所有未保存集合，不能暗示记录已保存。
- 主桌宠只有在角色 ID 变化时替换 Chat Presentation reducer，清除旧回复浏览/打字/TTS 状态并显示新角色
  初始消息。同角色普通 restart 保留已经稳定显示的画面。
- 已打开的历史窗口收到角色 reset 事件后先清空旧页面和 cursor，再读取新角色；reset 前已发出的读取结果
  即使迟到也不得提交到 DOM。

## 5. 验收与回退

自动验收使用 Harness `journey-character-switch`，所有角色包、配置、Memory 和 Timeline 数据只创建在 Harness
临时根目录。journey 覆盖 A→B→A、会话隔离、Memory degraded、整理游标、历史分页失效、前端草稿
阻断/reducer 重建/迟到页以及受控 restart cleanup。

回退只能关闭设置页角色选择入口并移除新命令接线；不得改写用户已保存的目标角色、删除 Memory/Timeline，
不得以复用进程为由混用角色历史、记忆或旧回调。会话与插件寿命的边界见
[ADR-0051](../../adr/0051-character-switch-retains-engines.md)。
