---
kind: devdoc
status: current
audience: plugin-author
source_of_truth: ../specs/runtime-v2/sakura-plugin-runtime-v4.md
updated: 2026-09-06
---

# 编写 Sakura 插件

这份文档面向 Plugin API v4。读完后，你应该可以独立完成一个插件：声明依赖、保存配置、提供 Service，
再按需要接入工具、动态上下文、设置区块、现有设置页面、数据集合和模型槽位。

希望通过 AI 开发插件时，可以复制[插件开发 AI 提示词](SAKURA_PLUGIN_AI_PROMPT.md)，填入需求并附上本指南。
已有插件升级时，先看本文末尾的[插件管理与日志适配清单](#插件管理与日志适配清单)。

先说明最容易产生误解的一点：Sakura 不会加载插件自己的 HTML、JavaScript 或 CSS。插件贡献的是结构化
数据，页面和控件由 Sakura 渲染。这样做少了一些前端自由度，但插件停用、重载或崩溃时，宿主能完整撤销
它留下的页面、回调和资源。

插件是可信的本地 Python 代码，不是安全沙箱。它以当前用户权限运行，可以访问文件和网络。不要在插件中
导入 `app.*`、Core bootstrap 或其他插件源码；需要宿主能力时，通过 `context` 和 `sakura.host.*` Service
调用。

## 五分钟写出第一个插件

新建一个目录：

```text
example_greeter/
├── plugin.yaml
├── plugin.py
└── config.json
```

`plugin.yaml` 描述插件和它依赖的能力：

```yaml
api: 4
id: dev.example.greeter
name: Example Greeter
author: Your Name
description: 提供问候 Service、聊天工具和设置项。
version: 1.0.0
entry: plugin:GreeterPlugin
enabled: false
priority: 100
presentation:
  kind: extension
  category: tools
  icon: messages-square
provides:
  - dev.example.greeter
requires:
  - sakura.host.tools
  - sakura.host.settings
  - sakura.host.logging
```

`config.json` 是随插件分发的默认配置：

```json
{
  "greeting": "你好"
}
```

`plugin.py`：

```python
class GreeterService:
    def __init__(self, config):
        self._config = config

    def greet(self, request):
        name = str(request.get("name") or "朋友")
        greeting = str(self._config.get().get("greeting") or "你好")
        return {"text": f"{greeting}，{name}！"}


class GreeterPlugin:
    def setup(self, context) -> None:
        logger = context.get("sakura.host.logging")
        service = GreeterService(context.config)

        context.provide(
            "dev.example.greeter",
            service,
            exports=("greet",),
        )

        current_config = context.config.get()

        def apply_config(values):
            nonlocal current_config
            if values != current_config:
                current_config = dict(values)
                logger.info("问候语配置已更新")
            return "applied"

        context.config.on_change(apply_config)

        context.get("sakura.host.tools").register(
            {
                "name": "greet_someone",
                "description": "向指定的人打招呼。",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                "group": "plugin",
                "risk": "low",
            },
            lambda arguments: service.greet(arguments),
        )

        context.get("sakura.host.settings").register(
            {
                "sectionId": "general",
                "title": "问候语",
                "order": 100,
                "fields": [
                    {
                        "key": "greeting",
                        "label": "开场白",
                        "type": "string",
                        "default": "你好",
                        "required": True,
                        "maxLength": 40,
                    }
                ],
                "actions": [],
            },
            load=context.config.get,
            save=lambda values: {
                "applicationState": context.config.update(values)
            },
        )

        logger.info("问候工具和设置已注册")
```

打开“设置 → 插件”，从安装菜单选择“安装文件夹”，指向 `example_greeter`。安装完成后启用插件并保存。
选中插件，点击详情右上角“插件设置”，即可编辑“问候语”；点击“完成”后，还需要在设置页底栏点击“应用”
或“保存并关闭”。聊天侧会得到 `greet_someone` 工具；运行日志窗口的“插件”页可查看注册和配置变化记录。

修改源码或依赖声明后，重新安装该文件夹。只改 `config.json` 不会覆盖用户已经保存的同名配置；测试新默认值
时，应换一个干净的用户数据目录或删除这个插件自己的配置覆盖。

## 插件包和 Manifest

### 目录规则

`plugin.yaml` 必须位于安装目录或 ZIP 的根目录，而且安装包里只能有这一份 Manifest。入口可以位于子包中，
例如 `entry: my_plugin.main:Plugin` 对应 `my_plugin/main.py` 中的 `Plugin` 类。

常见目录如下：

```text
my_plugin/
├── plugin.yaml             # 必需
├── plugin.py               # 或自定义 Python 包
├── config.json             # 可选，默认配置
├── requirements.lock       # 可选，优先级最高
├── requirements.txt        # 可选
├── pyproject.toml          # 可选，可搭配 uv.lock
└── README.md               # 建议提供
```

安装包不能包含符号链接、路径逃逸、特殊文件或跨平台非法文件名。

### Manifest 字段

| 字段 | 是否必需 | 说明 |
|---|---:|---|
| `api` | 是 | 当前必须为整数 `4`。 |
| `id` | 是 | 全局唯一，最多 64 个字符；建议使用稳定命名空间，如 `com.example.weather`。 |
| `entry` | 是 | `Python模块:类名`，类必须有 `setup(context)`。 |
| `name` | 建议 | 设置页显示名称，最多 120 个字符。 |
| `author` | 建议 | 作者名称，最多 120 个字符。 |
| `description` | 建议 | 一句话说明插件做什么，最多 500 个字符。 |
| `version` | 建议 | 插件版本，1 到 64 个字符。 |
| `enabled` | 否 | 首次发现时是否启用。第三方插件建议默认 `false`。 |
| `priority` | 否 | 稳定排序值，默认 `100`；不用于解决 Service 冲突。 |
| `provides` | 否 | 本插件会在 `setup()` 中发布的 Service key。 |
| `requires` | 否 | 启动前必须可用的 Host 或其他插件 Service。 |
| `presentation` | 否 | 展示分类与 Lucide 图标名称，见下文；不影响插件运行。 |

`provides` 必须和 `context.provide()` 的结果完全一致。少发布、多发布或导出失败，插件都不会进入 `active`。
固定依赖也应写进 `requires`；运行时临时查找的 Service 不会自动变成硬依赖。

不要再使用旧版字段 `plugin_id`、`api_version` 或 `optional`。`permissions` 只是遗留元数据，不构成安全边界，
新插件不要依赖它做授权。

### 选择分类和图标

在 `plugin.yaml` 中声明展示信息，例如语音提供方：

```yaml
presentation:
  kind: provider
  category: voice
  icon: audio-lines
```

`kind` 决定插件列表分组：

| `kind` | 分组 | 适合的插件 |
|---|---|---|
| `extension` | 功能扩展 | 聊天工具、记忆、手机连接等用户直接使用的功能。 |
| `provider` | 能力提供方 | 实现某项能力的提供方，例如语音引擎。 |
| `infrastructure` | 系统组件 | 主要被其他插件依赖的基础服务，与其他分组一起显示在插件列表中。 |

`category` 决定详情中的领域说明和默认图标：

| `category` | 领域 | 默认图标 |
|---|---|---|
| `model` | 模型 | `cpu` |
| `voice` | 语音 | `audio-lines` |
| `memory` | 记忆 | `brain` |
| `tools` | 工具 | `wrench` |
| `connectivity` | 连接 | `globe` |
| `other` | 其他 | `puzzle` |

系统组件没有有效自选图标时使用 `layers`。分类缺失、类型错误或值未知时，分别回退为 `extension/other`。
这些字段只影响展示，不授予权限、不改变安装来源，也不参与 Service 选择或依赖解析。名称、作者、ID 和简介
会参与搜索，建议填写能让用户认出用途的信息。

`icon` 是插件作者选择的本地 Lucide 图标名称，须匹配 `[a-z][a-z0-9-]{0,63}`，不带 `.svg` 后缀。
例如手机连接可用 `smartphone`，笔记可用 `sticky-note`，数据库可用 `database`。以宿主的
[`iconNames` 目录](../../desktop/frontend/core/icons.js)为可用列表；Lucide 网站上的图标不一定已被 Sakura 收录。
省略、格式不符或宿主尚未收录时使用上面的默认图标，不影响插件加载。

插件只能选名称，不能传图片路径、远程 URL、SVG 代码或自带 CSS；颜色、尺寸、线宽和动效由宿主控制。
完整契约见[展示分类与图标](../specs/runtime-v2/sakura-plugin-runtime-v4.md#31-展示分类与图标)。

### Python 依赖

Sakura 按下面的优先级读取一份依赖声明：

1. `requirements.lock`
2. `requirements.txt`
3. `pyproject.toml`，有 `uv.lock` 时按锁文件安装

每个插件有独立的 dependency root。安装、更新或用户点击重试时，Sakura 用 uv 在 staging 目录构建环境，
确认入口可以导入后再发布。普通启动不联网，也不会自动补装或修复依赖。

插件进程能导入的内容只有 Python 标准库、Plugin SDK、当前插件代码和自己的 dependency root。同一个库的
不同版本可以分别安装在两个插件中，不要把依赖写进 Sakura 的主 Python 环境。

## 停止自己启动的子进程

公开 SDK 提供标准库实现的 `sakura_process.terminate_process_tree(process, timeout=...)`，
接收插件自己创建的 `subprocess.Popen`。在 Effect 中调用它可先记录后代，再终止、等待整个进程树；
POSIX 下父进程先退出也会继续强杀未退出的后代。不要创建独立 session 逃离 Shell 的最终回收范围。

Core 与内置 TTS Provider 共用这段实现。Windows 用系统进程句柄查询存活状态，不能用 `os.kill(pid, 0)`
作探测。它只对调用时仍能从受控父进程发现的后代做尽力清理；Shell 仍负责 generation 的最终整树回收。

本地直接导入 Provider 做配置校验时，不需要启动 SDK 或进程；内置 Provider 在实际清理时才导入该工具。
运行仓库测试时，pytest 会将公开 SDK 目录加入导入路径，与插件 Runner 的路径保持一致。

## `setup()` 和生命周期

入口类只需要实现：

```python
class Plugin:
    def setup(self, context) -> None:
        ...
```

不要再写独立的 `shutdown()`。需要清理的线程、文件、网络连接或子进程，统一交给 `context.effect()`：

```python
worker = BackgroundWorker()
worker.start()


def close_worker():
    worker.cancel()
    worker.join()


context.effect(close_worker)
```

清理函数按注册顺序的反方向执行。它应当幂等、有界，并等待自己启动的线程或子进程退出。`setup()` 失败时，
已经暂存的 Service、设置区块和回调不会发布；已经注册的 Effect 仍会清理。

`setup()` 是串行启动路径，不要在里面下载模型、扫描大量历史或等待长期任务。把重活放进后台线程，并用
Effect 负责取消和 `join()`。

一个插件在一个 Core generation 中最多有一个进程。插件停用、重载、崩溃或 generation 切换后，旧的
ServiceProxy、回调、资源 descriptor 和文件 artifact 都会失效，不能缓存到下一代继续用。

## PluginContext

`context` 是插件唯一的宿主入口：

| 成员 | 用途 |
|---|---|
| `plugin_id` | 当前 Manifest 中的插件 ID。 |
| `get(service_key)` | 取得 Host 或其他插件提供的 ServiceProxy。 |
| `provide(service_key, service, exports=...)` | 发布本插件的 Service。 |
| `on(event_name, handler)` | 监听 Host 事实事件。 |
| `effect(cleanup)` | 登记随插件 scope 反向执行的清理函数。 |
| `config` | 读取、更新和监听插件配置。 |
| `data_path(relative_path)` | 取得插件私有数据路径。 |

当前 Host Service 如下。插件使用其中任何一个，都应在 Manifest 的 `requires` 中声明：

| Service key | 用途 |
|---|---|
| `sakura.host.tools` | 注册聊天工具。 |
| `sakura.host.context` | 注册动态 Prompt 上下文贡献者。 |
| `sakura.host.settings` | 注册宿主渲染的设置区块、字段和 Action。 |
| `sakura.host.settings.surface-v0` | 把设置区块放到现有宿主页面。 |
| `sakura.host.settings.collection-v0` | 注册分页查询和 CRUD Collection。 |
| `sakura.host.logging` | 提交插件运行日志，见下文日志示例。 |
| `sakura.host.model_slots` | 注册模型用途，读取目录并解析用户选择。 |
| `sakura.host.character` | 读取当前角色、插件私有角色扩展和角色资源。 |
| `sakura.host.timeline` | 按当前角色读取只读 Timeline。 |
| `sakura.host.storage` | 取得明确授权的共享 data/cache 目录。 |
| `sakura.host.artifacts` | 分配、提交和释放受控大文件。 |
| `sakura.host.mobile` | 使用 Core 持有的当前角色、历史和聊天能力。 |
| `sakura.host.ui.composer-tools-v0` | Host 已实现，但公共 SDK 暂不能注册；见后文限制。 |

Host Contribution 的 `register()` 会返回一个可提前撤销的 disposer，同时已经绑定插件 Effect。正常情况下
不用再手工登记清理；只有插件想在仍然 active 时提前移除某项贡献，才需要调用返回的 disposer。

### 取得和提供 Service

```python
service = context.get("other.service")
result = service.run({"value": 1})

dispose = context.provide(
    "com.example.service",
    MyService(),
    exports=("run", "status"),
)
```

插件不需要知道 Service 在 Core 还是另一个插件进程里。这个透明层只保证方法名和 JSON 合同一致，不保证
Python 对象 identity、共享内存或无限调用时间。远端调用有 deadline，超时、提供者退出或旧 generation
都会明确失败，而且不会自动重放。

`exports` 是唯一的方法导出表。不要指望未导出方法、属性访问、反射或 `__getattr__` 穿过进程边界。

普通 Service 的参数和返回值只使用有界 JSON。不能传 Python 对象、类、异常、callable、文件句柄、生成器、
pickle、裸本地路径或 Host callback handle。需要交换大文件时使用 artifact descriptor。

### 配置

插件目录中的 `config.json` 提供默认值，用户覆盖保存在插件自己的数据目录。可用方法为：

```python
values = context.config.get()
state = context.config.update({"enabled": True})   # 合并覆盖
state = context.config.replace({"enabled": True})  # 替换全部覆盖
dispose = context.config.on_change(apply_config)
```

变更处理器收到合并后的完整配置，并返回：

- `applied`：当前进程已经应用新配置；
- `restart_required`：需要重启本插件；
- `error`：配置已写入，但当前进程应用失败。

如果没有注册 `on_change()`，`update()` 和 `replace()` 默认返回 `restart_required`。当结果是
`restart_required` 时，Sakura 只在本次保存操作中重启目标插件及其硬依赖 consumer，不重启无关插件。

### 插件私有数据

```python
cache_file = context.data_path("cache/index.json")
cache_file.parent.mkdir(parents=True, exist_ok=True)
```

`data_path()` 只接受相对路径，拒绝绝对路径、`..` 和目录逃逸。它返回的位置归当前插件所有，适合数据库、
索引和普通缓存。不要从这个路径的物理位置反推 Sakura 根目录。

### 监听 Host 事件

```python
context.on(
    "sakura.host.chat.completed",
    lambda event: handle_completed_turn(event),
)
```

常用事件如下：

| 事件 | payload | 说明 |
|---|---|---|
| `sakura.host.app.started` | `{"generationId": "..."}` | 当前 generation 的插件启动完成。 |
| `sakura.host.message.received` | `{"role": "user", "characters": 12}` | 收到用户消息；不含正文。 |
| `sakura.host.message.sent` | `{"role": "assistant", "characters": 24}` | 助手已生成消息；不含正文。 |
| `sakura.host.chat.completed` | `{"characterId": "...", "turnId": "...", "cursor": "..."}` | 对话已写入 Timeline 后发送。 |
| `sakura.host.tool.started/finished/failed` | 有界工具状态 | 工具执行状态通知。 |
| `sakura.host.tts.started/ended` | 有界播放状态 | TTS 播放状态通知。 |

Host 事件是事实通知，不是可靠消息队列。派发是 best-effort，插件重启时不会补发。需要避免漏数据时，保存
Timeline cursor，并在下一次事件或插件启动时调用 `sakura.host.timeline.read_since()` 补读。插件不能伪造
或向其他插件发送 `sakura.host.*` 事件。

## 贡献设置和页面

普通配置只需注册 Settings Contribution，宿主会提供独立的“插件设置”窗口。它不是插件自己的前端页面，
不需要新增 HTML、按钮路由或保存接口。`presentation` 控制列表展示，`surface` 控制设置区块的位置，两者独立。

### 设置区块

`sakura.host.settings` 注册一个由宿主渲染的设置区块。没有指定 surface 时，它显示在详情右侧按钮打开的插件设置窗口中。

```python
settings = context.get("sakura.host.settings")


def load_settings():
    return context.config.get()


def save_settings(values):
    return {"applicationState": context.config.update(values)}


def test_connection(values):
    ok = ping(str(values.get("endpoint") or ""))
    return {"message": "连接成功" if ok else "连接失败"}


settings.register(
    {
        "sectionId": "connection",
        "title": "连接",
        "order": 100,
        "fields": [
            {
                "key": "endpoint",
                "label": "服务地址",
                "type": "string",
                "default": "http://127.0.0.1:8000",
                "required": True,
                "maxLength": 512,
            },
            {
                "key": "token",
                "label": "访问令牌",
                "type": "password",
                "default": "",
                "copyable": True,
                "placement": "advanced",
            },
        ],
        "actions": [
            {
                "actionId": "testConnection",
                "label": "测试连接",
                "description": "使用当前表单内容发起一次连接测试。",
                "danger": False,
            }
        ],
    },
    load=load_settings,
    save=save_settings,
    actions={"testConnection": test_connection},
)
```

`load()` 返回所有字段的当前值。`save(values)` 只收到可编辑字段，并返回 `applied`、`restart_required`、
`error`，也可以返回 `{"applicationState": "applied"}`。Action 收到当前可编辑值，返回空对象，或返回：

```python
{
    "values": {"endpoint": "http://127.0.0.1:9000"},
    "message": "已发现本地服务",
}
```

`values` 只更新当前页面投影，不替代持久化；需要保存时仍由 Action 自己调用 `context.config.update()`。
每个声明的 Action 都必须在 `actions={...}` 中提供同名回调。目前 `danger` 只支持 `false`。

`load()` 应当快速读取本地配置和当前状态，不能在刷新表单时启动下载、重置任务或应用配置。`save()` 负责校验
并持久化可编辑值；连接测试等 Action 使用收到的草稿值，不应顺手保存整张表单。确实会持久化的 Action
应在描述里说明，例如“安装完成后更新组件路径”。普通字段保存、Action 和 Collection CRUD 是不同的操作。

区块的 `sectionId`、字段 `key` 和 `actionId` 应保持稳定。迁移显示位置时保留它们及原回调，避免把已有配置
误当成新字段。字段范围校验之外的业务约束仍由插件检查，例如地址是否合法、所选模式需要哪些配置。
每个区块最多 32 个字段、15 个 Action；标题与字段标签最多 120 个字符。`select` 最多 64 个选项，
选项的 `value` 保持稳定，标签可以面向用户调整。descriptor 只使用已支持的属性，未知属性会导致注册失败。

### 设置窗口与保存时机

| 用户操作 | 对插件的影响 |
|---|---|
| 打开“插件设置”或刷新状态 | 读取设置投影，不应修改业务状态。 |
| 编辑字段、点击“完成” | 保留草稿；此时尚未调用普通设置的保存回调。 |
| 底栏“应用”或“保存并关闭” | 沿原保存链路提交，结果以 `applicationState` 为准。 |
| “取消”、右上角关闭或 Esc | 恢复本插件打开窗口时的可编辑值；其他插件草稿保留。 |
| 点击 Action 或提交 Collection 操作 | 立即走对应回调；关闭或取消窗口不会撤销已执行操作。 |

下载中的状态刷新会更新只读状态和进度，不应覆盖正在编辑的普通字段。`voice` 的两个入口复用同一份控件
和保存链路；普通插件刷新后，同一 generation、同一角色的语音草稿保留。插件设置贡献或 Core generation
失效时，宿主关闭窗口，旧草稿不会写回新实例。插件不需要自建另一份表单状态或恢复机制。

### 字段类型

| `type` | 值 | 常用属性 |
|---|---|---|
| `string` | 字符串 | `required`、`maxLength`、`copyable` |
| `password` | 字符串 | `required`、`maxLength`、`copyable` |
| `boolean` | 布尔值 |  |
| `integer` | 整数 | `minimum`、`maximum`、`step` |
| `number` | 整数或小数 | `minimum`、`maximum`、`step` |
| `select` | 字符串、布尔或数字 | `options: [{"label": ..., "value": ...}]` |
| `readonly` | 字符串 | `copyable`、`maxLength` |
| `status` | 状态对象 | 只读，适合短状态说明 |
| `resource` | 资源对象 | 只读，适合安装、下载、进度和重试 |

兼容别名有 `text/path → string`、`secret → password`、`toggle → boolean`、`slider → number`。新文档和新
插件建议直接使用规范类型。

通用字段属性还包括：

- `description`：最多 240 个字符；
- `placement`：`row`、`advanced` 或 `section_header`；`section_header` 只适用于 `status`；
- `readonly`：普通字段可声明只读；`readonly/status/resource` 类型由宿主按只读处理，不进入普通保存值；
- `restartRequired`：给界面的重启提示，实际是否重启仍以保存回调返回值为准；
- `enabledWhen`：形如 `{"field": "mode", "equals": "custom"}`，目前 `equals` 只接受字符串。

`status` 的值必须完整包含：

```python
{
    "state": "ready",  # neutral | ready | working | warning | error
    "label": "已连接",
    "message": "本地服务可以使用。",
}
```

`resource` 的值必须完整包含：

```python
{
    "applicability": "required",  # required | not_required | unsupported
    "subtitle": "模型或组件名称",
    "ready": False,
    "taskState": "idle",  # idle | queued | running | succeeded | failed | cancelled
    "message": "尚未安装",
    "detail": "",
    "progress": None,  # null 或 0 到 100 的整数
    "availableActionIds": ["install"],
}
```

资源字段通过 `actionIds` 声明它可能使用的 Action。`availableActionIds` 只能从这个集合里选择，用于根据当前
状态显示“安装”“取消”或“重试”。状态读取不得偷偷联网；联网下载应由用户点击 Action 明确触发。

### 提供资源下载与组件总览

下面展示注册方式。`manager` 代表插件自己实现的资源管理器，不是 SDK 内置类；它需要提供快速状态读取、
后台安装、取消和有界清理。使用此片段时，在 Manifest 中声明 `sakura.host.settings`。

```python
settings = context.get("sakura.host.settings")
context.effect(manager.close)
settings.register(
    {
        "sectionId": "resources",
        "title": "本地模型",
        "order": 200,
        "fields": [{
            "key": "modelResource",
            "label": "插件模型",
            "type": "resource",
            "readonly": True,
            "actionIds": ["install", "retry", "cancel"],
            "default": {
                "applicability": "required",
                "subtitle": "示例模型",
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
    load=lambda: {"modelResource": manager.snapshot()},
    actions={
        "install": lambda _values: manager.start(),
        "retry": lambda _values: manager.start(),
        "cancel": lambda _values: manager.cancel(),
    },
)
```

`snapshot()` 返回完整的 `resource` 值，`start()/cancel()` 返回合法 Action 结果，例如 `{}` 或
`{"message": "下载已开始"}`。开始、重试回调只启动后台任务并及时返回；进度由后续 `load()` 读取。
下载中可返回 `taskState="running"`、真实 `progress` 和 `availableActionIds=["cancel"]`；失败后提供
`retry`。`ready` 表示资源是否已安装可用，不能直接据此报告插件进程或外部服务已经就绪。

不用注册额外的总览接口：“关于 → 组件”会自动聚合已启用插件设置快照中的 `resource` 字段，包括默认
`plugin` surface。总览只读，点击“前往下载设置”才跳到所属插件并定位资源；下载、重试和取消仍由插件
设置里的 Action 执行。停用插件不会出现在总览中，未提交的启停草稿也不会提前改变总览。

可参考 [GPT-SoVITS 资源管理器](../../plugins/builtin/sakura_gpt_sovits/_bundle.py)。该文件属于插件实现，
不是公共 SDK；第三方插件应自行实现或使用公开依赖，不能跨目录导入它。

### 把区块放到现有页面

先注册设置区块，再通过实验性 `sakura.host.settings.surface-v0` 放置：

```python
surface = context.get("sakura.host.settings.surface-v0")
surface.register("connection", "voice")
```

当前桌面端支持以下放置方式：

| surface | 显示位置 | 约束 |
|---|---|---|
| 不注册或 `plugin` | 插件设置窗口 | 普通字段、Action 和 Collection 都可用。 |
| `voice` | “语音”页及插件设置窗口 | 适合语音引擎配置；两个入口复用 Voice controller 的同一组控件与保存链路。 |
| `memory` | “记忆”页 | 适合记忆管理区块和 Collection。 |
| `about` | 历史资源区块，管理操作显示在插件设置 | 只能有只读 `resource` 字段；不能保存，也不能挂 Collection。所有 Action 必须被资源字段引用。 |

surface 不会创建新的左侧导航项。传入其他字符串也不会得到一个自定义页面，所以插件不要自创 surface 名称。
`surface-v0` 仍是实验接口，将来可能随宿主页面调整。

显式注册 surface 时，Manifest 还需声明 `sakura.host.settings.surface-v0`。新插件的常规设置和资源管理
建议不注册 surface，使用默认插件窗口即可；只有确实属于语音或记忆页面的内容才使用相应入口。
GPT-SoVITS 与 Genie 的 `aboutBundle` 已改为 `plugin`，section ID 和原有回调保留；Mem0 向量模型下载仍
声明 `about`，管理操作同样进入插件设置窗口。新资源不需要为了出现在组件总览里而使用 `about`。

### 分页 Collection

需要让用户搜索、增删或编辑一组数据时，使用 `sakura.host.settings.collection-v0`。它仍由 Sakura 渲染，
插件只负责 descriptor 和 CRUD 回调。

```python
collections = context.get("sakura.host.settings.collection-v0")

collections.register(
    "notes",
    {
        "collectionId": "items",
        "title": "笔记",
        "description": "当前角色的插件笔记。",
        "columns": [
            {"key": "title", "label": "标题", "type": "string", "maxLength": 120},
            {"key": "updatedAt", "label": "更新时间", "type": "datetime"},
        ],
        "fields": [
            {
                "key": "title",
                "label": "标题",
                "type": "string",
                "default": None,
                "required": True,
                "maxLength": 120,
            },
            {
                "key": "content",
                "label": "内容",
                "type": "string",
                "default": "",
                "maxLength": 4000,
            },
        ],
        "filters": [],
        "searchable": True,
        "pageSize": 25,
        "deleteConfirmation": "确定删除这条笔记吗？",
    },
    query=query_notes,
    create=create_note,
    update=update_note,
    delete=delete_note,
)
```

承载 Collection 的 `notes` 设置区块必须先用 `sakura.host.settings.register()` 注册。回调合同为：

```python
def query_notes(request):
    # request: cursor、limit、search、filters
    return {
        "items": [
            {
                "itemId": "note-1",
                "values": {
                    "title": "示例",
                    "content": "正文",
                    "updatedAt": "2026-08-30T12:00:00+08:00",
                },
            }
        ],
        "nextCursor": None,
        "total": 1,
    }


def create_note(values):
    return {"itemId": "note-2", "values": values}


def update_note(item_id, values):
    return {"itemId": item_id, "values": values}


def delete_note(item_id):
    return {"deleted": True}
```

不支持的操作传 `None` 或省略对应关键字。要提供删除回调，`deleteConfirmation` 不能留空。cursor 是插件
定义的 opaque 字符串；不要让界面解析它。Collection v0 每页最多 100 项，结果应保持有界。

## 贡献聊天能力

### 工具

通过 `sakura.host.tools` 注册的工具会进入 Sakura 的工具注册表，模型可按描述和 JSON Schema 调用它。

```python
tools = context.get("sakura.host.tools")

tools.register(
    {
        "name": "weather_lookup",
        "description": "查询指定城市的当前天气。",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        "group": "weather",
        "risk": "low",  # low | medium | high
        "capability": "network",
    },
    lambda arguments: lookup_weather(str(arguments["city"])),
)
```

工具名只能包含字母、数字、下划线和连字符，最多 64 个字符，而且必须全局唯一。描述要告诉模型什么时候
调用，以及参数含义；不要写宣传文案。回调有 15 秒 deadline，长任务应设计成 `begin/poll/cancel`，不要让
一次回调长时间阻塞。

工具结果通常是 JSON。如果需要返回图片，先用 artifact Service 提交文件，再返回：

```python
{"content": "截图完成", "artifact": committed_descriptor}
```

### 动态上下文

上下文贡献者在每次 Prompt 组装时收到有界请求，并返回少量相关事实：

```python
context_host = context.get("sakura.host.context")


def build_context(request):
    current_input = str(request.get("current_input") or "")
    facts = find_relevant_facts(current_input)
    return [
        {
            "id": f"fact-{index}",
            "content": fact,
            "priority": 60,
            "budgetHint": 160,
            "sensitivity": "private",
        }
        for index, fact in enumerate(facts[:5])
    ]


context_host.register(
    {
        "providerId": "com.example.notes.recall",
        "description": "从插件笔记中选择与当前消息相关的内容。",
        "order": 80,
        "enabled": True,
    },
    build_context,
)
```

request 使用 snake_case，常用字段有 `current_input`、`character_id`、`current_turn_id`、`source`、`mode`、
`recent_messages`、`available_tools`、`visual_summaries`、`screen_context_available` 和 `current_time`。

一次最多返回 16 个 fragment。`content` 必填并最多保留 8192 个字符；`priority` 范围为 0–100，
`budgetHint` 范围为 1–4096；`sensitivity` 可为 `public`、`private` 或 `sensitive`。Host 会把插件内容标为
untrusted，并按全局 Prompt 预算决定是否采用。不要把完整数据库、长期历史或无关资料每轮都塞进 Prompt。

## 模型、角色、历史和文件

### 模型槽位

插件需要用户选择一个 Chat Completion 模型时，向 `sakura.host.model_slots` 注册槽位，不要自己复制 Provider、
模型和 API key 设置页。

```python
model_slots = context.get("sakura.host.model_slots")

model_slots.register(
    {
        "slotId": "summary",
        "label": "摘要模型",
        "description": "用于生成笔记摘要；留空时继承当前对话模型。",
        "modelKind": "chat_completion",
        "required": False,
        "order": 50,
    },
    load=lambda: {
        "profileId": context.config.get().get("profileId", ""),
        "model": context.config.get().get("model", ""),
    },
    save=lambda selection: {
        "applicationState": context.config.update(selection)
    },
)
```

`catalog()` 返回宿主可选模型目录，`resolve({"profileId": ..., "model": ...})` 返回实际调用信息，包括
`baseUrl`、`apiKey` 和 `timeoutSeconds`。解析结果只在插件进程内使用，不要写日志、设置投影或普通 Service
返回值。`required: false` 时，空的 `profileId/model` 表示动态继承当前对话模型。

### 当前角色和角色扩展

```python
character = context.get("sakura.host.character")
current = character.current()
character_id = current["id"]
system_prompt = current["systemPrompt"]

extension = character.get(character_id)
updated = character.update(character_id, {"voice": "alice"})
resource_path = character.resolve_resource(character_id, "voices/alice.wav")
```

`get()` 和 `update()` 只能看到当前插件 ID 对应的 `character.json.extensions` 子对象，不会泄漏或覆盖其他
插件的数据。单插件角色扩展最多 64 KiB。`resolve_resource()` 只解析角色包内部既有资源，并拒绝路径逃逸。

### Timeline

`sakura.host.timeline` 只读当前角色的 Timeline：

```python
timeline = context.get("sakura.host.timeline")

start = timeline.latest_cursor()  # {"cursor": "..."}
recent = timeline.read_recent({"limit": 50})
delta = timeline.read_since({"cursor": start["cursor"], "limit": 100})
```

`read_recent()` 返回 `{"entries": [...], "cursor": "..."}`；`read_since()` 返回
`{"entries": [...], "nextCursor": "...", "hasMore": false}`。每个 entry 包含 `entryId`、`turnId`、
`characterId`、`kind`、`origin`、`createdAt` 和 `payload`。`kind` 当前可为 `human`、`assistant`、
`observation` 或 `system`。

limit 范围为 1–500。cursor 绑定角色和数据库 lineage，是不透明字符串；只能原样保存和回传，不能解析、
拼接或当作整数。cursor 失效时会得到 `TIMELINE_CURSOR_INVALID`，由插件明确决定是否从 `read_recent()`
重新建立起点。

### 私有数据与共享存储

大多数插件只应使用 `context.data_path()`。确实要和 Sakura 的某类公共数据协作时，才调用：

```python
storage = context.get("sakura.host.storage")
data_dir = storage.resolve("data", "my-domain")
cache_dir = storage.resolve("cache", "my-domain")
```

scope 只能是 `data` 或 `cache`，name 必须是一个有界标识符。这个接口不会替你建立权限隔离；名称应属于明确
的跨插件数据合同，避免随意读写其他领域目录。

### Artifact

Artifact 用于把图片等大文件交给 Host，而不是把 base64 或裸路径塞进 JSON：

```python
artifacts = context.get("sakura.host.artifacts")
allocation = artifacts.allocate({"mediaType": "image/png", "suffix": ".png"})

try:
    render_png(allocation["path"])
    descriptor = artifacts.commit(allocation["artifactId"])
except Exception:
    artifacts.release(allocation["artifactId"])
    raise
```

`allocate()` 返回的 `path` 是 Host 特意签发的临时写入位置，是普通 Service 禁止传裸路径规则的例外。
`commit()` 后返回的 descriptor 含 `artifactId`、`mediaType` 和 `byteLength`。每个插件同时最多持有 16 个
artifact，单个文件最多 64 MiB。未提交文件会随插件 scope 清理；提交后由接收它的 Host consumer 释放。

### 移动端聊天能力

`sakura.host.mobile` 是为替代前端或远程入口准备的较窄接口，普通业务插件通常不需要它。当前方法为：

```text
characters()
history(character_id, limit)
theme()
begin(plugin_id, character_id, text, artifact_descriptor_or_none) -> {jobId}
poll(plugin_id, job_id)
cancel(plugin_id, job_id)
```

调用 `begin/poll/cancel` 时传 `context.plugin_id`，只能访问当前角色，图片必须来自本插件已提交的 artifact。
聊天是 job 合同，应轮询并设置自己的超时，退出时取消未完成 job。

## 当前暂不开放的界面能力

`sakura.host.ui.composer-tools-v0` 已有 Host 端实现，用于输入栏 `+` 菜单，但当前 Plugin API v4 公共 SDK
没有注册 callable 的封装。第三方插件现在无法只靠公开接口正确注册它，不要调用私有 `_register_callback()`
绕过边界。等公共代理和回归测试补齐后，再把它视为可用接口。

插件也不能贡献任意窗口、WebView、HTML、脚本、样式、托盘菜单或原生控件。需要新的宿主界面扩展点时，
应先在 Sakura 中定义有界 descriptor、回调合同和清理规则，而不是让插件直接进入前端 Runtime。

## 写入插件日志

建议每个插件都接入宿主日志，至少记录初始化结果、实际配置变化、业务失败和资源清理异常。用户遇到问题时，
应能从日志看出失败的操作和原因，而不是只看到插件处于 `failed`。日志不是启用插件的强制条件，但应作为
新插件开发和旧插件适配的常规工作。

在 Manifest 的 `requires` 中加入 `sakura.host.logging`，然后在 `setup()` 里取得 logger 并传给自己的业务类：

```python
from time import monotonic


class Plugin:
    def setup(self, context):
        logger = context.get("sakura.host.logging")
        # 这里的 resource_manager 是插件自己的业务对象。
        manager = self.resource_manager

        def refresh():
            started = monotonic()
            try:
                count = manager.refresh()
            except Exception as error:
                logger.error("刷新索引失败", fields={
                    "operation": "refresh_index",
                    "reason_code": "INDEX_REFRESH_FAILED",
                    "error_type": type(error).__name__,
                })
                raise
            logger.info("索引已更新", fields={
                "item_count": count,
                "elapsed_ms": int((monotonic() - started) * 1000),
            })
            return count

        def cleanup():
            try:
                manager.close()
            except Exception as error:
                logger.warning("索引资源清理失败", fields={
                    "reason_code": "INDEX_CLOSE_FAILED",
                    "error_type": type(error).__name__,
                })
                raise

        context.effect(cleanup)
        # 按业务需要把 refresh 接到工具、Action 或后台任务。
        logger.info("索引插件已初始化")
```

这段代码展示记录和清理方式，`self.resource_manager` 需由插件初始化。普通 Host 回调有 deadline，耗时刷新
应在后台执行，不能直接阻塞设置 Action。公共类型提示可从 `sakura_plugin_api` 导入 `PluginLogger`，无需
导入 SDK 私有代理或 Core 日志模块。

### 接口与分级

`debug/info/warning/error(message, *, fields=None)` 的 `message` 是非空字符串；`fields` 使用少量有界 JSON
字段，常用 `operation`、`reason_code`、`error_type`、`elapsed_ms`、计数或插件自己的任务编号。它不是标准
Python logger，不支持 `%s` 位置参数、`exc_info=True` 或 `logger.exception()`。

| 方法 | 推荐记录 | 避免的噪声 |
|---|---|---|
| `info` | 启动、就绪、实际配置变化、有产出的任务完成。 | 每次读取设置、无变化的状态刷新。 |
| `warning` | 可恢复失败、请求未受理、清理超时或异常。 | 正常取消和用户未启用的可选能力。 |
| `error` | 初始化、业务请求、后台任务失败。 | 原始异常正文、完整堆栈和用户输入。 |
| `debug` | 开发排查需要的轮询、阶段进度、缓存命中。 | 默认级别下持续输出高频记录。 |

默认 `info` 不显示 `debug`。同一任务的失败或终态在负责收尾的位置记录一次，避免各层重复打印。
日志不能替代工具错误返回、Action 结果、`status/resource` 状态或配置的 `applicationState`。

返回 `True` 只表示 SDK 本地队列接收，不能证明已经落盘。队列拥塞、传输中断、无效参数或 scope 关闭时
可能返回 `False`；不要据此改变业务结果、重试业务请求或另开文件补写。SDK 后台批量发送并汇总丢弃数量，
清理 Effect 中仍可记录，退出时只做有界刷新，不能把日志当作可靠事务或业务数据存储。

### 内容边界与查看方式

使用自己编写的短消息，加稳定错误码、异常类型、耗时和计数。不要记录 `str(error)`、异常对象、API key、
Token、完整配置、环境变量、请求头、对话正文、Prompt、工具参数或模型输出。即使 SDK 会清洗凭据、URL、
绝对路径和敏感字段，也无法识别任意私密内容；日志内容由插件作者负责选择。

消息上限为 1024 UTF-8 字节，字段字符串上限 256 字节；嵌套最多 3 层、每层最多 8 项，总遍历预算 32 项，
字段编码预算 1800 字节。超限会截断或标记，详见[统一宿主日志合同](../specs/runtime-v2/sakura-plugin-runtime-v4.md#41-统一宿主日志)。

插件身份由宿主按当前调用进程绑定，不需要也不能通过参数指定来源插件或日志文件。打开运行日志窗口的
“插件”页，可按插件筛选；文件位于 `data/logs/sakura-plugins.log`，约 10 MiB 轮转，最多保留 5 个备份。
宿主记录的安装、加载、依赖和进程失败仍在 `data/logs/sakura-runtime.log`，排障时可能需要同时查看两份。

Python 标准 `logging`、`print`、stderr 和外部程序输出不会自动进入统一日志。新插件应显式使用上述接口，
无需自建日志文件、轮转器或 GUI 缓冲。原有第三方引擎输出文件不会被自动汇入；插件应另外报告其启动、
就绪和失败结果。`sakura.host.diagnostics.emit()` 仍兼容已有固定 TTS 事件，新插件的普通日志使用
`sakura.host.logging`。Agent Trace 用于宿主的模型调用记录，不是插件保存私密调试内容的通道。

## 生命周期、冲突和错误

- `setup()` 成功、`provides` 全部兑现后，插件状态才是 `active`。
- 同一个 Service key 只能有一个启用提供者。发生冲突时，参与者都以 `SERVICE_CONFLICT` 失败；`priority`
  不会替用户选赢家。
- 硬依赖 Provider 退出时，声明它的 consumer 会停止。动态 `context.get()` 不建立这层传播关系。
- 单插件崩溃、超时或 cleanup 失败不会重启无关插件。
- Runtime 没有后台健康轮询、自动重启、自动切换提供者或调用重放。
- 安装、启用、停用、重载、卸载和配置重启都是明确操作。

插件状态只有 `disabled`、`active` 和 `failed`。常见原因码：

| 原因码 | 先检查什么 |
|---|---|
| `API_VERSION_UNSUPPORTED` | `plugin.yaml` 是否为 `api: 4`。 |
| `PLUGIN_MANIFEST_INVALID` | 字段类型、入口路径和安装包布局。 |
| `PLUGIN_DEPENDENCIES_MISSING/STALE` | 依赖是否安装，声明或 Python ABI 是否变化。 |
| `MISSING_SERVICE` | `requires` 中的 Service 是否由已启用插件或 Host 提供。 |
| `SERVICE_CONFLICT` | 是否同时启用了两个同名 Service 提供者。 |
| `DEPENDENCY_CYCLE` | 插件之间的硬依赖是否成环。 |
| `PLUGIN_CALL_TIMEOUT` | 回调或 Service 方法是否阻塞。 |
| `PLUGIN_PROCESS_EXITED` | 插件是否崩溃，Effect 是否误杀自身进程。 |
| `PLUGIN_ID_CONFLICT` | bundled 和用户插件中是否出现重复 ID。 |

开发时先在插件页 reload。仍失败时结合 `sakura-runtime.log` 的加载诊断与 `sakura-plugins.log` 的插件记录，
按插件、时间和任务编号定位；具体查看方式见[运行日志与故障排查](../userdocs/RUNTIME_LOG_TROUBLESHOOTING.md)。

## 插件管理与日志适配清单

本轮插件管理与统一日志改动仍使用 Plugin API v4，没有新增插件 HTML 入口，也没有要求重写普通设置回调。
已有插件按下表检查即可；使用新日志能力的包应在 README 写明需要支持 `sakura.host.logging` 的 Sakura，
并在 `requires` 中声明它。仅有 `api: 4` 不能证明旧宿主已提供这项新增 Service。

| 改动 | 兼容情况 | 作者需要做什么 |
|---|---|---|
| 分类、图标和搜索 | `presentation` 可省略，会回退到默认分组和图标。 | 推荐补上 `kind/category/icon`，检查名称、作者和简介，选择宿主已收录图标。 |
| 普通设置独立成窗口 | 原 `sakura.host.settings.register()` 和 `load/save/actions` 继续使用。 | README 的配置步骤改为“插件设置 → 完成 → 底栏应用”；不用新增前端代码。 |
| 草稿与即时操作 | 取消窗口只恢复本插件打开时的可编辑字段。 | 确保 `load` 不产生副作用，`save` 才保存普通字段；说明 Action、CRUD 和下载不会随取消回滚。 |
| 语音与记忆入口 | `voice` 复用原控件，`memory` 内容管理仍在记忆页。 | 保留原 surface 和回调；资源下载放到普通插件设置，避免重复注册同一组配置。 |
| 组件总览只读 | 已启用插件的 `resource` 字段自动进入总览，不再按 `about` 筛选。 | 新资源使用默认或显式 `plugin`；迁移旧资源保留 section ID、字段 key 和 Action。历史 `about` 仍兼容。 |
| 统一日志 | 不会自动接管标准 logger 或旧文件 writer。 | 推荐声明并使用 `sakura.host.logging`，把生命周期和失败记录接入宿主，删除因此失效的自建日志路径。 |
| 日志文件与内容 | 插件主动日志进入 `sakura-plugins.log`，加载诊断仍属宿主日志。 | 更新排障说明；改掉异常正文、配置、输入输出等私密记录，默认只保留有用状态变化。 |

核对界面时，至少走一次“编辑 → 取消”和“编辑 → 完成 → 应用”，确认持久值符合预期；资源插件还要验证
状态刷新不吞掉草稿、失败后可重试、取消可停止任务，以及组件总览能跳回所属插件。检查日志中能找到一次
真实失败的操作和稳定原因码，但没有输入正文或凭据。现有 v4 的依赖隔离、Service JSON 边界、Effect 清理
和 generation 失效规则继续适用。

## 开发和验证清单

安装前至少检查：

- `plugin.yaml` 位于包根目录，`api/id/entry/provides/requires` 与代码一致；
- 源码不导入 `app.*` 或其他插件目录；
- Service、工具和回调只收发有界 JSON；
- 所有线程、子进程、连接和临时文件都有 Effect 清理；
- 设置字段有合法默认值，Action 名称与回调完全对应；
- 长任务由用户 Action 启动，并有状态、取消和明确失败；
- 推荐接入宿主日志，能看见初始化结果、实际配置变化和失败；高频状态用 `debug` 或不记录，日志不含凭据、私密配置或用户正文；
- 插件停用、reload 和 Core 重启后，不会继续使用旧代理或旧资源。

如果你在 Sakura 仓库内开发，使用 bundled Runtime，先运行最接近插件调用链的测试。按实际风险选择
Harness profile，不必为一个插件默认重跑全部 Runtime 测试；可先用 `-m harness list` 查看入口。例如：

```text
runtime\python.exe -m pytest -q tests/unit/test_playwright_browser_plugin_v4.py
runtime\python.exe -m harness run journey-plugins
```

只改文档时运行：

```text
runtime\python.exe -m harness run docs
```

可直接参考这些现有插件：

- [Playwright Browser](../../plugins/optional/playwright_browser/plugin.py)：工具、Artifact、配置和普通设置；
- [Sakura Mobile](../../plugins/builtin/sakura_mobile/plugin.py)：事件、线程清理、配置热应用和只读状态；
- [Sakura Mem0](../../plugins/builtin/sakura_mem0/plugin.py)：上下文、工具、surface、Collection 和模型槽位；
- [GPT-SoVITS](../../plugins/builtin/sakura_gpt_sovits/plugin.py)：跨插件 Service、角色资源、资源 Action 和语音页面。

规范以 [Sakura Plugin Runtime v4](../specs/runtime-v2/sakura-plugin-runtime-v4.md) 为准。公共类型提示位于
[`app/plugin_sdk/sakura_plugin_api.py`](../../app/plugin_sdk/sakura_plugin_api.py)；实际接口行为还应以 Runtime
v4 的 focused tests 为证据。
