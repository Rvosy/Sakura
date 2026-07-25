# WP-3-01 Assistant Adapter and Real Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the existing Qt-free Core Host lifecycle to a real Sakura Assistant session that reports deterministic readiness and a five-field public character snapshot without networking or later chat/platform capabilities, while allowing normal runtime data writes owned by the exercised product path.

**Architecture:** Keep the Core Host control plane minimal and inject one background initializer. The initializer lazily imports a thin `AssistantAdapter`, which uses one strict raw-YAML `CoreConfigReader`, a `CharacterRegistry` with an injected issue sink, an OpenAI-compatible client constructed locally, a real `AgentRuntime` with empty tools and disabled memory, and an unexecuted `ChatPipeline`. Python remains the readiness/Snapshot owner; Rust only validates and caches the current generation and uses one 5000 ms shutdown deadline.

**Tech Stack:** Python 3.12, pytest, PyYAML already present in the repository, Rust 1.96/Cargo, Tauri 2.11.3, GitHub Actions Windows x64/macOS arm64/Linux x64.

## Global Constraints

- WP-3-01 was the only active Work Package throughout implementation. It was accepted on 2026-07-26; the next activation point is maintained only in the Work Package status table.
- Production `core.initialize` accepts exactly `{}` and returns quickly; fake readiness modes exist only through an injected test initializer and are not protocol fields.
- Startup performs no DNS, socket, HTTP, TLS, authentication, model-list, chat, or Provider test call.
- The real session is exactly `CharacterProfile + OpenAICompatibleClient + AgentRuntime(ToolRegistry([]), truthy disabled MemoryLike) + ChatPipeline(not run)`.
- Do not initialize or import Qt/PySide6, ResourceManager, Memory, MCP, plugins, voice/TTS, screen/visual observation, history/stores, scheduler, settings UI, Router, Gateway, Operation, or chat commands.
- `currentCharacterSummary` has exactly `id`, `displayName`, `initialMessage`, `replyTones`, and `portraitChoices`; `activeInteractionSummary` remains `null`.
- `SUPPORTED_CORE_CONFIG_VERSION` is the non-bool integer `4`; the reader never migrates, normalizes-and-saves, backs up, logs to the legacy file, or changes bytes/mtime.
- API key has no authorized output serializer. Generation credential may be serialized only by the existing controlled framed Core IPC envelope.
- All WP-3-01 readiness results are `retryable=false`; no state/code causes an automatic Core restart.
- Rust shutdown uses one shared 5000 ms end-to-end deadline beginning after the shutdown request is successfully written; the 3000 ms graceful interval is included in that budget.
- Zero new dependencies or manifest/lockfile changes.
- `data/` is writable runtime state. Allow task-scoped product writes and audit only the declared expected/forbidden write sets; never clean, truncate, restore, or delete unrelated user data. Use isolated temporary roots for destructive fault injection. Modify `characters/` or `runtime/` only when the task explicitly requires it.
- Allowed production candidates are limited to the paths frozen in `docs/runtime-v2/WP-3-01-qt-free-assistant-adapter-readiness.md`; all forbidden paths in that specification remain forbidden.

---

## File Map

