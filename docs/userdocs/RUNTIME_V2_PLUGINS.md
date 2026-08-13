---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-12
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
4. 点击“应用”或“保存”。保存成功后 Sakura 会受控重启 Python Core，设置窗口留在原位并自动连接到
   新 generation。

保存失败时页面会保留尚未提交的草稿，旧 Core generation 继续生效。不要连续点击保存；先根据错误码
检查配置或插件自己的设置值。

## 当前支持范围

Runtime v2 当前支持：

- Agent 工具；需要写入或高风险的工具继续使用原生一次性确认。
- prompt patch 和动态 context；宿主始终把插件文本视为不可信内容并执行预算、截断和防注入规则。
- `app`、`message`、`tool` 生命周期摘要事件；不向插件传递消息正文、完整历史、工具参数或结果。
- 插件启停、声明式字段和非危险设置 action。

以下贡献会显示为不可用，不会穿过 worker 边界：Qt `tools_tab`、聊天输入控件、角色 renderer、TTS、
浏览器/移动桥接，以及依赖宿主 UI/TTS/Input/Mobile 服务门面的能力。

## 状态和故障排查

- `disabled`：插件已禁用。
- `starting`：worker 正在发现和加载插件；Core 和聊天不需要等待它。
- `ready`：插件贡献已绑定到当前 Core generation。
- `degraded`：清单、API、权限、贡献、回调或 worker 失败；其他插件和 Core 功能仍可用。
- `stopping` / `stopped`：Core 正在重启或 Sakura 正在退出，旧贡献已失效。

常见原因码：

- `API_VERSION_UNSUPPORTED`：插件的 `api_version` 不是当前支持的 `2`。
- `PERMISSION_UNKNOWN`：`plugin.yaml` 声明了未知权限。
- `CONTRIBUTION_DUPLICATE`：工具、patch、provider、设置区块或 action ID 重复。
- `PLUGIN_CALL_TIMEOUT` / `PLUGIN_WORKER_EOF`：插件回调超时或 worker 意外退出。当前 generation 不会自动
  重启插件；受控重启 Core 后才会重新加载。
- `CONFIG_REVISION_CONFLICT`：配置已被另一个设置窗口修改。保留草稿，刷新到当前 generation 后重试。

设置页和统一运行日志不会显示插件 entry、安装/数据路径、异常正文、私有设置、消息正文或工具参数/结果。
排查时可查看[统一运行日志](RUNTIME_LOG_TROUBLESHOOTING.md)，但不要公开上传插件配置或私有数据。
