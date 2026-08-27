---
kind: devdoc
status: current
audience: plugin-author
source_of_truth: ../specs/runtime-v2/sakura-plugin-kernel-v3.md
updated: 2026-08-27
---

# Plugin API v3 开发指南

一个插件包含 `plugin.yaml`、Python entry class，以及可选的 `config.json`。Sakura 在 Plugin Worker 中构造 entry，并调用一次 `setup(context)`。

## 最小插件

```yaml
api: 3
id: example
name: Example
author: Your Name
description: 提供一个回声工具。
version: 1.0.0
entry: plugin:ExamplePlugin
enabled: false
priority: 100
provides: []
requires:
  - sakura.host.tools
```

```python
class ExamplePlugin:
    def setup(self, context) -> None:
        tools = context.get("sakura.host.tools")
        tools.register(
            {
                "name": "example_echo",
                "description": "返回输入文字。",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                "group": "example",
                "risk": "low",
            },
            lambda arguments: {"text": arguments["text"]},
        )
```

`setup()` 返回 `None`。不要实现独立 `shutdown()`；清理动作交给 `context.effect()`。

## Manifest

- `api` 必须是 `3`。
- `id` 在内置插件和用户插件中全局唯一。
- `entry` 使用 `module:ClassName`。
- `provides` 声明插件发布的 Service。
- `requires` 声明其他插件 Service 或 Host Service。
- `priority` 决定同层插件的稳定加载顺序，不解决 Service 冲突。

缺少依赖时插件处于 `waiting`。同名 Service、依赖环、导入失败或 `setup()` 异常会得到稳定原因码，其他插件继续加载。

## PluginContextV3

```python
service = context.get("other.service")
dispose_service = context.provide("example.service", service_object, exports=("run",))
dispose_event = context.on("sakura.host.message.received", handle_message)
dispose_resource = context.effect(close_resource)
path = context.data_path("cache/index.json")
config = context.config.get()
```

`get()` 的 key 必须出现在 `requires`，`provide()` 的 key 必须出现在 `provides`。`exports` 是允许跨 Worker 调用的方法白名单。事件只能订阅 `sakura.host.*`，插件不能主动发布 Host event。

`data_path()` 只接受插件私有根下的相对路径。绝对路径、`..` 和路径逃逸会失败。

Effect 按 LIFO 清理。`setup()` 未完整成功时，暂存的 Host 注册不会对外可见。

## Host Services

| Service | 用途 |
|---|---|
| `sakura.host.tools` | 注册聊天工具 |
| `sakura.host.context` | 注册动态上下文贡献者 |
| `sakura.host.artifacts` | 分配、提交和释放受控文件 |
| `sakura.host.character` | 读取插件扩展字段和解析角色资源 |
| `sakura.host.timeline` | 按当前角色读取 Timeline |
| `sakura.host.settings` | 注册声明式设置区块 |
| `sakura.host.settings.surface-v0` | 把设置区块放到指定宿主页面 |
| `sakura.host.settings.collection-v0` | 注册分页 Collection |
| `sakura.host.model_slots` | 在模型页注册插件拥有的模型用途 |
| `sakura.host.ui.composer-tools-v0` | 在输入栏 `+` 菜单注册动作 |

Host Service 对象只在当前 Worker generation 有效。不要缓存到外部进程或跨重建复用。

## 配置

插件目录中的 `config.json` 是默认值，用户覆盖写入 `data/plugins/<plugin_id>/`。

```python
class ExamplePlugin:
    def setup(self, context) -> None:
        values = context.config.get()

        def apply_config(next_values):
            return "applied"

        context.config.on_change(apply_config)
```

可用操作为 `get()`、`update(values)`、`replace(values)` 和 `on_change(handler)`。handler 返回 `applied`、`restart_required` 或 `error`。没有 handler 时，保存默认要求重建 Worker。

## 声明式设置

```python
settings = context.get("sakura.host.settings")

def save_settings(values):
    states = context.config.update(values)
    if "error" in states:
        state = "error"
    elif "restart_required" in states:
        state = "restart_required"
    else:
        state = "applied"
    return {"applicationState": state}

settings.register(
    {
        "sectionId": "example",
        "title": "Example",
        "order": 100,
        "fields": [
            {
                "key": "enabled",
                "label": "启用功能",
                "type": "boolean",
                "default": False,
            }
        ],
        "actions": [],
    },
    load=lambda: context.config.get(),
    save=save_settings,
)
```

设置页只渲染 Host 支持的字段。普通输入字段会参与保存；`status` 和 `resource` 是只读投影，不能回传给 save callback。Resource 的 `taskState` 只使用 `idle`、`queued`、`running`、`succeeded`、`failed` 或 `cancelled`，progress 为 `null` 或 0–100。

`sakura.host.settings.surface-v0.register(section_id, surface)` 可以把区块放到 Host 提供的页面。具体 surface 名必须来自当前 Host 契约，不要自行拼接。

## 注册“关于 → 组件”