- `app/config/visual_effect.py`: pure `VisualEffectMode` constants and validation, with no Qt import.
- `app/config/core_config_reader.py`: the sole explicit projection of `system_config.yaml`, `api.yaml`, and `characters.yaml`; its non-persisting behavior is a component responsibility, not a global `data/` write ban.
- `app/core_host/assistant_adapter.py`: readiness DTOs, disabled memory object, public character projector, real session construction, ownership, and idempotent close.
- `app/core_host/server.py`: injected initializer lifecycle, atomic readiness/Snapshot publication, empty production initialize payload, and cleanup aggregation.
- `app/core_host/__main__.py`: required RuntimeLocator-provided app root CLI argument and secret-safe `HostConfig` construction.
- `app/config/character_loader.py`: injectable issue sink with legacy `log_event` as the default.
- `app/config/models.py`, `app/llm/api_client.py`: repr exclusion for API keys without changing constructors or equality.
- `app/agent/__init__.py`, `app/agent/runtime.py`, `app/agent/memory_recall.py`, `app/core/chat_pipeline.py`, `app/ui/theme.py`, `app/ui/window_backdrop.py`: only the import-only seams proven necessary to construct the approved session without Qt or forbidden optional domains; legacy behavior remains equivalent.
- `desktop/src-tauri/src/core_host_runtime.rs`: app-root argument, real Adapter acceptance, public Snapshot validation, and one shared shutdown deadline.
- `desktop/src-tauri/src/core_supervisor.rs`: narrow non-retryable readiness classification only if a failing test proves the current classifier would restart.
- `.github/workflows/runtime-v2-platform-foundation.yml`, `tests/unit/test_runtime_v2_platform_workflow.py`: explicit three-platform execution of new `core_host_*` tests and symmetric push/PR filters.
- `tests/fixtures/runtime_v2/wp_3_01/**`: deterministic, sanitized config and character packages only.

### Task 1: Establish Qt-free, secret-safe import seams

**Files:**
- Create: `app/config/visual_effect.py`
- Modify: `app/ui/window_backdrop.py`
- Modify: `app/ui/theme.py`
- Modify: `app/config/character_loader.py`
- Modify: `app/config/models.py`
- Modify: `app/llm/api_client.py`
- Modify: `app/agent/__init__.py`
- Modify: `app/agent/runtime.py`
- Modify: `app/agent/memory_recall.py`
- Modify: `app/core/chat_pipeline.py`
- Test: `tests/unit/test_core_host_import_guard.py`
- Test: `tests/unit/test_agent_runtime.py`
- Test: `tests/integration/test_chat_pipeline.py`

**Interfaces:**
- Produces: `VisualEffectMode`, `CharacterRegistry(base_dir, issue_sink=log_event)`, lazy public exports from `app.agent`, and repr-excluded `ApiSettings.api_key`/`ApiConfigProfile.api_key`.
- Preserves: all existing import names, `ApiConfigProfile` default `api_key=""`, constructor signature, equality, theme validation, and legacy default Memory creation when `AgentRuntime(memory=None)` is used outside WP-3-01.

- [ ] **Step 1: Write the failing import and equivalence tests**

Add two subprocess probes. Before initialization, importing `app.core_host` must load no module whose name starts with `PySide6`, `app.ui`, `app.agent`, `app.plugins`, or `app.voice`. After constructing the approved session imports, permit only the pure `app.ui.theme` namespace required by existing `CharacterProfile` theme semantics; still reject `PySide6`, `app.ui.window_backdrop`, every other Qt UI module, `app.agent.memory`, `app.agent.mcp`, `app.plugins`, `app.voice`, `app.agent.screen`, `app.storage.chat_history`, and `app.storage.visual_observation`. Add manifests with and without a theme section and assert their `ThemeSettings`, normalized values, `theme_source`, and legacy behavior match before and after the refactor. Add direct tests that `from app.ui.window_backdrop import VisualEffectMode` still works, lazy `app.agent.AgentRuntime`/`ToolRegistry` exports resolve, and API-key fields are absent from `repr()` while `ApiConfigProfile("id", "alias", "https://example.invalid") == ApiConfigProfile("id", "alias", "https://example.invalid", "")`.

- [ ] **Step 2: Run RED tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 -m pytest \
  tests/unit/test_core_host_import_guard.py \
  tests/unit/test_agent_runtime.py \
  tests/integration/test_chat_pipeline.py -q
```

Expected: the new post-initialize probe reports Qt/Memory/plugin/screen/visual-observation imports, and secret repr assertions expose API keys.

- [ ] **Step 3: Implement the pure visual-effect module and compatible re-export**

Create the pure class in `app/config/visual_effect.py`, import it from `app/ui/theme.py`, and re-export it from `app/ui/window_backdrop.py`:

```python
from __future__ import annotations

