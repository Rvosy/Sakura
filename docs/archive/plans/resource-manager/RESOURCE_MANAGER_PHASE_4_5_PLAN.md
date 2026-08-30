---
kind: plan
status: archived
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# 第 4 & 5 阶段实施计划：接话资源化 / memory·MCP·plugin 统一治理（issue #94）

> 承接第 1+2 阶段（QThread worker 托管）与第 3 阶段（TTS Provider 拆分，见
> `docs/archive/plans/tts/TTS_PROVIDER_SPLIT_PLAN.md`）。本文给出第 4、5 阶段的可执行计划，供新会话直接照做。
> 配合阅读 `docs/archive/plans/resource-manager/RUNTIME_RESOURCE_MANAGER_PLAN.md`（设计/状态机/线程域）与
> `docs/archive/plans/resource-manager/RESOURCE_MANAGER_HANDOFF.md`（总交接）。

## 完成记录（2026-06-20）

第 4/5 阶段已按“彻底版 App 级资源域收口”实现：

- 第 4 阶段：Backchannel 分类线程改由 `ThreadGroupResource` 托管；PetWindow 关闭时只做 cancel，
  等待/lingering 统一交给 `resource_manager.stop_all()`。
- 第 5 阶段：新增纯 Python、线程安全的 `ResourceRegistry`；`ResourceManager(QObject)` 持有 shared registry。
- `AppContext.resource_registry` 由 bootstrap 创建，并传入 MemoryStore、PluginManager、MCP provider 等服务。
- MCP bridge 使用 `AsyncLoopResource`；`MCPToolProvider` 自身注册为 `ServiceResource`。
- MemoryStore loader/reloader 使用 `ThreadGroupResource`，新增 `close()` 失效 generation、停止线程、关闭 runtime。
- PluginManager 接入 shared registry，`PluginServices.resources` 提供 cleanup/thread/executor 登记入口；
  内置 `playwright_browser` 已通过资源门面登记浏览器清理。
- `PetWindow.close_external_tools()` 主链路收敛为：发关闭事件、取消 UI 流、`resource_manager.stop_all()`；
  不再手写串联 TTS / MCP / plugin / renderer close。

验证见 `docs/archive/plans/resource-manager/RESOURCE_MANAGER_HANDOFF.md`。

## 0. 项目与分支
- 仓库根目录：`C:\Users\LBW\MyFile\sakura-project\Sakura`（PySide6/Qt 桌宠，Windows）
- 当前分支：`refactor/resource-manager`（从 `origin/dev` 切出），第 1-5 阶段核心改造已完成。
- 测试：`./runtime/python.exe -m pytest`（**别用系统 Python**，Anaconda 的 PySide6 会崩 0xc0000139）。
- 已知 3 个**环境性/计时性**问题，与重构无关：
  - `tests/ui/test_history_window.py`（runtime 没装 pytest-qt）。
  - `test_public_api_cleanup.py::test_legacy_sdk_package_is_removed`（工作树残留未跟踪 `sdk/`）。
  - **tests/ui 退出阶段约 1/3 概率的 `Windows fatal exception: access violation`**：早于第 3 阶段
    就存在的 daemon 线程 / Qt 析构竞态，详见 `docs/records/incidents/TTS_SHUTDOWN_NATIVE_CRASH.md`。重跑即可，
    关注非崩溃运行是否全绿（`tests/unit + tests/ui` = 988 passed）。
- 工作方式：**分段提交 git**，每个提交保持测试绿（破坏某测试就在同一提交里改它）；用中文。
  工作树里两个未跟踪的 `docs/*CHANGELOG.md` 与本次无关，别动。

---

# 第 4 阶段：接话（Backchannel）模块资源化

## 4.1 现状事实（执行前已确认）
`app/backchannel/controller.py` 的 `BackchannelController`：
- `_dispatch_async(text)`：每次后台分类 `threading.Thread(target=run_classification, name="sakura-backchannel-<token>")`，
  **非 daemon**，加入 `self._classify_threads`（`set`，由 `self._classify_threads_lock` 守护）。
- 结果经 QObject 信号 `self._classify_signals.done.emit(token, label)` queued 回 UI 线程的
  `_on_classify_done`；`emit` 包 `try/except RuntimeError`（宿主窗口销毁时静默丢弃）。
