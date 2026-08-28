---
kind: devdoc
status: current
audience: plugin-author
source_of_truth: ../specs/runtime-v2/sakura-plugin-runtime-v4.md
updated: 2026-08-28
---

# Plugin API v4 开发指南

一个插件包含 `plugin.yaml`、Python entry class，以及可选的 `config.json` 和 Python 依赖声明。Sakura 为每个
启用插件启动独立进程，并只向该进程暴露 Plugin SDK、插件代码和该插件自己的 dependency root。

插件是可信本地 Python 代码，不是安全沙箱。不要导入 `app.*`、其他插件源码或 Core 私有 bootstrap；需要的
宿主能力统一通过 `context` 和 `sakura.host.*` 取得。

## 最小插件

```yaml
api: 4
id: example.echo
name: Example Echo
author: Your Name
description: 提供一个回声 Service。
version: 1.0.0
entry: plugin:ExamplePlugin
enabled: false
priority: 100
provides:
  - example.echo
requires: []
```

```python
class EchoService:
    def run(self, request):
        return {"text": str(request["text"])}


class ExamplePlugin:
    def setup(self, context) -> None:
        context.provide("example.echo", EchoService(), exports=("run",))
```

`setup()` 返回 `None`。不要实现独立 `shutdown()`；文件、线程和子进程清理由 `context.effect()` 注册。

## Manifest 与依赖

- `api` 必须是 `4`。
- `id` 在 bundled 和用户插件中全局唯一，建议使用反向域名或稳定命名空间。
- `entry` 使用 `module:ClassName`。
- `provides` 声明插件发布的 Service，`requires` 声明启动所需的硬 Service 依赖。
- `priority` 只决定稳定启动和展示顺序，不解决同名 Service 冲突。
- 同一个 Service 有多个启用提供者时，冲突参与者都失败；Runtime 不自动选择赢家。

Python 包依赖按以下优先级声明：

1. `requirements.lock`
2. `requirements.txt`
3. `pyproject.toml`，可配合 `uv.lock`

安装、更新或用户显式重试时，Sakura 使用 uv 构建该插件私有的 staging dependency root，验证 entry 可导入后
再发布。普通启动只校验 fingerprint 和 Python ABI，不联网，也不自动修复环境。

## PluginContext

```python
service = context.get("other.service")
dispose_service = context.provide("example.echo", service_object, exports=("run",))
dispose_event = context.on("sakura.host.message.received", handle_message)
dispose_resource = context.effect(close_resource)
path = context.data_path("cache/index.json")
config = context.config.get()
```

`get()` 对本地和跨进程 Service 使用相同对象式调用。远端调用有 deadline，可能明确失败，且不会自动重放。
`provide(..., exports=...)` 是唯一的方法导出表；未导出的方法不能跨进程调用。

普通 Service 的参数和返回值只使用有界 JSON，以及 Host 签发的 artifact/resource descriptor。不要传递真实
Python 对象、类、模块、异常、callable、文件句柄、裸路径或 pickle。callback handle 只由既有 Host
Contribution 接口内部管理，不能在 Plugin Service 之间传递。

`data_path()` 只接受插件私有数据根下的相对路径。绝对路径、`..` 和路径逃逸会失败。Effect 按 LIFO 清理；
`setup()` 未完整成功时，暂存的 Host 注册不会发布。

## 调用其他插件 Service

在 manifest 的 `requires` 中声明固定硬依赖：

```yaml
requires:
  - example.echo
```

然后按普通 Python 对象调用：

```python
echo = context.get("example.echo")
result = echo.run({"text": "hello"})
```

Provider 退出时，已有代理失效，声明该硬依赖的 consumer 会停止。Runtime 不自动重启 Provider、不恢复
consumer，也不重放失败调用。由用户 reload、重新安装或启动新 Core generation 恢复。

## Host Services

| Service | 用途 |
|---|---|
| `sakura.host.tools` | 注册聊天工具 |
| `sakura.host.context` | 注册动态上下文贡献者 |
| `sakura.host.artifacts` | 分配、提交和释放受控文件 |
| `sakura.host.character` | 读取当前角色、插件扩展和角色资源 |
| `sakura.host.timeline` | 按当前角色读取 Timeline |
| `sakura.host.storage` | 取得 Host 授权的数据或缓存目录 descriptor |
| `sakura.host.model_slots` | 注册模型用途并解析 Host 模型选择 |
| `sakura.host.settings` | 注册声明式设置区块 |
| `sakura.host.settings.surface-v0` | 把设置区块放到指定宿主页面 |
| `sakura.host.settings.collection-v0` | 注册分页 Collection |
| `sakura.host.ui.composer-tools-v0` | 在输入栏 `+` 菜单注册动作 |
| `sakura.host.mobile` | 使用 Core 拥有的移动端聊天和历史能力 |

Host Service、ServiceProxy、callback handle 和 resource descriptor 都绑定当前 generation 与插件 scope，不要
跨 reload 或进程退出缓存复用。

## 配置

插件目录中的 `config.json` 是默认值，用户覆盖保存在插件私有数据目录。

```python
class ExamplePlugin:
    def setup(self, context) -> None:
        values = context.config.get()

        def apply_config(next_values):
            return "applied"

        context.config.on_change(apply_config)
```

可用操作为 `get()`、`update(values)`、`replace(values)` 和 `on_change(handler)`。handler 返回 `applied`、
`restart_required` 或 `error`。`restart_required` 只在当前保存操作内重启目标插件及其硬依赖 consumer，不重启
无关插件。

## 声明式设置

```python
settings = context.get("sakura.host.settings")


def save_settings(values):
    state = context.config.update(values)
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

设置页只渲染 Host 支持的字段。普通输入字段参与保存；`status` 和 `resource` 是只读投影，不能回传给 save
callback。Resource 的 `taskState` 只使用 `idle`、`queued`、`running`、`succeeded`、`failed` 或
`cancelled`，progress 为 `null` 或 0–100。

本地模型、浏览器和其他大型资源仍由插件自己的显式 Action 管理。状态读取不得联网；只有用户点击安装或重试
才可联网。插件必须用 `context.effect()` 取消并等待自己启动的下载线程或子进程。

## 生命周期与错误

- 一个插件在一个 generation 中最多一个 active 进程。
- `setup()` 成功并兑现全部 `provides` 后才进入 `active`。
- enable、disable、reload、install 和 uninstall 只处理目标插件及必要的硬依赖 consumer。
- 单插件崩溃只使目标和硬依赖 consumer 失败；无关插件继续运行。
- Runtime 不运行后台 reconcile、健康轮询、自动重启、自愈或调用重放。
- 官方插件与第三方插件使用同一个 Runner、SDK、dependency root 和生命周期；`bundled` 只影响分发与卸载。

完整合同见 [Sakura Plugin Runtime v4](../specs/runtime-v2/sakura-plugin-runtime-v4.md)。
