---
kind: devdoc
status: current
audience: developer
source_of_truth: self
updated: 2026-09-15
---

# 复用 MCP 系统组件

服务插件声明 `requires: [sakura.mcp]`。官方 SDK 由组件管理，服务插件只负责配置和业务适配。
基础组件不贡献设置页、管理入口或模型工具。消费插件自行选择 Host 设置、工具及其他产品接口。

完整消费插件示例见 [Windows-MCP 包装插件](../../plugins/optional/windows_mcp/README.md)：私有服务端依赖、
stdio 注册、异步目录发现、宿主工具贡献、图像 artifact 和停用回收都通过公共接口完成。

```python
def setup(self, context):
    self.mcp = context.get("sakura.mcp")
    self.handle = self.mcp.registerServer({
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer ..."},
    })["handle"]
    context.effect(lambda: self.mcp.unregisterServer(self.handle))

def start_listing(self):
    return self.mcp.begin(self.handle, "tools/list", {})["operationId"]

def poll(self, operation_id):
    return self.mcp.inspect(operation_id)
```

registerServer 不等待连接；begin 等待对应连接就绪。完整消费结果后调用 release。
目录 nextCursor 原样传给下一次请求的 cursor，不要只读第一页。
大结果的 result 为 null 且附有 length，用 readResult 的 nextOffset 逐段读取 JSON 文本后解析。

需要 elicitation/sampling 时显式启用，通过 events 获取待答请求，插件取得用户输入或调用模型后用 respond 回复。
旧回调与新版输入往返都由 SDK 处理。未知扩展或手动输入往返可使用
`begin(handle, method, params, {"raw": True})`。
完整合同、预算和未支持项见 [组件 Spec](../specs/runtime-v2/mcp-system-component.md)。

句柄不跨插件 scope 共享；重启后重新注册，不保存句柄供下次使用。Runtime 崩溃回收是显式 cleanup 的补充。
组件没有用户服务器列表，不保存连接配置。需要持久 OAuth 时传入稳定 credential_key 作为 registerServer 第二个参数，
凭据按调用插件 ID 隔离。通过 events 接收 authorization 事件，消费插件负责打开其 URL；组件不会自行打开浏览器。

实现位于 plugins/builtin/sakura_mcp，依赖位于组件私有目录。开发环境沿用 tools/development_plugin_dependencies.py，
发行包由 staging 准备。验证运行：

```text
runtime\python.exe -m pytest -q tests/unit/test_mcp_component.py
```

macOS/Linux 使用 runtime/bin/python。测试在隔离进程加载 SDK，不把 MCP 安装进 Core。
不要引用已删除的 app.agent.mcp 或调用 mcp.status.get；旧 journey-mcp 已退役。
