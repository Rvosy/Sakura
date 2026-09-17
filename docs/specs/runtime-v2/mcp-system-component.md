---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-15
---

# MCP 系统组件

## 宿主日志

组件通过 `sakura.host.logging` 写入统一日志。连接开始、就绪和关闭记录为 info；连接、传输、请求失败及工具 `isError` 结果记录为 error；正常取消不记录错误，状态与结果轮询不产生日志。
记录附带消费插件 ID、连接编号、传输类型；请求失败另带操作编号与原因码，不主动写入连接配置、OAuth URL、请求参数或结果正文。
stdio 服务端 stderr 按最多 4096 字符分段读取，以 info 诊断输出送入宿主；MCP 日志通知按协议等级转发。两者经过宿主现有脱敏、有界队列和插件文件归属流程，不另建文件。
消费插件负责自己的业务日志。Windows-MCP 独立插件记录工具发现、就绪、调用失败和停止；其源码位于可选插件目录，独立 ZIP 分发，不纳入安装包或内置插件扫描。

## 职责与默认行为

内置基础组件 `sakura.mcp` 使用 Plugin API v4，默认启用，只提供 Service。它没有独立设置页、服务器列表、
导入入口或 Assistant 工具；也不读取服务器配置、自行启动服务或恢复旧 `mcp.yaml`。
具体插件负责配置、安装资源、界面和业务工具，通过 Service 注册所需服务器。

官方 Python SDK `mcp==2.2.0` 和 HTTP 客户端位于组件私有依赖目录。开发准备和发布 staging 包含该目录，
Core 不导入 MCP，普通启动不下载依赖或服务器程序。旧边界见[退役说明](WP-4-03-mcp-lifecycle-tool-parity.md)。

## 注册参数

连接描述由消费插件传给 registerServer，组件不保存服务器清单。每次启动由插件重新注册。
凭据需要跨重启复用时，可另传 credential_key：1–128 位英文字母、数字、下划线或连字符。
组件按调用插件 ID 与该 key 隔离存储，拒绝同时占用同一凭据记录的连接；不传 key 时仅保留内存凭据。

| 配置 | 行为 |
| --- | --- |
| `transport` | `stdio`、`streamable-http`、`sse`；缺省由 command/url 推断 |
| `type` | 也接受此导入字段；http、streamableHttp 映射到 streamable-http |
| `command`、`args` | 可执行程序及参数数组，由 SDK 启动，不拼接 shell 命令 |
| `env`、`cwd` | 显式环境变量、工作目录；默认环境继承遵循 SDK 允许列表 |
| `encoding`、`encoding_error_handler` | stdio 编码与错误策略，交给 SDK 验证 |
| `url`、`headers`、`proxy` | HTTP/SSE 地址、静态认证头及代理；TLS 校验保持启用 |
| `connectTimeout` | 首次连接与授权期限，默认 60 秒，可调整 |
| `requestTimeout` | 操作总期限，默认 300 秒，可调整 |
| `mode` | 默认 auto，由 SDK 探测新协议并兼容旧握手 |
| `oauth` | true 或配置对象，启用 SDK 授权码流程 |
| `elicitation`、`sampling` | 默认不声明，启用后由消费插件处理待答请求 |
| `roots` | 显式根目录列表，不自动暴露用户目录 |
| `extensions` | 扩展标识到设置对象的映射，交给 SDK advertise |

HTTP 认证失败不触发切换传输或自定义重试。版本协商、游标、取消、重定向与协议验证由 SDK 处理，
组件不另写 JSON-RPC 或握手，也不把任何界面字段或模型工具名的长度限制用于远端名称、Schema 和结果。

## Service 合同

消费插件声明 `requires: [sakura.mcp]`，调用 `context.get("sakura.mcp")`，不导入组件源码或安装另一份 SDK。

| 方法 | 用途 |
| --- | --- |
| capabilities() | API 版本、传输、交互和运行预算 |
| registerServer(config, credential_key?) | 返回 handle，后台连接；不保存服务器配置 |
| unregisterServer(handle) | 标记断开，后台取消操作并回收传输 |
| status() | 当前调用者的状态、协商版本、服务器能力和说明 |
| begin(handle, method, params?, options?) | 返回 operationId；options.raw=true 使用 SDK 低层请求 |
| inspect(operationId) | running/completed/error/cancelled、进度与小结果 |
| readResult(operationId, offset=0, limit=8192) | 分段读取完整结果 JSON 文本，offset 以字符计 |
| cancel(operationId) | 取消操作，SDK 负责协议取消 |
| release(operationId) | 取消并释放操作与临时文件，消费结果后调用 |
| events(handle, after=0, pending_offset=0) | 通知、订阅、待答请求，包含游标和事件丢失标记 |
| readEvent(handle, sequence, offset=0) | 读取大事件的完整 JSON |
| readInput(handle, requestId, offset=0) | 读取大交互请求的完整参数 |
| respond(handle, requestId, response) | 提交 elicitation/sampling 响应，SDK 验证并继续请求 |