import sys


class VisualEffectMode:
    SOLID = "solid"
    GAUSSIAN_BLUR = "gaussian_blur"
    WINDOWS_ACRYLIC = "windows_acrylic"
    MACOS_VISUAL_EFFECT = "macos_visual_effect"
    _ALL = (SOLID, GAUSSIAN_BLUR, WINDOWS_ACRYLIC, MACOS_VISUAL_EFFECT)
    DEFAULT = GAUSSIAN_BLUR

    @classmethod
    def available_modes(cls) -> list[str]:
        modes = [cls.SOLID, cls.GAUSSIAN_BLUR]
        if sys.platform == "darwin":
            modes.append(cls.MACOS_VISUAL_EFFECT)
        return modes

    @classmethod
    def validate(cls, value: str) -> str:
        return value if value in cls._ALL else cls.DEFAULT
```

- [ ] **Step 4: Implement lazy and typing-only domain imports**

Replace eager `app.agent` re-exports with `__getattr__` backed by an explicit name-to-module allowlist. In `runtime.py`, keep Memory/plugin/screen/history/store/visual-observation symbols under `TYPE_CHECKING` and import their concrete implementations only inside methods that execute those optional features; `ChatHistoryStore` is annotation-only and must not import `app.storage.chat_history` during session construction. Move the top-level `SESSION_DIGEST_INJECT_MAX_RECENT_MESSAGES`/`build_session_state_fragment` import from `app.agent.session_state_context` into `_session_state_fragments`, because that module transitively imports `app.storage.chat_history`; the entire session-state/history chain must remain unloaded until that actual history path executes. Preserve the legacy `memory is None` branch with a local `MemoryStore()` import. Remove the top-level `ContextOrchestrator`/`build_context_request` import: store `_context_orchestrator = None`, expose a lazy `context_orchestrator` property that locally imports and constructs the existing class on first actual context-building use, and locally import `build_context_request` in that execution path. Add legacy tests proving the first real chat/context request still constructs exactly one orchestrator and preserves output, while Adapter construction alone loads neither `app.agent.context_orchestrator`, `app.agent.session_state_context`, `app.plugins`, nor `app.storage.chat_history`; invoking the legacy history path must still load it and preserve the existing fragment output. In `memory_recall.py`, accept a local `MemoryLike` protocol exposing `search_memory`. In `chat_pipeline.py`, import `AgentRuntime` directly from `app.agent.runtime`, action DTOs directly from `app.agent.actions`, and visual-observation types only under `TYPE_CHECKING` or inside the method that records them.

Use explicit secret fields in `app/llm/api_client.py`:

```python
@dataclass(frozen=True)
class ApiSettings:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: int = 60
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
```

Use the existing defaulted shape in `app/config/models.py`:

```python
@dataclass(frozen=True)
class ApiSettings:
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    timeout_seconds: int = 60
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class ApiConfigProfile:
    id: str
    alias: str
    base_url: str
    api_key: str = field(default="", repr=False)
    models: tuple[str, ...] = ()
