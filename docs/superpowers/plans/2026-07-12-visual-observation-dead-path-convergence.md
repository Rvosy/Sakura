# Visual Observation Dead Path Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除零生产调用的独立视觉摘要器和视觉记录搜索 API，同时保持当前同回复摘要、脱敏落盘与截图追问行为不变。

**Architecture:** `AgentRuntime` 继续从包含图片的最终回复提取 `visual_observation`，`ChatPipeline` 继续把它转换并保存。`app/storage/visual_observation.py` 只保留这条真实链需要的记录模型、存储、解析、脱敏和上下文构造，不再保留二次视觉模型请求入口。

**Tech Stack:** Python 3.11、pytest、现有 AgentRuntime / ChatPipeline / VisualObservationStore。

## Global Constraints

- 宏观程序逻辑、UI、模型请求数量和视觉观察 JSONL 格式不变。
- 不修改 `visual_observation` 回复协议、Agent 最终回复修复或截图编码。
- 严格 TDD；每个生产删除前先看到结构 RED。
- 每次提交后做规格 review 与质量 review，问题通过 amend 修正。
- 生产净删只统计 `app/`、`plugins/`，删除量必须大于新增量。
- 不触碰 `link_sakura_runtime_tts.bat`。

---

### Task 1: 删除独立视觉摘要器

**Files:**
- Modify: `app/storage/visual_observation.py:1-15,164-215,284-364,436-456`
- Modify: `tests/unit/test_visual_observation.py:1-132`

**Interfaces:**
- Removes: `summarize_visual_observation()` 和只服务它的私有 helper。
- Preserves: `extract_visual_observation_summary()`、`visual_observation_record_from_summary()`、存储、脱敏和上下文消息。

- [ ] **Step 1: 写死入口删除失败测试**

在 `tests/unit/test_visual_observation.py` 增加模块 import：

```python
import app.storage.visual_observation as visual_observation_module
```

并增加：

```python
def test_legacy_visual_summarizer_is_removed() -> None:
    assert not hasattr(visual_observation_module, "summarize_visual_observation")
```

- [ ] **Step 2: 运行并确认 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_visual_observation.py -q -k "legacy_visual_summarizer"
```

Expected：失败，模块仍暴露 `summarize_visual_observation`。

- [ ] **Step 3: 删除生产死链和自证测试**

从 `app/storage/visual_observation.py` 删除：

```python
summarize_visual_observation
_build_visual_summary_prompt
_build_visual_summary_user_text
_job_image_parts
_normalize_image_detail
_fallback_record
```

同时删除只被这些函数使用的：

```python
from app.agent.screen_awareness import SCREEN_AWARENESS_IMAGE_DETAIL
from app.core.cancellation import CancelChecker, OperationCancelled
```

从 `tests/unit/test_visual_observation.py` 删除前三条 `test_summarize_visual_observation_*` 和 `summarize_visual_observation` import。保留结构测试及所有真实链测试。

- [ ] **Step 4: 运行 GREEN 与结构扫描**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_visual_observation.py -q
rg -n "summarize_visual_observation|_build_visual_summary_prompt|_job_image_parts|_fallback_record" app plugins tests
```

Expected：视觉观察单元测试通过；`rg` 只允许命中结构测试中的字符串 `summarize_visual_observation`。

- [ ] **Step 5: 提交并双 review**

```powershell
git add app/storage/visual_observation.py tests/unit/test_visual_observation.py
git commit -m "refactor: remove legacy visual summarizer"
git show --check --stat HEAD
```

Review：`_load_json_object()` 必须保留，因为当前回复提取仍使用；`_job_metadata()` 与 `_record_from_summary()` 必须仍覆盖 observation 和 screen_contexts；不得改变存储格式或脱敏。

### Task 2: 删除无调用视觉搜索 API

**Files:**
- Modify: `app/storage/visual_observation.py:107-130`
- Modify: `tests/unit/test_visual_observation.py`

