# PetWindow State Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变 Sakura 宏观程序逻辑、功能入口、UI 表现和正常时序的前提下，删除 `PetWindow` 周边遗留的旧主动感知路径、内部兼容层和镜像状态，并确保 `app/`、`plugins/` 的生产代码删除量大于新增量。

**Architecture:** `PetWindow` 继续作为 QWidget 与业务编排入口，不引入大型 Controller 或通用状态机。屏幕感知只保留 `screen_awareness` 路径；等待 UI 从 worker busy 的原时点同步；主动事件只保留 `active_event`；记忆整理只保留一个不可变运行上下文。用户数据兼容只存在于版本化迁移层，运行时不再维护旧命名回退。

**Tech Stack:** Python 3.12、PySide6、pytest、pytest-qt、YAML 配置迁移、Git 小步提交。

> **2026-07-12 Tauri 集成说明：** 本计划最初基于 Qt `SettingsDialog` 编写。新版 `dev` 已删除 `app/ui/settings_dialog.py` 与 `app/ui/settings/pages/sections.py`，设置入口改为 `app/ui/tauri_settings.py` 和 `tools/settings-tauri/`。下文涉及旧 Qt 设置文件的步骤仅保留为历史实施记录；最终实现应在 Tauri 请求、结果解析和应用流程中保持 `screen_awareness` 为唯一正式命名，不得恢复旧文件。

---

## 文件职责与修改边界

- `app/config/migration_runner.py`：新增 v3→v4 一次性配置迁移；收紧 v2 旧节非法结构的失败语义。
- `app/config/settings_service.py`：只读写 `screen_awareness`，删除旧 API 与运行时回退。
- `app/config/defaults.py`：删除无调用的 `DEFAULT_PROACTIVE_*` 常量别名。
- `app/core/app_context.py`：只暴露 `screen_awareness_settings`。
- `main.py`：首启设置流程只使用新设置名。
- `app/ui/tauri_settings.py`、`tools/settings-tauri/`：设置请求、结果和前端字段统一使用 `screen_awareness_*`；旧 Qt 设置文件保持删除。
- `app/ui/history_window.py`：直接使用新历史标记常量。
- `app/agent/proactive_care.py`：删除内部兼容模块。
- `app/agent/runtime.py`：不再接受 `proactive_check` 事件，只接受 `screen_awareness_check` 与提醒事件。
- `app/llm/prompt_templates.py`、`app/llm/prompts/recipes.py`、`app/llm/prompts/blocks.py`：将屏幕感知 prompt helper 设为唯一正式命名，删除旧函数别名。
- `app/ui/pet_window.py`：删除旧 proactive 流程、等待镜像、事件镜像和记忆整理镜像字段。
- `tests/unit/test_migration_runner.py`、`tests/unit/test_settings_service.py`、`tests/unit/test_config.py`：锁定配置迁移与兼容边界。
- `tests/unit/test_agent_runtime.py`、`tests/unit/test_prompt_templates.py`、`tests/unit/test_resource_manager.py`：锁定事件入口、prompt API 与 worker finalizer 顺序。
- `tests/integration/test_agent_core.py`、`tests/integration/test_chat_worker.py`：把有效的主动事件行为测试迁移到 `screen_awareness_check`。
- `tests/ui/test_pet_window.py`、`tests/ui/test_sakura_mobile_ui.py`、`tests/ui/test_history_window.py`：通过当前生产入口测试 UI、主动事件和记忆整理，不再要求生产方法支持半初始化测试对象。

不得修改 `third_party/`、`tools/mcp/`、角色资源、运行时缓存和用户已有的 `link_sakura_runtime_tts.bat`。

## 每个提交的强制审查门

每个任务完成并提交后，必须先完成以下审查，再进入下一任务：

```powershell
git show --check --stat HEAD
git diff HEAD^..HEAD -- app plugins tests main.py
```

随后进行两阶段 review：

1. 规格审查：逐项核对本任务的行为锁定、删除目标、兼容边界和测试命令。
2. 代码质量审查：重点寻找仍可删除的重复状态、动态回退、无效测试桩、重复日志和不必要新增抽象。

若 review 发现问题，先修改、重跑本任务全部测试并执行：

```powershell
git add -u -- app plugins tests main.py
git commit --amend --no-edit
git show --check --stat HEAD
```

只有审查无剩余问题时才开始下一任务。不得用后续提交掩盖当前提交的问题。

### Task 1: 补齐 pytest 开发环境并建立可信基线

**Files:**
- Read: `requirements-dev.txt`
- Read: `pytest.ini`
- Verify only: repository test suite

- [ ] **Step 1: 安装项目声明的 pytest 插件**

Run:

```powershell
.\runtime\python.exe -m pip install -r requirements-dev.txt
```

Expected: 安装或确认存在 `pytest-qt`、`pytest-env`、`pytest-timeout`；命令退出码为 0。

- [ ] **Step 2: 确认 pytest 配置已被识别**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_migration_runner.py -q
```

Expected: 测试通过，输出中不出现 `unknown config option: env`、`qt_api`、`timeout` 或 `timeout_method`。

- [ ] **Step 3: 运行改动前全量基线**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit -q
.\runtime\python.exe -m pytest tests/integration -q
.\runtime\python.exe -m pytest tests/ui -q
.\runtime\python.exe -m pytest -q
```

Expected: 全部可运行测试通过；跳过项必须有明确的平台或可选依赖原因。若出现 native crash，保存失败命令、退出码和最小复现测试，不以简单重跑替代分析。

- [ ] **Step 4: 记录起始工作树与生产代码基准**

Run:

```powershell
git status --short
git diff --numstat e004f44..HEAD -- app plugins
```

Expected: 仅保留用户原有未跟踪文件 `link_sakura_runtime_tts.bat`；第二条命令无生产代码差异。

此任务不产生提交。

### Task 2: 增加 v3→v4 配置迁移并删除日常旧节依赖的前置条件

**Files:**
- Modify: `app/config/migration_runner.py:31-33,362-405`
- Modify: `tests/unit/test_migration_runner.py:74-93,302-369`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: 先写失败的迁移测试**

在 `tests/unit/test_migration_runner.py` 将 pending 版本期望改为 `[1, 2, 3, 4]`，并用下面的最终状态测试完整替换现有 `TestV2ToV3`。删除“迁移结束后 `proactive_care` 仍被保留”的过时断言，不继续测试已经退役的中间形态：

```python
class TestV3ToV4:
    def test_only_legacy_section_is_normalized_and_removed(self) -> None:
        base = _make_base("screen_awareness_v4_legacy")
        paths = StoragePaths(base)
        from app.config.yaml_config import save_yaml_mapping

        save_yaml_mapping(
            paths.system_config(),
            {
                CONFIG_VERSION_KEY: 3,
                "proactive_care": {
                    "enabled": "false",
                    "screen_context_enabled": "yes",
                    "check_interval_minutes": "0",
                    "cooldown_minutes": "999",
                    "screen_context_batch_limit": "4",
                },
            },
        )
        before = paths.system_config().read_bytes()

        report = MigrationRunner(base).run()
        system = load_yaml_mapping(paths.system_config())

        assert not report.failed
        assert system[CONFIG_VERSION_KEY] == 4
        assert "proactive_care" not in system
        assert system["screen_awareness"] == {
            "enabled": False,
            "screen_context_enabled": False,
            "check_interval_minutes": 1,
            "cooldown_minutes": 120,
            "screen_context_batch_limit": 4,
        }
        backups = list(paths.migration_backup_dir.rglob("system_config.yaml"))
        assert backups
        assert any(path.read_bytes() == before for path in backups)
        after = paths.system_config().read_bytes()
        second = MigrationRunner(base).run()
        assert not second.results
        assert paths.system_config().read_bytes() == after

    def test_new_section_wins_and_legacy_section_is_removed(self) -> None:
        base = _make_base("screen_awareness_v4_new_wins")
        paths = StoragePaths(base)
        from app.config.yaml_config import save_yaml_mapping

        new_section = {"check_interval_minutes": 11, "custom_key": "preserve"}
        save_yaml_mapping(
            paths.system_config(),
            {
                CONFIG_VERSION_KEY: 3,
                "proactive_care": {"check_interval_minutes": 5},
                "screen_awareness": new_section,
            },
        )

        assert not MigrationRunner(base).run().failed
        system = load_yaml_mapping(paths.system_config())
        assert system["screen_awareness"] == new_section
        assert "proactive_care" not in system

    def test_invalid_v3_legacy_section_fails_without_writing_or_advancing(self) -> None:
        base = _make_base("screen_awareness_v4_invalid")
        paths = StoragePaths(base)
        from app.config.yaml_config import save_yaml_mapping

        save_yaml_mapping(
            paths.system_config(),
            {CONFIG_VERSION_KEY: 3, "proactive_care": ["invalid"]},
        )
        before = paths.system_config().read_bytes()

        report = MigrationRunner(base).run()

        assert report.failed
        assert paths.system_config().read_bytes() == before
        assert MigrationRunner(base).current_version() == 3
        assert "proactive_care 配置节必须是对象" in report.results[0].error

    def test_invalid_new_section_fails_instead_of_deleting_legacy_data(self) -> None:
        base = _make_base("screen_awareness_v4_invalid_new")
        paths = StoragePaths(base)
        from app.config.yaml_config import save_yaml_mapping

        save_yaml_mapping(
            paths.system_config(),
            {
                CONFIG_VERSION_KEY: 3,
                "proactive_care": {"check_interval_minutes": 5},
                "screen_awareness": "invalid",
            },
        )
        before = paths.system_config().read_bytes()

        report = MigrationRunner(base).run()

        assert report.failed
        assert paths.system_config().read_bytes() == before
        assert MigrationRunner(base).current_version() == 3
        assert "screen_awareness 配置节必须是对象" in report.results[0].error

    def test_invalid_v2_legacy_section_fails_before_v3_is_written(self) -> None:
        base = _make_base("screen_awareness_v2_invalid")
        paths = StoragePaths(base)
        from app.config.yaml_config import save_yaml_mapping

        save_yaml_mapping(
            paths.system_config(),
            {CONFIG_VERSION_KEY: 2, "proactive_care": "invalid"},
        )
        before = paths.system_config().read_bytes()

        report = MigrationRunner(base).run()

        assert report.failed
        assert paths.system_config().read_bytes() == before
        assert MigrationRunner(base).current_version() == 2
        backups = list(paths.migration_backup_dir.rglob("system_config.yaml"))
        assert backups
        assert any(path.read_bytes() == before for path in backups)

    def test_valid_v2_legacy_section_reaches_normalized_v4(self) -> None:
        base = _make_base("screen_awareness_v2_valid")
        paths = StoragePaths(base)
        from app.config.yaml_config import save_yaml_mapping

        save_yaml_mapping(
            paths.system_config(),
            {
                CONFIG_VERSION_KEY: 2,
                "proactive_care": {
                    "enabled": "false",
                    "check_interval_minutes": "0",
                    "cooldown_minutes": "999",
                },
            },
        )

        assert not MigrationRunner(base).run().failed
        system = load_yaml_mapping(paths.system_config())
        assert system[CONFIG_VERSION_KEY] == 4
        assert "proactive_care" not in system
        assert system["screen_awareness"]["enabled"] is False
        assert system["screen_awareness"]["check_interval_minutes"] == 1
        assert system["screen_awareness"]["cooldown_minutes"] == 120

    def test_v2_valid_new_section_wins_over_invalid_legacy_section(self) -> None:
        base = _make_base("screen_awareness_v2_new_wins")
        paths = StoragePaths(base)
        from app.config.yaml_config import save_yaml_mapping

        save_yaml_mapping(
            paths.system_config(),
            {
                CONFIG_VERSION_KEY: 2,
                "proactive_care": "invalid",
                "screen_awareness": {},
            },
        )

        assert not MigrationRunner(base).run().failed
        system = load_yaml_mapping(paths.system_config())
        assert system[CONFIG_VERSION_KEY] == 4
        assert system["screen_awareness"] == {}
        assert "proactive_care" not in system

    def test_v4_write_version_failure_retries_without_reapplying_data(
        self,
        monkeypatch,
    ) -> None:  # type: ignore[no-untyped-def]
        base = _make_base("screen_awareness_v4_version_retry")
        paths = StoragePaths(base)
        from app.config.yaml_config import save_yaml_mapping

        save_yaml_mapping(
            paths.system_config(),
            {
                CONFIG_VERSION_KEY: 3,
                "proactive_care": {"check_interval_minutes": 5},
            },
        )
        runner = MigrationRunner(base)
        real_write_version = runner._write_version

        def fail_v4_once(version: int) -> None:
            if version == 4:
                raise OSError("simulated version write failure")
            real_write_version(version)

        monkeypatch.setattr(runner, "_write_version", fail_v4_once)
        first = runner.run()
        migrated = load_yaml_mapping(paths.system_config())

        assert first.failed
        assert migrated[CONFIG_VERSION_KEY] == 3
        assert "proactive_care" not in migrated
        assert migrated["screen_awareness"]["check_interval_minutes"] == 5

        second = MigrationRunner(base).run()
        assert not second.failed
        assert load_yaml_mapping(paths.system_config())[CONFIG_VERSION_KEY] == 4
```

