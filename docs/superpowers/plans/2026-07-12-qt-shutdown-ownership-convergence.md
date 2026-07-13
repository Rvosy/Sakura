# Qt Shutdown Ownership Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 ResourceManager 第一阶段遗留兼容面，并让 timeout QThread、Shiboken wrapper 与不可取消 TTS 迁移在退出时拥有真实且唯一的所有者。

**Architecture:** clean QThread 继续由 `QtWorkerResource` 正常 finalize；timeout QThread 立即从 owner 和 PetWindow QObject parent 脱离，转交 ResourceManager lingering。正常/lingering 完成都经同一个 QObject retire helper。启动迁移保持不可取消，但运行期间 PetWindow 拒绝关闭。

**Tech Stack:** Python 3.11、PySide6 QObject/QThread/QTimer、shiboken6、pytest/pytest-qt、现有 ResourceRegistry / ResourceManager。

## Global Constraints

- 不强杀 QThread，不改有限 wait timeout，不改 ResourceRegistry shutdown order。
- 正常聊天、TTS、插件、迁移完成和 UI 行为不变；只收紧退出/超时边界。
- timeout 后不运行 `on_finished` 业务回调，防止 shutdown 中重启任务。
- 严格 TDD；每个生产修改前必须看到对应 RED。
- 每个提交后规格 review + 质量 review，问题 amend。
- 生产净删只统计 `app/`、`plugins/`，删除量必须大于新增量。
- 不触碰 `link_sakura_runtime_tts.bat`。

---

### Task 1: 删除 Qt 第一阶段兼容 facade

**Files:**
- Modify: `app/core/resource_manager.py:941-955,1118-1133`
- Modify: `app/ui/pet_window.py:3375-3385`
- Modify: `tests/unit/test_resource_manager.py:100-138` 及 manager facade 断言

**Interfaces:**
- Preserves: `QtWorkerResource.stop()` → `_stop_thread_mechanics()`。
- Removes: `ResourceManager.stop_qt_thread()`、`ResourceManager._resources`、`ResourceManager._lingering_threads`、PetWindow wrapper delegate。

- [ ] **Step 1: 写 facade 退出失败测试**

在 `tests/unit/test_resource_manager.py` 增加：

```python
from pathlib import Path


def test_legacy_qt_resource_facades_are_removed() -> None:
    assert not hasattr(ResourceManager, "stop_qt_thread")
    assert not hasattr(ResourceManager, "_resources")
    assert not hasattr(ResourceManager, "_lingering_threads")

    root = Path(__file__).resolve().parents[2]
    pet_window_source = (root / "app/ui/pet_window.py").read_text(encoding="utf-8")
    assert "_retain_qobject_wrappers_until_deleted" not in pet_window_source
```

- [ ] **Step 2: 运行并确认 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_resource_manager.py -q -k "legacy_qt_resource_facades"
```

Expected：四项旧 facade 均仍存在，测试失败。

- [ ] **Step 3: 删除生产 facade 与只验证死入口的测试**

删除 `ResourceManager` 两个 registry 代理 property 和整个 `stop_qt_thread()`。保留 `_stop_thread_mechanics()`。

删除 `PetWindow._retain_qobject_wrappers_until_deleted()`。

删除三项 `test_stop_qt_thread_*`。其他测试中的：

```python
mgr._resources
mgr._lingering_threads
```

分别改为：

```python
mgr.registry._resources
mgr.registry._lingering_threads
```

不要改 `ResourceRegistry._resources` 自身的测试观察点。

- [ ] **Step 4: 运行 GREEN 与结构扫描**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_resource_manager.py tests/ui/test_backchannel_controller.py -q
rg -n "stop_qt_thread|_retain_qobject_wrappers_until_deleted|def _lingering_threads" app plugins
```

Expected：测试通过；`rg` 无输出。`test_legacy_qt_resource_facades_are_removed`
单独验证 `ResourceManager._resources` 已删除，避免误报保留的
`ResourceRegistry._resources` 真实状态观察点。

- [ ] **Step 5: 提交并双 review**

```powershell
git add app/core/resource_manager.py app/ui/pet_window.py tests/unit/test_resource_manager.py tests/ui/test_backchannel_controller.py
git commit -m "refactor: remove legacy qt resource facades"
git show --check --stat HEAD
```

Review：`_stop_thread_mechanics` 必须仍只有 `QtWorkerResource.stop()` 生产调用；registry 状态不再由 QObject wrapper 二次暴露；删除测试只属于死入口。问题 amend。

### Task 2: 转移 lingering Qt worker 所有权

**Files:**
- Modify: `app/core/resource_manager.py:123-220,1169-1225`
- Modify: `tests/unit/test_resource_manager.py:40-220,317-338`

**Interfaces:**
- Produces: timeout 后 owner attrs 为 None，thread parent 为 None，manager lingering 独占运行对象。
- Produces: `thread.finished` → ResourceManager bound slot → UI 线程 release。
- Produces: 正常与 lingering 完成都调用 `_retire_qobjects(worker, thread)`。

- [ ] **Step 1: 扩展 thread stub 并改写 timeout 失败测试**

