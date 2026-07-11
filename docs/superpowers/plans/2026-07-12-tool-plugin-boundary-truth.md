# Tool and Plugin Boundary Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除无生产调用的旧工具 Provider 和虚构 renderer 事件，统一插件 handler 适配，并让未知宿主事件显式失败。

**Architecture:** `create_builtin_tool_registry()` 保持内置工具唯一装配入口；插件能力注册表在贡献进入时完成唯一一次 handler 规范化；AgentRuntime 与 fixed-hook PluginManager 对宿主未知事件 fail-fast；Renderer 事件表只声明真实 emit 点。Plugin API v1 顶层类型和服务门面保持不变。

**Tech Stack:** Python 3.11、PySide6、pytest、dataclasses、inspect、现有 ToolRegistry / PluginManager / PluginEventBus。

## Global Constraints

- 正常用户流程、UI、工具功能和已公开 Plugin API v1 顶层 exports 不变。
- 不修改 `third_party/`、`tools/mcp/`、TTS 或 ResourceManager。
- 严格 TDD：每项生产改动前必须看到对应测试因旧行为而失败。
- 生产代码统计只包含 `app/` 与 `plugins/`，本轮目标净删至少 300 行。
- 不删除有效行为测试；死 Provider 的兼容测试必须替换为唯一入口结构测试。
- 每个提交后先规格 review，再代码质量 review；问题修复后 amend 并重审。
- 用户原有未跟踪文件 `link_sakura_runtime_tts.bat` 不得触碰。

---

### Task 1: 删除无生产调用的内置工具 Provider

**Files:**
- Delete: `app/agent/tools/builtin/provider.py`
- Delete: `app/agent/tools/builtin/__init__.py`
- Delete: `app/agent/tools/screen/__init__.py`
- Modify: `app/agent/tools/registry.py:132-168`
- Modify: `tests/unit/test_tool_registry.py:1-12,180-210`

**Interfaces:**
- Consumes: `app.agent.builtin_tools.create_builtin_tool_registry(base_dir, memory, reminders) -> ToolRegistry`
- Produces: `ToolRegistry.register(tool)`, `ToolRegistry.get(name)`；不再提供未使用的 `register_from_provider()`。

- [ ] **Step 1: 写唯一装配入口失败测试**

在 `tests/unit/test_tool_registry.py` 增加：

```python
from pathlib import Path


def test_builtin_tools_have_one_production_assembly_path() -> None:
    project_root = Path(__file__).resolve().parents[2]

    assert not (project_root / "app/agent/tools/builtin/provider.py").exists()
    assert not (project_root / "app/agent/tools/builtin/__init__.py").exists()
    assert not (project_root / "app/agent/tools/screen/__init__.py").exists()
    assert not hasattr(ToolRegistry, "register_from_provider")
```

删除旧 `test_register_from_provider` 与 `test_register_from_provider_no_contribute`，因为它们只要求死入口继续存在；保留 ToolRegistry 的注册、查询、描述、确认与执行测试。

- [ ] **Step 2: 运行并确认 RED**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_tool_registry.py -q
```

Expected: 新结构测试因三个文件仍存在且方法仍可见而失败；其他测试通过。

- [ ] **Step 3: 删除死路径**

删除三个文件；从 `ToolRegistry` 删除整个 `register_from_provider()`。不要修改 `create_builtin_tool_registry()` 或 `BuiltinTool` 的运行逻辑。

- [ ] **Step 4: 运行 GREEN 与引用扫描**

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_tool_registry.py tests/integration/test_native_tool_calls.py -q
rg -n "BuiltinToolProvider|register_from_provider|app\.agent\.tools\.(builtin|screen)" app plugins
```

Expected: pytest 全部通过；`rg` 无输出。

- [ ] **Step 5: 提交并双 review**

```powershell
git add app/agent/tools tests/unit/test_tool_registry.py
git commit -m "refactor: remove dead builtin tool provider"
git show --check --stat HEAD
```

Review：只删除无生产导入的实现；`create_builtin_tool_registry()` 仍被 bootstrap 和 integration 使用；生产净删应超过 300 行。发现问题后 amend。

### Task 2: 统一插件工具 handler 适配

**Files:**
- Modify: `app/plugins/capabilities.py:1-130`
- Modify: `app/plugins/manager.py:1-20,570-640`
- Modify: `tests/unit/test_plugin_system.py`

**Interfaces:**
- Consumes: `ToolContribution.handler: Callable | None`
- Produces: `PluginCapabilityRegistry.register_tool()` 中的统一 `Callable[[dict[str, Any]], Any]`；`PluginManager` 不再二次适配。

- [ ] **Step 1: 写两条注册路径的一致性失败测试**

