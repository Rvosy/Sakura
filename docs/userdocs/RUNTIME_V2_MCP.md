---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-09-15
---

# MCP 基础组件

MCP 是供其他插件使用的底层组件。它没有独立设置页，也不提供服务器管理或导入功能。

需要 MCP 的服务插件负责自己的配置、授权和具体功能，再调用基础组件连接服务器。
组件统一处理协议、传输、请求和资源回收。单独启用组件不会启动任何服务器或增加 Assistant 工具。

默认不安装具体 MCP 服务，也不恢复旧 mcp.yaml。以后添加服务时，按对应插件的说明配置；
插件开发者可参考[接口说明](../devdocs/RUNTIME_V2_MCP.md)。

网页搜索和读取仍由[联网工具插件](WEB_SEARCH.md)提供，不需要 MCP。