```

- [ ] **Step 5: Run GREEN and legacy equivalence tests**

Run the Step 2 command. Expected: all selected tests pass, subprocess imports are clean, and existing public import/default behavior remains unchanged.

- [ ] **Step 6: Commit**

Commit only Task 1 files with `refactor(runtime): 解耦 Assistant 无 Qt 导入边界` and a detailed WP-3-01 body including RED/GREEN evidence, unchanged legacy semantics, non-goals, risk, and revert instructions.

### Task 2: Implement the explicit Core configuration projection

**Files:**
- Create: `app/config/core_config_reader.py`
- Test: `tests/unit/test_core_host_config_reader.py`
- Create: `tests/fixtures/runtime_v2/wp_3_01/ready/data/config/system_config.yaml`
- Create: `tests/fixtures/runtime_v2/wp_3_01/ready/data/config/api.yaml`
- Create: `tests/fixtures/runtime_v2/wp_3_01/ready/data/config/characters.yaml`
- Create: `tests/fixtures/runtime_v2/wp_3_01/ready/characters/sakura/character.json`
- Create: only the minimal card and portrait files referenced by that manifest.

**Interfaces:**
- Produces: `SUPPORTED_CORE_CONFIG_VERSION = 4`, `StableReadinessError`, `ProviderSelection`, `CoreConfigReadResult`, and `CoreConfigReader.read(app_root)`.
- Consumes: pure `ApiConfigProfile`, `ModelSelectionSettings`, `ModelSlotSelection`, `app.llm.api_client.ApiSettings` (aliased as `ClientApiSettings`), `resolve_model_slot`, and `yaml.safe_load` without any settings service or migration import.

- [ ] **Step 1: Write table-driven RED tests for every frozen config row**

Cover missing/zero/blank/null/bad/nonmapping files; non-bool version 4; invalid version types/values; malformed containers and fields; missing and mismatched profile/slot/model/base URL/key; invalid URL scheme/host; empty current character id. Before and after each read, record relative path, bytes, size, nanosecond mtime, SHA-256, mode, and absence of `.bak`; assert exact state/code and byte-for-byte equality. Install fail-on-call guards for `Path.write_text`, `write_bytes`, `touch`, `mkdir`, `rename`, `replace`, `unlink`, `chmod`, write/append/update modes passed to `Path.open`/`open`, and OS rename/replace/unlink/mkdir/chmod functions; assert the reader only opens the three approved YAML files for reading. Add static import assertions rejecting `AppSettingsService`, `MigrationRunner`, `save_yaml_mapping`, migration constants, and legacy `log_event`.

- [ ] **Step 2: Run RED**

```bash
PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 -m pytest tests/unit/test_core_host_config_reader.py -q
```

Expected: collection fails because `app.config.core_config_reader` does not exist.

- [ ] **Step 3: Implement the reader with explicit parsing and no writes**

Use these public DTOs and stable error factory:

```python
SUPPORTED_CORE_CONFIG_VERSION = 4


@dataclass(frozen=True)
class StableReadinessError:
    state: Literal["setup_required", "failed"]
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class ProviderSelection:
    api_settings: ClientApiSettings = field(repr=False)


@dataclass(frozen=True)
class CoreConfigReadResult:
    current_character_id: str | None
    provider_selection: ProviderSelection | None = field(repr=False)
    config_problem: StableReadinessError | None = None