目录保留 cursor/nextCursor，调用者逐页发现。tools/call、resources/read、prompts/get 默认走高层 Client，
使旧回调与新版 MRTR 都能通过 events/respond 处理。其他方法走 session.send_request，包括补全和旧资源订阅。
raw=true 允许手动处理扩展结果和输入往返，不设第三方方法白名单。

非 raw 的 subscriptions/listen 使用 SDK 持续订阅，参数沿用 SDK snake_case，是否可用取决于协议和服务器。
旧通知通过 SDK message handler 进入队列。订阅也是可取消操作，受配置期限约束。

## 所有权与回收

句柄属于 Runtime 注入的 (caller_id, caller_scope)，不从业务参数读取身份。同名插件重新启动得到新 scope，
不能使用旧句柄。不同插件的连接和操作相互隔离，不提供全局管理或跨 owner 枚举入口。

Runtime 在停用、重载或崩溃清理后发送 sakura.host.scope.closed。组件撤销 scope，拒绝迟到注册，
取消操作、关闭连接、释放文件。退出时关闭所有连接；stdio 进程树由 SDK 和已有 Worker 监管回收。
SDK 异步上下文始终在同一任务进入和退出。插件注销后重新注册相同凭据 key 时，等待旧连接退出，避免端口和文件冲突。
失败交由消费插件处理，组件不建设后台自愈循环。

## 数据与业务边界

组件不向模型贡献工具。服务插件自行查询目录、选择功能，并通过现有 Host 工具贡献接口发布业务工具。
工具名作为 MCP 请求参数传递，组件不重命名、不截断，也不设置远端方法白名单。
业务插件决定工具风险等级、用户确认、模型调用和内容呈现。

结果保留原始 JSON，包括图片/音频编码、资源链接、结构化结果和扩展字段。大于 32 KiB 的结果进入临时文件，
通过 readResult 读取，不塞入单个 IPC 帧或静默截断。每个 owner 最多保留 128 个操作，超出返回
MCP_OPERATIONS_FULL；这是公开资源预算，调用者必须 release。事件保留最近 128 条，每页最多 16 条事件、
8 个待答请求，大数据使用单独读取接口。

图片/音频附件呈现、MCP Apps、sampling/elicitation 界面和模型调用均属于消费插件。
底层保留内容和交互请求，不把一种产品界面规定为所有服务的使用方式。

## OAuth

发现、注册、PKCE、state/issuer 验证、令牌交换和刷新使用 SDK OAuthClientProvider。
组件提供 loopback、授权事件和可选私有存储，不自行打开浏览器。消费插件从 events 读取 authorization URL，
负责向用户呈现及打开浏览器。回调只监听 127.0.0.1，使用凭据 key 时端口随客户端信息持久化。
oauth 对象可传 port、clientMetadata、clientInfo、clientMetadataUrl；redirect URI 由组件生成。

持久凭据按调用插件、credential_key 和完整 URL 隔离，URL 改变丢弃旧凭据。令牌不进入事件或 Core 日志；
当前是用户根内私有文件，没有系统钥匙串加密。不传 credential_key 的 OAuth 连接只保留内存凭据。
企业身份委托、Client Credentials 专用认证和自定义 Python 扩展解析器尚未提供公开接口。

## 验证与来源

tests/unit/test_mcp_component.py 在隔离 SDK 进程和真实 PluginApplicationHost 中验证新旧 stdio、
HTTP、SSE、工具/资源/提示词、旧 elicitation 与 MRTR、取消、大结果和 scope。
真实宿主用例验证无设置/工具贡献、消费插件停用和崩溃回收，且不重启无关组件。
本地 OAuth 夹具覆盖发现、注册、PKCE、loopback 与重启复用。真实第三方服务、macOS/Linux 进程体验和原生窗口需另行验证。

架构取舍见 [ADR-0059](../../adr/0059-mcp-client-system-component.md)。2026-09-15 查阅：

- [官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)：协议、传输、认证和客户端。
- [VS Code registry](https://github.com/microsoft/vscode/blob/main/src/vs/workbench/contrib/mcp/common/mcpRegistry.ts)：配置与实例分离。
- [Cherry Studio Runtime](https://github.com/CherryHQ/cherry-studio/blob/main/src/main/ai/mcp/McpRuntimeService.ts)：连接生命周期和迟到初始化回收。
- [Cherry Studio SDK 适配](https://github.com/CherryHQ/cherry-studio/blob/main/src/main/ai/mcp/mcpClientSdk.ts)：复用官方传输。

参考架构思路，没有复制这些应用的实现代码，也不把 main 分支文档当作已验证能力。
