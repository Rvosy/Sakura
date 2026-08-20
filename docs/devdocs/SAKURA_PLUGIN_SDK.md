---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-08-20
---

# Sakura Plugin API v3 开发指南

Runtime v2 只激活 Plugin API v3。插件运行在 generation-private Plugin Worker 中，普通插件间通过本地
Service 组合；只有 Host Service、公开 export 和 callback 才经过通用 Bridge。Plugin Worker 提供故障与
生命周期隔离，但不是权限沙箱：插件仍拥有当前账户权限。

完整不变量以 [Plugin Kernel v3 规范](../specs/runtime-v2/sakura-plugin-kernel-v3.md) 为准。不要依赖
`app.plugins.manager`、Legacy Capability Registry、Qt 对象或未写入规范的 Sakura 内部模块。

## 1. 目录与 manifest

可安装插件的最小目录：

```text
my-plugin/
  plugin.yaml
  plugin.py
```

`plugin.yaml`：

```yaml
api: 3
id: com.example.echo
name: Echo
version: 0.1.0
author: Example
description: 提供一个简单的 Echo Service。
entry: plugin:EchoPlugin
provides:
  - com.example.echo
requires: []
optional: []
enabled: true
```

| 字段 | 说明 |
|---|---|
| `api` | 必须为 `3`。其他版本只显示 `API_VERSION_UNSUPPORTED`，实现不会被导入。 |
| `id` | 稳定身份，1–64 个 ASCII 字母、数字、`.`、`_`、`-`，不得以 `.` 结尾；建议使用反向域名前缀。 |
| `entry` | `module:ClassName`，模块必须位于插件目录内。 |
| `provides` | setup 后稳定提供的 Service，用于冲突预检和加载顺序。 |
| `requires` | 缺失任一 Service 时插件保持 `waiting`，不会 import 实现。 |
| `optional` | 可通过 `ctx.inject()` 响应出现/消失的 Service。 |
| `enabled` | 内置清单的默认 desired state；本地安装始终额外保存为禁用。 |
| `required` | 仅供 Sakura 内置基础插件使用；用户插件声明它会被安装器拒绝，手工放入也不会被 import。 |

v3 没有 permission 字段、优先级选 Provider、依赖下载或 Service 版本协商。manifest 的依赖字段用于生命
周期和诊断，不是权限检查。

## 2. 最小 Provider

`plugin.py`：

```python
class EchoService:
    def echo(self, text: str) -> dict[str, str]:
        return {"text": text}


class EchoPlugin:
    def setup(self, ctx) -> None:
        ctx.provide(
            "com.example.echo",
            EchoService(),
            exports=("echo",),
        )
```

`ctx.provide()` 返回 disposer，并自动绑定插件 root Effect。普通 Worker 内消费者拿到的是本地 Python 对象，
不经过 JSON/RPC；`exports` 只决定哪些方法可以被 Core 通过 `service.call` 调用。

## 3. Consumer 与响应式依赖

required Consumer 可以直接 `get()`：

```python
class EchoConsumerPlugin:
    def setup(self, ctx) -> None:
        echo = ctx.get("com.example.echo")
        ctx.provide("com.example.echo-consumer", echo)
```

optional/dynamic Service 使用 `inject()`：

```python
class OptionalConsumerPlugin:
    def setup(self, ctx) -> None:
        def bind_echo(echo, scope) -> None:
            scope.on("com.example.echo.changed", lambda payload: echo.echo(payload["text"]))

        ctx.inject("com.example.echo", bind_echo)
```

Service 出现时创建 child Effect scope，消失时自动 dispose。不要把 `inject()` 回调取得的 Service 泄漏到
scope 外长期持有。

## 4. Context 与 Effect

插件入口只需实现 `setup(ctx)`。公开 Context：

| API | 用途 |
|---|---|
| `ctx.provide(key, service, exports=())` | 提供唯一 Application Service。 |
| `ctx.get(key)` | 一次性取得当前 Service；缺失时失败。 |
| `ctx.inject(key, setup)` | 跟随可选 Service 生命周期创建 child scope。 |
| `ctx.on(name, handler)` / `ctx.emit(name, payload)` | 事实事件；`sakura.host.*` 只能由 Host 发出。 |
| `ctx.on_transform(name, handler)` / `ctx.transform(name, value)` | 顺序转换；失败时保留上一个有效结果。 |
| `ctx.effect(cleanup)` | 绑定 timer、thread、文件、socket、子进程等清理动作。 |
| `ctx.config` | 插件私有 JSON 配置。 |
| `ctx.data_path(relative)` | 解析 `data/plugins/<plugin_id>/` 下的安全持久路径。 |

