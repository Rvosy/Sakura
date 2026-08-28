---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-28
---

# MCP 工具

Sakura 会读取用户数据目录下的 `config/mcp.yaml`，连接其中启用的 MCP Server，并把可用工具加入当前聊天。Server 在后台连接；单个 Server 失败不会阻止普通聊天或其他工具。

## 配置和状态

默认配置只提供 Web 搜索。Sakura 不内置桌面控制 Server，也不在设置页提供桌面 MCP 开关。运行日志中的
稳定原因码可用于判断 Server 状态：

- `disabled`：没有启用；
- `starting`：正在连接并读取工具；
- `ready`：工具已经注册；
- `degraded`：配置、连接或工具发现失败；
- `stopping` / `stopped`：Core 正在重启或退出。

## 高级配置

`mcp.yaml` 支持 stdio、SSE 和工具过滤。凭据应放在 Server 支持的环境变量或安全配置中，不要写进 URL userinfo。配置文件包含密钥时，不要上传到 Issue。

取消聊天会停止当前工具链。Sakura 不会自动重放失败的 MCP 工具调用，因为工具可能已经产生副作用。

## 排查

- `CONFIG_MISSING`：用户数据目录下的 `config/mcp.yaml` 不存在。
- `CONFIG_INVALID`：YAML 结构或字段类型不正确。
- `COMMAND_NOT_FOUND`：stdio 命令不在 bundled Runtime 可解析的路径中。
- `TIMEOUT`：Server 没有在限定时间内完成连接。
- `NO_READY_SERVERS`：没有 Server 成功提供工具。

先单独验证 Server 的启动命令，再查看[运行日志](RUNTIME_LOG_TROUBLESHOOTING.md)。日志不会保存 MCP 参数、结果、环境变量或凭据。
