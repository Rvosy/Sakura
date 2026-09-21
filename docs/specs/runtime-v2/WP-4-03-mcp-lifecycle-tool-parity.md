---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-15
---

# MCP 旧接入退役边界

旧 Core MCP 客户端已移除。Core 不再读取或启动 `config/mcp.yaml`，不公布 `assistant.mcp-v1`
能力，也不提供 `mcp.status.get`。原 stdio/SSE Bridge、工具 Provider 和 Core MCP SDK 依赖声明已删除。

旧版数据导入忽略 MCP 配置，不修复、不转换、不恢复旧服务器，也不再依据旧 MCP 状态初始化联网插件。
已有联网插件开关仍被保留；新安装使用插件 manifest 的默认值。

普通聊天、内置工具、联网插件及其他插件继续使用各自现有调用链。共享的工具图像处理和进程回收能力保留。
不能因为旧 MCP 缺失而使 Core 初始化失败，也不能在新组件缺失或停用时回退启动旧实现。

新的 MCP 系统组件从空列表开始，见[组件合同](mcp-system-component.md)。它独立选择 SDK 与协议兼容范围，
不沿用旧客户端实现和配置恢复路径。

验证覆盖 Core 能力协商、聊天初始化、插件运行、联网工具和旧版导入不恢复 MCP。历史验收只说明当时版本，
旧 `journey-mcp` 已退役，新的测试随新组件实现建立。
