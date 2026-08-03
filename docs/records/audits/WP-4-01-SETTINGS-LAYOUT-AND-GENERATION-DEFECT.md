---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-03
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

自动结果：

- `node --test desktop/frontend/tests/*.test.js`：116 passed、0 failed。
- `runtime\python.exe -m harness run runtime-v2-shell`：8/8 cases passed；其中前端 116、Provider/模型 25、
  Memory 10，以及角色、产品 Shell、窗口几何和交互 Rust 门全部通过。
- `runtime\python.exe -m harness verify WP-4-01`：23 automated passed、0 failed、3 manual pending；报告为
  `temp/harness/20260803T161746Z-WP-4-01.json`。首次执行只因外层 120 秒命令时限中止，重新执行在
  164.234 秒完成，没有测试失败。

本记录不填写人工验收。真实 Windows EXE 的页面排版、Core 强杀/恢复、IME 操作和退出清场仍由项目
负责人按修订后的清单亲自确认；WP-4-01 在确认前不得标记 `accepted`。
