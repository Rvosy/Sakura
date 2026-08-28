---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-28
---

# WP-4-03 MCP 生命周期与工具调用等价规范

## 1. 范围与非目标

本规范冻结 CAP-011 在 Runtime v2 的最小真实纵向链：Core 读取 MCP 配置，为当前 Assistant/Core
generation 建立 stdio 或 SSE session，把获准工具注册到 `ToolRegistry`，并通过既有聊天工具循环完成
直接调用、取消、故障收敛和清理。当前执行状态只以
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 为准。

真实消费者是当前 bundled Python Core、聊天 ToolRegistry 和 Rust Core supervisor/gateway；不得以测试
专用 manager 代替其中任一条产品路径。Runtime v2 不内置、不发行也不提供桌面 MCP 开关，通用 MCP
客户端仍按 `mcp.yaml` 连接用户明确配置的 Server。本 WP 不新增 MCP server，也不迁移 Python 插件、
TTS、截图 resource token、浏览器、主动调度、提醒或通用 worker 平台。

本 WP 复用 ADR-0001/0002/0004/0005/0007 已确定的受控进程、IPC、跨平台、headless Core 与增量设置
方向，不新增 ADR。MCP 是当前 Core generation 的领域资源，不成为第二个生命周期根。

## 2. 配置、凭据与状态边界

- 高级配置源为 `user_root/config/mcp.yaml`；缺失文件等价于 MCP 禁用。配置支持
  总开关、默认调用超时、server 启停、`stdio`/`sse`、command/args/env、URL/headers、工具名前缀、
  include/exclude 与风险元数据。`requires_confirmation` 字段已废止，当前 parser 直接拒绝。
- `{python}`、`{uv}` 等 runtime token 必须解析到当前受控 bundled runtime；不得回退到系统 Python 或
  未经定位器确认的可执行文件。command 缺失产生稳定、可操作且脱敏的 server 错误，不暴露原始异常。
- env、headers、URL userinfo、token、cookie、authorization、command 参数中的凭据和完整绝对路径只能在
  Core 私有配置/session 内存在；不得进入 WebView DTO、IPC event、Snapshot、工具描述、日志或异常正文。
- 配置顶层损坏、字段类型错误或未来不支持的 transport 必须使 MCP 域降级为不可用，并公布稳定原因；
  不得阻止 Core readiness、聊天、control 或其他已验收工具。单 server 失败只隔离该 server。
- MCP 不提供设置页或保存接口；启停和 Server 配置只来自 `mcp.yaml`，由用户在应用外明确维护。
- Core 的只读 `mcp.status.get` 只公布配置有效性、稳定 reason code，以及 Server 的脱敏 ID、transport、
  启用状态、`disabled|starting|ready|degraded|stopping|stopped` 状态和工具数量；不得暴露 command、args、
  env、headers、URL 凭据或工具参数。

## 3. generation 生命周期与 transport

- MCP provider、event loop、session、连接任务、工具映射、调用任务和状态快照全部由当前 Core
  generation 私有 owner 持有；ToolRegistry 注销或 generation 失效后 handler 必须 fail closed。
- stdio server 必须由 Core 创建且纳入 Runtime v2 受控进程树；Rust supervisor 在 Core 正常退出、崩溃、
  强杀或 shutdown deadline 超限时兜底终止其全部后代。不得 detached、拉起 shell 或遗留孙进程。
- SSE client session 同样绑定 generation；关闭时取消 request、关闭 stream/HTTP client 和 event loop，
  不得把 reconnect timer、socket 或迟到 response 转移到新 generation。
- server 初始化按配置逐个隔离。某个连接、initialize 或 `tools/list` 超时不得拖住其他 server 或 Core
  readiness；已成功 server 可以继续服务。重连只允许在当前 generation 内有界进行。
- 启动/列举使用 server 有效调用 deadline，单次工具调用使用配置的正数 deadline；Core shutdown 对所有
  MCP 清理使用独立、有限的总 deadline，超限即 fail closed 并交由受控进程树兜底，不阻止 Shell 退出。

## 4. 工具注册、直接调用与结果边界

- 工具内部名由经校验的 server prefix 与远端工具名确定，必须稳定、非空、长度有界且不覆盖内置工具；
  冲突、非法名称或非法 input schema 只跳过该工具并记录稳定状态。
- `tools/list` 的 description 与 JSON Schema 必须经过类型、深度、节点数和编码大小上限；未知 schema
  关键字可保留为数据但不能触发代码执行。include/exclude 与 tool policy 只作用于真实远端名称。
- MCP 工具使用同一 `ToolRegistry`、聊天 Operation、取消语义和唯一终态。参数 schema 与当前 generation
  校验通过后由 Core 直接执行；WebView 不接收工具参数，不提交确认决定，也不持有可恢复调用的租约。
- 调用前再次验证参数对象和 generation。超时、取消、transport 关闭、server crash 与协议错误返回稳定、
  有界且脱敏的 ToolResult；不得把 traceback、stderr、HTTP body/header 或原始异常传给模型或 WebView。
- 文本、structured content 和 content item 的总编码大小必须有界，超限返回稳定截断/错误结果。图像结果
  只允许已验证的 `image/*` 与有效 base64/data URL，在 Core 内按独立数量和字节上限提取；对普通 content
  只保留图像存在与 MIME 元数据。不得在本 WP 把图像变成长期 resource token、文件或截图能力。

## 5. 状态、可观测性与故障隔离

WP-4L-01 统一日志只记录 generation、server 的非敏感稳定 ID、transport 枚举、阶段、耗时、计数、结果
code 和截断信息。配置值、环境值、headers、工具 arguments/result、聊天正文、远端 stderr 正文和绝对路径
必须在持久化前为零。高频 stderr 或状态变化必须聚合/有界，不能阻塞 pipe 排水或业务调用。

必须覆盖：配置缺失/损坏、command 缺失、无执行权限、stdio 提前退出、stderr flood、SSE 拒绝/断流、
initialize/list/call/close 超时、重复工具名、恶意 schema、巨大或非法结果、取消竞态、Core crash、Shell
shutdown、旧 generation 迟到和重启后重新绑定。任何单域失败不得拖垮 Core health、聊天、control、Memory、
内置 Tools、设置关闭或共享锁释放。

## 6. 验收与回退

自动门必须在 Windows x64、macOS arm64 和 Linux x64 验证配置 parser、stdio/SSE fixture、ToolRegistry、
直接调用、只读状态 DTO、超时/取消、Core crash/显式重建、受控后代清理和日志脱敏。Runtime v2
Shell/Core 和既有 Tools 回归必须保持通过。

Windows 实机验收使用通用 stdio/SSE fixture，确认 server ready、工具可见、成功调用、取消/超时、Core
重建和退出零残留；扫描日志与状态 DTO，确认 command、args、env、headers、凭据、工具参数/结果和绝对
路径零泄漏。任何平台都不得重新捆绑桌面 MCP。

回退按 WP-4-03 产品提交逆序 revert，关闭 Core MCP 注册后正常退出；不得删除或改写 `mcp.yaml`、
system config 或用户数据。即使清理超时，Rust 受控进程树仍须回收 Core 与 stdio 后代。