在 `tests/unit/test_config.py` 的 `TestMigration` 类内增加历史 `.env` 导入回归测试，并在文件顶部导入 `load_yaml_mapping`；旧键只允许留在迁移层：

```python
def test_migrate_proactive_env_keys_to_screen_awareness(self) -> None:
    base = _make_test_dir("migrate_proactive_env")
    try:
        config_dir = base / "data" / "config"
        config_dir.mkdir(parents=True)
        env_path = base / ".env"
        env_path.write_text(
            "PROACTIVE_CARE_ENABLED=false\n"
            "PROACTIVE_CHECK_INTERVAL_MINUTES=5\n",
            encoding="utf-8",
        )
        api_yaml = config_dir / "api.yaml"
        api_yaml.write_text("llm: {}\n", encoding="utf-8")
        system_yaml = config_dir / "system_config.yaml"
        system_yaml.write_text("{}\n", encoding="utf-8")

        migrate_env_to_yaml(env_path, api_yaml, system_yaml)
        system = load_yaml_mapping(system_yaml)

        assert system["screen_awareness"]["enabled"] is False
        assert system["screen_awareness"]["check_interval_minutes"] == 5
        assert "proactive_care" not in system
    finally:
        shutil.rmtree(base, ignore_errors=True)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_migration_runner.py::TestRunnerProtocol::test_fresh_dir_version_zero_and_pending_all tests/unit/test_migration_runner.py::TestV3ToV4 tests/unit/test_config.py -q -k "proactive_env or V3ToV4 or pending_all"
```

Expected: 版本列表仍为 `[1, 2, 3]`，v4 行为不存在或旧节仍被保留，因此测试失败。

- [ ] **Step 3: 实现最小迁移**

在 `app/config/migration_runner.py` 导入 `ScreenAwarenessSettings`，把当前版本改为 4，并加入只供迁移使用的解析函数：

```python
def _migration_bool_value(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _migration_int_value(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _normalize_legacy_screen_awareness(raw: dict[str, object]) -> dict[str, bool | int]:
    defaults = ScreenAwarenessSettings()
    settings = ScreenAwarenessSettings(
        enabled=_migration_bool_value(raw.get("enabled"), defaults.enabled),
        screen_context_enabled=_migration_bool_value(
            raw.get("screen_context_enabled"),
            defaults.screen_context_enabled,
        ),
        check_interval_minutes=_migration_int_value(
            raw.get("check_interval_minutes"),
            defaults.check_interval_minutes,
        ),
        cooldown_minutes=_migration_int_value(
            raw.get("cooldown_minutes"),
            defaults.cooldown_minutes,
        ),
        screen_context_batch_limit=_migration_int_value(
            raw.get("screen_context_batch_limit"),
            defaults.screen_context_batch_limit,
        ),
    ).normalized()
    return {
        "enabled": settings.enabled,
        "screen_context_enabled": settings.screen_context_enabled,
        "check_interval_minutes": settings.check_interval_minutes,
        "cooldown_minutes": settings.cooldown_minutes,
        "screen_context_batch_limit": settings.screen_context_batch_limit,
    }
```

收紧 `_migrate_v2_to_v3()`，顺序必须是：

```python
if "screen_awareness" in data:
    if not isinstance(data["screen_awareness"], dict):
        context.backup_file(system_path)
        raise ValueError("screen_awareness 配置节必须是对象")
    return

if "proactive_care" not in data:
    context.backup_file(system_path)
    data["screen_awareness"] = {}
    save_yaml_mapping(system_path, data)
    return

legacy = data["proactive_care"]
context.backup_file(system_path)
if not isinstance(legacy, dict):
    raise ValueError("proactive_care 配置节必须是对象")
data["screen_awareness"] = _normalize_legacy_screen_awareness(legacy)
save_yaml_mapping(system_path, data)
```

这保证有效 v2 旧值在 v4 “新节优先”之前已经规范化，同时让有效新节胜过无效旧节。随后新增 v3→v4：

```python
def _migrate_v3_to_v4(context: MigrationContext) -> None:
    system_path = context.paths.system_config()
    data = load_yaml_mapping(system_path)
    if "proactive_care" not in data:
        return

    context.backup_file(system_path)
    legacy = data["proactive_care"]
    if "screen_awareness" in data:
        if not isinstance(data["screen_awareness"], dict):
            raise ValueError("screen_awareness 配置节必须是对象")
    else:
        if not isinstance(legacy, dict):
            raise ValueError("proactive_care 配置节必须是对象")
        data["screen_awareness"] = _normalize_legacy_screen_awareness(legacy)

    del data["proactive_care"]
    save_yaml_mapping(system_path, data)
```

在 `ALL_MIGRATIONS` 末尾注册 version 4。新节存在时必须原样保留，包括空字典和未知键；不得与旧节合并。

- [ ] **Step 4: 运行定点与阶段回归**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_migration_runner.py tests/unit/test_config.py -q
```

Expected: 全部通过，且没有 unknown pytest config warning。

- [ ] **Step 5: 提交并审查**

```powershell
git add app/config/migration_runner.py tests/unit/test_migration_runner.py tests/unit/test_config.py
git commit -m "refactor: migrate proactive config to screen awareness"
git show --check --stat HEAD
git diff HEAD^..HEAD -- app tests
```

Review 重点：失败路径是否保持原文件与版本；新节是否严格优先；是否错误使用 `bool("false")`；新增 helper 是否仅服务迁移且无法复用现有公开 API。发现问题后 amend。

### Task 3: 将设置栈和内部调用点统一到 screen_awareness 命名

**Files:**
- Modify: `app/config/settings_service.py:594-636`
- Modify: `app/config/defaults.py:31-42`
- Modify: `app/core/app_context.py:144-151`
- Modify: `main.py:28,363-371,502-549`
- Modify: `app/ui/settings_dialog.py:164-274,680-711,2257-2295,2440`
- Modify: `app/ui/settings/pages/sections.py:746-806`
- Modify: `app/ui/history_window.py:25,39`
- Modify: `app/ui/pet_window.py:583-585,5185-5191,5315-5325,5420-5429,5528`
- Modify: `tests/unit/test_settings_service.py`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_migration_runner.py:26-43`
- Modify: `tests/ui/test_pet_window.py`
- Modify: `tests/ui/test_history_window.py`

- [ ] **Step 1: 写失败的公开形状与行为测试**

在 `tests/unit/test_settings_service.py` 增加：

```python
def test_screen_awareness_loader_does_not_fall_back_to_proactive_care() -> None:
    service = AppSettingsService(_runtime_root("screen_awareness_no_legacy_fallback"))
    service.save_system_values(
        "proactive_care",
        {"enabled": False, "check_interval_minutes": 99},
    )

    assert service.load_screen_awareness_settings() == ScreenAwarenessSettings()


def test_settings_service_exposes_only_screen_awareness_methods() -> None:
    assert not hasattr(AppSettingsService, "load_proactive_care_settings")
    assert not hasattr(AppSettingsService, "save_proactive_care_settings")
```

在 `tests/unit/test_config.py` 增加：

```python
def test_settings_stack_has_no_runtime_proactive_care_names() -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_files = (
        "app/config/settings_service.py",
        "app/config/defaults.py",
        "app/core/app_context.py",
        "app/ui/settings_dialog.py",
        "app/ui/settings/pages/sections.py",
        "app/ui/history_window.py",
        "main.py",
    )
    for relative in runtime_files:
        source = (root / relative).read_text(encoding="utf-8")
        assert "proactive_care" not in source
        assert "proactive_" not in source
```

更新 `test_data_migration_failure_message_keeps_step_error()`：

```python
assert "原数据没有被覆盖" in message
assert "受影响功能本次可能使用默认值或暂不可用" in message
assert "兼容模式" not in message
assert "v1_to_v2: WinError 5" in message
```

将设置 UI 测试改为只使用：

```python
dialog = SettingsDialog(
    api_settings=api_settings,
    tts_settings=tts_settings,
    base_dir=root,
    screen_awareness_settings=ScreenAwarenessSettings(
        screen_context_enabled=False,
        check_interval_minutes=20,
        cooldown_minutes=10,
        screen_context_batch_limit=6,
    ),
)
assert not dialog.screen_awareness_check_interval_spin.isEnabled()
assert not dialog.screen_awareness_cooldown_spin.isEnabled()
assert not dialog.screen_awareness_batch_limit_spin.isEnabled()
```