```

Implement `_read_required_system_mapping`, `_read_auxiliary_mapping`, `_parse_profiles`, `_parse_model_selection`, `_validate_provider_url`, and `read`. Each helper returns structured values or one frozen stable error; caught exceptions become constant diagnostics that contain neither exception text nor paths. Import `ApiSettings as ClientApiSettings` from `app.llm.api_client`; construct `base_settings = ClientApiSettings(base_url="", api_key="", model="")` and call `resolve_model_slot(profiles, selections, MODEL_SLOT_CHAT, base_settings)`. `ProviderSelection.api_settings` must be that exact client type and the test must assert `isinstance(result.provider_selection.api_settings, app.llm.api_client.ApiSettings)` and object identity with the resolver result. Use `Path.read_text(encoding="utf-8")` and `yaml.safe_load` only; never call any settings service.

- [ ] **Step 4: Run GREEN and secret scan**

Run the Step 2 command plus:

```bash
rg -n "AppSettingsService|MigrationRunner|save_yaml_mapping|atomic_write|\.write_" app/config/core_config_reader.py
```

Expected: pytest passes and the scan returns no matches.

- [ ] **Step 5: Commit**

Commit only Task 2 files with `feat(runtime): 增加 Core 显式配置投影` and the required detailed WP body.

### Task 3: Build the thin Assistant Adapter and safe public projection

**Files:**
- Create: `app/core_host/assistant_adapter.py`
- Modify: `app/config/character_loader.py`
- Test: `tests/unit/test_core_host_assistant_adapter.py`
- Test: `tests/unit/test_core_host_secrets.py`
- Test: `tests/unit/test_agent_runtime.py`
- Test: `tests/integration/test_chat_pipeline.py`

**Interfaces:**
- Consumes: Task 1 import seams and Task 2 `CoreConfigReader`.
- Produces: `ReadinessResult`, `AssistantSession`, `DisabledMemory`, `project_current_character_summary(profile)`, and `AssistantAdapter.initialize(cancel)`/`close()`.

- [ ] **Step 1: Write RED tests for real construction and forbidden calls**

Use the isolated ready fixture. Patch DNS/socket/urllib and every Provider request method to raise on call. Patch Memory/MCP/plugin/TTS/voice/screen/history/store entry points to raise on import or construction. Assert a real `OpenAICompatibleClient`, `AgentRuntime`, empty `ToolRegistry`, truthy `DisabledMemory`, and unexecuted real `ChatPipeline` are built; the public projector has exactly five keys. Add current-character fallback, optional corrupt package, no valid package, mid-construction failure, idempotent close, cancellation, reverse-close, sanitized issue sink, and no legacy log write tests. Add the mandatory combined case where configured current is invalid and a different optional package is corrupt: the only result is `degraded/CHARACTER_FALLBACK_APPLIED`, the safe fallback summary is published, and exactly one session is constructed; `OPTIONAL_CHARACTER_SKIPPED` is used only when the configured current remains valid.

- [ ] **Step 2: Run RED**

```bash
PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 -m pytest \
  tests/unit/test_core_host_assistant_adapter.py \
  tests/unit/test_core_host_secrets.py -q
```

Expected: collection fails because `assistant_adapter` does not exist.

- [ ] **Step 3: Implement the adapter boundary**

Use these DTO shapes:

```python
@dataclass
class AssistantSession:
    character: CharacterProfile
    provider: OpenAICompatibleClient = field(repr=False)
    runtime: AgentRuntime
    pipeline: ChatPipeline


@dataclass(frozen=True)
class ReadinessResult:
    state: Literal["ready", "setup_required", "degraded", "failed"]
    code: str
    message: str
    retryable: bool
    current_character_summary: dict[str, object] | None
    session: AssistantSession | None = field(default=None, repr=False)


class DisabledMemory:
    def __bool__(self) -> bool:
        return True

    def search_memory(self, _payload: dict[str, object], *, wait: bool = False) -> dict[str, object]:
        return {"status": "disabled", "memories": []}

    def summary(self) -> str:
        return ""

    def close(self) -> None:
        return None
```

`project_current_character_summary` returns only the frozen five keys and copies lists. `AssistantAdapter.initialize` checks cancellation before/after reads and each construction boundary, selects configured/fallback characters with the frozen precedence, constructs `OpenAICompatibleClient`, `ToolRegistry([])`, `DisabledMemory`, `AgentRuntime`, then `ChatPipeline`, and records ownership only after successful construction. `close` is locked, idempotent, and closes owned closable values in reverse order. `CharacterRegistry` accepts `issue_sink(scope, message, details)` and defaults to `log_event`; Adapter supplies a constant-message stderr sink with no path/error repr.

- [ ] **Step 4: Prove secret and serializer boundaries**

Add static AST tests rejecting `asdict`, `vars`, `__dict__`, `pickle`, and default object JSON serialization for `HostConfig`, `ProviderSelection`, `CoreConfigReadResult`, `AssistantSession`, and settings/profile DTOs. Dynamic tests scan repr, errors, stderr, Snapshot projection, and logs for planted API key, credential, endpoint, model, prompt, and absolute paths. Assert the API key has no output projector and only the existing protocol response path serializes generation credential.

- [ ] **Step 5: Run GREEN and affected legacy tests**

```bash
PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 -m pytest \
  tests/unit/test_core_host_assistant_adapter.py \
  tests/unit/test_core_host_secrets.py \
  tests/unit/test_agent_runtime.py \
  tests/integration/test_chat_pipeline.py -q
