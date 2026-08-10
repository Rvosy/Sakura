---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-08-11
---

# Runtime v2 MCP 开发与验证

Runtime v2 通过 `assistant.mcp-v1` 协商 MCP 设置和运行状态。MCP provider 是 Assistant/Core generation
私有资源：`AssistantAdapter` 创建 provider，后台完成 Server initialize 与 `tools/list`，再把通过校验的
工具注册到 WP-4-02 `ToolRegistry`。它不是独立生命周期根。

## 代码入口

- `app/agent/mcp/config.py`：有界 YAML parser、stdio/SSE 和工具策略配置。
- `app/agent/mcp/bridge.py`：官方 MCP ClientSession、调用 deadline、结果和图像边界。
- `app/agent/mcp/provider.py`：后台注册、Server 隔离、动态注销和脱敏状态。
- `app/core_host/mcp_settings.py`：`mcp.settings.get/save` 与桌面偏好的原子保存。
- `app/core_host/assistant_adapter.py`：generation owner 与 Qt-free 资源接线。
- `desktop/src-tauri/src/mcp_settings.rs`：Shell 对 Core DTO 的严格二次校验。
- `desktop/frontend/settings/mcp-runtime.js`：设置草稿、状态展示和 generation 重绑定。

stdio Server 由 Python Core 直接创建，因此仍处于 Rust supervisor 为 Core 建立的受控进程树内。正常退出时
provider 先注销工具并关闭 session；Core 崩溃、强杀或清理超时则由 Rust 进程树兜底回收全部后代。SSE
session、连接任务和 event loop 也不得跨 generation 复用。

## 安全边界

配置中的 command、args、env、headers、URL 凭据只留在 Core 私有对象内。设置 DTO 只允许平台支持性、
偏好、配置状态、稳定 reason code 以及最多 16 个脱敏 Server 状态。工具调用沿用 Action ID：Core 保存
不可变参数，WebView 只能提交确认决定。超时或关闭后的 handler fail closed。

新增或修改 Server 接入时应同时验证：

- runtime token 只解析到 bundled runtime，不回退系统 PATH；
- 初始化、列表和调用均有正 deadline，单 Server 失败不影响 Core readiness；
- 工具名、description、JSON Schema 和文本/structured/image 结果满足大小与深度限制；
- 错误、stderr、参数和结果不进入 IPC DTO 或统一日志；
- provider 关闭后工具被精确注销，旧 handler、Action ID 和 generation 决定不能执行。

## 测试

定向自动旅程使用真实 FastMCP stdio fixture：

```powershell
runtime\python.exe -m harness run journey-mcp
```

它覆盖慢启动期间 Core 先就绪、Server 最终注册、配置损坏、命令缺失、状态脱敏和 Core 退出后子进程零
残留，并同时运行 Rust capability/DTO 与前端重绑定测试。Windows 实机候选使用：

```powershell
& .\desktop\tests\windows_wp_4_03_mcp_acceptance.ps1
```

脚本只操作系统临时目录中的隔离 assistant root，使用本地 provider 验证允许、拒绝和超时，检查真实
Windows MCP、Core 重建、统一日志脱敏和退出零残留；脚本完成不等于项目负责人验收。