所有 settings-service stub 只实现 `save_screen_awareness_settings()`，对话框结果只设置 `result_screen_awareness_settings`。
`_minimal_settings_window` 必须直接提供 `screen_awareness_settings` 与 `_sync_screen_awareness_timer`，不得保留旧属性或靠生产代码 fallback；该 helper 的其他设置域测试职责不在本轮扩展。
机械迁移 `tests/ui/test_pet_window.py` 当前约 76 处 `proactive_care_settings=`、7 处 `result_proactive_care_settings` 和 6 处 `save_proactive_care_settings`，统一替换为对应 `screen_awareness` 名称；不得为这些测试保留生产别名。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_settings_service.py tests/unit/test_config.py tests/ui/test_pet_window.py tests/ui/test_history_window.py -q -k "screen_awareness or settings_stack or first_run_settings or show_settings or history"
```

Expected: 旧 loader 仍回退、旧方法仍存在、UI 仍暴露 proactive 名称，因此失败。

- [ ] **Step 3: 最小化统一设置 API 与 UI 命名**

实施以下精确变化：

- `AppSettingsService.load_screen_awareness_settings()` 删除旧节回退；删除两个 proactive 方法。
- `defaults.py` 删除五个 `DEFAULT_PROACTIVE_*` 别名，不调整当前未使用的 screen 默认常量数值。
- `AppContext` 删除 `proactive_care_settings` 属性。
- `main.py` 导入 `ScreenAwarenessSettings`，首启对话框传 `screen_awareness_settings`，校验/保存 `result_screen_awareness_settings`。
- `SettingsDialog.__init__()` 删除 `proactive_care_settings` 参数与回退；删除 `result_proactive_care_settings`。
- 控件、布局和方法统一改名为：

```python
screen_awareness_enabled_check
screen_awareness_check_interval_spin
screen_awareness_cooldown_spin
screen_awareness_batch_limit_spin
screen_awareness_token_estimate_label
_screen_awareness_form_layout
_sync_screen_awareness_controls
_sync_screen_awareness_token_estimate
```

- `history_window.py` 从 `app.agent.screen_awareness` 导入 `SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER`。
- `PetWindow.__init__()` 直接读取 `context.screen_awareness_settings`。
- `PetWindow.show_settings()` 直接传 `self.screen_awareness_settings`。
- 设置完成处理直接读取 `dialog.result_screen_awareness_settings`，直接调用 `self.settings_service.save_screen_awareness_settings(result_screen_awareness_settings)`，直接调用 `_sync_screen_awareness_timer()`；删除这些位置的旧方法动态回退。
- `_format_data_migration_failure()` 删除“兼容模式继续运行”的不实描述，改为：旧文件未覆盖，受影响功能本次可能使用默认值或暂不可用，下次启动继续重试。

保留 `app/config/migrations.py` 中 `PROACTIVE_*` `.env` 键映射，它属于用户数据迁移。

- [ ] **Step 4: 运行设置栈回归**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_settings_service.py tests/unit/test_config.py -q
.\runtime\python.exe -m pytest tests/unit/test_migration_runner.py::test_data_migration_failure_message_keeps_step_error -q
.\runtime\python.exe -m pytest tests/ui/test_history_window.py tests/ui/test_pet_window.py -q
```

Expected: 全部通过。

- [ ] **Step 5: 提交并审查**

```powershell
git add app/config/settings_service.py app/config/defaults.py app/core/app_context.py main.py app/ui/settings_dialog.py app/ui/settings/pages/sections.py app/ui/history_window.py app/ui/pet_window.py tests/unit/test_settings_service.py tests/unit/test_config.py tests/unit/test_migration_runner.py tests/ui/test_pet_window.py tests/ui/test_history_window.py
git commit -m "refactor: remove screen awareness setting aliases"
git show --check --stat HEAD
git diff HEAD^..HEAD -- app tests main.py
```

Review 重点：正常设置值是否保持；是否误删历史 `.env` 键；UI 控件 signal 是否全部连接到新方法；是否仍有运行时旧设置回退。发现问题后 amend。

### Task 4: 退役 proactive_check prompt 与 AgentRuntime 兼容入口

**Files:**
- Modify: `app/llm/prompts/blocks.py:104-211`
- Modify: `app/llm/prompts/recipes.py:8-383`
- Modify: `app/llm/prompts/types.py:47`
- Modify: `app/llm/prompt_templates.py:10-65`
- Modify: `app/agent/context_orchestrator.py:50-73`
- Modify: `app/agent/memory.py:1722`
- Modify: `app/agent/runtime.py:63,497-582,1159-1215,1361-1390,2249,2323`
- Modify: `tests/unit/test_prompt_templates.py`
- Modify: `tests/unit/test_agent_runtime.py`
- Modify: `tests/integration/test_agent_core.py`
- Modify: `tests/integration/test_chat_worker.py`

- [ ] **Step 1: 把有效测试迁移到新事件名，并增加旧事件拒绝测试**

所有验证主动屏幕感知 prompt、图片批次、recent conversation、工具循环和 vision fallback 的测试改用 `screen_awareness_check`，不删除其行为断言。增加：

精确迁移：

- `tests/unit/test_agent_runtime.py`：`TestProactiveEventFlow` 改为 `TestScreenAwarenessEventFlow`；保留新事件进入工具循环测试，把旧事件测试改成下面的明确拒绝测试。
- `tests/unit/test_prompt_templates.py`：`_build_proactive_tool_prompt` 和六个 `test_proactive_*` 测试改为 `screen_awareness` 名称；事件类型字符串全部改为 `screen_awareness_check`。
- `tests/integration/test_agent_core.py`：从 `test_proactive_check_tool_prompt_uses_single_segment_heading` 到 `test_proactive_check_vision_unsupported_uses_silent_fallback` 的十二个测试逐个改名并改事件类型，断言内容保持。
- `tests/integration/test_chat_worker.py`：EventWorker progress 测试改用 `screen_awareness_check`。

```python
def test_retired_proactive_check_event_is_rejected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.agent.runtime as runtime_module

    logs = []
    monkeypatch.setattr(
        runtime_module,
        "log_event",
        lambda channel, message, payload=None, **kwargs: logs.append(
            (channel, message, payload)
        ),
    )
    client = _dummy_api_client()
    runtime = AgentRuntime(client, _dummy_system_prompt())

    with pytest.raises(ValueError, match="不支持的主动事件类型：proactive_check"):
        runtime.handle_event(AgentEvent(type="proactive_check", payload={}))
    assert not client.complete_with_tools.called
    assert (
        "AgentRuntime",
        "拒绝退役主动事件",
        {"event_type": "proactive_check", "handler": "AgentRuntime.handle_event"},
    ) in logs
```

在 prompt export 测试中只导入并断言：

```python
from app.llm.prompt_templates import (
    build_screen_awareness_check_reply_protocol,
    build_screen_awareness_check_tool_system_prefix,
    build_screen_awareness_check_tool_system_prompt,
)

import app.llm.prompt_templates as prompt_templates

for name in (
    "build_proactive_check_reply_protocol",
    "build_proactive_check_tool_system_prefix",
    "build_proactive_check_tool_system_prompt",
    "build_proactive_reply_decision_flow",
    "build_proactive_reply_examples",
    "build_proactive_rules",
    "build_proactive_scene_strategy_rules",
    "build_proactive_tool_loop_rules",
    "build_proactive_web_research_rules",
):
    assert not hasattr(prompt_templates, name)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_agent_runtime.py tests/unit/test_prompt_templates.py tests/integration/test_agent_core.py tests/integration/test_chat_worker.py -q -k "screen_awareness or proactive_check"
```

Expected: 旧事件仍被当作有效主动感知处理，且旧 prompt exports 仍存在，因此测试失败。

- [ ] **Step 3: 将 screen_awareness helper 设为唯一正式实现**

在 `blocks.py`、`recipes.py` 将 `build_proactive_check_*`、`proactive_*_block` 和 `build_proactive_*` 定义直接重命名为对应的 `screen_awareness_*` 名称，更新内部互调；删除文件末尾整组反向别名。`prompt_templates.py` 只导出新名。

把 prompt/context 的内部 mode 从 `"proactive"` 改为 `"screen_awareness"`：

```python
mode: Literal["normal", "screen_awareness"] = "normal"
```

`context_orchestrator.py` 的合法集合使用 `{"normal", "screen_awareness"}`；`memory.py` 的 procedural-memory 判断使用 `{"tool", "screen_awareness"}`。

在 `AgentRuntime.handle_event()` 使用：

```python
if event.type == "proactive_check":
    log_event(
        "AgentRuntime",
        "拒绝退役主动事件",
        {"event_type": event.type, "handler": "AgentRuntime.handle_event"},
    )
    raise ValueError(f"不支持的主动事件类型：{event.type}")
if event.type not in {"reminder_due", "screen_awareness_check"}:
    return AgentResult(reply=parse_chat_reply("未対応のイベントだよ。"))

if event.type == "screen_awareness_check":
    screen_context_allowed = bool(event.payload.get("screen_context_allowed"))
    allow_screen_observation = (
        screen_context_allowed and not messages_contain_image(event_messages)
    )
    return self._run_tool_loop(
        event_messages,
        allow_screen_observation=allow_screen_observation,
        turn_started_at=time.perf_counter(),
        screen_awareness_mode=True,
        context_source="event",
        event_type=event.type,
        event_payload=event.payload,
        initial_actions=[event_action],
        vision_unsupported_reply=_build_screen_awareness_vision_unsupported_reply(),
        progress_callback=progress_callback,
        cancel_checker=cancel_checker,
    )
```

`AgentRuntime` 内的 `proactive_mode`、`_build_proactive_tool_*`、`_build_proactive_vision_unsupported_reply`、`agent.proactive` 和 `proactive_tool_loop` 全部同步改为 `screen_awareness_*`，仅改内部命名，不改变工具循环分支。`_format_event_for_model()` 只把 `screen_awareness_check` 视为屏幕感知事件。

- [ ] **Step 4: 运行 Agent 与 prompt 回归**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_agent_runtime.py tests/unit/test_prompt_templates.py -q
.\runtime\python.exe -m pytest tests/integration/test_agent_core.py tests/integration/test_chat_worker.py -q
```

Expected: 全部通过；有效主动屏幕感知行为断言数量不减少。

- [ ] **Step 5: 提交并审查**

```powershell
git add app/llm/prompts/blocks.py app/llm/prompts/recipes.py app/llm/prompts/types.py app/llm/prompt_templates.py app/agent/context_orchestrator.py app/agent/memory.py app/agent/runtime.py tests/unit/test_prompt_templates.py tests/unit/test_agent_runtime.py tests/integration/test_agent_core.py tests/integration/test_chat_worker.py
git commit -m "refactor: retire proactive check event alias"
git show --check --stat HEAD
git diff HEAD^..HEAD -- app tests
```

Review 重点：是否只是移除旧事件入口而未改变新事件 prompt；测试是否被迁移而非删除；公开插件 SDK 是否未触及。发现问题后 amend。

### Task 5: 删除 PetWindow 旧 proactive_care 流程和兼容模块

**Files:**
- Delete: `app/agent/proactive_care.py`
- Modify: `app/ui/pet_window.py:289-313,1001-1054,3012,3432-3455,3682,3833-4023,4190,6653`
- Create: `tests/ui/conftest.py`
- Modify: `tests/ui/test_pet_window.py:2554-2645,9082-9647,10809-10989,11148,11374-11397`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: 写旧生产路径必须消失的失败测试**

先在 `tests/ui/conftest.py` 增加本轮状态域共用的真实窗口 fixture；必须调用真实 `PetWindow.__init__()`，后续 `test_pet_window.py` 与 `test_sakura_mobile_ui.py` 共同复用：

```python
from dataclasses import replace
from pathlib import Path

import pytest