**Interfaces:**
- Removes: `VisualObservationStore.search()`。
- Preserves: `append()`、`recent()`、保留期限和最大记录数。

- [ ] **Step 1: 写搜索 API 删除失败测试**

增加：

```python
def test_unused_visual_observation_search_is_removed() -> None:
    assert not hasattr(VisualObservationStore, "search")
```

- [ ] **Step 2: 运行并确认 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_visual_observation.py -q -k "unused_visual_observation_search"
```

Expected：失败，类仍暴露 `search`。

- [ ] **Step 3: 删除 search 实现**

删除 `VisualObservationStore.search()` 整个方法。不增加替代 facade；当前关键词策略仍由 `should_inject_visual_context()` 决定 `recent()` 的读取数量和时间窗口。

- [ ] **Step 4: 运行 GREEN 与调用扫描**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_visual_observation.py tests/ui/test_pet_window.py -q -k "visual_context or visual_observation or screenshot_followup"
rg -n "def search\(" app/storage/visual_observation.py
rg -n "VisualObservationStore\.search|visual_observation_store\.search" app plugins tests
```

Expected：相关测试通过；两条 `rg` 均无输出。

- [ ] **Step 5: 提交并双 review**

```powershell
git add app/storage/visual_observation.py tests/unit/test_visual_observation.py
git commit -m "refactor: remove unused visual observation search"
git show --check --stat HEAD
```

Review：PetWindow 必须仍只通过 `recent()` 注入视觉记录；关键词追问仍读取最近三条，普通追问仍读取十分钟内一条。

### Task 3: 真实视觉链与全量验收

**Files:**
- Verify only: 本计划所有改动

- [ ] **Step 1: 编译与真实链组合验证**

```powershell
.\runtime\python.exe -m compileall -q app plugins main.py
.\runtime\python.exe -m pytest tests/unit/test_visual_observation.py tests/unit/test_agent_runtime.py tests/integration/test_chat_pipeline.py tests/integration/test_chat_worker.py tests/ui/test_pet_window.py -q -k "visual or screen_observation or screenshot_followup"
```

Expected：全部通过；同回复摘要、脱敏落盘、手动截图、自动观察和主动屏幕感知仍绿色。

- [ ] **Step 2: 分层与全量验证**

```powershell
.\runtime\python.exe -m pytest tests/unit -q
.\runtime\python.exe -m pytest tests/integration -q
.\runtime\python.exe -m pytest tests/ui -q
.\runtime\python.exe -m pytest -q
```

Expected：全部退出 0，无新增 QThread/QWaitCondition 原生退出警告。

- [ ] **Step 3: 生产净删与 Git 审查**

```powershell
$base = "7c3193b"
$added = 0
$deleted = 0
git diff --numstat "$base..HEAD" -- app plugins | ForEach-Object {
    $parts = $_ -split "`t"
    if ($parts[0] -ne "-") { $added += [int]$parts[0] }
    if ($parts[1] -ne "-") { $deleted += [int]$parts[1] }
}
Write-Output "production added=$added deleted=$deleted net_deleted=$($deleted - $added)"
if ($deleted -le $added) { throw "生产代码删除量未大于新增量" }
git log --oneline "$base..HEAD"
git diff --check "$base..HEAD"
git status --short
```

- [ ] **Step 4: 最终规格与质量 review**

逐项回答：

1. 生产是否只剩 Agent 同回复视觉摘要链？
2. 视觉摘要缺失是否仍跳过保存，而不是写失败记录？
3. observation 与 screen_contexts 元数据构造是否仍保留？
4. 敏感信息是否仍在记录构造和磁盘写入两层脱敏？
5. 截图追问是否仍按关键词读取三条、普通场景读取十分钟内一条？
6. JSONL 格式、保留期限和记录上限是否未变？
7. 工作树是否只剩用户 bat？

发现问题后补 RED、最小修复、amend 并重验；不创建空提交。