在 `tests/unit/test_plugin_system.py` 增加 helper：

```python
def _registered_plugin_handler(handler, *, decorator: bool):  # type: ignore[no-untyped-def]
    registry = PluginCapabilityRegistry()
    if decorator:
        registry.tool(name="demo", description="demo")(handler)
    else:
        registry.register_tool(
            ToolContribution(
                name="demo",
                description="demo",
                parameters={"type": "object", "properties": {}},
                handler=handler,
            )
        )
    return registry.tools[0].handler
```

增加参数化测试，六种 handler 必须在装饰器和直接贡献两条路径得到相同结果：

```python
@pytest.mark.parametrize("decorator", [False, True])
def test_plugin_handler_receives_argument_dict(decorator: bool) -> None:
    handler = _registered_plugin_handler(lambda arguments: arguments["text"], decorator=decorator)
    assert handler is not None
    assert handler({"text": "ok"}) == "ok"


@pytest.mark.parametrize("decorator", [False, True])
def test_plugin_handler_maps_named_arguments(decorator: bool) -> None:
    handler = _registered_plugin_handler(lambda text, count=1: text * count, decorator=decorator)
    assert handler is not None
    assert handler({"text": "a", "count": 2}) == "aa"


@pytest.mark.parametrize("decorator", [False, True])
def test_plugin_handler_supports_zero_arguments(decorator: bool) -> None:
    handler = _registered_plugin_handler(lambda: "ok", decorator=decorator)
    assert handler is not None
    assert handler({}) == "ok"


@pytest.mark.parametrize("decorator", [False, True])
def test_plugin_handler_supports_keyword_only_arguments(decorator: bool) -> None:
    def keyword_only(*, text: str) -> str:
        return text

    handler = _registered_plugin_handler(keyword_only, decorator=decorator)
    assert handler is not None
    assert handler({"text": "ok"}) == "ok"


@pytest.mark.parametrize("decorator", [False, True])
def test_plugin_handler_supports_var_keyword_arguments(decorator: bool) -> None:
    handler = _registered_plugin_handler(lambda **kwargs: kwargs, decorator=decorator)
    assert handler is not None
    assert handler({"text": "ok"}) == {"text": "ok"}


@pytest.mark.parametrize("decorator", [False, True])
def test_plugin_handler_falls_back_when_signature_is_unavailable(decorator: bool) -> None:
    class Uninspectable:
        __signature__ = "invalid"

        def __call__(self, arguments):  # type: ignore[no-untyped-def]
            return arguments["text"]

    handler = _registered_plugin_handler(Uninspectable(), decorator=decorator)
    assert handler is not None
    assert handler({"text": "ok"}) == "ok"
```

保留现有插件加载端到端测试，确保规范化后的 handler 经 `ToolRegistry.execute()` 仍工作。

- [ ] **Step 2: 运行并确认 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_plugin_system.py -q -k "handler or contributions"
```

Expected: 直接 `register_tool()` 路径在 named/zero/keyword-only/`**kwargs` 中失败，证明两套入口语义不一致。

- [ ] **Step 3: 把完整适配器收敛到 capabilities**

在 `app/plugins/capabilities.py`：

```python
from dataclasses import dataclass, field, replace


def _normalize_tool_handler(handler: Callable[..., Any] | None):  # type: ignore[no-untyped-def]
    if handler is None or not callable(handler):
        return None
    try:
        parameters = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):
        return lambda arguments: handler(arguments)
    if not parameters:
        return lambda _arguments: handler()
    if len(parameters) == 1:
        parameter = parameters[0]
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        } and (
            parameter.name in {"args", "arguments"}
            or parameter.annotation in {dict, dict[str, Any]}
        ):
            return lambda arguments: handler(arguments)

    def wrapped(arguments: dict[str, Any]) -> Any:
        if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
            return handler(**arguments)
        return handler(
            **{
                parameter.name: arguments[parameter.name]
                for parameter in parameters
                if parameter.kind in {
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }
                and parameter.name in arguments
            }
        )

    return wrapped
```

`register_tool()` 改为：

```python
self.tools.append(
    replace(contribution, handler=_normalize_tool_handler(contribution.handler))
)
```

`tool()` 装饰器把原始 `func` 放入 contribution；删除 `_handler_from_callable()`。

在 `app/plugins/manager.py` 删除 `inspect` import 和 `_normalize_tool_handler()`，`_contribution_to_app_tool()` 直接使用 `handler=contribution.handler`。

- [ ] **Step 4: 运行 GREEN 与结构扫描**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_plugin_system.py tests/unit/test_plugin_advanced.py tests/unit/test_plugin_services.py tests/integration/test_native_tool_calls.py -q
rg -n "def (_handler_from_callable|_normalize_tool_handler)" app/plugins
```

