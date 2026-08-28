---
kind: spec
status: superseded
audience: maintainer
source_of_truth: self
updated: 2026-08-28
---

# Sakura Plugin API v3

> 本文件是 cutover 前的历史合同，已由 [Plugin Runtime v4](sakura-plugin-runtime-v4.md) 取代。当前 Runtime
> 不再激活 `api: 3` manifest，也不保留 v3 compatibility shim。

Plugin v3 是 Runtime v2 的 Python 扩展边界。它只解决当前需要的插件组合、进程隔离、配置和数据目录，
不提供动态治理平台。`api: 3` 本轮直接破坏性收缩；不提供旧 v3 compatibility shim。

## 1. 运行模型

- 一个 Core generation 最多拥有一个 Plugin Worker 子进程。
- Core 在 Worker 启动前扫描 bundled/user inventory，并在管理变化时传入完整最新运行规格。
- Worker 根据 manifest `provides/requires` 计算确定性拓扑顺序，一次完成加载。
- 缺依赖、依赖循环、Service 冲突、清单错误或 `setup()` 异常只把相关插件置为 `failed`。
- Worker 通过私有 `lifecycle.reconcile` 局部 reload；不提供公共治理 API 或自动停用。
- Core/Shell 退出时先请求普通关闭，超时后强杀整棵 Worker 进程树。

插件公开状态只有：

| state | 含义 |
|---|---|
| `disabled` | 用户配置为停用 |
| `active` | `setup()` 完成并已发布贡献 |
| `failed` | 最近一次启动或局部 reconcile 未能加载 |

公开插件记录只含基本身份、来源、启用/必需/兼容标志、三态结果、一个稳定 `reasonCode`、manifest 中有界的
`provides/requires`、当前 `MISSING_SERVICE` 的精确 `missingServices` 和声明式设置 sections；不公开路径、
entry、完整依赖图、handler/effect 数量、冲突集合或调和状态。

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
- `restart_required`：原保存不重放，局部 reload 该插件及其传递消费者；成功后报告 `applied`。
- `error`：保存调用明确失败，不自动调和。

`data_path()` 拒绝绝对路径、盘符、UNC、`..` 和越过插件私有根的解析结果。插件安装路径、用户私有数据
和 manifest entry 不进入公开 DTO。

## 6. Worker 管理与超时

启用、停用、显式 reload、安装和卸载均先保存期望状态，再局部 reconcile。无关插件 scope 保持不动；
目标 setup 失败时记录为 `failed`，Worker 继续服务。

设置页根据公开的 `provides/requires` 只解析唯一的已安装插件提供者。启用插件时，如果它的直接或传递提供者
仍处于停用状态，必须列出插件名称和 ID，经用户确认后把这些提供者一并加入当前设置草稿；取消时恢复原开关。
停用被其他已启用插件直接或传递依赖的提供者时，必须列出受影响插件并说明它们将无法使用，经确认后才允许
停用；消费者的期望启用状态不被隐式修改。`MISSING_SERVICE` 详情优先用 `missingServices` 对应的提供者名称和
Service key 指明缺少的组件；没有已安装提供者时至少显示 Service key。

任意 Worker 请求超时后：

1. 该次调用失败，绝不重放。
2. Core 强制终止旧 Worker 进程树并失效其 token/callback/contribution。
3. 对同一失败 token，后台最多尝试重建一次。
4. 重建失败后保持 `failed`，等待用户 reload 或 Core 重启。

IPC 继续保留 generation/token 身份、单 writer、pending 上限、JSON/frame 大小限制和 deadline。Worker IPC
只包含 initialize/status、Service call、Host callback、Host event 和 close；没有局部 lifecycle 或 Session 命令。

## 7. Host Services 与 Legacy

当前 Host Service 包括 tools、context、settings、model slots、character、artifacts 和
`sakura.host.ui.composer-tools-v0`。Collection/surface 是现有官方插件使用的有界 settings 扩展；
composer tools 是桌宠输入栏 `+` 工具坞的声明式动作扩展。两者都不允许插件注入 HTML/JavaScript/CSS。

Plugin Settings Snapshot 使用 schema v1。`resource` 值包含 `applicability`：`required`、
`not_required` 或 `unsupported`；旧 Plugin API v3 源码省略时由 Host 归一化为 `required`。插件可把一个
section 通过 `sakura.host.settings.surface-v0.register(sectionId, "about")` 投影到“关于 → 组件”。该
section 必须有 load callback、至少一个只读 `resource` 字段、不得有 save callback 或 Collection，且每个
Action 都必须被资源字段引用。Host 只验证、展示、调用 Action 和在任务运行时轮询；下载、取消、续传、校验、
原子安装与 cleanup 始终由插件实例拥有。

composer tool descriptor 只公开 `toolId/label/description/icon/order`。`icon` 必须选择 Host 内置图标，公开
ID 由 Host 组合为 `<pluginId>:<toolId>`；UI 不接收 callback handle。用户点击后，Host 以
`{"source": "composer"}` 调用对应 callback，callback 只返回
`{"status": "completed", "message": "..."}`。插件停用、局部 reload、Worker 重建或 generation 失效时，
条目和 callback 必须随 scope 一起移除；截图仍是 Host 内置工具，不伪装成插件贡献。

工具坞使用主 WebView 的常驻后备空间：布局提交时一次性在输入栏下方预留四项工具的最大高度，之后打开、
关闭只切换工具坞 CSS 裁切和精确原生命中区域，不得创建第二个 WebView、调整主窗口 bounds 或改变立绘
锚点。工具坞关闭时预留透明区不得占用桌面点击；输入栏贴住原立绘 envelope 底边时仍从上向下展开。
TTS、Memory、Mobile 等领域协议是普通 Service 或 Host descriptor，不扩张 Kernel API。

Plugin v3 只属于 Runtime v2。Legacy Qt 不加载 v3 插件，也不作为兼容宿主。

## 8. 验证

最低回归覆盖：确定性顺序、缺依赖/循环/冲突/setup 失败、LIFO、Handler/Service 异常不改状态、局部
reconcile 不触碰无关 scope、配置触发局部 reload、故障超时只重建一次、composer tool 的有界投影与
callback 清理、About surface 约束与资源默认归一化，以及已删除 Context 命令被拒绝。

开发示例见 [`SAKURA_PLUGIN_SDK.md`](../../devdocs/SAKURA_PLUGIN_SDK.md)，生命周期取舍见
[`ADR-0032`](../../adr/0032-runtime-hot-application-and-local-plugin-lifecycle.md)。