给 `_ThreadStub` 增加：

```python
self.parent_value: object | None = object()

def setParent(self, parent: object | None) -> None:
    self.parent_value = parent
```

把 `test_resource_stop_timeout_lingers_and_unregisters` 的旧 owner 期望改为：

```python
assert owner.t is None
assert owner.w is None
assert thread.parent_value is None
assert mgr._lingering == [(thread, worker)]
```

当前实现保留 owner 属性且不 detach parent，因此 RED。

- [ ] **Step 2: 写统一 retire 与 bound slot 失败测试**

在同一 timeout 测试后断言：

```python
callback = thread.finished.callbacks[-1]
assert getattr(callback, "__self__", None) is mgr
assert getattr(callback, "__name__", "") == "_release_finished_lingering"

mgr._release_lingering(thread)

assert mgr._lingering == []
assert thread in mgr._retired_wrappers
assert worker in mgr._retired_wrappers
assert worker.deleted is True
assert thread.deleted is True
```

当前连接是 lambda，且 release 不 retain wrapper，因此失败。

- [ ] **Step 3: 写 wrapper 重试 prune 失败测试**

替换 `test_retain_wrappers_prunes_invalid` 为：

```python
def test_wrapper_prune_retries_until_cpp_object_is_invalid(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _qt_app_or_skip()
    mgr = ResourceManager()
    wrapper = QObject()
    valid = True
    callbacks: list[object] = []
    fake = types.ModuleType("shiboken6")
    fake.isValid = lambda _obj: valid  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "shiboken6", fake)
    monkeypatch.setattr(
        resource_manager_module.QTimer,
        "singleShot",
        staticmethod(lambda _delay, callback: callbacks.append(callback)),
    )

    mgr._retain_wrappers(wrapper)
    assert len(callbacks) == 1
    callbacks.pop(0)()
    assert mgr._retired_wrappers == [wrapper]
    assert len(callbacks) == 1

    valid = False
    callbacks.pop(0)()
    assert mgr._retired_wrappers == []
    assert callbacks == []
```

在测试顶部增加 `import app.core.resource_manager as resource_manager_module`。

当前方法仍为公开名且首次 prune 不重排 timer，因此 RED。

- [ ] **Step 4: 运行并确认 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_resource_manager.py -q -k "timeout_lingers or wrapper_prune"
```

Expected：owner、parent、bound slot、retained wrapper 和重排 timer 断言失败。

- [ ] **Step 5: 实现所有权转移**

`QtWorkerResource.stop()` timeout 分支在清空自身引用前调用：

```python
self._null_owner_attrs()
```

`ResourceManager._keep_lingering()`：

```python
try:
    thread.setParent(None)
except RuntimeError as exc:
    log_event(
        "ResourceManager",
        "后台线程脱离窗口父对象失败",
        {"error": str(exc)},
    )
```

然后连接：

```python
thread.finished.connect(self._release_finished_lingering)
```

新增 UI-thread slot：

```python
@Slot()
def _release_finished_lingering(self) -> None:
    thread = self.sender()
    if isinstance(thread, QThread):
        self._release_lingering(thread)
```

保留 `_release_lingering(thread)` 作为可直接测试的机制 helper。

- [ ] **Step 6: 统一 retire 和 wrapper 生命周期**

把 `retain_wrappers` 改名 `_retain_wrappers`。新增：

```python
def _retire_qobjects(
    self,
    worker: QObject | None,
    thread: QThread | None,
) -> None:
    self._retain_wrappers(thread, worker)
    _delete_later_quietly(worker)
    _delete_later_quietly(thread)
```

正常 `_finalize()` 与 `_release_lingering()` 都调用此 helper。

`_prune_wrappers()` 末尾：

```python
self._retired_wrappers = alive
if alive:
    QTimer.singleShot(WRAPPER_RETENTION_MS, self._prune_wrappers)
```

- [ ] **Step 7: 运行 GREEN 与 Qt 回归**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_resource_manager.py tests/ui/test_backchannel_controller.py tests/ui/test_pet_window.py -q -k "resource or lingering or shutdown or cleanup_worker or memory_curation or tts_ready_warmup"
rg -n "retain_wrappers\(" app plugins tests
```

Expected：pytest 通过；`rg` 只命中私有 `_retain_wrappers` 定义/调用/测试。

- [ ] **Step 8: 提交并双 review**

```powershell
git add app/core/resource_manager.py tests/unit/test_resource_manager.py tests/ui/test_backchannel_controller.py tests/ui/test_pet_window.py
git commit -m "fix: transfer lingering qt worker ownership"
git show --check --stat HEAD
```

Review：owner 清空必须早于资源丢引用；新 worker 身份保护仍绿；thread 必须脱离 parent；business callback 在 timeout 后仍不运行；release 必须在 bound slot/UI 线程。问题 amend。

### Task 3: 阻止迁移中关闭窗口

**Files:**
- Modify: `app/ui/pet_window.py:1264-1285`
- Modify: `main.py:580-596`（注释）
- Modify: `tests/ui/test_pet_window.py`

