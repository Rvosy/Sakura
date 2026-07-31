---
kind: plan
status: archived
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# 交接：Sakura issue #94 资源管理器重构（第 5 阶段后）

> 给后续会话的上下文交接。配合 `docs/archive/plans/resource-manager/RUNTIME_RESOURCE_MANAGER_PLAN.md`（设计文档）和
> `docs/archive/plans/resource-manager/RESOURCE_MANAGER_PHASE_4_5_PLAN.md`（第 4/5 阶段实施记录）一起读。

## 项目与分支
- 仓库根目录：`C:\Users\LBW\MyFile\sakura-project\Sakura`（PySide6/Qt 桌宠，Windows）。
- 当前分支：`refactor/resource-manager`。issue #94 的第 1-5 阶段核心改造已完成。
- 用 `./runtime/python.exe -m pytest ...` 运行测试，不要用系统 Python。

## 已完成
1. 第 1+2 阶段：`QtWorkerResource` 与 `ResourceManager(QObject)` 接管 PetWindow 的 QThread worker 生命周期、
   lingering QThread 与 Shiboken wrapper 保留窗口。
2. 第 3 阶段：TTS Provider 拆分为服务进程、合成队列、播放端点；`ThreadResource` / `ProcessResource`
   接入资源层。
3. 第 4 阶段：Backchannel 分类线程由 `ThreadGroupResource` 管理；`PetWindow.close_external_tools()`
   只保留即时 `cancel()`，join/lingering 统一走 `resource_manager.stop_all()`。
4. 第 5 阶段：建立 App 级 `ResourceRegistry`，`ResourceManager(QObject)` 作为 Qt wrapper 持有同一个 registry。
   - `AppContext.resource_registry` 由 bootstrap 创建并传给 MemoryStore、PluginManager、MCP provider 等服务。
   - `ServiceResource` / `AsyncLoopResource` 已落地。
   - MCP bridge 的 asyncio loop + daemon thread 由 `AsyncLoopResource` 托管，Provider 自身注册为 service。
   - MemoryStore 的 preload/reload 线程由 `ThreadGroupResource` 托管，新增 `close()` 失效迟到结果并关闭 runtime。
   - PluginManager 接 shared registry，`shutdown_all()` 幂等；`PluginServices.resources` 允许插件登记 cleanup/thread/executor。
   - 内置 `playwright_browser` 通过资源门面登记 `browser.shutdown_browser()`，`shutdown()` 只作兼容兜底。
   - PetWindow 主关闭链路收敛为：发关闭事件、取消 UI 流、`resource_manager.stop_all()`。

## 必须保持的约束
- `QWidget` / `QPixmap` / `QMediaPlayer` / `QAudioOutput` / Qt UI timer 仍留在 UI 主线程。
- `PetWindow` 仍持有 `self.worker` / `self.worker_thread` 等属性，避免打断现有处理器与测试断言。
- 插件只能经 service facade；不要让插件接触 `PetWindow`、TTS provider 内部对象或全局 ResourceManager。
- `MemoryStore.close()` 必须先失效 generation，再停登记线程，最后关闭当前 runtime。
- `close_external_tools()` 不再手写串联 `close_tts_tools()` / `close_mcp_tools()` / `close_plugins()` /
  `_close_renderer_manager()`；这些入口只保留为测试、设置热切换或兜底调用。

## 验证命令
已验证：

```powershell
./runtime/python.exe -m pytest tests/unit/test_resource_manager.py tests/ui/test_backchannel_controller.py tests/ui/test_pet_window.py::test_close_external_tools_cancels_and_keeps_lingering_thread tests/ui/test_pet_window.py::test_pet_window_registers_runtime_services_in_registry_order tests/unit/test_mcp_runtime.py tests/unit/test_plugin_services.py tests/unit/test_memory_store_resources.py -q -p no:warnings
```

结果：`61 passed`。

```powershell
./runtime/python.exe -m pytest tests/unit/test_bootstrap.py tests/unit/test_plugin_system.py tests/unit/test_plugin_advanced.py tests/unit/test_plugin_services.py tests/unit/test_mcp_runtime.py tests/unit/test_memory_store_resources.py -q -p no:warnings
```

结果：`76 passed`。

```powershell
./runtime/python.exe -m pytest tests/ui/test_pet_window.py::test_pet_window_locks_controls_during_startup_initialization tests/ui/test_pet_window.py::test_pet_window_unlocks_after_deferred_services_are_applied -q -p no:warnings
```

结果：`2 passed`。

```powershell
./runtime/python.exe -m pytest tests/integration/test_native_tool_calls.py::test_plugin_manager_loads_playwright_browser_plugin tests/integration/test_agent_core.py::test_playwright_plugin_registers_native_browser_tools -q -p no:warnings
```

结果：`2 passed`。

说明：部分 pytest 运行结束后，Windows 临时目录 `pytest-current` 清理会打印 `PermissionError: [WinError 5]`
的 atexit 提示，但进程退出码为 0，测试本身已通过。

## 已知环境问题
- `tests/ui/test_history_window.py` 需要 `qtbot`（runtime 未装 pytest-qt）。
- `test_public_api_cleanup.py::test_legacy_sdk_package_is_removed` 可能受工作树未跟踪 `sdk/` 残留影响。
- 若 tests/ui 退出阶段遇到 native access violation，先按既有文档重跑并对照
  `docs/records/incidents/TTS_SHUTDOWN_NATIVE_CRASH.md` 判断是否为既有环境性问题。

## 后续建议
- 若要继续演进，可把更多插件后台资源迁到 `context.services.resources`，但不要破坏旧插件 `shutdown()` 兼容。
- 若要做 release，请先跑更大范围回归，并确认未跟踪 changelog / 临时文件是否属于本次发布范围。
