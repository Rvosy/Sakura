---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-04
---

# WP-4-01 设置布局与 generation 重绑定缺陷记录

## 发现与影响

2026-08-03，项目负责人在 WP-4-01 Windows 可见 UI 人工验收中否决当前候选并报告三项缺陷：设置页
新增控件挤压既有排版；记忆整理模型与 embedding 模型被放在“记忆”页而非统一的“模型”页；Memory
列表无法稳定加载，编辑也无法保存。Work Package 状态仍只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准，本记录不填写人工验收结论。

可见错误包括 `REQUEST_DEADLINE_EXCEEDED`、`GENERATION_INVALIDATED: Router closed`、
`SETTINGS_CORE_GENERATION_MISMATCH`，以及要求关闭并重新打开设置的提示。已有 Memory 数据没有被判定
损坏，本次不执行数据修复、迁移或删除。

## 根因

保存记忆整理模型槽会按既有 change plan 受控重启 Python Core，但已打开的设置窗口仍持有旧
`coreGenerationId`。旧代请求随后超时或被 Router 关闭，迟到错误又直接进入 Memory 列表状态，导致新代
读取和后续保存被 identity mismatch 拒绝，并覆盖已有可读内容。与此同时，整理 Provider、整理模型和
embedding 模型卡片被追加到记忆页顶部，破坏了旧设置页面的紧凑统计和稳定双栏布局。

2026-08-04 的复验又发现一个跨域生命周期缺口：`appearance-runtime.js` 仍沿用旧行为，在观察到 Core
generation 更换后废弃外观控制器、禁用全局“应用”和“保存并关闭”按钮，并提示关闭重开设置。即使
Memory 控制器已成功原位重绑定，设置窗口仍会被外观控制器锁死。该路径与本次既有设置重绑定契约直接
冲突，不是 Memory 数据或保存命令失败。

同日继续检查真实 Windows 设置窗口，确认 Memory 从未完成首次加载时，footer 的首个异常为
`TypeError: Cannot read properties of undefined (reading 'api_temperature')`。外观快照初始化及跨代重绑定会
整体替换设置页共享 `request`，删除已由 Provider/模型域写入的 API limits，并可能同时删除 Memory 域
状态；随后 Provider 脏状态计算同步抛错，中止后续 Memory 初始化。这是设置域共享前端状态的所有权
缺陷，不是 Memory 存储为空、损坏或 Gateway 返回失败。

## 修订契约与修复门

- 所有模型相关设置统一呈现在“模型”页：记忆整理 Provider/模型和固定 embedding 模型状态、导入、
  下载、取消均从“记忆”页移出。
- “记忆”页只保留自动整理轮次和 Memory 状态、搜索、筛选、列表、编辑器；布局以旧设置页面为基线。
- Core generation 更换后，当前设置窗口原位重新绑定；不关闭、不重建、不要求用户重新打开。
- 重绑定期间保留列表、筛选、选中项、未保存草稿与中文/日文 IME composition。旧代成功或错误不得
  覆盖新代状态，不得自动提交、清空或重发用户操作。
- 修复必须覆盖窄窗口、模型槽保存后继续搜索/CRUD、旧代迟到响应和重绑定失败的稳定可重试状态。

本次是 UI 归属与既有 generation 生命周期契约的收紧，不改变 Python Memory 数据所有权、配置 schema、
协议 major、依赖或 Legacy Qt 的仅参考边界，因此不新增 ADR。

## 修复候选与自动验证

修订契约以提交 `7717c544` 建立独立 `0002` activation。后续工作树候选完成以下修复：

- 把整理 Provider、整理模型和固定 embedding 模型卡片移到“模型”页；“记忆”页恢复自动整理轮次、
  紧凑状态/筛选和稳定列表/编辑器双栏。
- Memory frontend 使用单一重绑定 promise；搜索在确认新 generation 后最多安全重试一次，旧代迟到结果
  按本地 revision 丢弃。Rust 在写入前明确拒绝的 identity mismatch 可在新代安全补发一次；Router 关闭
  或结果不确定的新增、编辑和删除只重绑定并刷新列表，绝不自动重发。
- 重绑定保留筛选、选中项和编辑草稿；正在进行的中文/日文 IME composition 不被赋值覆盖，文本编辑在
  恢复期间保持可用，只有搜索、保存、删除和模型任务等动作暂时禁用。
- Provider/模型域先触发 Core restart 时，Memory controller 在继续独立保存前刷新当前 identity；Memory
  整理槽自身触发 restart 后自动刷新列表，不再要求关闭并重新打开设置。
- 外观控制器在 Core generation 更换后取消旧代预览并原位打开新代预览会话；同一角色的外观草稿继续
  保留并恢复预览。重绑定不再废弃设置会话、禁用全局应用/保存动作或要求关闭重开；保存和取消会等待
  当前重绑定完成，旧代预览错误不得覆盖新代页面状态。
- 外观快照只合并其拥有的角色、主题和布局字段，不再整体替换设置页共享状态；Provider limits/API 与
  Memory settings 在外观首次初始化和跨代重绑定后都必须继续存在，首屏 Memory 初始化不得被其他设置域
  的脏状态计算打断。

自动结果：

- `node --test desktop/frontend/tests/*.test.js`：最新候选 118 passed、0 failed；新增用例覆盖
  generation A→B 后外观草稿、全局应用/保存按钮、新代保存能力，以及外观重绑定不得删除 Provider limits
  与 Memory 状态。
- `runtime\python.exe -m harness run runtime-v2-shell`：最新候选 8/8 cases passed；其中前端 118、Provider/模型 25、
  Memory 10，以及角色、产品 Shell、窗口几何和交互 Rust 门全部通过。
- `runtime\python.exe -m harness verify WP-4-01`：23 automated passed、0 failed、3 manual pending；报告为
  `temp/harness/20260803T161746Z-WP-4-01.json`。首次执行只因外层 120 秒命令时限中止，重新执行在
  164.234 秒完成，没有测试失败。
- 2026-08-04 新候选首次完整 verify 的 docs、smoke、core-host、runtime-v2-shell 均通过；python-full 中
  3 个 WP-3-06 单实例测试因人工验收窗口 `sakura-runtime-v2-shell.exe` 仍在运行并持有生产锁而失败，
  报告为 `temp/harness/20260803T164111Z-WP-4-01.json`。该次结果不作为自动门通过证据，须正常退出真实
  应用后重跑；未强杀验收窗口或修改 Legacy 参考测试来规避锁。

本记录不填写人工验收。真实 Windows EXE 的页面排版、Core 强杀/恢复、IME 操作和退出清场仍由项目
负责人按修订后的清单亲自确认；WP-4-01 在确认前不得标记 `accepted`。
