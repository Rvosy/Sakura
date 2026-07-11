# TTS Playback State Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除虚构的 TTS 后端选择与备用播放器预热，让 AudioSink 成为唯一主路径，并修复长音频提前完成和迟到 sink 信号推进错误。

**Architecture:** `TTSPlaybackEndpoint` 固定先尝试 `AudioSinkPlayer`，仅在其 `start()` 返回 `False` 时惰性创建 `QMediaPlayer`。播放端点继续持有当前音频与 watchdog token；AudioSink 只报告带路径的开始/完成/错误。配置模型不再声明内部播放后端，服务预热保留，播放器预热删除。

**Tech Stack:** Python 3.11、PySide6 QtCore/QtMultimedia、wave、pytest、现有 TTSPlaybackEndpoint / AudioSinkPlayer。

## Global Constraints

- 正常 TTS 开关、Provider、播放顺序、字幕 started/finished 回调、错误 UI 和配置 UI 不变。
- 保留 `QMediaPlayer` 作为 sink 格式/设备/启动失败时的兼容兜底。
- 不修改 TTS 合成、服务监督、预生成/接话音频和 ResourceManager。
- 严格 TDD：每个生产改动前必须运行并看到对应测试因旧行为失败。
- 生产净删统计只包含 `app/`、`plugins/`，删除量必须大于新增量。
- 每个提交后先规格 review，再代码质量 review；问题修复后 amend。
- 用户文件 `link_sakura_runtime_tts.bat` 不得触碰。

---

### Task 1: 让 QMediaPlayer 成为惰性兼容兜底

**Files:**
- Modify: `app/voice/tts_settings.py:12-64`
- Modify: `app/config/settings_service.py:1-5,365-458`
- Modify: `app/voice/tts.py:96-360`
- Modify: `app/voice/tts_playback.py:27-180,373-423`
- Modify: `app/ui/pet_window.py:820-835,4238-4270,4538-4555,5210-5250`
- Modify: `tests/unit/test_tts.py:380-405,1260-1330,1370-1480,2050-2075`
- Modify: `tests/ui/test_pet_window.py:2910-2955,11425-11445`

**Interfaces:**
- Produces: `TTSPlaybackEndpoint(parent, cache_dir, is_closed)`；`_play_next()` 固定调用 `_play_next_with_sink()`。
- Preserves: `_play_next_with_sink()` 在 `AudioSinkPlayer.start()` 为 `False` 时调用 `_play_next_with_media_player()`。
- Removes: `playback_backend` 配置、`warm_up_playback()` 协议与 PetWindow playback warmup。

- [ ] **Step 1: 写结构与惰性创建失败测试**

在 `tests/unit/test_tts.py` 增加导入：

```python
from dataclasses import fields
import inspect

import app.voice.tts_settings as tts_settings_module
```

把 `test_playback_backend_is_configurable` 替换为：

```python
def test_tts_playback_has_no_external_backend_selector() -> None:
    assert "playback_backend" not in {
        item.name for item in fields(GPTSoVITSTTSSettings)
    }
    assert not hasattr(tts_settings_module, "TTS_PLAYBACK_BACKEND_AUDIO_SINK")
    assert not hasattr(tts_settings_module, "TTS_PLAYBACK_BACKEND_MEDIA_PLAYER")
    assert "playback_backend" not in inspect.signature(
        tts_playback.TTSPlaybackEndpoint
    ).parameters
```

把原 QMediaPlayer warmup 测试改成惰性兜底测试：构造 provider 后断言 `QAudioOutput` / `QMediaPlayer` stub 尚未创建；向队列加入合法 WAV，并让 `_create_audio_sink_player()` 返回 `start() -> False` 的 stub；调用 `_play_next()` 后断言播放器才创建并调用 `setSource()` / `play()`。

在 `tests/ui/test_pet_window.py::test_pet_window_unlocks_after_deferred_services_are_applied` 中让 provider 的旧接口直接失败：

```python
class ServiceReadyTTSProvider(NullTTSProvider):
    def warm_up_playback(self) -> None:
        raise AssertionError("备用播放器不应被预热")
```

删除 `warm_up_count` 断言；保留窗口解锁、错误提示和服务注入断言。

