---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-20
---

# Runtime v2 Python 插件

Runtime v2 会从 Sakura 目录下的 `plugins/*/plugin.yaml` 发现 Python 插件，并在“插件”设置页显示版本、
权限、启用状态和稳定的加载原因码。插件在当前 Core generation 的私有 worker 中运行；某个插件损坏、
不兼容或调用超时不会阻塞桌宠、普通聊天、内置工具或 MCP。

插件不是安全沙箱。worker 进程用于超时终止、故障隔离和随 Core 一起回收，插件仍拥有当前用户账户的
文件与网络权限。只安装你信任来源的插件。

## 启用、禁用与详细设置

1. 打开 Sakura 设置，进入“插件”。
2. 查看插件状态、权限和 `reasonCode`。必需插件的开关会锁定，不能禁用。
3. 修改开关或插件声明的详细设置。设置页只会渲染受支持的声明式字段；不会加载插件提供的网页或 Qt
   控件。
4. 点击“应用”或“保存”。Plugin API v3 的启停会在当前 worker 内生效；需要重建运行对象的设置会显示
   “重新加载插件”，不会重启 Python Core。仍使用 API v2 的旧插件保存后会受控重启 Core，设置窗口留在
   原位并自动连接到新 generation。

保存失败时页面会保留尚未提交的草稿，旧 Core generation 继续生效。不要连续点击保存；先根据错误码
检查配置或插件自己的设置值。

## 当前支持范围

Runtime v2 当前支持：

- 助手工具；用户发起请求后工具会直接执行，不再弹出权限或二次确认。
- prompt patch 和动态 context；宿主始终把插件文本视为不可信内容并执行预算、截断和防注入规则。
- `app`、`message`、`tool` 生命周期摘要事件；不向插件传递消息正文、完整历史、工具参数或结果。
- 插件启停、声明式字段、受限 Collection 和非危险设置 action。
- 使用普通 Host Service 接入的 TTS、Memory 与 Playwright 浏览器工具；截图通过 generation-bound
  Artifact 传递，不会把二进制/Base64 放进 Plugin Bridge。

以下贡献会显示为不可用，不会穿过 worker 边界：Qt `tools_tab`、聊天输入控件、角色 renderer、移动桥接，
以及依赖尚不存在的宿主 UI/Input/Mobile 服务门面的能力。

当前 Runtime v2 中，声明 `mobile_chat` 的 Sakura Mobile 会显示
`degraded / HOST_SERVICE_UNAVAILABLE`，不会启动一个无法聊天的网页入口。移动端桥接将在后续平台桥接
阶段迁移；如需现有手机端能力，请继续使用 legacy Qt 入口。

插件设置中的运行状态、链接和错误等只读字段只用于显示，不会随“应用”或设置 action 回传给插件。

## 状态和故障排查

- `disabled`：插件已禁用。
- `waiting`：Plugin API v3 插件缺少 required Service，Provider 恢复后会自动重试激活。
- `active`：Plugin API v3 setup 完成，Service、Tool、Settings 与 Effects 已整体发布。
- `failed` / `conflict`：插件 setup/运行失败，或 Service 唯一性冲突；其他插件和 Core 功能仍可用。
- `starting` / `ready` / `degraded`：worker 或 API v2 兼容链的发现、就绪与局部失败状态。
- `stopping` / `stopped`：Core 正在重启或 Sakura 正在退出，旧贡献已失效。

常见原因码：

- `API_VERSION_UNSUPPORTED`：插件 manifest 不是当前支持的 `api: 3` 或兼容 `api_version: 2`。
- `PERMISSION_UNKNOWN`：`plugin.yaml` 声明了未知权限。
- `CONTRIBUTION_DUPLICATE`：工具、patch、provider、设置区块或 action ID 重复。
- `PLUGIN_CALL_TIMEOUT` / `PLUGIN_CALLBACK_TIMEOUT` / `PLUGIN_WORKER_EOF`：插件调用超时或 worker 意外退出。
  原调用不会自动重放；Sakura 会在同一个 Core generation 按已保存的启停状态重建 worker 和贡献。
- `PLUGIN_CALLBACK_IO_FAILED` / `PLUGIN_CALLBACK_DATA_INVALID` / `PLUGIN_CALLBACK_FAILED`：插件 callback 的
  脱敏失败分类；原因码不包含网址、系统路径、工具参数或插件异常正文。
- `CONFIG_REVISION_CONFLICT`：配置已被另一个设置窗口修改。保留草稿，刷新到当前 generation 后重试。

设置页和统一运行日志不会显示插件 entry、安装/数据路径、异常正文、私有设置、消息正文或工具参数/结果。
排查时可查看[统一运行日志](RUNTIME_LOG_TROUBLESHOOTING.md)，但不要公开上传插件配置或私有数据。
