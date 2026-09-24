---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-09-24
---

# MCP 基础组件

需要 MCP 功能时，安装对应的服务插件，并按插件说明完成配置和授权。MCP 基础组件负责连接服务器、处理请求和释放连接资源。

基础组件没有独立设置页，也不提供服务器管理或导入功能。单独启用它不会启动服务器或增加聊天工具。

Sakura 默认不安装具体的 MCP 服务，也不恢复旧 `mcp.yaml`。服务插件的开发者可参考[接口说明](../devdocs/RUNTIME_V2_MCP.md)。

网页搜索和读取由[联网工具插件](WEB_SEARCH.md)提供，不需要 MCP。