- [ ] **Step 2: 运行并确认 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_tts.py tests/ui/test_pet_window.py -q -k "external_backend_selector or lazy or unlocks_after_deferred"
```

Expected：结构测试因字段/常量/构造参数仍存在而失败；UI 测试因 PetWindow 仍调用 `warm_up_playback()` 而失败；惰性测试因旧显式后端分支或 warmup 预期不符而失败。

- [ ] **Step 3: 删除后端选择配置**

在 `app/voice/tts_settings.py` 删除两个 `TTS_PLAYBACK_BACKEND_*` 常量和 dataclass 的 `playback_backend` 字段。

在 `app/config/settings_service.py`：

- `from dataclasses import dataclass, replace` 改为只导入 `dataclass`；
- 删除 `playback_backend = ...`；
- 删除两处 `replace(settings, playback_backend=playback_backend)`。

在 `app/voice/tts.py::_create_playback_endpoint()` 删除 settings 参数和 backend 转发：

```python
def _create_playback_endpoint(self) -> TTSPlaybackEndpoint:
    return TTSPlaybackEndpoint(
        self,
        cache_dir=self._tts_cache_dir,
        is_closed=self._is_closed,
    )
```

构造函数调用同步改为 `self._playback = self._create_playback_endpoint()`。

在 `app/voice/tts_playback.py` 删除 backend 常量 import、`_DEFAULT_PLAYBACK_BACKEND`、构造参数和 `_playback_backend` 字段；播放日志固定写 `"audio_sink"`；`_play_next()` 校验后直接：

```python
self._play_next_with_sink()
```

现有 sink 启动失败 fallback 分支保持不变。

- [ ] **Step 4: 删除备用播放器 warmup 链**

删除：

- `TTSProvider.warm_up_playback()`；
- `NullTTSProvider.warm_up_playback()`；
- `GPTSoVITSTTSProvider.warm_up_playback()`；
- `TTSPlaybackEndpoint._playback_warmup_requested`、`warm_up_playback()`、`_warm_up_playback()`；
- `PetWindow` 启动、deferred services、设置保存处的 playback warmup 调用；
- `PetWindow._warm_up_current_tts_playback()` 与 `_warm_up_tts_playback()`。

不要删除 `_start_current_tts_ready_warmup()`、`_start_tts_ready_warmup()` 或 `TTSReadyWarmupWorker`。

删除/改写只验证旧 warmup 和显式 media 后端选择的测试；media 状态、EndOfMedia、StoppedState 和 watchdog 测试通过让 sink stub 返回 `False` 自然进入 QMediaPlayer。

- [ ] **Step 5: 运行 GREEN 与结构扫描**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_tts.py tests/unit/test_config.py tests/ui/test_pet_window.py -q -k "tts or playback or deferred_services or settings"
rg -n "playback_backend|TTS_PLAYBACK_BACKEND|warm_up_playback|_warm_up_tts_playback|_warm_up_current_tts_playback" app plugins
rg -n "_start_tts_ready_warmup|TTSReadyWarmupWorker|ensure_ready" app/ui/pet_window.py app/voice/tts.py
```

Expected：pytest 通过；第一条 `rg` 无输出；第二条仍命中真实服务预热链。

- [ ] **Step 6: 提交并双 review**

```powershell
git add app/voice/tts_settings.py app/config/settings_service.py app/voice/tts.py app/voice/tts_playback.py app/ui/pet_window.py tests/unit/test_tts.py tests/unit/test_config.py tests/ui/test_pet_window.py
git commit -m "refactor: make media player a lazy tts fallback"
git show --check --stat HEAD
```

Review：正常音频必须固定走 sink；QMediaPlayer 必须仍可由 sink `False` 路径到达；服务 warmup 不得误删；配置保存格式不得新增字段。发现问题后 amend。

### Task 2: 修复长音频与迟到完成信号

**Files:**
- Modify: `app/voice/tts.py:50-62`
- Modify: `app/voice/tts_playback.py:46-52,487-505,611-657`
- Modify: `tests/unit/test_audio_verification.py:18-125`
- Modify: `tests/unit/test_tts.py`（sink 完成测试区）

**Interfaces:**
- Produces: 已知时长 watchdog 为 `max(2000, duration + 1500)`；未知时长仍为 60000 ms。
- Produces: `_on_sink_finished()` 只接受与 `_current_audio` 路径一致的完成信号。

- [ ] **Step 1: 改写长音频失败测试**

`tests/unit/test_audio_verification.py` 从 `app.voice.tts_playback` 直接导入常量，并把超长测试改为：