setup 是 all-or-nothing。setup 返回前，Service、Host contribution 和 callback 都处于 staged 状态；任何异常
都会逆序清理整个 root scope。cleanup 必须幂等，不能只依赖可选的兼容 `shutdown()` hook。

```python
class ResourcePlugin:
    def setup(self, ctx) -> None:
        worker = start_owned_worker()
        ctx.effect(worker.close)
```

不要在后台线程直接调用 Host Service；Bridge 由 Worker dispatch owner thread 串行拥有。后台任务应只更新
自身线程安全状态，主回调再读取结果。

## 5. Config 与数据

```python
class ConfigurablePlugin:
    def setup(self, ctx) -> None:
        current = ctx.config.get()
        ctx.config.on_change(self._apply)
        cache = ctx.data_path("cache/index.db")

    def _apply(self, values):
        return "applied"  # 或 restart_required / error
```

- 安装目录中的 `config.json` 是默认值；用户 override 写入 `data/plugins/<plugin_id>/config.json`。
- `config.save()` / `update()` 顶层 merge，`replace()` 才替换整份 override。
- 没有 `on_change` handler 时，保存默认返回 `restart_required`。
- 插件数据库、模型和缓存写入 `ctx.data_path()`，不得写入代码目录、角色包或 generation artifact。
- 卸载代码不会删除插件数据。

## 6. Host Service

第一阶段可通过 `ctx.get()` 使用：

| Service | 用途 |
|---|---|
| `sakura.host.tools` | 注册 Agent Tool descriptor 与 callback。 |
| `sakura.host.context` | 注册每轮 prompt 的受限 Context Contributor。 |
| `sakura.host.settings` | 注册声明式字段、Action 与受限 Collection。 |
| `sakura.host.character` | 读写本插件的角色 extension，并解析角色包资源。 |
| `sakura.host.artifacts` | 分配、提交、释放 generation-bound 二进制工件。 |

示例 Tool：

```python
class ToolPlugin:
    def setup(self, ctx) -> None:
        tools = ctx.get("sakura.host.tools")
        tools.register(
            {
                "name": "example_echo",
                "description": "回显输入文本。",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                "group": "default",
                "risk": "low",
            },
            lambda args: {"text": str(args["text"])},
        )
```

Host registration 和 callback handle 自动绑定当前 Plugin/Effect/generation。插件禁用、setup 回滚或 Worker
重建后旧 handle 立即失效。大文件和二进制不得 Base64 塞进 callback；使用 artifacts 的
`allocate → 写入 → commit`，并把 committed descriptor 交给支持它的 Host consumer。

Settings 只接受宿主定义的声明式字段、非危险 Action 和受限 Collection，不支持 HTML、JavaScript、CSS、
Qt widget 或自定义 renderer。参考内置插件 `plugins/playwright_browser/` 和 `plugins/sakura_mem0/` 的真实
descriptor；这些是普通 v3 消费者，不代表新的领域 API。

## 7. 安装与本地验证

设置页支持选择插件文件夹或 ZIP。安装器在 import 前执行边界检查，复制到：

```text
data/user_plugins/<plugin_id>/
```

安装后默认禁用；显式启用并保存才会加载代码。ZIP 可直接包含插件目录内容，或只有一层包装目录。包内不
得包含 symlink/junction、特殊文件、绝对路径、`..`、重复/大小写冲突路径或 Windows 非法名称。安装器不会
运行 `pip`，依赖必须由 Sakura 已有运行环境提供，或由插件以合规方式自带纯 Python 模块。

用户插件不能通过 manifest 或 `plugins.yaml` 获得 `required`。手工复制的违规插件会显示
`PLUGIN_MANIFEST_INVALID`，但仍保留禁用和卸载入口；修正 manifest 或卸载代码不会删除其 plugin-data。

开发时可把插件目录放到 `data/user_plugins/`，然后运行：

```bash
runtime/bin/python -m harness run journey-plugins
runtime/bin/python -m pytest -q tests/unit/test_plugin_kernel_v3.py
```

Windows 使用 `runtime\\python.exe`。至少验证 setup 失败回滚、Provider 消失/恢复、禁用清理、Worker 重建
和配置应用状态；会写数据的 callback 还应验证 timeout 后不自动重放。

## 8. 兼容与故障语义

- Runtime v2 不兼容 API v2。旧 manifest 只显示稳定诊断，不能通过关闭/重载改变不兼容原因。
- Callback/Event/Service 调用有 deadline。超时会使失去响应的 Worker 失效并在同一 Core generation 重建；
  原调用不重试。
- 插件异常对外只投影稳定 reason code；不要让 URL、系统路径、参数、返回值或异常正文进入公开 DTO/日志。
- 不要为单个领域增加 Bridge RPC 或 `sakura.host.<domain>`。优先用普通 Service、Event、Effect、Config 和
  已存在的 Host Service 组合。
