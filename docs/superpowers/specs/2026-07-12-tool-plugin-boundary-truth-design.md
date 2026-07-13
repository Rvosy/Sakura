# 工具、事件与插件边界真实性设计

## 1. 背景

上一轮已经把 `PetWindow` 的等待态、主动事件与记忆整理状态收敛为单一来源。本轮继续保持正常用户流程、UI 和已公开 Plugin API v1 不变，审查工具注册、宿主事件和插件内部边界中“看似支持、实际没有生产调用”的代码。

当前审计确认四类问题：

1. `app/agent/tools/builtin/provider.py` 重新实现了 291 行内置工具 Provider，但生产启动始终调用 `create_builtin_tool_registry()`，该 Provider 没有任何生产导入。
2. 插件装饰器和 `PluginManager` 各自维护一套 callable → `handler(dict)` 签名适配，支持集合略有差异，直接注册与装饰器注册可能得到不同调用语义。
3. `AgentRuntime.handle_event()` 对未知事件返回普通回复，宿主拼写错误会伪装成一次成功处理；只有退役的 `proactive_check` 会显式失败。
4. 插件事件模块和 `RendererManager` 声明了 pet、idle 等从未由宿主事件总线派发的预留事件，形成不存在的 renderer 能力表面。

## 2. 目标与非目标

### 目标

- 删除未导出、无生产调用的旧工具 Provider 和空 namespace。
- 所有插件工具贡献经过同一个 handler 适配函数。
- `AgentRuntime` 只接受 `reminder_due` 与 `screen_awareness_check`；其他事件在调用模型前显式失败。
- 旧 fixed-hook `PluginManager.emit_event()` 对未知宿主事件显式失败，不再静默忽略编程错误。
- `RendererManager.RENDERER_EVENTS` 只包含宿主真实派发的事件。
- `app/`、`plugins/` 的删除量显著大于新增量。

### 非目标

- 不删除或改名 `app.plugins` 已公开的 Plugin API v1 类型、服务门面和权限。
- 不新增 Plugin API v2，也不为尚未接线的 UI/TTS/Agent 服务补写推测性后端。
- 不改变未知模型工具调用的现有失败结果；模型可能生成过期工具名，这属于受控输入错误，不是宿主编程错误。
- 不合并 fixed hook 与字符串事件总线；它们属于已公开的两种插件订阅方式，需通过独立版本化设计处理。
- 不触碰 `third_party/`、`tools/mcp/`、TTS 和 ResourceManager。

## 3. 方案比较

### 方案 A：安全边界收缩（采用）

只删除无生产调用、未从顶层 SDK 导出的死路径；统一内部 handler 适配；对宿主内部未知事件 fail-fast；保留 Plugin API v1。

优点：正常用户流程和现有插件清单不变，净删最大，回滚边界清晰。缺点：公开但尚未接真实后端的服务门面继续存在，留待版本化治理。

### 方案 B：发布 Plugin API v2

删除未接后端的服务，合并两套事件系统，要求插件升级清单版本。

优点：最终边界最干净。缺点：需要兼容矩阵、迁移文档和至少一个双版本加载期，明显超出本轮“宏观逻辑不变”的约束。

### 方案 C：把全部预留能力接上线

实现 UI、TTS、Agent 服务和所有 renderer 预留事件。

优点：表面 API 变成真实能力。缺点：大量新增行为和线程安全风险，与删除优先目标相反，也没有当前用户需求支撑。

## 4. 工具注册收敛

删除：

- `app/agent/tools/builtin/provider.py`
- `app/agent/tools/builtin/__init__.py`
- `app/agent/tools/screen/__init__.py`
- `ToolRegistry.register_from_provider()` 及只验证该死入口的测试

保留 `app/agent/builtin_tools.py::create_builtin_tool_registry()` 作为唯一内置工具装配入口。

把当前 `PluginManager._normalize_tool_handler()` 的完整语义移入 `app/plugins/capabilities.py`，作为插件能力层的单一内部适配器。`PluginCapabilityRegistry.register_tool()` 在接收 `ToolContribution` 时规范化 handler；`tool()` 装饰器直接注册原函数，不再先经过另一套 `_handler_from_callable()`。`PluginManager` 构造 `Tool` 时直接使用已经规范化的 handler。

必须保留以下输入形态：