```python
def test_known_long_duration_is_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _make_dir("fallback_long")
    wav = root / "long.wav"
    _write_wav(wav, frames=16000 * 120)
    scheduled: list[int] = []
    monkeypatch.setattr(
        tts_playback.QTimer,
        "singleShot",
        staticmethod(lambda delay, _fn: scheduled.append(int(delay))),
    )

    TTSPlaybackEndpoint._schedule_current_audio_finish_fallback(
        self._provider_stub(), wav, 1
    )

    assert scheduled == [120_000 + _AUDIO_FINISH_FALLBACK_GRACE_MS]
```

- [ ] **Step 2: 写迟到 sink 信号失败测试**

在 `tests/unit/test_tts.py` 增加：

```python
def test_stale_sink_completion_does_not_finish_current_audio(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = GPTSoVITSTTSProvider(_minimal_tts_settings())
    endpoint = provider._playback
    root = _runtime_root("stale_sink_finished")
    stale = root / "stale.wav"
    current = root / "current.wav"
    endpoint._current_audio = current
    finished: list[str] = []
    played: list[bool] = []
    monkeypatch.setattr(endpoint, "_finish_current_audio", finished.append)
    monkeypatch.setattr(endpoint, "_play_next", lambda: played.append(True))

    endpoint._on_sink_finished("idle_after_all_pcm_written", str(stale))

    assert finished == []
    assert played == []
    assert endpoint._current_audio == current
```

- [ ] **Step 3: 运行并确认 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_audio_verification.py tests/unit/test_tts.py -q -k "known_long or stale_sink"
```

Expected：长音频得到 60000 而非 121500；迟到路径仍调用 finish/play-next，两项失败。

- [ ] **Step 4: 实现真实 watchdog 与路径校验**

在 `app/voice/tts_playback.py`：

- `_AUDIO_FINISH_FALLBACK_MAX_MS` 改名 `_AUDIO_FINISH_FALLBACK_UNKNOWN_MS = 60_000`；
- duration 为 `None` 时直接使用 unknown 值；
- duration 已知时不再 `min(..., 60_000)`：

```python
delay_ms = (
    _AUDIO_FINISH_FALLBACK_UNKNOWN_MS
    if duration_ms is None
    else max(_AUDIO_FINISH_FALLBACK_MIN_MS, duration_ms + _AUDIO_FINISH_FALLBACK_GRACE_MS)
)
```

在 `_on_sink_finished()` 开头：

```python
finished_path = Path(audio_path_str)
if self._current_audio != finished_path:
    log_event(
        "TTS",
        "忽略过期的 AudioSink 完成信号",
        {
            "finished_audio": str(finished_path),
            "current_audio": str(self._current_audio) if self._current_audio else "",
            "reason": reason,
        },
    )
    return
```

从 `app/voice/tts.py` 删除兜底常量 re-export；测试从真实归属模块导入。

- [ ] **Step 5: 运行 GREEN 与相关回归**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_audio_verification.py tests/unit/test_tts.py tests/unit/test_audio_sink_player.py -q
rg -n "_AUDIO_FINISH_FALLBACK_MAX_MS" app tests
```

Expected：全部通过；旧 MAX 名称无输出；未知时长仍为 60000，120 秒时长为 121500。

- [ ] **Step 6: 提交并双 review**

```powershell
git add app/voice/tts.py app/voice/tts_playback.py tests/unit/test_audio_verification.py tests/unit/test_tts.py
git commit -m "fix: preserve truthful tts completion"
git show --check --stat HEAD
```

Review：路径比较必须发生在任何 finish/queue 副作用前；token watchdog 仍防旧 timer；未知时长仍不会永久挂起；合法长音频不被硬上限截断。发现问题后 amend。

### Task 3: 收敛 AudioSink 完成状态

**Files:**
- Modify: `app/voice/audio_sink_player.py:18-410`
- Modify: `tests/unit/test_audio_sink_player.py:270-310`

**Interfaces:**
- Preserves: `start(path) -> bool`、`stop()`、`started/finished/error` signals。
- Removes: `_finished_emitted`、`_ever_active`、`cancel()` 及未使用局部状态。

- [ ] **Step 1: 写单一状态来源失败测试**

在 `tests/unit/test_audio_sink_player.py` 增加：

```python
def test_sink_completion_has_one_state_source() -> None:
    player = AudioSinkPlayer()

    assert hasattr(player, "_finishing")
    assert not hasattr(player, "_finished_emitted")
    assert not hasattr(player, "_ever_active")
    assert not hasattr(player, "cancel")
```