Expected: pytest 通过；`rg` 只在 `app/plugins/capabilities.py` 命中一次 `_normalize_tool_handler`。

- [ ] **Step 5: 提交并双 review**

```powershell
git add app/plugins/capabilities.py app/plugins/manager.py tests/unit/test_plugin_system.py
git commit -m "refactor: unify plugin tool handlers"
git show --check --stat HEAD
```

Review：所有贡献都必须经过 `register_tool()`；不得保留第二套签名推断；不得改变 schema 推导。发现问题后 amend。

### Task 3: 未知宿主事件显式失败

**Files:**
- Modify: `app/agent/runtime.py:1150-1175`
- Modify: `app/plugins/manager.py:228-245`
- Modify: `tests/unit/test_agent_runtime.py:380-430`
- Modify: `tests/unit/test_config.py:88-100`
- Modify: `tests/unit/test_plugin_system.py:360-400`

**Interfaces:**
- Produces: `AgentRuntime.handle_event()` 只接受 `reminder_due`、`screen_awareness_check`；`PluginManager.emit_event()` 只接受 `_EVENT_HOOKS` 中的 fixed-hook 名称。

- [ ] **Step 1: 改写未知 AgentEvent 失败测试**

把 fallback 测试替换为：

```python
def test_unsupported_event_type_is_rejected_before_client_call(monkeypatch) -> None:
    import app.agent.runtime as runtime_module

    logs = []
    monkeypatch.setattr(
        runtime_module,
        "log_event",
        lambda channel, message, payload=None, **kwargs: logs.append((channel, message, payload)),
    )
    client = _dummy_api_client()
    runtime = AgentRuntime(client, _dummy_system_prompt())

    with pytest.raises(ValueError, match="不支持的主动事件类型：unknown_event"):
        runtime.handle_event(AgentEvent(type="unknown_event", payload={}))

    assert client.mock_calls == []
    assert ("AgentRuntime", "拒绝不支持的主动事件", {"event_type": "unknown_event"}) in logs
```

更新退役事件测试使用同一日志，并在 `tests/unit/test_config.py` 断言 Runtime 中不再出现 `"proactive_check"` 字面量。

- [ ] **Step 2: 增加未知 fixed-hook 失败测试并确认 RED**

```python
def test_emit_event_rejects_unknown_host_event(monkeypatch) -> None:
    import app.plugins.manager as manager_module

    logs = []
    monkeypatch.setattr(
        manager_module,
        "log_event",
        lambda channel, message, payload=None, **kwargs: logs.append((channel, message, payload)),
    )
    manager = PluginManager(_runtime_root("unknown_event"))

    with pytest.raises(ValueError, match="未知插件事件：message.typo"):
        manager.emit_event("message.typo")

    assert ("PluginManager", "拒绝未知插件事件", {"event_type": "message.typo"}) in logs
```

Run:

```powershell
.\runtime\python.exe -m pytest tests/unit/test_agent_runtime.py tests/unit/test_plugin_system.py tests/unit/test_config.py -q -k "unsupported_event or proactive_check or unknown_host_event"
```

Expected: AgentRuntime 仍返回 fallback，PluginManager 仍静默 return，结构断言仍看到退役字面量，因此失败。

- [ ] **Step 3: 实现统一拒绝**

`AgentRuntime.handle_event()` 开头保留 cancellation check，随后：

```python
if event.type not in {"reminder_due", "screen_awareness_check"}:
    log_event("AgentRuntime", "拒绝不支持的主动事件", {"event_type": event.type})
    raise ValueError(f"不支持的主动事件类型：{event.type}")
```

删除 proactive 专用分支和 fallback reply。

`PluginManager.emit_event()` 的未知分支：

```python
if hook is None:
    log_event("PluginManager", "拒绝未知插件事件", {"event_type": event_type})
    raise ValueError(f"未知插件事件：{event_type}")
```