- `handler(arguments)` / `handler(args)` 接收完整字典；
- 零参数 handler；
- 普通命名参数与 keyword-only 参数；
- `**kwargs`；
- 无法读取签名的 callable 回退为接收完整字典。

未知模型工具名继续返回 `ToolExecutionResult(success=False, error="未知工具…")`，让工具循环把真实错误反馈给模型。

## 5. 宿主事件真实性

`AgentRuntime.handle_event()` 的支持集合固定为：

```python
{"reminder_due", "screen_awareness_check"}
```

任何不在集合中的事件都记录一次拒绝日志并抛出 `ValueError`，不得调用 chat、tool loop 或 prompt 构建。`proactive_check` 不再需要独立分支；它自然走同一个未知事件拒绝路径。Runtime 中最后一个退役字面量随专用分支一起删除，历史配置键只允许保留在迁移模块。

`PluginManager.emit_event()` 是宿主内部 fixed-hook 入口。未知事件代表宿主调用点拼写或映射错误，应记录后抛出 `ValueError`；插件 hook 自身异常仍继续隔离，不能影响其他插件或宿主流程。

## 6. Renderer 事件表

删除 `app/plugins/events.py` 中没有生产 emit 点的以下常量：

- `pet.clicked`、`pet.dragged`、`pet.hidden`、`pet.reopened`
- `user.idle`、`user.returned`
- `screen.changed`、`screen.summary.updated`、`screen.error_detected`
- `agent.thinking.started`、`agent.thinking.finished`

同步从 `RendererManager.RENDERER_EVENTS` 删除对应订阅。`tts.failed` 同样没有生产 emit 点，因此连同预留常量一起删除；只保留并验证真实派发的 app、LLM 以及 TTS started/finished 事件。工具和聊天事件虽真实存在，但 renderer 当前没有消费契约，本轮不擅自扩展 renderer 行为。

## 7. 错误处理与兼容边界

- 未知 AgentEvent 和未知 fixed-hook 事件属于宿主编程错误，fail-fast。
- 模型生成未知工具名属于运行时输入错误，返回结构化失败结果。
- 插件 handler 抛错继续由 `ToolRegistry.execute()` 转成结构化失败结果。
- 插件 hook 抛错继续逐插件隔离。
- Plugin API v1 顶层 exports、manifest 版本、权限和真实服务 `input/mobile/resources` 不变。
- 未接后端的公开服务不在本轮删除；后续若治理，必须新建 Plugin API v2 设计。

## 8. 测试策略

严格 TDD：

1. 结构测试先证明旧 Provider 仍存在、`register_from_provider()` 仍可见。
2. 参数化测试同时经装饰器和直接 `ToolContribution` 路径验证六种 handler 签名，先暴露两套适配的不一致。
3. 未知 AgentEvent 测试断言 `ValueError`、客户端零调用和拒绝日志。
4. 未知 fixed-hook 事件测试断言显式失败；合法 hook 与 hook 异常隔离测试继续通过。
5. Renderer 结构测试断言事件表只含真实 emit 事件，并扫描预留常量退出生产代码。
6. 每个任务后运行相关 unit/integration；最终运行 compileall、unit、integration、UI 与全量 pytest。

不删除有效行为测试；只删除专门证明死 Provider 兼容入口存在的测试，并以“唯一装配入口”结构测试替代。

## 9. 提交组织

1. `refactor: remove dead builtin tool provider`
2. `refactor: unify plugin tool handlers`
3. `fix: reject unsupported host events`
4. `refactor: remove fictional renderer events`

每个提交先 RED、后 GREEN，并在提交后分别做规格 review 与代码质量 review；发现问题后 amend。

## 10. 验收标准

1. `create_builtin_tool_registry()` 是内置工具唯一生产装配入口。
2. 插件工具 handler 只有一套签名适配实现。
3. 未知 AgentEvent 和 fixed-hook 事件不会伪装成成功。
4. Renderer 只订阅宿主真实派发的事件。
5. Plugin API v1 顶层 exports 与现有内置插件行为不变。
6. 相关测试和全量测试通过，无 pytest 配置 warning。
7. `app/`、`plugins/` 删除量大于新增量，目标净删至少 300 行。
8. 工作树最终只保留用户原有 `link_sakura_runtime_tts.bat`。
