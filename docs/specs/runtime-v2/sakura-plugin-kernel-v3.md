---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-23
---

# Sakura Plugin API v3

Plugin v3 是 Runtime v2 的 Python 扩展边界。它只解决当前需要的插件组合、进程隔离、配置和数据目录，
不提供动态治理平台。`api: 3` 本轮直接破坏性收缩；不提供旧 v3 compatibility shim。

## 1. 运行模型

- 一个 Core generation 最多拥有一个 Plugin Worker 子进程。
- Core 在 Worker 启动前扫描一次 bundled/user inventory，并传入不超过 64 个运行规格。
- Worker 根据 manifest `provides/requires` 计算确定性拓扑顺序，一次完成加载。
- 缺依赖、依赖循环、Service 冲突、清单错误或 `setup()` 异常只把相关插件置为 `failed`。
- Worker 不做局部 reload、teardown/rebind、reconcile、失效传播或自动停用。
- Core/Shell 退出时先请求普通关闭，超时后强杀整棵 Worker 进程树。

插件公开状态只有：

| state | 含义 |
|---|---|
| `disabled` | 用户配置为停用 |
| `active` | `setup()` 完成并已发布贡献 |
| `failed` | 该次 Worker 启动中未能加载 |

公开插件记录只含基本身份、来源、启用/必需/兼容标志、三态结果、一个稳定 `reasonCode` 和声明式设置
sections；不公开路径、entry、依赖图、handler/effect 数量、冲突集合或调和状态。

## 2. Manifest

最小清单：

```yaml
api: 3
id: example_plugin
name: Example Plugin
version: 1.0.0
entry: plugin:ExamplePlugin
enabled: true
priority: 100
provides:
  - example.service
requires:
  - sakura.host.tools
```

`id`、`entry`、`provides` 和 `requires` 必须可在启动时完整验证。`priority` 只用于稳定排序，不改变依赖
关系。`optional` 已删除；出现该字段的 v3 清单明确失败。只有 bundled 插件可以声明 `required`。

`provides` 是必须兑现的声明：`setup()` 返回前没有提供对应 Service 时，插件以
`DECLARED_SERVICE_MISSING` 失败。一个 Service 只能有一个插件提供者，也不能覆盖 Host Service。

## 3. 插件入口与 Context

entry 指向一个可无参构造的类。Worker 只调用一次：

```python
class ExamplePlugin:
    def setup(self, context) -> None:
        ...
```

`setup()` 必须返回 `None`。公共 context 精确为：

- `get(service_key)`：取得已加载的 Service。
- `provide(service_key, service, exports=...)`：提供有界、可跨 Worker 调用的方法集合。
- `on(event_name, handler)`：订阅 Host 派发的事件。
- `effect(cleanup)`：登记普通 cleanup；返回可提前执行的 dispose。
- `config`：仅含 `get/update/replace/on_change`。
- `data_path(relative_path)`：取得插件私有数据目录内的安全路径。

不存在 `inject`、插件侧 `emit`、transform、Session context 或 `plugin.shutdown()`。插件只能响应 Host
事件；需要跨插件调用时使用 Service。

## 4. Cleanup 与错误

每个插件只有一个 root cleanup 栈。`provide/on/effect/config.on_change` 产生的清理项按登记顺序入栈，
`setup()` 失败或 Worker 关闭时严格 LIFO 执行。cleanup 应幂等；单个 cleanup 失败不阻止其余项继续执行。

- Event Handler 异常记录一次，继续派发，插件保持 `active`。
- Service 调用异常原样作为该次调用失败返回，插件保持 `active`。
- 两者都不触发自动停用、consumer 失效传播或 reconcile。

## 5. 配置与数据

插件根目录的 `config.json` 是 packaged defaults，私有数据目录的 `config.json` 是用户 overrides；读取时
后者覆盖前者。`update/replace` 仅接受有界 JSON，并以原子写入保存 overrides。

`on_change` 返回 `applied`、`restart_required` 或 `error`。没有 handler 时默认
`restart_required`：

- `applied`：保留当前 Worker。
- `restart_required`：原保存不重放，Core 重建整个 Worker；重建成功后向设置调用者报告 `applied`。
- `error`：保存调用明确失败，不自动调和。

`data_path()` 拒绝绝对路径、盘符、UNC、`..` 和越过插件私有根的解析结果。插件安装路径、用户私有数据
和 manifest entry 不进入公开 DTO。

## 6. Worker 管理与超时

启用、停用、显式 reload、安装和卸载均通过“保存期望状态/代码变更，然后重建整个 Worker”生效。
管理调用成功时只返回 `applied`；重建失败时 Worker 保持不可用并返回明确错误。

任意 Worker 请求超时后：

1. 该次调用失败，绝不重放。
2. Core 强制终止旧 Worker 进程树并失效其 token/callback/contribution。
3. 对同一失败 token，后台最多尝试重建一次。
4. 重建失败后保持 `failed`，等待用户 reload 或 Core 重启。

IPC 继续保留 generation/token 身份、单 writer、pending 上限、JSON/frame 大小限制和 deadline。Worker IPC
只包含 initialize/status、Service call、Host callback、Host event 和 close；没有局部 lifecycle 或 Session 命令。

## 7. Host Services 与 Legacy

当前 Host Service 包括 tools、context、settings、model slots、character 和 artifacts。Collection/surface 是
现有官方插件使用的有界 settings 扩展，不允许插件注入 HTML/JavaScript/CSS。TTS、Memory、Mobile 等
领域协议是普通 Service 或 Host descriptor，不扩张 Kernel API。

Plugin v3 只属于 Runtime v2。Legacy Qt 不加载 v3 插件，也不作为兼容宿主。

## 8. 验证

最低回归覆盖：确定性顺序、缺依赖/循环/冲突/setup 失败、LIFO、Handler/Service 异常不改状态、整
Worker 重建、配置触发重建、超时不重放且只重建一次，以及已删除 Context/IPC 命令被拒绝。

开发示例见 [`SAKURA_PLUGIN_SDK.md`](../../devdocs/SAKURA_PLUGIN_SDK.md)，生命周期取舍见
[`ADR-0029`](../../adr/0029-coarse-plugin-worker-lifecycle.md)。
