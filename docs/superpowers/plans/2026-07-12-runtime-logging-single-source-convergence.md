# Runtime Logging Single Source Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让运行日志只读取 UI 实际保存的 `system.yaml:debug`，并删除旧 debug log 兼容层。

**Architecture:** `runtime_log` 继续拥有事件映射、sink 过滤、脱敏和输出；配置只从 `debug` 节读取。内置移动端插件直接调用 `log_event`，不再经过兼容模块。

**Tech Stack:** Python 3.11、pytest、现有 runtime_log / GUI log / 插件系统。

## Global Constraints

- 日志格式、等级、verbosity、脱敏、轮转和 UI 不变。
- `debug` 节字段和设置界面不变。
- 严格 TDD；生产修改前看到准确 RED。
- 每次提交后规格 review + 质量 review，问题 amend。
- 不触碰用户 bat；生产删除量必须大于新增量。

---

### Task 1: 收敛 runtime 日志配置源

**Files:**
- Modify: `app/core/runtime_log.py:208-258,303,1207-1215`
- Modify: `tests/unit/test_debug_log.py`
- Modify: `tests/unit/test_gui_log.py`
- Modify: `tests/unit/test_tts.py`

- [ ] **Step 1: 写单一配置源失败测试**

在 `tests/unit/test_debug_log.py` 增加：

```python
import app.core.runtime_log as runtime_log_module


def test_runtime_log_has_one_persisted_config_source() -> None:
    assert not hasattr(runtime_log_module, "_load_logging_values")
    assert not hasattr(runtime_log_module, "gui_log_enabled")
```

- [ ] **Step 2: 运行 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_debug_log.py -q -k "one_persisted_config_source"
```

Expected：两个旧符号仍存在，测试失败。

- [ ] **Step 3: 实现单一读取路径**

将 console/file/level 改为只读取 `_load_debug_values()`：

```python
def console_log_enabled() -> bool:
    return _bool_value(_load_debug_values().get("enabled"), False)


def file_log_enabled() -> bool:
    return _bool_value(_load_debug_values().get("file_enabled"), True)


def log_level() -> str:
    debug_values = _load_debug_values()
    raw = debug_values.get("level", debug_values.get("profile"))
    value = str(raw or LOG_LEVEL_INFO).strip().lower()
    if value in LOG_LEVELS:
        return value
    return _LOG_LEVEL_ALIASES.get(value, LOG_LEVEL_INFO)
```

删除 `gui_log_enabled()` 和 `_load_logging_values()`。GUI 分发改为：

```python
if _event_visible(record, sink="gui"):
```

删除测试中对 `_load_logging_values` 的 monkeypatch。

- [ ] **Step 4: 运行 GREEN**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_debug_log.py tests/unit/test_gui_log.py tests/unit/test_tts.py -q -k "log or tts_output_reader"
rg -n "_load_logging_values|gui_log_enabled|logging_values" app plugins tests
```

Expected：测试通过；`rg` 只允许命中结构测试字符串。

- [ ] **Step 5: 提交与双 review**

```powershell
git add app/core/runtime_log.py tests/unit/test_debug_log.py tests/unit/test_gui_log.py tests/unit/test_tts.py
git commit -m "refactor: use one runtime logging config source"
git show --check --stat HEAD
```

Review：debug.enabled/file_enabled/profile 语义和默认值不变；GUI sink 仍执行 `_event_visible`。

### Task 2: 删除 debug log 兼容层

**Files:**
- Delete: `app/core/debug_log.py`
- Modify: `app/core/runtime_log.py`
- Modify: `plugins/sakura_mobile/server.py`
- Modify: `tests/unit/test_debug_log.py`

- [ ] **Step 1: 写兼容层删除失败测试**

增加：

```python
def test_legacy_debug_log_facades_are_removed() -> None:
    root = Path(__file__).resolve().parents[2]
    assert not (root / "app/core/debug_log.py").exists()
    assert not hasattr(runtime_log_module, "raw_tts_service_log_enabled")
    assert not hasattr(runtime_log_module, "_close_file_logger_for_tests")
```

- [ ] **Step 2: 运行 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_debug_log.py -q -k "legacy_debug_log_facades"
```

Expected：兼容模块和两个函数仍存在，测试失败。

- [ ] **Step 3: 迁移生产调用并删除 facade**

`plugins/sakura_mobile/server.py`：

```python
from app.core.runtime_log import log_event
```

把五处 `debug_log(...)` 改为 `log_event(...)`。

删除 `app/core/debug_log.py`、`raw_tts_service_log_enabled()` 和 `_close_file_logger_for_tests()`。删除测试 fixture 对 no-op 的 import/调用，以及 `test_debug_log_shim_forwards_to_runtime_log`。

- [ ] **Step 4: 运行 GREEN 与插件回归**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_debug_log.py tests/unit/test_sakura_mobile.py tests/unit/test_tts.py -q
rg -n "app\.core\.debug_log|debug_log\(|raw_tts_service_log_enabled|_close_file_logger_for_tests" app plugins tests
```

Expected：测试通过；`rg` 只允许命中结构测试字符串。

- [ ] **Step 5: 提交与双 review**

```powershell
git add app/core/runtime_log.py plugins/sakura_mobile/server.py tests/unit/test_debug_log.py
git rm app/core/debug_log.py
git commit -m "refactor: remove legacy debug log shim"
git show --check --stat HEAD
```

Review：移动端日志 category/message/data 不变；TTS 原始服务日志写入路径没有条件化。

### Task 3: 分层与全量验收

- [ ] **Step 1: 编译与相关回归**

```powershell
.\runtime\python.exe -m compileall -q app plugins main.py
.\runtime\python.exe -m pytest tests/unit/test_debug_log.py tests/unit/test_gui_log.py tests/unit/test_sakura_mobile.py tests/unit/test_tts.py tests/ui/test_pet_window.py -q -k "log or mobile or tts_output_reader"
```

- [ ] **Step 2: 分层与全量**

```powershell
.\runtime\python.exe -m pytest tests/unit -q
.\runtime\python.exe -m pytest tests/integration -q
.\runtime\python.exe -m pytest tests/ui -q
.\runtime\python.exe -m pytest -q
```

- [ ] **Step 3: 净删与最终 review**

```powershell
$base = "6433bbc"
$added = 0; $deleted = 0
git diff --numstat "$base..HEAD" -- app plugins | ForEach-Object {
    $parts = $_ -split "`t"
    if ($parts[0] -ne "-") { $added += [int]$parts[0] }
    if ($parts[1] -ne "-") { $deleted += [int]$parts[1] }
}
Write-Output "production added=$added deleted=$deleted net_deleted=$($deleted - $added)"
if ($deleted -le $added) { throw "生产代码删除量未大于新增量" }
git diff --check "$base..HEAD"
git status --short
```

逐项确认单一 debug 配置源、GUI sink、移动端日志、脱敏/轮转/TTS 日志和用户 bat；问题补 RED 后 amend。