- 串行门控：`_classify_token`/`_inflight_token`——新一轮 dispatch 递增 token 使旧结果失效；
  但**旧线程可能仍在跑**（lingering），靠 `_shutdown` 标志在 emit 前判定不再投递。
- `shutdown(timeout)`：置 `_shutdown=True` → `cancel()` → 轮询 `join` 所有 `_classify_threads`，
  超时返回 `False`（线程非 daemon，后台自然完成）。`_classify_timeout_timer`（QTimer）做超时兜底。
- 分类器：`HybridBackchannelClassifier`（`prefers_background=True` 走后台线程）、`RuleClassifier`（同步）。
  模型/manifest 加载见 `app/backchannel/{hybrid_classifier,model_cache,manifest}.py`。

`PetWindow.close_external_tools()`（`app/ui/pet_window.py:1220`）当前关闭序列：
1. app-closed 事件、停 speaking watchdog、`subtitle_controller.cancel_reply_flow()`。
2. **`backchannel_controller.shutdown(THREAD_SHUTDOWN_WAIT_MS/1000)`**（独立于 RM 的 join 逻辑）。
3. `self.resource_manager.stop_all(THREAD_SHUTDOWN_WAIT_MS)`。
4. `close_tts_tools()` / `close_mcp_tools()` / `close_plugins()` / `_close_renderer_manager()`。

控制器在 `PetWindow.__init__`（约 `:883`）创建：`self.backchannel_controller = BackchannelController(...)`，
设置变更时会重建（settings 保存路径）。PetWindow 已持有 `self.resource_manager`（`:582`）。

## 4.2 待拍板决策（动手前确认）
1. **RM 挂载方式**：接话线程注册到**谁的 RM**？
   - 备选 A（推荐）：复用 **PetWindow 的 `resource_manager`**，把它注入 `BackchannelController`。
     接话控制器生命周期与窗口一致、关闭统一走 `stop_all`，无 TTS 那种「热切换需自持 RM」的诉求。
   - 备选 B：控制器自持 RM（对齐 TTS）。仅在「接话控制器会被频繁独立退役」时才需要——目前不是。
   > 取 A，除非发现控制器重建时机要求独立退役。
2. **多线程并发模型**：接话允许多个在飞线程（superseded 线程 lingering），不是 TTS 的单线程串行。
   需要 RM 支持「一组线程」的资源。见 §4.3。
3. **结果信号归属**：`_classify_signals`（QObject + `done` 信号）保持现状（控制器持有），
   不强行并入 RM；RM 只接管线程生命周期，信号 marshal 语义不动。

## 4.3 RM 扩展（提交 4.1）
在 `app/core/resource_manager.py` 新增并扩 `tests/unit/test_resource_manager.py`：
- **`ThreadGroupResource`**（或给现有 `ThreadResource` 加「集合模式」）：托管一组裸 Python 线程。
  - `add(thread)`：登记一个新在飞线程；线程结束自行 `discard`（用 wrapper 包 target，在 `finally`
    里回调 `resource._on_thread_done(thread)`，对齐控制器现有 `finally: discard`）。
  - `stop(timeout_ms)`：`cancel()`（置关闭标志）→ 对每个在飞线程 `join`，整体超时则转 lingering
    （记 `_lingering_threads`），返回是否全部干净停止。**复刻控制器现有 `shutdown` 的轮询 join 语义**。
  - `is_running()`：任一线程 `is_alive()`。
- `ResourceManager.track_thread_group(*, cancel=None, label="") -> ThreadGroupResource` 工厂。
- 保持现有 22 个 RM 测试绿；新增 group 的 add/完成自摘除/stop 干净/stop 超时 lingering 测试。

> 本提交只动 `resource_manager.py` + 其单测，不碰 backchannel，独立绿。