- [ ] **Step 4: 运行事件回归**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_agent_runtime.py tests/unit/test_plugin_system.py tests/unit/test_config.py tests/integration/test_chat_pipeline.py -q
rg -n '"proactive_check"' app/agent/runtime.py
```

Expected: pytest 通过；`rg` 无输出。合法 reminder、screen-awareness、fixed hooks 和 hook 异常隔离保持绿色。

- [ ] **Step 5: 提交并双 review**

```powershell
git add app/agent/runtime.py app/plugins/manager.py tests/unit/test_agent_runtime.py tests/unit/test_config.py tests/unit/test_plugin_system.py
git commit -m "fix: reject unsupported host events"
git show --check --stat HEAD
```

Review：两类未知宿主事件都在任何业务副作用前失败；模型未知工具行为没有改变。发现问题后 amend。

### Task 4: 删除虚构 Renderer 事件

**Files:**
- Modify: `app/plugins/events.py:1-75`
- Modify: `app/renderers/manager.py:18-70`
- Modify: `tests/unit/test_renderer_manager.py`

**Interfaces:**
- Produces: `RENDERER_EVENTS` 只含有生产 emit 点的 app、LLM、TTS started/finished 事件。

- [ ] **Step 1: 写真实事件表失败测试**

```python
def test_renderer_subscribes_only_to_host_emitted_events() -> None:
    from app.plugins.events import (
        EVENT_APP_CLOSING,
        EVENT_APP_STARTED,
        EVENT_LLM_REQUEST_FAILED,
        EVENT_LLM_REQUEST_FINISHED,
        EVENT_LLM_REQUEST_STARTED,
        EVENT_TTS_FINISHED,
        EVENT_TTS_STARTED,
    )
    from app.renderers.manager import RENDERER_EVENTS

    assert RENDERER_EVENTS == (
        EVENT_APP_STARTED,
        EVENT_APP_CLOSING,
        EVENT_TTS_STARTED,
        EVENT_TTS_FINISHED,
        EVENT_LLM_REQUEST_STARTED,
        EVENT_LLM_REQUEST_FINISHED,
        EVENT_LLM_REQUEST_FAILED,
    )
```

- [ ] **Step 2: 运行并确认 RED**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_renderer_manager.py -q
```

Expected: `RENDERER_EVENTS` 仍额外包含 pet/user 预留事件，测试失败。

- [ ] **Step 3: 删除未派发常量与订阅**

从 `app/plugins/events.py` 删除第 4-6 节全部未接线预留常量以及同样无生产 emit 点的 `EVENT_TTS_FAILED`；从 renderer import 和 `RENDERER_EVENTS` 删除对应名称。保留 app、LLM、TTS started/finished、chat、tool 的真实事件常量。

- [ ] **Step 4: 运行 GREEN 与全仓扫描**

```powershell
.\runtime\python.exe -m pytest tests/unit/test_renderer_manager.py tests/unit/test_plugin_advanced.py tests/ui/test_pet_window.py -q -k "renderer or plugin_bus or app_closed or tts"
rg -n "(^|[^A-Z_])EVENT_(PET|USER|SCREEN|AGENT)_" app plugins tests
```

Expected: pytest 通过；`rg` 无输出。

- [ ] **Step 5: 提交并双 review**

```powershell
git add app/plugins/events.py app/renderers/manager.py tests/unit/test_renderer_manager.py
git commit -m "refactor: remove fictional renderer events"
git show --check --stat HEAD
```

Review：只删除没有 emit 点的声明；真实 app/LLM/TTS renderer 转发测试保持通过。发现问题后 amend。

### Task 5: 结构、全量与净删验收

**Files:**
- Verify only: 本计划所有改动

- [ ] **Step 1: 精确结构扫描**

```powershell
rg -n "BuiltinToolProvider|register_from_provider|_handler_from_callable" app plugins
rg -n "def _normalize_tool_handler" app/plugins
rg -n '"proactive_check"' app/agent/runtime.py
rg -n "(^|[^A-Z_])EVENT_(PET|USER|SCREEN|AGENT)_" app plugins tests
```

Expected: 第一、三、四条无输出；第二条只命中 `app/plugins/capabilities.py` 一次。

- [ ] **Step 2: 编译与分层测试**

```powershell
.\runtime\python.exe -m compileall -q app plugins main.py
.\runtime\python.exe -m pytest tests/unit -q
.\runtime\python.exe -m pytest tests/integration -q
.\runtime\python.exe -m pytest tests/ui -q
.\runtime\python.exe -m pytest -q
```

Expected: 全部退出 0，无 unknown pytest config warning。

- [ ] **Step 3: 生产净删与 Git 审查**

```powershell
$base = "bacb176"
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

Expected: 净删至少 300 行；提交按四个独立任务排列；工作树只剩 `link_sakura_runtime_tts.bat`。

- [ ] **Step 4: 最终规格与代码质量 review**

逐项回答：

1. 是否仍有第二个内置工具装配或 handler 适配实现？
2. 未知宿主事件是否可能调用模型或插件 hook？
3. Renderer 事件表中的每个值是否有生产 emit 点？
4. 是否误删 Plugin API v1 顶层 export、合法 hook 或模型未知工具失败反馈？
5. 删除的测试是否只属于已删除死入口，并有结构测试替代？

若发现问题，归属到 Task 1-4 最近提交，补失败测试、最小修正、amend、定点与全量重验；不创建“测试通过”空提交。