保留 `test_sink_player_do_finish_is_exactly_once`；删除当前没有调用 `cancel()`、只直接调用 `_finish_once()` 的 `test_sink_player_cancel_calls_do_finish`。

- [ ] **Step 2: 运行并确认 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_audio_sink_player.py -q -k "one_state_source or exactly_once"
```

Expected：结构测试因三个冗余成员仍存在而失败；exactly-once 测试通过。

- [ ] **Step 3: 删除冗余状态与死代码**

在 `app/voice/audio_sink_player.py`：

- 删除 `_finished_emitted` 与 `_ever_active` 的初始化、重置、赋值和清理；
- `_finish_once()` guard 只检查 `_finishing`，并只设置 `_finishing = True`；
- 删除 `cancel()`；
- 删除重复 `@Slot()`；
- `_finish_if_drained()` 只保留 `_finishing` / `_all_pcm_written` guard 和 `_finish_once(...)`；
- `_on_sink_state_changed()` 保留用于比较的 `state_name`，删除未使用的 `is_active`、`is_idle` 和 active 跟踪；
- 更新类 docstring，使完成条件与真实代码一致。

- [ ] **Step 4: 运行 GREEN、结构扫描与 TTS 回归**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_audio_sink_player.py tests/unit/test_tts.py tests/unit/test_audio_verification.py -q
rg -n "_finished_emitted|_ever_active|def cancel|@Slot\(\)\s*@Slot" app/voice tests/unit
```

Expected：pytest 通过；`rg` 无输出；stop/error/drain/Idle 重复完成仍 exactly-once。

- [ ] **Step 5: 提交并双 review**

```powershell
git add app/voice/audio_sink_player.py tests/unit/test_audio_sink_player.py
git commit -m "refactor: consolidate audio sink completion state"
git show --check --stat HEAD
```

Review：删除字段不得参与任何真实判断；`stop()` 仍被 playback endpoint 调用；start 失败不得 emit finished；重复完成仍只 emit 一次。发现问题后 amend。

### Task 4: 全量、净删与边界验收

**Files:**
- Verify only: 本计划所有改动

- [ ] **Step 1: 精确结构扫描**

```powershell
rg -n "playback_backend|TTS_PLAYBACK_BACKEND|warm_up_playback|_warm_up_tts_playback|_warm_up_current_tts_playback" app plugins
rg -n "_finished_emitted|_ever_active|def cancel" app/voice/audio_sink_player.py
rg -n "_start_tts_ready_warmup|TTSReadyWarmupWorker|ensure_ready" app/ui/pet_window.py app/voice/tts.py
rg -n "_play_next_with_media_player" app/voice/tts_playback.py tests/unit/test_tts.py
```

Expected：前两条无输出；服务 warmup 与 media fallback 仍有真实命中。

- [ ] **Step 2: 编译与分层测试**

```powershell
.\runtime\python.exe -m compileall -q app plugins main.py
.\runtime\python.exe -m pytest tests/unit/test_tts.py tests/unit/test_audio_sink_player.py tests/unit/test_audio_verification.py tests/unit/test_config.py -q
.\runtime\python.exe -m pytest tests/unit -q
.\runtime\python.exe -m pytest tests/integration -q
.\runtime\python.exe -m pytest tests/ui -q
.\runtime\python.exe -m pytest -q
```

Expected：全部退出 0，无新增 warning。

- [ ] **Step 3: 生产净删与 Git 审查**

```powershell
$base = "30be0a2"
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

Expected：生产净删为正；三个实现提交顺序清晰；工作树只剩 `link_sakura_runtime_tts.bat`。

- [ ] **Step 4: 最终规格与 Ponytail review**

逐项回答：

1. 正常路径是否可能绕过 sink 直接创建 QMediaPlayer？
2. sink 启动失败时是否仍能到达 QMediaPlayer？
3. 服务 warmup 是否仍真实执行，播放器 warmup 是否彻底退出？
4. 已知长音频是否仍存在固定上限提前 finish？
5. sink 路径和 watchdog token 是否分别拒绝过期完成？
6. AudioSink 是否仍有第二个完成布尔状态或无调用别名？
7. 是否改变 Provider/UI/字幕回调/配置保存的正常语义？

若发现问题，回到最近任务补失败测试、最小修正、amend，并重新运行定点和全量验证；不创建空的“测试通过”提交。