## 4.4 提交序列（每个提交独立保持测试绿）
1. **RM 扩展**：见 §4.3。`./runtime/python.exe -m pytest tests/unit/test_resource_manager.py -q`。
2. **控制器接 RM**：`BackchannelController.__init__` 接收注入的 `resource_manager`（备选 A），
   创建 `self._thread_group = resource_manager.track_thread_group(cancel=<置 _shutdown>, label="backchannel")`；
   `_dispatch_async` 改 `self._thread_group.add(thread)`，删 `_classify_threads`/`_classify_threads_lock`
   的手写集合与 `finally: discard`（由 group 的 target wrapper 接管）。`shutdown(timeout)` 委托
   `self._thread_group.stop(timeout*1000)`。**`_classify_signals` / token 门控 / 超时 QTimer 不动**。
   改 `tests/ui/test_backchannel_controller.py` 中依赖 `_classify_threads`/`shutdown` 内部的断言。
3. **PetWindow 装配**：`PetWindow.__init__` 把 `self.resource_manager` 传给 `BackchannelController`；
   `close_external_tools` 删第 2 步那段独立 `backchannel_controller.shutdown(...)`（线程现由
   `resource_manager.stop_all` 统一收敛）——**但保留 `cancel()` 即时止血**（停新任务、失效 token）。
   注意控制器**重建**时（settings 变更）：旧控制器的 group 资源要从 RM 注销（`stop` 或 `detach`），
   避免泄漏。改 `tests/ui/test_pet_window.py` 相关关闭序列断言。

## 4.5 必保语义（回归核对清单）
- 后台分类结果只在 `token == _inflight_token` 时采纳；cancel/超时/新一轮使旧结果失效。
- 宿主窗口销毁后迟到的 `done.emit` 静默丢弃（`try/except RuntimeError` 不回归）。
- `shutdown` 超时不强杀线程，转后台自然完成（非 daemon，lingering）。
- 关闭顺序：先 `cancel`（停新任务）再 `stop_all` join；UI 退出不被无限阻塞（有限 timeout）。
- `prefers_background` 同步/后台两条分类路径行为不变。

## 4.6 测试耦合
- `tests/ui/test_backchannel_controller.py`：直接断言 `shutdown` 返回值、`is_pending`、token 失效、
  `_classify_threads` 清空——迁到 group 后改为断言 group 状态 / `is_running`。
- `tests/ui/test_pet_window.py`：`close_external_tools` 关闭序列、ThreadStub/WorkerStub 时序。
- `tests/unit/test_backchannel_audio_cache.py`：与线程无关，应无需改。

---

# 第 5 阶段：memory / MCP / plugin 统一治理（风险最高）

## 5.1 现状事实（执行前已确认）
- **MCP bridge**（`app/agent/mcp/bridge.py`，`ASYNC_LOOP_THREAD` 域）：
  `_run_loop` = `asyncio.new_event_loop()` + `run_forever()`，跑在 **daemon** `self._thread`；
  `run_coroutine_threadsafe(coro, self._loop)` 投递协程；`close()` = `call_soon_threadsafe(loop.stop)`
  + `self._thread.join(timeout=5)` + 置空 `_loop/_thread`。PetWindow `close_mcp_tools` →
  `self.mcp_tool_provider.close()`（`pet_window.py:1403` 区域）。
- **memory**（`app/agent/memory.py`，`PYTHON_THREAD` 域）：`preload(wait)`；后台 **daemon** 线程
  `sakura-mem0-loader`（`:643`）与 `sakura-mem0-reloader`（`:333`）做后端模型加载/重载。
- **plugins**（`app/plugins/manager.py`）：`shutdown_all()`（`:324`）；插件经 service facade，
  **不得接触 PetWindow/TTS 内部实例**（硬约束）。插件可能自带线程/子进程——治理目标是让插件
  生命周期也走统一资源契约，且 facade 边界不被打破。

## 5.2 待拍板决策（动手前确认）
1. **ServiceResource 契约**：实现设计文档 `ManagedResource`（`start/stop(timeout)/restart(reason)/
   health()/close()/state_changed`）的最小可用子集，先服务 MCP，再推广 memory/plugin。
2. **MCP 挂载**：MCP bridge 建模为 **`AsyncLoopResource`**（新资源类型），注册进 **PetWindow 的 RM**；
   `stop` 复刻 `call_soon_threadsafe(stop) → join(timeout) → linger`；`restart` 供重连复用。
3. **memory 线程优先级**：daemon 线程随进程退出，收益主要是「可见性 + 统一关闭」，**优先级低**；
   可只做轻量 `ThreadResource`/`ThreadGroupResource` 登记，不强求 join（保持 daemon 语义）。