```

Expected: all tests pass with zero network calls and zero fixture writes.

- [ ] **Step 6: Commit**

Commit only Task 3 files with `feat(runtime): 构建无 Qt Assistant Session` and the required detailed WP body.

### Task 4: Integrate real initialization into the Core Host lifecycle

**Files:**
- Modify: `app/core_host/server.py`
- Modify: `app/core_host/__main__.py`
- Test: `tests/unit/test_core_host_readiness.py`
- Test: `tests/unit/test_core_host_cli.py`
- Test: `tests/unit/test_core_host_negotiation.py`
- Test: `tests/unit/test_core_host_protocol.py`
- Test: `tests/integration/test_core_host_lifecycle.py`
- Test: `tests/unit/test_core_host_import_guard.py`

**Interfaces:**
- Consumes: Task 3 `AssistantAdapter`/`ReadinessResult`.
- Produces: `HostConfig(app_root, generation_id, generation_credential, generation_number=1)`, `ReadinessController(config, initializer_factory=factory)`, atomic schema-1 snapshots, and cleanup aggregation.

- [ ] **Step 1: Write RED lifecycle tests**

Change production initialize tests to send `{}` and assert unknown/mode/delay fields fail. Move existing readiness modes behind an injected fake initializer. Add duplicate initialize, slow read with responsive health, shutdown-before/during/after initialize, EOF, writer failure, initializer failure, close throw, close block, old-worker late result, two generations, monotonic revision, and exact Snapshot tests. Require `--app-root` in CLI tests and assert it has no fallback to cwd/repository/user paths.

- [ ] **Step 2: Run RED**

```bash
PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 -m pytest \
  tests/unit/test_core_host_readiness.py \
  tests/unit/test_core_host_cli.py \
  tests/unit/test_core_host_negotiation.py \
  tests/unit/test_core_host_protocol.py \
  tests/integration/test_core_host_lifecycle.py \
  tests/unit/test_core_host_import_guard.py -q
