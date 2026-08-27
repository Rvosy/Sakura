---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-08-26
---

# MCP 开发与验证

MCP 由当前 Core generation 拥有。配置解析、Server 会话、工具注册和关闭都在 Python Core 内完成；WebView 只显示受限状态。

## 代码入口

- `app/agent/mcp/config.py`：解析 `user_root/config/mcp.yaml`；
- `app/agent/mcp/provider.py`：连接 Server、发现工具并执行调用；
- `app/core_host/assistant_adapter.py`：把 MCP owner 接入 Assistant session；
- `app/core_host/mcp_settings.py`：设置快照和桌面 MCP 开关；
- `app/core_host/server.py`：capability、request 路由和 generation 校验；
- `desktop/frontend/settings/mcp-runtime.js`：设置页状态投影与重绑定。

## 配置

```yaml
enabled: true
default_call_timeout: 30
servers:
  web:
    transport: stdio
    command: runtime-command
    args: [serve]
    enabled: true
    name_prefix: web__
    include_tools: [search, fetch]
    call_timeout: 20
```

transport 只支持 `stdio` 和 `sse`。SSE URL 必须使用 `http` 或 `https`，且不能包含 userinfo。Server 名称、参数、环境变量、headers、工具过滤和超时都有数量或长度上限；解析失败时整份 MCP 配置进入 `invalid`，其他 Core 能力继续初始化。

stdio 命令必须由 bundled Runtime 布局解析，不依赖系统 PATH 的偶然状态。SSE/stdio 凭据不能进入公开 DTO 或运行日志。

## 生命周期

MCP capability 是 `assistant.mcp-v1`。握手未协商该 capability 时，Core 不创建 MCP 边界，也拒绝 MCP 设置请求。

Server 状态为 `disabled`、`starting`、`ready`、`degraded`、`stopping` 或 `stopped`。连接和工具发现异步进行，普通聊天不等待所有 Server。首轮 Prompt 会在有界 dependency gate 中等待 Memory 和 MCP；超时后按当前已就绪能力继续。

取消聊天会取消仍在执行的工具链。Core 关闭时逐个关闭 MCP 会话；超时或关闭错误只写诊断，不允许旧工具进入下一 generation。

## 设置边界

`MCPSettingsBoundary` 只公开：

- 当前平台是否支持桌面 MCP；
- `desktopEnabled`；
- `configState` 和稳定 `reasonCode`；
- Server ID、transport、启用状态、运行状态和工具数量。

command、args、env、URL、headers、工具参数和异常正文不得跨到 WebView。保存请求必须携带当前 generation identity；写入失败时原配置保持不变。

## 工具注册

MCP 工具进入当前 session 的 ToolRegistry。Server 的 `name_prefix` 用于避免命名冲突，`include_tools` 与 `exclude_tools` 决定暴露范围。每次调用都使用 Server 或工具策略给出的 timeout 和 risk。

工具调用结果必须满足 Core 的大小和类型边界。失败不自动重放，因为 Server 可能已经完成外部操作。

## 验证

下面使用 macOS/Linux 路径；Windows 使用 `.\runtime\python.exe`。

```bash
./runtime/bin/python3 -m harness run journey-mcp
./runtime/bin/python3 -m pytest -q tests/unit/test_core_host_mcp.py
node --test desktop/frontend/tests/mcp-runtime.test.js
cargo test --manifest-path desktop/src-tauri/Cargo.toml mcp
```

测试使用临时 app root 和可控的本地 Server，覆盖缺失配置、无效配置、启动超时、工具过滤、取消、Core 重建、迟到状态和进程清理。敏感 sentinel 不得出现在 DTO 或日志中。