def _write_pet_window_runtime_root(root: Path, QPixmap, Qt) -> None:  # type: ignore[no-untyped-def]
    config_dir = root / "data" / "config"
    character_dir = root / "characters" / "demo"
    config_dir.mkdir(parents=True)
    character_dir.mkdir(parents=True)
    (config_dir / "api.yaml").write_text(
        "llm:\n"
        "  base_url: https://api.example.com/v1\n"
        "  api_key: test-key\n"
        "  model: test-model\n"
        "tts:\n"
        "  provider: none\n"
        "  enabled: false\n",
        encoding="utf-8",
    )
    (config_dir / "characters.yaml").write_text(
        "current_character_id: demo\n",
        encoding="utf-8",
    )
    (config_dir / "system_config.yaml").write_text(
        "ui:\n"
        "  portrait_scale_percent: 100\n"
        "memory_curation:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )
    (character_dir / "card.md").write_text("system prompt", encoding="utf-8")
    portrait = QPixmap(320, 480)
    portrait.fill(Qt.GlobalColor.white)
    assert portrait.save(str(character_dir / "portrait.png"))
    (character_dir / "character.json").write_text(
        "{\n"
        '  "id": "demo",\n'
        '  "display_name": "Demo",\n'
        '  "initial_message": "hello",\n'
        '  "card": "card.md",\n'
        '  "portrait": {"default": "portrait.png"}\n'
        "}\n",
        encoding="utf-8",
    )


@pytest.fixture
def pet_window_factory(qtbot, monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from app.agent.memory import MemoryStore
    from app.core.bootstrap import build_initial_app_context
    from app.ui.pet_window import PetWindow

    monkeypatch.setattr(MemoryStore, "preload", lambda *args, **kwargs: None)
    monkeypatch.setattr(PetWindow, "_maybe_start_memory_backfill", lambda self: None)
    monkeypatch.setattr(PetWindow, "_activate_renderer_manager", lambda self: None)
    windows = []

    def create(*, startup_initializing: bool = False):  # type: ignore[no-untyped-def]
        root = tmp_path / f"pet_window_{len(windows)}"
        _write_pet_window_runtime_root(root, QPixmap, Qt)
        context = build_initial_app_context(root)
        context = replace(context, startup_initializing=startup_initializing)
        window = PetWindow(context)
        window.reminder_timer.stop()
        window.screen_awareness_timer.stop()
        qtbot.addWidget(window)
        window.show()
        windows.append(window)
        return window

    yield create

    for window in reversed(windows):
        window._shutdown_in_progress = False
        window.close_external_tools()
        window.close()


@pytest.fixture
def pet_window(pet_window_factory):  # type: ignore[no-untyped-def]
    return pet_window_factory(startup_initializing=False)


@pytest.fixture
def startup_pet_window(pet_window_factory):  # type: ignore[no-untyped-def]
    return pet_window_factory(startup_initializing=True)
```

把现有两个 startup PetWindow 测试改用 `startup_pet_window`，并从 `test_pet_window.py` 删除重复的 `_build_runtime_root_with_character()`。

在 `tests/unit/test_config.py` 增加结构断言，迁移文件明确豁免：

```python
def test_runtime_has_no_proactive_care_compatibility_path() -> None:
    root = Path(__file__).resolve().parents[2]
    assert not (root / "app/agent/proactive_care.py").exists()

    excluded = {
        root / "app/config/migration_runner.py",
        root / "app/config/migrations.py",
        root / "app/agent/runtime.py",
    }
    checked = [root / "main.py"]
    checked.extend(path for path in (root / "app").rglob("*.py") if path not in excluded)
    checked.extend((root / "plugins").rglob("*.py"))
    forbidden = (
        "LEGACY_PROACTIVE_EVENT_TYPE",
        "proactive_check",
        "ProactiveCare",
        "PROACTIVE_",
        "_check_proactive_care",
        "proactive_care_settings",
        "proactive_care_timer",
        "proactive_screen_contexts",
        "proactive_context",
        "proactive_mode",
        "build_proactive",
        "agent.proactive",
        "proactive_tool_loop",
    )
    for path in checked:
        source = path.read_text(encoding="utf-8")
        assert "proactive" not in source.lower()
        for name in forbidden:
            assert name not in source

    runtime_source = (root / "app/agent/runtime.py").read_text(encoding="utf-8")
    for name in forbidden:
        if name != "proactive_check":
            assert name not in runtime_source
    assert runtime_source.count('"proactive_check"') == 1
    assert 'if event.type == "proactive_check":' in runtime_source
    assert 'raise ValueError(f"不支持的主动事件类型：{event.type}")' in runtime_source
    runtime_without_guard = "\n".join(
        line
        for line in runtime_source.splitlines()
        if '"proactive_check"' not in line
    )
    assert "proactive" not in runtime_without_guard.lower()
```

删除 `_build_minimal_proactive_window` 与 `_build_minimal_screen_awareness_window`。按下列清单把两组测试迁移到 `pet_window` fixture，并通过一个只负责赋值测试输入的 `_configure_screen_awareness_window(window, settings)` helper 配置真实对象；该 helper 不复制任何 `PetWindow` 未绑定方法。批次、冷却、抖动、批次上限、recent conversation、禁用、用户发送清理和历史显示断言必须全部保留。

```python
def _configure_screen_awareness_window(
    pet_window,
    *,
    screen_context_enabled: bool,
    check_interval_minutes: int,
    cooldown_minutes: int,
    screen_context_batch_limit: int = 6,
):  # type: ignore[no-untyped-def]
    pet_window.screen_awareness_settings = ScreenAwarenessSettings(
        enabled=screen_context_enabled,
        screen_context_enabled=screen_context_enabled,
        check_interval_minutes=check_interval_minutes,
        cooldown_minutes=cooldown_minutes,
        screen_context_batch_limit=screen_context_batch_limit,
    )
    pet_window.worker_thread = None
    pet_window.active_event = None
    pet_window.pending_tool_action = None
    pet_window.pending_screen_observation_messages = None
    pet_window.pending_screen_observation_event = None
    pet_window.screen_observation_followup_in_progress = False
    pet_window.screen_observation_encode_thread = None
    pet_window.active_interaction_id = ""
    pet_window.last_user_activity_at = 0.0
    pet_window.last_screen_awareness_at = None
    pet_window.last_screen_awareness_context_at = None
    pet_window.screen_awareness_context_batch_started_at = None
    pet_window.screen_awareness_contexts = []
    pet_window.screen_awareness_context_dropped_count = 0
    return pet_window
```

精确迁移清单：

- 删除与 `test_screen_awareness_batches_screenshots_until_cooldown` 完全重复的 `test_proactive_care_batches_screenshots_until_cooldown`。
- `test_screen_context_cache_log_uses_summary_without_image_payload` 删除旧流程参数分支，日志期望只保留新路径的一次记录。
- 将 `test_proactive_care_event_includes_recent_conversation`、`test_proactive_care_event_reads_recent_conversation_from_history_store`、`test_proactive_recent_conversation_limits_count_and_content` 重命名并改调 `_build_screen_awareness_event()`。
- 将 `test_proactive_care_capture_interval_allows_timer_jitter`、`test_proactive_care_keeps_recent_screenshot_batch`、`test_proactive_care_uses_configured_screenshot_batch_limit`、`test_proactive_care_disabled_does_not_capture_or_send` 改调对应 `screen_awareness_*` 方法。
- 将 `test_user_activity_keeps_pending_proactive_screenshot_batch` 和 `test_send_message_clears_pending_proactive_screenshot_batch` 改为断言 `screen_awareness_contexts`，并保留用户发送时清空批次的行为。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_config.py tests/ui/test_pet_window.py tests/ui/test_history_window.py -q -k "proactive or screen_awareness or runtime_has_no"
```

Expected: 兼容模块、旧属性和旧流程仍存在，结构测试失败。

- [ ] **Step 3: 删除旧生产路径**

在 `PetWindow` 中删除：

- `LEGACY_PROACTIVE_EVENT_TYPE` 与四个 `PROACTIVE_*` 常量别名。
- 七组旧属性 getter/setter。
- 用户发送时把 `_clear_proactive_screen_context_batch("sent_user_message")` 替换为 `_clear_screen_awareness_context_batch("sent_user_message")`，保留清空待发送截图批次的行为。
- encode context 对 `"proactive_context"` 的接受。
- `_current_screen_awareness_settings()` 中旧属性 fallback，直接访问 `self.screen_awareness_settings`。
- `_check_proactive_care()` 到 `_clear_proactive_screen_context_batch()` 的完整旧流程。
- 设置保存、错误处理和事件类型判断中的旧分支。

将事件类型判断收敛为：

```python
def _is_screen_awareness_event_type(event_type: str) -> bool:
    return event_type == SCREEN_AWARENESS_EVENT_TYPE
```

删除 `app/agent/proactive_care.py`。历史窗口和测试只从 `app.agent.screen_awareness` 导入标记与设置。

- [ ] **Step 4: 运行 UI 与集成回归**

Run:

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py tests/ui/test_history_window.py tests/ui/test_sakura_mobile_ui.py -q
.\runtime\python.exe -m pytest tests/unit/test_config.py tests/unit/test_agent_runtime.py tests/integration/test_agent_core.py -q
```

Expected: 全部通过。

- [ ] **Step 5: 提交并审查**

```powershell
git add -A app/agent/proactive_care.py app/ui/pet_window.py tests/ui/conftest.py tests/ui/test_pet_window.py tests/unit/test_config.py
git commit -m "refactor: remove legacy proactive care path"
git show --check --stat HEAD
git diff HEAD^..HEAD -- app tests
```

Review 重点：新 screen-awareness timer、截图批次和健康提醒过滤是否完全保留；是否还有旧测试通过复制退役方法要求生产容错；本提交生产代码应显著净删。发现问题后 amend。

### Task 6: 删除 reply_waiting_ui_active 并让 _set_busy 成为唯一等待 UI 入口

**Files:**
- Modify: `app/ui/pet_window.py:887,1596-1613,2099-2145,3024-3042,4905-4924`
- Modify: `tests/ui/test_pet_window.py:9820-10035,10756-10808,12190-12245`

- [ ] **Step 1: 使用真实 PetWindow fixture 写失败测试**

使用 Task 5 创建的 `pet_window` fixture，增加：

```python
def test_set_busy_uses_reply_waiting_property_as_previous_state(
    pet_window,
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.activateWindow()
    pet_window.input_bar_animator.set_force_visible(True)
    pet_window.input_edit.setProperty("replyWaiting", True)
    pet_window.input_edit.setText("")
    pet_window.input_edit.setFocus()
    qtbot.waitUntil(pet_window.input_edit.hasFocus)
    assert pet_window.input_edit.hasFocus()

    pet_window._set_busy(False)

    assert pet_window.input_edit.property("replyWaiting") is False
    assert not pet_window.input_edit.hasFocus()
    assert not hasattr(pet_window, "reply_waiting_ui_active")


def test_set_busy_does_not_change_pet_ui_state(pet_window) -> None:
    from app.ui.state import PetUiState

    pet_window.ui_state.begin_speaking()
    pet_window._set_busy(False)

    assert pet_window.ui_state.state is PetUiState.SPEAKING


def test_set_busy_keeps_focus_when_waiting_ends_with_next_input(
    pet_window,
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.activateWindow()
    pet_window.input_bar_animator.set_force_visible(True)
    pet_window.input_edit.setProperty("replyWaiting", True)
    pet_window.input_edit.setText("下一句")
    pet_window.input_edit.setFocus()
    qtbot.waitUntil(pet_window.input_edit.hasFocus)

    pet_window._set_busy(False)

    assert pet_window.input_edit.hasFocus()
    assert pet_window.input_edit.property("replyWaiting") is False


def test_set_busy_preserves_startup_placeholder(startup_pet_window) -> None:
    from app.ui.pet_window import STARTUP_INITIALIZING_TEXT

    startup_pet_window._set_busy(True)

    assert startup_pet_window.input_edit.placeholderText() == STARTUP_INITIALIZING_TEXT
    assert startup_pet_window.send_button.text() == "初始化"
    assert startup_pet_window.input_edit.property("replyWaiting") is not True
```

第一个测试签名加入 `qtbot`。参数化桌宠点击必须使用完整测试：

```python
@pytest.mark.parametrize(
    "busy_state",
    ("worker", "encoding", "pending_chat", "pending_event", "idle"),
)
def test_pet_click_reads_derived_worker_busy_state(
    pet_window,
    qtbot,
    busy_state: str,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.worker_thread = None
    pet_window.screen_observation_followup_in_progress = False
    pet_window.pending_screen_observation_messages = None
    pet_window.pending_screen_observation_event = None
    if busy_state == "worker":
        pet_window.worker_thread = object()
    elif busy_state == "encoding":
        pet_window.screen_observation_followup_in_progress = True
    elif busy_state == "pending_chat":
        pet_window.pending_screen_observation_messages = [{"role": "user", "content": "x"}]
    elif busy_state == "pending_event":
        pet_window.pending_screen_observation_event = AgentEvent(
            type="screen_awareness_check",
            payload={},
        )

    pet_window.activateWindow()
    pet_window.input_edit.clearFocus()
    assert not pet_window.input_edit.hasFocus()
    pet_window._handle_pet_click()

    if busy_state == "idle":
        qtbot.waitUntil(pet_window.input_edit.hasFocus)
        assert pet_window.input_edit.hasFocus()
    else:
        assert not pet_window.input_edit.hasFocus()
    pet_window.worker_thread = None
    pet_window.screen_observation_followup_in_progress = False
    pet_window.pending_screen_observation_messages = None
    pet_window.pending_screen_observation_event = None
```

删除当前 `MinimalBusyWindow`、`MinimalReplyWaitingWindow` 以及为 `_input_bar_pinned` 注入 `reply_waiting_ui_active` 的测试结构；等待 UI、点击和焦点行为全部通过真实 `pet_window` fixture 驱动。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py -q -k "set_busy or reply_waiting or pet_click or input_bar_pinned"
```

Expected: `reply_waiting_ui_active` 仍存在，结构断言失败。

- [ ] **Step 3: 实现删除优先的等待态收敛**

删除字段初始化，把方法改为：

```python
def _sync_reply_waiting_ui(self, waiting: bool) -> None:
    if self.startup_initializing:
        return
    was_waiting = bool(self.input_edit.property("replyWaiting"))
    self.input_edit.setPlaceholderText(
        self._reply_waiting_placeholder_text()
        if waiting
        else self._normal_input_placeholder_text()
    )
    self._set_widget_dynamic_property(self.input_edit, "replyWaiting", waiting)
    self._set_widget_dynamic_property(self.send_button, "replyWaiting", waiting)
    if waiting or was_waiting:
        self._release_empty_input_focus_after_reply_waiting()
    self.input_bar_animator.sync()
```

`_release_empty_input_focus_after_reply_waiting()` 直接访问真实 `QLineEdit`：非空直接 return；有焦点时 `clearFocus()`。`_set_busy()` 在非 startup 分支直接调用 `_sync_reply_waiting_ui(busy)`，不动态查找。`_show_waiting_reply_placeholder()` 删除等待 UI 直接写入，只保留字幕等待动效。

`_handle_pet_click()` 用以下实际状态判断是否允许聚焦：

```python
worker_busy = (
    self.worker_thread is not None
    or self.screen_observation_followup_in_progress
    or self.pending_screen_observation_messages is not None
    or self.pending_screen_observation_event is not None
)
if not worker_busy:
    self.input_bar_animator.set_force_visible(True)
    self.input_edit.setFocus()
    self.input_bar_animator.set_force_visible(False)
```

删除只为 `reply_waiting_ui_active` 存在的 `_input_bar_pinned` 测试赋值。
删除 `_build_minimal_manual_screenshot_window`，将其四个调用点改用真实 `pet_window` 与下面的配置 helper。等待动效在这些消息组装测试中显式隔离，其行为由本任务 `_set_busy` 测试负责：

```python
def _configure_manual_screenshot_window(
    pet_window,
    monkeypatch,
    text: str,
):  # type: ignore[no-untyped-def]
    requests = []
    history = []
    pet_window.input_edit.setText(text)
    pet_window.pending_manual_screen_observation = ScreenObservation(
        data_url="data:image/jpeg;base64,manual",
        width=320,
        height=180,
        captured_at="2026-05-31T12:00:00+08:00",
        screen_name="manual-selection",
    )
    pet_window.messages = []
    pet_window.active_interaction_id = ""
    monkeypatch.setattr(pet_window, "_mark_user_activity", lambda: None)
    monkeypatch.setattr(
        pet_window,
        "_begin_interaction",
        lambda source: setattr(pet_window, "active_interaction_id", source),
    )
    monkeypatch.setattr(pet_window, "_log_interaction_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(pet_window, "_record_history", lambda *args: history.append(args))
    monkeypatch.setattr(pet_window, "_show_waiting_reply_placeholder", lambda: None)
    monkeypatch.setattr(pet_window, "_start_chat_worker", requests.append)
    monkeypatch.setattr(pet_window, "_update_manual_screenshot_button", lambda: None)
    monkeypatch.setattr(pet_window, "_collapse_auto_fit_bubble_height", lambda: None)
    return requests, history
```

迁移 `test_send_message_clears_pending_proactive_screenshot_batch`（同步改为 screen-awareness 名称）、`test_manual_screenshot_empty_input_sends_default_text`、`test_manual_screenshot_text_input_records_marker_without_image_data` 和 `test_send_message_injects_runtime_event_context_before_user_message`，每个测试接收 `pet_window, monkeypatch` 并直接调用真实 `pet_window.send_message`。

- [ ] **Step 4: 运行定点与 UI 状态回归**

Run:

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py -q -k "set_busy or reply_waiting or pet_click or input_bar_pinned"
.\runtime\python.exe -m pytest tests/ui/test_ui_state.py tests/ui/test_pet_window.py -q
rg -n "= PetWindow\._(set_busy|set_reply_waiting_ui|sync_reply_waiting_ui|send_message|show_waiting_reply_placeholder|record_user_message)" tests/ui
```

Expected: pytest 全部通过；启动初始化仍显示“初始化”，busy 与 SPEAKING 状态相互独立；`rg` 无输出。

- [ ] **Step 5: 提交并审查**

```powershell
git add app/ui/pet_window.py tests/ui/test_pet_window.py
git commit -m "refactor: consolidate reply waiting state"
git show --check --stat HEAD
git diff HEAD^..HEAD -- app tests
```

Review 重点：`_set_busy(False)` 仍只在 ResourceManager finalizer 后的 cleanup 原时点发生；startup placeholder 未被普通 placeholder 覆盖；没有新增单独 busy 状态。发现问题后 amend。

### Task 7: 让 active_event 成为主动事件唯一状态

**Files:**
- Modify: `app/ui/pet_window.py:642,656-659,3276-3356,3650-3658,4027-4199,4240-4270,4699-4700,5884-5922`
- Modify: `tests/ui/test_pet_window.py:1600-1710,10845-10920`
- Modify: `tests/ui/test_sakura_mobile_ui.py:192-239`
- Modify: `tests/unit/test_resource_manager.py:89-145`

- [ ] **Step 1: 写 payload 生命周期与 finalizer 顺序失败测试**

使用 Task 5 的真实 fixture，增加：

```python
def test_reminder_event_error_uses_active_event_payload(pet_window, monkeypatch) -> None:
    pet_window.active_event = AgentEvent(
        type="reminder_due",
        payload={"id": "reminder-1", "text": "喝水"},
    )
    completed: list[str] = []
    consumed: list[AgentResult] = []
    monkeypatch.setattr(pet_window, "_mark_reminder_completed", completed.append)
    monkeypatch.setattr(pet_window, "_consume_agent_result", consumed.append)

    pet_window._handle_event_error("network error")

    assert pet_window.active_event is None
    assert completed == ["reminder-1"]
    assert consumed[0].reply.segments[0].translation == "到时间了：喝水"
    for name in ("active_event_type", "active_reminder_id", "active_reminder_text"):
        assert not hasattr(pet_window, name)


def test_reminder_event_reply_marks_payload_id_after_consuming_result(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    event = AgentEvent(
        type="reminder_due",
        payload={"id": "reminder-1", "text": "喝水"},
    )
    result = AgentResult(reply=ChatReply([ChatSegment("時間だよ。", translation="到时间了。")]))
    order = []
    pet_window.active_event = event
    monkeypatch.setattr(
        pet_window,
        "_queue_event_screen_observation_followup",
        lambda result, active_event: False,
    )
    monkeypatch.setattr(
        pet_window,
        "_filter_screen_awareness_reply",
        lambda current, active_event: current,
    )

    def consume(current: AgentResult) -> None:
        assert pet_window.active_event is None
        assert current is result
        order.append("consume")

    def complete(reminder_id: str) -> None:
        assert pet_window.active_event is None
        order.append(("complete", reminder_id))

    monkeypatch.setattr(pet_window, "_consume_agent_result", consume)
    monkeypatch.setattr(pet_window, "_mark_reminder_completed", complete)

    pet_window._handle_event_reply(result)

    assert order == ["consume", ("complete", "reminder-1")]


def test_due_reminder_passes_single_agent_event_argument(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    pet_window.reminder_store = SimpleNamespace(
        due_reminders=lambda: [
            {
                "id": "reminder-1",
                "text": "喝水",
                "trigger_at": "2026-07-11T12:00:00+08:00",
            }
        ]
    )
    events = []
    monkeypatch.setattr(pet_window, "_run_event_worker", events.append)

    pet_window._check_due_reminders()

    assert events == [
        AgentEvent(
            type="reminder_due",
            payload={
                "id": "reminder-1",
                "text": "喝水",
                "trigger_at": "2026-07-11T12:00:00+08:00",
            },
        )
    ]


def test_due_reminder_does_not_start_while_active_event_exists(pet_window) -> None:
    class ReminderStore:
        def due_reminders(self):  # type: ignore[no-untyped-def]
            raise AssertionError("active event 应在读取提醒前阻止本轮检查")

    pet_window.active_event = AgentEvent(type="screen_awareness_check", payload={})
    pet_window.reminder_store = ReminderStore()

    pet_window._check_due_reminders()


def test_screen_awareness_does_not_start_while_active_event_exists(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.active_event = AgentEvent(type="reminder_due", payload={"id": "r1"})
    pet_window.input_edit.clear()
    pet_window.speech_timer.stop()
    pet_window.active_interaction_id = ""
    monkeypatch.setattr(pet_window, "_screen_awareness_context_allowed", lambda: True)
    monkeypatch.setattr(
        pet_window.subtitle_controller,
        "current_segment_in_progress",
        lambda: False,
    )

    assert not pet_window._can_run_screen_awareness()


def test_cleanup_worker_restarts_pending_event_from_payload_only(
    pet_window,
    monkeypatch,
) -> None:
    event = AgentEvent(
        type="reminder_due",
        payload={"id": "reminder-1", "text": "喝水", "screen_context": {}},
    )
    pet_window.pending_screen_observation_event = event
    restarted: list[AgentEvent] = []
    monkeypatch.setattr(pet_window, "_run_event_worker", restarted.append)

    pet_window._cleanup_worker()

    assert restarted == [event]
    assert not hasattr(pet_window, "pending_screen_observation_event_reminder_id")
```

在 `tests/unit/test_resource_manager.py` 增加：

```python
def test_resource_finalizer_nulls_owner_before_business_callback() -> None:
    _qt_app_or_skip()
    owner = _OwnerStub()
    thread = _ThreadStub(running=True, wait_result=True)
    worker = _WorkerStub()
    owner.t, owner.w = thread, worker
    seen = []
    resource = QtWorkerResource(
        ResourceManager(),
        thread,
        worker,
        owner=owner,
        thread_attr="t",
        worker_attr="w",
        on_finished=lambda: seen.append((owner.t, owner.w)),
    )

    resource._on_thread_finished()

    assert seen == [(None, None)]
```

同时强化现有真实 QThread 测试：

```python
seen = []
res = mgr.spawn_qt_worker(
    worker,
    parent=owner,
    owner=owner,
    thread_attr="worker_thread",
    worker_attr="the_worker",
    quit_on=[worker.finished],
    on_finished=lambda: seen.append(
        (owner.worker_thread, owner.the_worker)
    ),
    label="worker_thread",
)
_spin_until(lambda: bool(seen))
assert seen == [(None, None)]
assert res not in mgr._resources
```

增加两种 follow-up 竞态测试。共用：

```python
def _event_followup_inputs() -> tuple[AgentEvent, ScreenObservation]:
    return (
        AgentEvent(
            type="screen_awareness_check",
            payload={"id": "reminder-1", "text": "喝水"},
        ),
        ScreenObservation(
            data_url="data:image/jpeg;base64,screen",
            width=320,
            height=180,
            captured_at="2026-07-11T12:00:01+08:00",
            screen_name="primary",
        ),
    )
```

原 event worker 先 finalize：

```python
def test_event_followup_restarts_once_when_worker_finishes_before_encode(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    event, observation = _event_followup_inputs()
    restarted = []
    busy = []
    pet_window.worker_thread = None
    pet_window.screen_observation_followup_in_progress = True
    monkeypatch.setattr(pet_window, "_run_event_worker", restarted.append)
    monkeypatch.setattr(pet_window, "_set_busy", busy.append)
    monkeypatch.setattr(pet_window, "_record_history", lambda *args: None)
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: callback(),
    )

    pet_window._cleanup_worker()
    assert restarted == []
    assert busy == []

    pet_window._finish_event_screen_observation_followup(
        {"event": event, "reason": "看看屏幕"},
        observation,
    )

    assert len(restarted) == 1
    assert restarted[0].payload["id"] == "reminder-1"
    assert busy == []
```

编码先完成：

```python
def test_event_followup_waits_for_worker_finalizer_when_encode_finishes_first(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    event, observation = _event_followup_inputs()
    restarted = []
    busy = []
    pet_window.worker_thread = object()
    pet_window.screen_observation_followup_in_progress = True
    monkeypatch.setattr(pet_window, "_run_event_worker", restarted.append)
    monkeypatch.setattr(pet_window, "_set_busy", busy.append)
    monkeypatch.setattr(pet_window, "_record_history", lambda *args: None)

    pet_window._finish_event_screen_observation_followup(
        {"event": event, "reason": "看看屏幕"},
        observation,
    )
    assert restarted == []
    assert busy == []

    pet_window.worker_thread = None
    pet_window._cleanup_worker()

    assert len(restarted) == 1
    assert restarted[0].payload["id"] == "reminder-1"
    assert busy == []
```

两个测试通过 helper 获得各自的 `event` 与 `observation`，避免跨测试共享可变状态。把三个现有事件测试完整迁移到真实 fixture：

```python
def test_silent_screen_awareness_reply_ends_interaction(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from app.ui.pet_window import TRANSIENT_PROGRESS_MESSAGE_KEY

    pet_window.messages = [
        {"role": "assistant", "content": "途中", TRANSIENT_PROGRESS_MESSAGE_KEY: True}
    ]
    pet_window.active_event = AgentEvent(type="screen_awareness_check", payload={})
    pet_window.active_interaction_id = "interaction-1"
    ended = []
    consumed = []
    monkeypatch.setattr(
        pet_window,
        "_queue_event_screen_observation_followup",
        lambda result, event: False,
    )
    monkeypatch.setattr(
        pet_window,
        "_filter_screen_awareness_reply",
        lambda result, event: result,
    )
    monkeypatch.setattr(pet_window, "_consume_agent_result", consumed.append)

    def end(outcome: str) -> None:
        ended.append(outcome)
        pet_window.active_interaction_id = ""

    monkeypatch.setattr(pet_window, "_end_interaction", end)

    pet_window._handle_event_reply(AgentResult(reply=ChatReply([]), actions=[]))

    assert pet_window.messages == []
    assert pet_window.active_event is None
    assert consumed == []
    assert ended == ["event_silent"]


def test_event_error_cleans_transient_progress_during_shutdown(pet_window) -> None:
    from app.ui.pet_window import TRANSIENT_PROGRESS_MESSAGE_KEY

    pet_window.active_event = AgentEvent(type="custom", payload={})
    pet_window.messages = [
        {"role": "assistant", "content": "途中", TRANSIENT_PROGRESS_MESSAGE_KEY: True}
    ]
    pet_window._shutdown_in_progress = True
    try:
        pet_window._handle_event_error("late error")
    finally:
        pet_window._shutdown_in_progress = False

    assert pet_window.messages == []
    assert pet_window.active_event is None


def test_screen_awareness_event_error_ends_interaction(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.active_event = AgentEvent(type="screen_awareness_check", payload={})
    pet_window.active_interaction_id = "interaction-3"
    ended = []

    def end(outcome: str) -> None:
        ended.append(outcome)
        pet_window.active_interaction_id = ""

    monkeypatch.setattr(pet_window, "_end_interaction", end)

    pet_window._handle_event_error("API 请求超时。")

    assert pet_window.active_event is None
    assert pet_window.active_interaction_id == ""
    assert ended == ["screen_awareness_error_silent"]
```

`tests/ui/test_sakura_mobile_ui.py` 增加 `from app.agent import AgentEvent`；四个 `_mobile_chat_busy` 测试使用共享 fixture，不再复制未绑定方法：

```python
def test_mobile_chat_ignores_background_memory_curation(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.memory_curation_thread = object()
    pet_window.active_event = None
    monkeypatch.setattr(
        pet_window.subtitle_controller,
        "is_reply_sequence_active",
        lambda: False,
    )

    assert not pet_window._mobile_chat_busy()
    pet_window.memory_curation_thread = None


def test_mobile_chat_allows_stale_interaction_id_after_reply_sequence_done(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.active_interaction_id = "interaction-stale"
    pet_window.active_event = None
    monkeypatch.setattr(
        pet_window.subtitle_controller,
        "is_reply_sequence_active",
        lambda: False,
    )

    assert not pet_window._mobile_chat_busy()


def test_mobile_chat_is_busy_while_reply_sequence_active(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.active_event = None
    monkeypatch.setattr(
        pet_window.subtitle_controller,
        "is_reply_sequence_active",
        lambda: True,
    )

    assert pet_window._mobile_chat_busy()


def test_mobile_chat_is_busy_while_active_event_exists(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    pet_window.active_event = AgentEvent(type="reminder_due", payload={"id": "r1"})
    monkeypatch.setattr(
        pet_window.subtitle_controller,
        "is_reply_sequence_active",
        lambda: False,
    )

    assert pet_window._mobile_chat_busy()
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_resource_manager.py tests/ui/test_pet_window.py tests/ui/test_sakura_mobile_ui.py -q -k "event or reminder or screen_awareness or finalizer or mobile_chat"
```

Expected: 镜像字段仍存在，`_run_event_worker` 仍接收独立 reminder ID，因此失败。

- [ ] **Step 3: 删除事件镜像与并行 reminder 参数**

实施：

```python
def _run_event_worker(self, event: AgentEvent) -> None:
    if self.startup_initializing:
        return
    if self.worker_thread is not None or self.active_event is not None:
        return
    self.active_event = event
    self._set_busy(True)


def _clear_active_event(self) -> None:
    self.active_event = None
```

reply/error handler 首先捕获局部 `event`，随后清空状态；类型、提醒 ID 和文本只从 `event.type` 与 `event.payload` 读取。shutdown guard 保持在清除 transient progress 和 active event 之后，避免 late callback 留脏状态。

follow-up 编码 context、pending event 与 cleanup 只携带 `AgentEvent`；删除 `pending_screen_observation_event_reminder_id`。所有 busy guard 改为 `active_event is not None`。到期提醒调用：

具体包括 `_can_run_screen_awareness()`、`_check_due_reminders()` 与 `_mobile_chat_busy()`；不得只修改 `_run_event_worker()` 自身。

```python
self._run_event_worker(
    AgentEvent(
        type="reminder_due",
        payload={
            "id": reminder_id,
            "text": reminder_text,
            "trigger_at": reminder_trigger_at,
        },
    )
)
```

`_mobile_chat_busy()` 直接调用必需的 `self.subtitle_controller.is_reply_sequence_active()`，busy 条件使用 `self.active_event is not None`；删除这一路的动态方法查找和旧镜像字段判断。

- [ ] **Step 4: 运行定点、时序与阶段回归**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_resource_manager.py -q
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py tests/ui/test_sakura_mobile_ui.py -q -k "event or reminder or screen_awareness or mobile_chat"
.\runtime\python.exe -m pytest tests/ui/test_ui_state.py tests/ui/test_pet_window.py tests/ui/test_sakura_mobile_ui.py tests/unit/test_resource_manager.py -q
rg -n "= PetWindow\._(handle_event_reply|handle_event_error|clear_active_event|cleanup_worker|mobile_chat_busy)" tests/ui
```

Expected: pytest 全部通过；ResourceManager 仍先清 owner worker 属性，再进入 `_cleanup_worker()` 重启 pending event；`rg` 无输出。

- [ ] **Step 5: 提交并审查**

```powershell
git add app/ui/pet_window.py tests/ui/test_pet_window.py tests/ui/test_sakura_mobile_ui.py tests/unit/test_resource_manager.py
git commit -m "refactor: consolidate active event state"
git show --check --stat HEAD
git diff HEAD^..HEAD -- app tests
```

Review 重点：follow-up 编码先完成或原 worker 先完成两种时序均可重启；提醒失败仍生成同一兜底文案并标记完成；不得提前 `_set_busy(False)`。发现问题后 amend。

### Task 8: 用不可变运行上下文替代记忆整理镜像字段

**Files:**
- Modify: `app/ui/pet_window.py:1-40,607-610,4378-4521,6549-6565`
- Modify: `tests/ui/test_pet_window.py:208-306,882-1170`

- [ ] **Step 1: 写冻结上下文、缺失上下文和清理顺序失败测试**

删除 `MinimalMemoryWindow`，保留不复制生产方法的 `_MemoryRetryHistoryStore`。增加真实窗口配置 helper：

```python
def _configure_memory_curation_window(
    pet_window,
    tmp_path,
    *,
    trigger_turns: int = 3,
    entries=None,
):  # type: ignore[no-untyped-def]
    from app.agent.memory_curator import MemoryCurationSettings, MemoryCurationState
    from app.storage.chat_history import ChatHistoryEntry

    if entries is None:
        entries = [
            ChatHistoryEntry("2026-06-28T21:09:14+08:00", "user", "第一轮"),
            ChatHistoryEntry("2026-06-28T21:09:20+08:00", "assistant", "第二轮"),
        ]
    pet_window.memory_curation_settings = MemoryCurationSettings(
        enabled=True,
        trigger_turns=trigger_turns,
    )
    pet_window.memory_curation_state = MemoryCurationState(
        tmp_path / "memory_curation_state.json"
    )
    pet_window.history_store = _MemoryRetryHistoryStore(entries)
    pet_window.worker_thread = None
    pet_window.memory_curation_thread = None
    pet_window.pending_tool_action = None
    pet_window.pending_screen_observation_messages = None
    pet_window.pending_screen_observation_event = None
    pet_window.screen_observation_followup_in_progress = False
    pet_window.memory_curation_run = None
    pet_window._auto_memory_curation_failure_attempts = 0
    pet_window._suppress_auto_memory_curation_restart = False
    return pet_window


def _set_memory_curation_run(
    pet_window,
    *,
    mode: str = "auto",
    character_id: str | None = None,
    target_history_count: int = 8,
    consumed_turns: int = 3,
):  # type: ignore[no-untyped-def]
    from app.ui.pet_window import _MemoryCurationRunContext

    run = _MemoryCurationRunContext(
        mode=mode,
        character_id=character_id or pet_window.character_profile.id,
        target_history_count=target_history_count,
        consumed_turns=consumed_turns,
    )
    pet_window.memory_curation_run = run
    return run
```

增加：

```python
def test_memory_curation_run_context_is_frozen() -> None:
    from dataclasses import FrozenInstanceError
    from app.ui.pet_window import _MemoryCurationRunContext

    run = _MemoryCurationRunContext("auto", "demo", 8, 3)
    with pytest.raises(FrozenInstanceError):
        run.mode = "backfill"  # type: ignore[misc]


def test_start_memory_curation_sets_context_before_spawning_worker(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from app.storage.chat_history import ChatHistoryEntry
    from app.ui.pet_window import _MemoryCurationRunContext

    _configure_memory_curation_window(pet_window, tmp_path)
    captured = {}

    def capture(worker, **kwargs):  # type: ignore[no-untyped-def]
        captured["run_at_spawn"] = pet_window.memory_curation_run
        captured["worker"] = worker
        captured["kwargs"] = kwargs

    monkeypatch.setattr(
        pet_window.resource_manager,
        "spawn_qt_worker",
        capture,
    )
    started_prompt = pet_window.system_prompt
    entries = [
        ChatHistoryEntry("2026-07-11T10:00:00+08:00", "user", "旧角色对话")
    ]

    pet_window._start_memory_curation(
        entries,
        mode="auto",
        target_history_count=8,
        consumed_turns=3,
    )

    run = pet_window.memory_curation_run
    assert run == _MemoryCurationRunContext(
        mode="auto",
        character_id=pet_window.character_profile.id,
        target_history_count=8,
        consumed_turns=3,
    )
    assert captured["run_at_spawn"] is run
    pet_window.memory_store.set_scope("new-character")
    pet_window.memory_curator.set_system_prompt("新角色人格卡")
    assert captured["worker"].curator.system_prompt == started_prompt
    assert captured["worker"].curator.memory_store.scope_id == run.character_id


def test_start_memory_curation_clears_context_when_spawn_fails(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from app.storage.chat_history import ChatHistoryEntry

    _configure_memory_curation_window(pet_window, tmp_path)

    def fail_spawn(worker, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(
        pet_window.resource_manager,
        "spawn_qt_worker",
        fail_spawn,
    )

    with pytest.raises(RuntimeError, match="spawn failed"):
        pet_window._start_memory_curation(
            [ChatHistoryEntry("2026-07-11T10:00:00+08:00", "user", "对话")],
            mode="auto",
            target_history_count=1,
            consumed_turns=1,
        )

    assert pet_window.memory_curation_run is None


def test_memory_curation_finished_without_run_context_logs_state_error(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from app.agent.memory_curator import MemoryCurationResult
    import app.ui.pet_window as pet_window_module

    _configure_memory_curation_window(pet_window, tmp_path)
    logs = []
    monkeypatch.setattr(
        pet_window_module,
        "log_event",
        lambda channel, message, payload=None, **kwargs: logs.append(
            (channel, message, payload)
        ),
    )
    pet_window.memory_curation_run = None
    pet_window._auto_memory_curation_failure_attempts = 2
    pet_window._suppress_auto_memory_curation_restart = True
    before = pet_window.memory_curation_state.snapshot()

    pet_window._handle_memory_curation_finished(
        MemoryCurationResult(processed_entries=3)
    )

    assert pet_window.memory_curation_state.snapshot() == before
    assert pet_window._auto_memory_curation_failure_attempts == 2
    assert pet_window._suppress_auto_memory_curation_restart is True
    assert (
        "Memory",
        "记忆整理回调缺少运行上下文",
        {"callback": "finished"},
    ) in logs


def test_memory_curation_failed_without_run_context_skips_retry(
    pet_window,
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    _configure_memory_curation_window(pet_window, tmp_path)
    for _ in range(3):
        pet_window.memory_curation_state.increment_pending_turns()
    before = pet_window.memory_curation_state.snapshot()
    pet_window._auto_memory_curation_failure_attempts = 1
    pet_window.memory_curation_run = None
    logs = []
    messages = []
    monkeypatch.setattr(
        pet_window_module,
        "log_event",
        lambda channel, message, payload=None, **kwargs: logs.append(
            (channel, message, payload)
        ),
    )
    monkeypatch.setattr(
        pet_window.subtitle_controller,
        "show_text_immediately",
        messages.append,
    )

    pet_window._handle_memory_curation_failed("network error")

    assert pet_window.memory_curation_state.snapshot() == before
    assert pet_window._auto_memory_curation_failure_attempts == 1
    assert pet_window._suppress_auto_memory_curation_restart is False
    assert messages == []
    assert (
        "Memory",
        "记忆整理回调缺少运行上下文",
        {"callback": "failed"},
    ) in logs


def test_memory_curation_cleanup_clears_context_before_auto_restart(
    pet_window,
    monkeypatch,
) -> None:
    from app.ui.pet_window import _MemoryCurationRunContext
    import app.ui.pet_window as pet_window_module

    observed = []
    pet_window.memory_curation_run = _MemoryCurationRunContext("auto", "demo", 8, 3)
    monkeypatch.setattr(
        pet_window,
        "_maybe_start_auto_memory_curation",
        lambda: observed.append(pet_window.memory_curation_run),
    )
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda _delay, callback: callback(),
    )

    pet_window._cleanup_memory_curation_worker()

    assert observed == [None]


def test_memory_curation_cleanup_during_shutdown_clears_without_restart(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import app.ui.pet_window as pet_window_module

    _set_memory_curation_run(pet_window)
    timers = []
    monkeypatch.setattr(
        pet_window_module.QTimer,
        "singleShot",
        lambda delay, callback: timers.append((delay, callback)),
    )
    pet_window._shutdown_in_progress = True
    try:
        pet_window._cleanup_memory_curation_worker()
    finally:
        pet_window._shutdown_in_progress = False

    assert pet_window.memory_curation_run is None
    assert timers == []


def test_memory_curation_late_callbacks_during_shutdown_skip_context_error(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from app.agent.memory_curator import MemoryCurationResult
    import app.ui.pet_window as pet_window_module

    logs = []
    monkeypatch.setattr(
        pet_window_module,
        "log_event",
        lambda channel, message, payload=None, **kwargs: logs.append(message),
    )
    pet_window.memory_curation_run = None
    pet_window._shutdown_in_progress = True
    try:
        pet_window._handle_memory_curation_finished(
            MemoryCurationResult(processed_entries=1)
        )
        pet_window._handle_memory_curation_failed("late error")
    finally:
        pet_window._shutdown_in_progress = False

    assert "记忆整理回调缺少运行上下文" not in logs


def test_apply_character_keeps_inflight_memory_curation_context(
    pet_window,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    run = _set_memory_curation_run(pet_window)
    next_profile = replace(
        pet_window.character_profile,
        id="character-b",
        display_name="Character B",
    )
    monkeypatch.setattr(pet_window, "_emit_plugin_event", lambda *args, **kwargs: None)

    pet_window._apply_character(next_profile)

    assert pet_window.memory_curation_run is run
    pet_window.memory_curation_run = None
```

逐个迁移既有测试，全部先调用 `_configure_memory_curation_window()`：

- 用上面的 `test_start_memory_curation_sets_context_before_spawning_worker` 完整替换旧 `test_start_memory_curation_snapshots_prompt_and_scope` 及其复制 `_start_memory_curation` 的 `MinimalWindow`；必须保留父 `memory_store` scope 和父 curator prompt 改变后，已创建 worker 仍持有启动时 snapshot 的断言。

- `test_auto_memory_curation_failure_retries_first_two_attempts`：每次失败前 `_set_memory_curation_run(consumed_turns=9)`；handler 后 context 仍是同一对象，cleanup 后为 `None`，两次各排队一个 timer。
- `test_auto_memory_curation_success_resets_failure_count`：run 使用 `target_history_count=8, consumed_turns=3`；完成后 processed=8、pending=0、attempt=0、suppress=False，context 仍保留到 cleanup。
- `test_auto_memory_curation_finish_after_character_switch_skips_progress`：run 的 `character_id="character-a"`，当前 profile 改为 `character-b`；完成后 processed/pending 不变，context 仍保留。
- `test_auto_memory_curation_can_start_after_next_trigger_turns`：第三次失败与 cleanup 后不排队；再记录两轮后只排队一次，并启动 `mode="auto", consumed_turns=2`。
- `test_auto_memory_choices_empty_failures_stop_after_three_requests`：测试内 fake start 每次创建新的 `_MemoryCurationRunContext` 后调用 failed/cleanup；请求次数严格等于 `MAX_AUTO_RETRY_ATTEMPTS`。

同角色第三次失败使用完整断言：

```python
import app.ui.pet_window as pet_window_module

_configure_memory_curation_window(pet_window, tmp_path)
for _ in range(9):
    pet_window.memory_curation_state.increment_pending_turns()
timers = []
monkeypatch.setattr(
    pet_window_module.QTimer,
    "singleShot",
    lambda delay, callback: timers.append((delay, callback)),
)
run = _set_memory_curation_run(pet_window, consumed_turns=9)
pet_window._auto_memory_curation_failure_attempts = 2
pet_window._handle_memory_curation_failed("insufficient_user_quota")
assert pet_window.memory_curation_state.pending_turns() == 0
assert pet_window._auto_memory_curation_failure_attempts == 0
assert pet_window._suppress_auto_memory_curation_restart is True
assert pet_window.memory_curation_run is run
pet_window._cleanup_memory_curation_worker()
assert pet_window.memory_curation_run is None
assert timers == []
```

角色切换后的第三次失败使用：

```python
import app.ui.pet_window as pet_window_module

_configure_memory_curation_window(pet_window, tmp_path)
for _ in range(9):
    pet_window.memory_curation_state.increment_pending_turns()
timers = []
monkeypatch.setattr(
    pet_window_module.QTimer,
    "singleShot",
    lambda delay, callback: timers.append((delay, callback)),
)
run = _set_memory_curation_run(
    pet_window,
    character_id="character-a",
    consumed_turns=9,
)
pet_window.character_profile = replace(
    pet_window.character_profile,
    id="character-b",
)
pet_window._auto_memory_curation_failure_attempts = 2
pet_window._handle_memory_curation_failed("insufficient_user_quota")
assert pet_window.memory_curation_state.pending_turns() == 9
assert pet_window._auto_memory_curation_failure_attempts == 0
assert pet_window._suppress_auto_memory_curation_restart is False
assert pet_window.memory_curation_run is run
pet_window._cleanup_memory_curation_worker()
assert pet_window.memory_curation_run is None
assert len(timers) == 1
```

增加结构测试：

```python
def test_pet_window_memory_curation_has_single_context(pet_window) -> None:
    import app.ui.pet_window as pet_window_module

    source = Path(pet_window_module.__file__).read_text(encoding="utf-8")
    assert pet_window.memory_curation_run is None
    for name in (
        "memory_curation_mode",
        "memory_curation_character_id",
        "memory_curation_target_history_count",
        "memory_curation_consumed_turns",
    ):
        assert name not in source
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py -q -k "memory_curation or auto_memory"
```

Expected: `_MemoryCurationRunContext` 尚不存在，测试失败。

- [ ] **Step 3: 实现单一不可变上下文**

在文件顶部加入 `from dataclasses import dataclass`，并在 `PetWindow` 前定义：

```python
@dataclass(frozen=True)
class _MemoryCurationRunContext:
    mode: str
    character_id: str
    target_history_count: int
    consumed_turns: int
```

构造器只保留：

```python
self.memory_curation_run: _MemoryCurationRunContext | None = None
```

启动 guard 与赋值顺序必须是：

```python
if (
    not entries
    or self.memory_curation_thread is not None
    or self.memory_curation_run is not None
):
    return
run = _MemoryCurationRunContext(
    mode=mode,
    character_id=self.character_profile.id,
    target_history_count=target_history_count,
    consumed_turns=consumed_turns,
)
worker_curator = self.memory_curator.snapshot(
    memory_store=self.memory_store.scoped(run.character_id),
    system_prompt=self.system_prompt,
)
worker = MemoryCurationWorker(worker_curator, entries)
self.memory_curation_run = run
try:
    self.resource_manager.spawn_qt_worker(
        worker,
        parent=self,
        owner=self,
        thread_attr="memory_curation_thread",
        worker_attr="memory_curation_worker",
        signal_bindings=[
            (worker.finished, self._handle_memory_curation_finished),
            (worker.failed, self._handle_memory_curation_failed),
        ],
        quit_on=[worker.finished, worker.failed, worker.cancelled],
        on_finished=self._cleanup_memory_curation_worker,
    )
except Exception:
    self.memory_curation_run = None
    raise
```

snapshot 与 worker 构造成功后，`self.memory_curation_run = run` 必须紧邻并早于 `spawn_qt_worker()`；spawn 抛错时恢复为 `None`，避免无对应线程的 context 永久阻止重试。完成和失败 handler 必须先执行 shutdown guard，再读取局部 `run`；缺失时记录：

```python
log_event(
    "Memory",
    "记忆整理回调缺少运行上下文",
    {"callback": "finished"},
)
```

失败回调用 `"failed"`。后续所有 mode、target、consumed、character 比较只读局部 `run`。finished/failed handler 不得清空 context；唯一清空点是 finalizer 调用的 cleanup。删除 `_memory_curation_character_changed()` 与 `_memory_curation_character_payload()` 两个兼容 helper，直接比较 `run.character_id != self.character_profile.id` 并构造日志 payload。`_show_auto_memory_curation_stopped_message()` 直接调用 `self.subtitle_controller.show_text_immediately(message)`，删除必需组件的动态查找。

cleanup 必须保持：

```python
def _cleanup_memory_curation_worker(self) -> None:
    self.memory_curation_run = None
    if self._shutdown_in_progress:
        return
    if self._suppress_auto_memory_curation_restart:
        self._suppress_auto_memory_curation_restart = False
        return
    QTimer.singleShot(0, self._maybe_start_auto_memory_curation)
```

不得修改 `app/agent/memory_curator.py` 或 `app/agent/memory_curation_worker.py`。

- [ ] **Step 4: 运行记忆整理契约与扩大回归**

Run:

```powershell
.\runtime\python.exe -m pytest tests/ui/test_pet_window.py -q -k "memory_curation or auto_memory"
.\runtime\python.exe -m pytest tests/unit/test_memory_curator.py::test_curator_snapshot_keeps_prompt_and_store_context tests/unit/test_memory_curator.py::test_scoped_memory_store_keeps_scope_after_parent_switch tests/unit/test_memory_curator.py::test_memory_curation_state_waits_until_trigger_turns tests/unit/test_memory_curator.py::test_memory_curation_state_consumes_pending_turns_without_advancing_history -q
.\runtime\python.exe -m pytest tests/ui/test_ui_state.py tests/ui/test_pet_window.py -q
```

Expected: 全部通过；角色切换、三次失败消费和 cleanup 自动重启语义不变。

- [ ] **Step 5: 提交并审查**

```powershell
git add app/ui/pet_window.py tests/ui/test_pet_window.py
git commit -m "refactor: consolidate memory curation context"
git show --check --stat HEAD
git diff HEAD^..HEAD -- app tests
```

Review 重点：shutdown late callback 不产生伪状态错误；角色切换不清空旧 worker 的上下文；同角色与切换角色的第三次失败语义分别保持；cleanup 清上下文发生在排队下一轮之前；新增 dataclass 必须换来四字段、两个 helper 和 subtitle 动态回退的删除。`QtWorkerResource.stop()` lingering 超时路径不会调用 business cleanup，此时 context 可能保留到窗口销毁，但 shutdown guard 必须阻止 late handler 写进度或重启；该 ResourceManager 生命周期问题留给后续轮次。发现问题后 amend。

### Task 9: 结构验收与提交归属复核

**Files:**
- Verify only: files changed in Tasks 2-8

- [ ] **Step 1: 运行精确结构扫描**

Run:

```powershell
rg -n "reply_waiting_ui_active|active_event_type|active_reminder_id|active_reminder_text|pending_screen_observation_event_reminder_id|memory_curation_mode|memory_curation_character_id|memory_curation_target_history_count|memory_curation_consumed_turns" app plugins
rg -ni "proactive" app plugins main.py -g "!app/config/migration_runner.py" -g "!app/config/migrations.py" -g "!app/agent/runtime.py"
rg -n "load_proactive_care_settings|save_proactive_care_settings|result_proactive_care_settings|DEFAULT_PROACTIVE" app plugins main.py
rg -n "= PetWindow\._(set_busy|set_reply_waiting_ui|sync_reply_waiting_ui|send_message|show_waiting_reply_placeholder|record_user_message|handle_event_reply|handle_event_error|clear_active_event|cleanup_worker|mobile_chat_busy|start_memory_curation|handle_memory_curation_finished|handle_memory_curation_failed|cleanup_memory_curation_worker)" tests/ui
$runtimeSource = Get-Content -Raw -LiteralPath "app/agent/runtime.py"
if (([regex]::Matches($runtimeSource, '"proactive_check"')).Count -ne 1) { throw "退役事件守卫数量异常" }
if (-not $runtimeSource.Contains('if event.type == "proactive_check":')) { throw "缺少退役事件拒绝守卫" }
$runtimeWithoutGuard = ($runtimeSource -split "`r?`n" | Where-Object { $_ -notmatch '"proactive_check"' }) -join "`n"
if ($runtimeWithoutGuard -match '(?i)proactive') { throw "runtime 仍含退役主动感知命名" }
```

Expected: 前四条扫描无输出；最后四行脚本退出 0，证明唯一旧事件字面量只留在明确拒绝守卫中。

- [ ] **Step 2: 审查本轮方法中的动态容错**

检查以下方法内是否还用 `getattr/hasattr/callable` 容忍本轮已确认必需的对象：

```text
_sync_reply_waiting_ui
_release_empty_input_focus_after_reply_waiting
_set_busy
_run_event_worker
_handle_event_reply
_handle_event_error
_cleanup_worker
_start_memory_curation
_handle_memory_curation_finished
_handle_memory_curation_failed
_cleanup_memory_curation_worker
show_settings / _on_settings_dialog_finished 的 screen-awareness 路径
```

对 `input_edit`、`send_button`、`ui_state`、`screen_awareness_settings`、`memory_curation_run` 正常路径使用直接访问。插件、平台能力、退出阶段 Qt 对象、可选 TTS/MCP 继续保留防御性边界。

- [ ] **Step 3: 将任何失败退回所属任务**

Task 9 不设计新实现，也不创建泛化 cleanup 提交。任何扫描或直接访问审查失败，都必须归属到 Task 2-8 中引入或遗漏该问题的具体提交，补上对应失败测试、最小修正、定点测试和提交后双 review；修正完成后从 Step 1 重新执行。扫描全部通过后直接进入 Task 10。

### Task 10: 全量验证、净删减核对与最终 review

**Files:**
- Verify only: all changed files

- [ ] **Step 1: 运行编译与分层测试**

Run:

```powershell
.\runtime\python.exe -m compileall -q app plugins main.py
.\runtime\python.exe -m pytest tests/unit -q
.\runtime\python.exe -m pytest tests/integration -q
.\runtime\python.exe -m pytest tests/ui -q
.\runtime\python.exe -m pytest -q
```

Expected: 命令全部退出 0，无 unknown pytest config warning。

- [ ] **Step 2: 重跑结构扫描**

Run Task 9 的三条 `rg` 命令。

Expected: 无输出。另运行：

```powershell
rg -n "proactive_care" app plugins main.py
```

Expected: 只允许 `app/config/migration_runner.py` 与 `app/config/migrations.py` 出现历史用户数据键；其他文件不得出现。

`"proactive_check"` 只允许在 `app/agent/runtime.py` 的退役事件拒绝守卫出现一次，不允许出现在支持集合、prompt 分支或工具循环中。

- [ ] **Step 3: 统计生产代码净删减**

Run:

```powershell
$added = 0
$deleted = 0
git diff --numstat e004f44..HEAD -- app plugins | ForEach-Object {
    $parts = $_ -split "`t"
    if ($parts[0] -ne "-") { $added += [int]$parts[0] }
    if ($parts[1] -ne "-") { $deleted += [int]$parts[1] }
}
Write-Output "production added=$added deleted=$deleted net_deleted=$($deleted - $added)"
if ($deleted -le $added) { throw "生产代码删除量未大于新增量" }
```

Expected: `deleted > added`；目标净删 400-650 行。若低于目标但仍净删，review 每个新增 helper 是否必要，优先删除仍存在的重复分支，不通过删除有效测试凑数。

- [ ] **Step 4: 最终提交序列与工作树审查**

Run:

```powershell
git log --oneline e004f44..HEAD
git diff --check e004f44..HEAD
git diff --stat e004f44..HEAD
git status --short
```

Expected: 提交按计划小步排列；无 whitespace error；工作树只剩用户原有 `link_sakura_runtime_tts.bat`。

- [ ] **Step 5: 最终规格与代码质量双审查**

规格审查逐项核对设计文档第 9 节验收标准。代码质量审查重点回答：

1. 是否仍有两个来源描述同一状态？
2. 是否为测试桩保留了生产 fallback？
3. 是否能在不改变时序的前提下继续删除分支或参数？
4. 是否误删用户数据迁移、公开插件 SDK 或有效行为测试？
5. 每个新增类型/helper 是否换来了更多生产代码删除和更强不变量？

若发现问题，修正到对应最近提交并 amend，重跑受影响测试与全部最终验证。最终不创建仅记录“测试通过”的空提交。
