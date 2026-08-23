---
kind: devdoc
status: current
audience: plugin-author
source_of_truth: ../specs/runtime-v2/sakura-plugin-kernel-v3.md
updated: 2026-08-23
---

# Sakura Plugin v3 开发指南

一个插件就是一个 `plugin.yaml`、一个 Python entry class，以及可选的 packaged `config.json`。Runtime v2
会在独立 Worker 中构造 entry 并调用一次 `setup(context)`。

## 最小插件

```yaml
# plugins/example/plugin.yaml
api: 3
id: example
name: Example
version: 1.0.0
entry: plugin:ExamplePlugin
enabled: true
priority: 100
provides: []
requires:
  - sakura.host.tools
```

```python
# plugins/example/plugin.py
class ExamplePlugin:
    def setup(self, context) -> None:
        tools = context.get("sakura.host.tools")
        tools.register(
            {
                "name": "example_echo",
                "description": "Return one string.",
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

`setup()` 返回 `None`。不要实现 `shutdown()`；资源清理由 `context.effect()` 登记。

## 六个 Context 成员

```python
service = context.get("other.service")
dispose_service = context.provide("example.service", service_object, exports=("run",))
dispose_handler = context.on("sakura.host.message.received", handle_message)
dispose_resource = context.effect(close_resource)
config = context.config
path = context.data_path("cache/index.json")
```

- `get` 的依赖必须同时写进 manifest `requires`。
- `provide` 的 key 必须写进 `provides`；`exports` 只列允许跨 Worker 调用的方法。
- `on` 只接收 Host 派发事件。插件不能主动 emit。
- `effect` 接收无参 cleanup。Worker 关闭和 setup 失败时按 LIFO 调用。
- `data_path` 只接受插件私有根下的相对路径。

不存在 `inject`、`emit`、`on_transform`、`transform`、`on_session` 或 Session context。需要共享能力时提供
Service；需要收到事实时订阅 Host event。

## 配置

packaged 默认值放在插件目录的 `config.json`。用户覆盖由 Sakura 原子写入插件私有数据目录：

```python
class ExamplePlugin:
    def setup(self, context) -> None:
        values = context.config.get()

        def apply_config(next_values):
            # 能在当前 Worker 安全更新时返回 applied。
            return "applied"

        context.config.on_change(apply_config)
```

可用操作：

- `config.get()`：读取 defaults 与 overrides 合并结果。
- `config.update(values)`：合并并保存 overrides。
- `config.replace(values)`：替换并保存 overrides。
- `config.on_change(handler)`：保存后调用 handler。

handler 只返回 `applied`、`restart_required` 或 `error`。返回 `restart_required` 时 Sakura 会重建整个
Worker；不要自行 reload 模块或局部重绑。没有 handler 时保存默认要求重建。

## Service 与事件失败

Service 方法抛出的异常只让当前调用失败；Event Handler 异常会被记录，其他 Handler 继续运行。Kernel
不会因为一次调用异常自动停用插件。因此插件应在自己的边界校验输入，释放自己创建的线程/进程/文件句柄，
并让不可处理的问题清晰抛出。

```python
class ExampleService:
    def run(self, value):
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        return {"value": value}
```

## 管理与调试

启停、显式 reload、安装、卸载和需要 restart 的配置都会重建整个 Plugin Worker。内存状态不会跨重建
保留；需要保留的内容写入 `config` 或 `data_path()` 指向的私有文件。

聚焦测试：

```text
runtime\python.exe -B -m pytest -q tests/unit/test_plugin_kernel_v3.py
runtime\python.exe -B -m pytest -q tests/unit/test_core_host_plugins.py
runtime\python.exe -m harness run journey-plugins
```

完整契约见 [`Sakura Plugin API v3`](../specs/runtime-v2/sakura-plugin-kernel-v3.md)。