```

Expected: failures show production mode payload is still accepted, app root is absent, and the fake controller owns readiness.

- [ ] **Step 3: Implement injected background initialization and atomic publication**

Make `HostConfig.app_root: Path` required and `generation_credential: str = field(repr=False)`. `ReadinessController.begin` accepts only an empty mapping, starts one worker, publishes revision 1/`initializing`, and returns immediately. The worker creates the initializer lazily, receives one `ReadinessResult`, and under one lock either publishes revision 2 plus the safe five-field summary or closes/discards a late result after shutdown. Snapshot `components` contains only `assistant: {state, code, retryable}` for the actually initialized adapter, and `activeInteractionSummary` remains `None`.

- [ ] **Step 4: Implement deterministic cleanup aggregation**

Make `ControlDispatcher.close()` close/cancel the readiness owner once. In `run_host`, preserve the primary exception and always attempt dispatcher/adapter cleanup before writer cleanup. If cleanup fails with no primary exception, raise the first cleanup error; attach later cleanup errors as notes without including their values in protocol/stderr diagnostics. Do not add a Python process killer or a second lifecycle owner.

- [ ] **Step 5: Implement required CLI app root**

Add `--app-root` to `parse_args`, resolve it without reading it, and pass it to `HostConfig`. The Rust RuntimeLocator will provide this value; Python has no implicit default.

- [ ] **Step 6: Run GREEN**

Run the Step 2 command. Expected: all selected tests pass, health remains under the frozen deadline, production payload is empty-only, and cleanup/order/race tests pass.

- [ ] **Step 7: Commit**

Commit only Task 4 files with `feat(runtime): 接入真实 Assistant readiness 生命周期` and the required detailed WP body.

### Task 5: Enforce Rust readiness classification and one shutdown deadline

**Files:**
- Modify: `desktop/src-tauri/src/core_host_runtime.rs`
- Modify only if RED proves necessary: `desktop/src-tauri/src/core_supervisor.rs`
- Modify: `desktop/src-tauri/src/phase_1c_core_host_acceptance.rs`
- Test: inline Rust unit/integration tests in those modules.

**Interfaces:**
- Consumes: Python CLI `--app-root`, stable state/code/retryable fields, and schema-1 Snapshot.
- Produces: RuntimeLocator app-root injection, exact five-field summary validation, all WP-3-01 states classified non-retryable, and one shutdown deadline shared by graceful and forced cleanup.

- [ ] **Step 1: Write RED Rust tests**

Add tests that command construction passes the approved app root; all frozen readiness codes are non-retryable; extra/missing character-summary keys fail; API key/credential/private fields fail Snapshot validation; and a fixture consuming nearly 3000 ms of graceful shutdown leaves only the remainder of the original 5000 ms for tree/pipe/thread/temp cleanup. Add ignore-shutdown and close-block tests proving total elapsed does not become 3000+5000 ms.

- [ ] **Step 2: Run RED**

```bash
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked core_host_runtime -- --test-threads=1
```

Expected: app-root/snapshot tests fail and the segmented deadline test exceeds the single-budget assertion.

- [ ] **Step 3: Implement the shared deadline**

Immediately after the shutdown frame write succeeds, compute one `Instant::now() + Duration::from_millis(5000)`. Pass that absolute deadline to graceful wait, stdin close, root wait, terminate-tree, verify-exited, pipe/stderr/writer join, handle/fd release, and temp cleanup helpers. Cap graceful work at `min(deadline, start + 3000 ms)` and every later wait at `deadline.saturating_duration_since(Instant::now())`. Never create a second full timeout.

- [ ] **Step 4: Implement app-root and Snapshot/classification validation**

Pass RuntimeLocator's approved app root as `--app-root`. Validate summary keys as an exact set and value types as strings/arrays of strings. Treat `READY`, `CORE_CONFIG_SETUP_REQUIRED`, `CONFIG_DATA_INVALID`, `CONFIG_VERSION_UNSUPPORTED`, `PROVIDER_SETUP_REQUIRED`, `CHARACTER_SETUP_REQUIRED`, `ASSISTANT_INITIALIZATION_FAILED`, `CHARACTER_FALLBACK_APPLIED`, and `OPTIONAL_CHARACTER_SKIPPED` as non-retryable without adding another restart path.

- [ ] **Step 5: Run GREEN and Rust gates**

```bash
PYTHONDONTWRITEBYTECODE=1 cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked -- --test-threads=1
PYTHONDONTWRITEBYTECODE=1 cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked
PYTHONDONTWRITEBYTECODE=1 cargo build --manifest-path desktop/src-tauri/Cargo.toml --release --locked
```

Expected: all commands exit 0 and the full Rust test suite reports no failures.

- [ ] **Step 6: Commit**

Commit only Task 5 files with `fix(runtime): 统一 Assistant 关闭期限与状态分类` and the required detailed WP body.

### Task 6: Add real-host three-platform acceptance and CI coverage

**Files:**
- Modify: `desktop/src-tauri/src/phase_1c_core_host_acceptance.rs`
- Add or modify: `tests/integration/test_core_host_assistant_lifecycle.py`
- Modify: `.github/workflows/runtime-v2-platform-foundation.yml`
- Modify: `tests/unit/test_runtime_v2_platform_workflow.py`
- Use: `tests/fixtures/runtime_v2/wp_3_01/**`

**Interfaces:**
- Consumes: Tasks 1-5 production path.
- Produces: deterministic real Adapter lifecycle/fault matrix on Windows x64, macOS arm64, and Linux x64; explicit CI execution of `core_host_*` pytest; protected-resource and residual-resource evidence.

- [ ] **Step 1: Write RED workflow and end-to-end tests**

Assert both push and pull_request filters cover every allowed Python/Core path and the workflow runs an explicit `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/unit/test_core_host_*.py tests/integration/test_core_host_*.py` step on every platform. Add a real-host scenario matrix for ready/setup_required/degraded/failed, including the combined invalid-current-plus-corrupt-optional input that must produce only `CHARACTER_FALLBACK_APPLIED`; also cover repeated health, snapshot, shutdown, crash, forced recovery, consecutive generations, stale Snapshot/credential, close throw/block, and one/multiple TERM-ignoring descendants.

- [ ] **Step 2: Run RED**

```bash
PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 -m pytest \
  tests/unit/test_runtime_v2_platform_workflow.py \
  tests/integration/test_core_host_assistant_lifecycle.py -q
```

Expected: the workflow lacks the explicit new pytest step and real Adapter scenarios are not wired.

- [ ] **Step 3: Implement the narrow workflow and acceptance wiring**

Reuse the existing RuntimeLocator, shared application lock, `ManagedProcessTree`, framed transport, and phase-1C harness. Do not add a new process or protocol. Each scenario uses a copied fixture root, a unique temp directory, an outer deadline, and exact PID/process-tree identity. Record root/descendant exit, pipe/fd/handle/thread/temp cleanup, lock immediate reacquisition, and before/after relative path/size/mtime/SHA-256 equality. The ready case must prove no network calls and no forbidden domain import.

- [ ] **Step 4: Run local GREEN gates**

```bash
PYTHONDONTWRITEBYTECODE=1 ./runtime/bin/python3 -m pytest \
  tests/unit/test_core_host_*.py \
  tests/integration/test_core_host_*.py \
  tests/unit/test_runtime_v2_platform_workflow.py -q
npm test --prefix desktop/frontend
PYTHONDONTWRITEBYTECODE=1 cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
PYTHONDONTWRITEBYTECODE=1 cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked -- --test-threads=1
git diff --check
```

Expected: every command exits 0; no temporary Python shim, Shell, Core, child process, shared-lock holder, or fixture temp remains.

- [ ] **Step 5: Commit**

Commit only Task 6 files with `test(runtime): 建立 Assistant 三平台 readiness 门禁` and the required detailed WP body.

## Completed Review Record

Task-level review was an implementation aid, not a repeatable status gate. Tasks 1-6 completed TDD and local verification; the one broad correction wave is commit `c630575a4`, followed by the policy-only synchronization in `8063f2066`. Do not dispatch another independent reviewer or invalidate successful evidence whose production inputs have not changed.

## Bounded Stabilization and Acceptance

WP-3-01 was accepted on 2026-07-26 under the bounded stabilization rules in the Runtime v2 delivery governance:

1. Task 6 candidate `ea32cf823` completed Windows x64, macOS arm64, and Linux x64 platform runs, explicit Core Host pytest, native Shell/Core lifecycle, local Python/frontend/Rust tests, resource-zero checks, and rollback recording.
2. `c630575a4` closed the single whole-WP correction wave and completed the expanded local Python and Rust suites. Its not-yet-run exact-SHA native platform matrix is an explicitly accepted residual evidence risk, not a known product failure.
3. Push-time same-SHA CI is release monitoring. Environment failures cause only same-SHA reruns; they do not authorize speculative product changes or another review.
4. Only a reproducible P0/P1 or frozen exit-condition regression attributable to the accepted implementation may pause the next Work Package and reopen WP-3-01. P2/P3, future enhancements, style suggestions, and non-attributable flakes go to backlog.
5. The status-only acceptance commit does not require another full verification cycle. Current status and the next activation point exist only in the Work Package table.

## Independent Rollback

Stop all current generations and verify the complete managed tree is empty, then revert the WP-3-01 accepted docs commit, stabilizing docs commit, Task 6 through Task 1 implementation commits in reverse order, and finally the activation commit. The resulting Core Host is the accepted WP-1C-04 fake-readiness lifecycle. Do not delete, restore, truncate, or otherwise modify `data/`, `characters/`, `runtime/`, logs, cache, migration backups, Qdrant/mem0, or plugin-private data.