4. **plugin 边界**：本阶段**不重写插件 API**；只把 `shutdown_all` 纳入统一关闭、并为「插件自带后台
   资源」提供可选的 RM 登记入口（经 facade，不暴露内部实例）。激进重构留到独立 issue。

## 5.3 RM 扩展（提交 5.1）
- **`AsyncLoopResource`**：托管「asyncio 事件循环 + 其 daemon 线程」。
  - `stop(timeout_ms)`：`loop.call_soon_threadsafe(loop.stop)` → `thread.join(timeout)` → 超时 lingering。
  - `restart()`：停旧循环线程，经注入的 `loop_factory` 重建（供重连）。
  - `is_running()`：`thread.is_alive()`；`health()`：循环是否在 `run_forever`。
  - `submit(coro)` 可选：包 `run_coroutine_threadsafe`，集中错误处理。
- 复用第 1 阶段的 lingering / `StoppableResource` 协议；扩 `test_resource_manager.py`。

> 本提交只动 `resource_manager.py` + 其单测，独立绿。

## 5.4 提交序列（每个提交独立保持测试绿）
1. **RM 扩展**：`AsyncLoopResource`（+ 视需要 ServiceResource 基类）。见 §5.3。
2. **MCP 接 AsyncLoopResource**：`bridge.py` 把 `_loop/_thread/_run_loop/close` 改为持有 RM 的
   `AsyncLoopResource`；`close()` 委托 `resource.stop(...)`。PetWindow 注入 RM、`close_mcp_tools`
   改走（或保留薄 `mcp_tool_provider.close()` 委托）。重写 MCP bridge 测试。
3. **memory 线程登记（轻量）**：`memory.py` 的 loader/reloader daemon 线程经注入的 RM 登记为
   `ThreadResource`/group（保持 daemon，不强 join）；仅为可见性与统一关闭兜底。改相关测试。
4. **plugin 统一关闭收口**：`shutdown_all` 纳入 RM 关闭序列；为插件后台资源提供经 facade 的
   可选 RM 登记入口（不暴露内部实例）。改 plugin 关闭相关测试。
5. **收尾**：更新设计文档（`ServiceResource` 不再「规划中」）、交接、本文件，标记第 4/5 阶段完成；
   全量回归（非崩溃运行 `tests/unit + tests/ui` 全绿）。

## 5.5 必保语义（回归核对清单）
- MCP：`run_coroutine_threadsafe` 投递、`close` 的 `call_soon_threadsafe(stop)+join(5s)` 语义、
  关闭后 `_loop/_thread` 置空、重连不泄漏旧循环线程。
- memory：`preload(wait)` 阻塞/非阻塞两路；daemon 线程不阻塞 UI 退出。
- plugin：facade 边界不被打破（插件不接触 PetWindow/TTS 内部）；`shutdown_all` 行为不回归。
- 关闭顺序整体不变（§设计文档「关闭顺序」），UI 退出不被无限阻塞。

## 5.6 测试耦合
- MCP bridge 单测（`_loop`/`_thread`/`run_coroutine_threadsafe`/`close` 内部断言）。
- `tests/ui/test_pet_window.py`：`close_mcp_tools`/`close_plugins`/关闭序列。
- 插件相关测试（`tests/.../plugins*`）：`shutdown_all` 与 facade 边界。
- memory 相关测试：preload / loader 线程。

## 5.7 风险提示
- `ASYNC_LOOP_THREAD` 的关闭最易出 native crash：`run_coroutine_threadsafe` 在循环 stop 后投递、
  或循环线程 join 超时与解释器退出叠加。严格 `call_soon_threadsafe` + 有限 timeout + lingering。
- 插件是开放生态，治理要克制：先「纳入统一关闭 + 可选登记」，不动插件 API。
- 先做 MCP（边界清晰、收益明确），memory/plugin 视余量推进；每段绿、逐项核对 §5.5。

---

## 后续提示

第 4/5 阶段已完成。本文件保留原计划作为实现依据；后续若继续演进，优先围绕
`PluginServices.resources` 迁移更多插件自带后台资源，并保持旧插件 `shutdown()` 兼容。
