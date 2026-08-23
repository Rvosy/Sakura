---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-23
---

# Runtime v2 MCP 工具

Runtime v2 可以把 `data/config/mcp.yaml` 中启用的 MCP Server 工具加入当前角色的聊天工具列表。MCP 在
后台启动，不会为了等待 Server 而阻塞桌宠和普通聊天；Server 尚未就绪或启动失败时，其他功能仍可用。

## MCP Server 配置

默认配置启用 `web` MCP，为聊天提供后台网页搜索和抓取工具。Windows 不再内置或发行桌面控制 MCP；
需要桌面自动化时应通过插件提供能力。macOS 仍可在设置的 Tools 区域启用实验性的桌面 MCP，保存后
Core 会受控重启，设置窗口会原位绑定到新一代 Core。

取消当前聊天会停止尚未完成的工具链。桌面开关只控制当前受支持平台的桌面 MCP；Windows 和 Linux
不会显示“桌面控制”设置分组，也不会启用桌面 Server。

## 状态含义

- `disabled`：配置或桌面开关未启用该 Server。
- `starting`：Core 正在连接并读取工具；桌宠不需要等待它。
- `ready`：工具已注册到当前 Core generation。
- `degraded`：该 Server 的配置、命令、连接或工具列表失败；其他 Server 和聊天不受影响。
- `stopping` / `stopped`：Core 正在重启或 Sakura 正在退出，旧工具不能继续调用。

在支持桌面 MCP 的平台，设置页只显示 Server 的稳定名称、transport、状态、原因码和工具数量，不显示
command、参数、环境变量、URL、headers 或凭据。

## 故障排查

- 显示 `CONFIG_MISSING`：检查 `data/config/mcp.yaml` 是否存在。
- 显示 `CONFIG_INVALID`：YAML 顶层、transport 或字段类型不符合配置契约。修正后重启 Core。
- 显示 `COMMAND_NOT_FOUND`：对应的本地运行命令未安装在 Sakura bundled runtime 中；不要依赖系统
  PATH 中的同名命令。
- 长时间停在 `starting` 或显示 `TIMEOUT` / `NO_READY_SERVERS`：检查 Server 本身能否在限定时间内启动，
  再查看[统一运行日志](RUNTIME_LOG_TROUBLESHOOTING.md)。日志不会记录 MCP 参数、结果或凭据。
- Core 重启后旧状态短暂出现：等待设置页自动重绑；旧 generation 的迟到状态不会生效。

高级用户可以继续编辑 `data/config/mcp.yaml` 配置 stdio/SSE 和工具过滤。旧确认策略字段当前只兼容读取，
不会激活助手二次确认。修改前先备份文件；
不要把 token 写入 URL userinfo，也不要把含凭据的配置或日志贴到公开问题中。