本地模型或运行时仍由插件自己下载和维护。插件用普通 Settings resource 声明状态，再投影到 About：

```python
settings = context.get("sakura.host.settings")
surface = context.get("sakura.host.settings.surface-v0")
settings.register(
    {
        "sectionId": "localRuntime",
        "title": "Example",
        "fields": [{
            "key": "runtime",
            "label": "本地运行组件",
            "type": "resource",
            "actionIds": ["install", "retry", "cancel"],
            "default": {
                "applicability": "required",
                "subtitle": "",
                "ready": False,
                "taskState": "idle",
                "message": "尚未安装",
                "detail": "",
                "progress": None,
                "availableActionIds": ["install"],
            },
        }],
        "actions": [
            {"actionId": "install", "label": "安装"},
            {"actionId": "retry", "label": "重试"},
            {"actionId": "cancel", "label": "取消"},
        ],
    },
    load=load_component,
    actions={"install": install, "retry": install, "cancel": cancel},
)
surface.register("localRuntime", "about")
```

About section 只能包含只读 `resource`，必须有 load，不得有 save 或 Collection，且所有 Action 都要被字段
引用。`applicability` 取 `required`、`not_required`、`unsupported`；省略时 Host 按 `required` 处理。状态读取
不得联网，只有用户点击安装或重试才可联网。插件必须用 `context.effect()` 取消并等待自己的下载线程。

## Collection

Collection 适合分页管理插件记录。注册时提供 descriptor 和 `query`，需要编辑时再提供 `create`、`update`、`delete`。

```python
collections = context.get("sakura.host.settings.collection-v0")
collections.register(
    "example",
    {
        "collectionId": "items",
        "title": "条目",
        "columns": [{"key": "title", "label": "标题", "type": "string"}],
        "fields": [{"key": "title", "label": "标题", "type": "text", "required": True}],
        "filters": [],
        "searchable": True,
        "pageSize": 25,
    },
    query=query_items,
    create=create_item,
    update=update_item,
    delete=delete_item,
)
```

query 返回 `items`、`nextCursor` 和可选 `total`。回调必须限制单页数量和编码大小，cursor 由插件解释。

## Timeline 和模型用途

```python
timeline = context.get("sakura.host.timeline")
cursor = timeline.latest_cursor()
recent = timeline.read_recent({"limit": 20})
delta = timeline.read_since({"cursor": cursor["cursor"], "limit": 100})
```

Timeline Service 固定绑定当前角色，只提供只读投影。插件保存自己的消费 cursor，不要直接打开 Host 的 SQLite 文件。

模型用途通过 `sakura.host.model_slots.register()` 注册 descriptor、`load` 和 `save`。load/save 使用 `profileId` 与 `model`，插件负责把选择写入自己的配置。保存回调失败后不要盲目重试可能已经落盘的写入；先 read back，再返回实际状态。

## Artifact

需要把截图、音频或其他受控文件交给 Host 时：

```python
artifacts = context.get("sakura.host.artifacts")
item = artifacts.allocate({"mediaType": "image/jpeg", "suffix": ".jpg"})
try:
    write_image(item["path"])
    descriptor = artifacts.commit(item["artifactId"])
except Exception:
    artifacts.release(item["artifactId"])
    raise
```

`commit()` 转移所有权，`release()` 或 Effect cleanup 删除未提交文件。插件不能把任意本地路径伪装成 Artifact。

## 输入栏工具

需要让用户从桌宠输入栏左侧 `+` 主动触发插件动作时，在 manifest 的 `requires` 中加入 `sakura.host.ui.composer-tools-v0`。

```python
composer = context.get("sakura.host.ui.composer-tools-v0")
composer.register(
    {
        "toolId": "open_note",
        "label": "便签",
        "description": "打开插件便签",
        "icon": "note",
        "order": 100,
    },
    lambda request: {"status": "completed", "message": ""},
)
```

`toolId` 在插件内唯一。icon 只支持 `camera`、`folder`、`globe`、`link`、`note`、`settings`、`sparkles` 和 `terminal`。插件不能向输入栏注入 HTML、CSS、SVG 或 JavaScript。

## 故障和清理

Service 异常只让本次调用失败；Event Handler 异常会被记录，其他 Handler 继续。回调可能因 timeout 或 Worker EOF 失败，Host 不自动重放。

插件自己创建的线程、进程、文件和网络连接必须注册 cleanup。清理要幂等、可重复调用，并设置有限超时。需要保留的状态写入 config 或 `data_path()`。

## 测试

下面使用 macOS/Linux 路径；Windows 使用 `.\runtime\python.exe`。

```bash
./runtime/bin/python3 -m pytest -q tests/unit/test_plugin_kernel_v3.py
./runtime/bin/python3 -m pytest -q tests/unit/test_core_host_plugins.py
```

测试至少覆盖 manifest、依赖缺失、setup 回滚、配置保存、callback timeout、Worker 重建和私有数据路径。安装测试要覆盖 ZIP 包装目录、符号链接、路径逃逸、重复 ID 和事务回滚。