**Interfaces:**
- Produces: running `tts_migration_thread` 时 close event 被 ignore，shutdown 零副作用。
- Preserves: migration 不运行或已结束时走现有下载检查与 `close_external_tools()`。

- [ ] **Step 1: 写迁移门禁失败测试**

在 `tests/ui/test_pet_window.py` 增加：

```python
def test_close_event_waits_for_running_tts_migration(pet_window, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtGui import QCloseEvent
    import app.ui.pet_window as pet_window_module

    class MigrationThreadStub:
        def isRunning(self) -> bool:
            return True

    closed: list[bool] = []
    messages: list[tuple[str, str]] = []
    pet_window.tts_migration_thread = MigrationThreadStub()
    monkeypatch.setattr(pet_window_module, "has_active_tts_bundle_download", lambda: False)
    monkeypatch.setattr(pet_window, "close_external_tools", lambda: closed.append(True))
    monkeypatch.setattr(
        pet_window_module.QMessageBox,
        "information",
        lambda _parent, title, message: messages.append((title, message)),
    )
    event = QCloseEvent()

    pet_window.closeEvent(event)

    assert not event.isAccepted()
    assert closed == []
    assert messages == [("TTS 数据迁移中", "请等待 TTS 数据迁移完成后再退出 Sakura。")]
```

- [ ] **Step 2: 运行并确认 RED**

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py -q -k "close_event_waits_for_running_tts_migration"
```

Expected：当前 closeEvent 进入现有关闭链，event accepted 且 `closed == [True]`。

- [ ] **Step 3: 实现门禁**

在 `PetWindow.closeEvent()` 最前面：

```python
migration_thread = getattr(self, "tts_migration_thread", None)
try:
    migration_running = bool(
        migration_thread is not None and migration_thread.isRunning()
    )
except RuntimeError:
    migration_running = False
if migration_running:
    QMessageBox.information(
        self,
        "TTS 数据迁移中",
        "请等待 TTS 数据迁移完成后再退出 Sakura。",
    )
    event.ignore()
    return
```

更新 `main.py` 的 `register=False` 注释，明确 PetWindow close gate 保证迁移期间窗口不销毁。

- [ ] **Step 4: 运行 GREEN 与启动/关闭回归**

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py tests/unit/test_resource_manager.py tests/unit/test_tts_bundle.py -q -k "migration or close_external_tools or shutdown or resource"
```

Expected：全部通过；迁移完成/未运行的正常关闭测试保持绿色。

- [ ] **Step 5: 提交并双 review**

```powershell
git add app/ui/pet_window.py main.py tests/ui/test_pet_window.py
git commit -m "fix: block shutdown during tts migration"
git show --check --stat HEAD
```

Review：门禁必须早于下载检查和 `_shutdown_in_progress`；无效 wrapper 不得永久挡退出；迁移仍 register=False 且完成后属性由 ResourceManager 清空。问题 amend。

### Task 4: 全量、净删与退出边界验收

**Files:**
- Verify only: 本计划所有改动

- [ ] **Step 1: 结构扫描**

```powershell
rg -n "stop_qt_thread|_retain_qobject_wrappers_until_deleted|def _lingering_threads" app plugins
rg -n "thread\.finished\.connect\(lambda|(^|[^_])retain_wrappers\(" app/core/resource_manager.py
rg -n "setParent\(None\)|_release_finished_lingering|_retire_qobjects" app/core/resource_manager.py
```

Expected：前两条无旧命中；第三条命中新所有权链。

- [ ] **Step 2: 编译与分层验证**

```powershell
.\runtime\python.exe -m compileall -q app plugins main.py
.\runtime\python.exe -m pytest tests/unit/test_resource_manager.py tests/ui/test_backchannel_controller.py tests/ui/test_pet_window.py tests/unit/test_tts_bundle.py tests/unit/test_mcp_runtime.py tests/unit/test_plugin_services.py tests/unit/test_memory_store_resources.py -q
.\runtime\python.exe -m pytest tests/unit -q
.\runtime\python.exe -m pytest tests/integration -q
.\runtime\python.exe -m pytest tests/ui -q
.\runtime\python.exe -m pytest -q
```

Expected：全部退出 0，无 QThread/QWaitCondition native 退出警告。

- [ ] **Step 3: 生产净删与 Git 审查**

```powershell
$base = "efc37d7"
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

- [ ] **Step 4: 最终规格与 Ponytail review**

逐项回答：

1. Qt worker 关闭是否仍只有 `_stop_thread_mechanics()` 一套？
2. timeout 后 owner / QObject parent / manager lingering 的所有权是否唯一？
3. 正常与 lingering wrapper 是否按同一顺序 retire？
4. finished release 是否在 ResourceManager bound slot 上执行？
5. wrapper prune 是否最终释放 invalid wrapper 且不会立即丢 valid wrapper？
6. 迁移期间是否可能进入 `stop_all()` 或销毁窗口？
7. clean worker 的 on_finished、shutdown order 和正常 UI 是否保持？

发现问题后回到最近提交补 RED、最小修复、amend 并重验；不创建空提交。
