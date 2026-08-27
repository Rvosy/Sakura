---
kind: userdoc
status: current
audience: user
source_of_truth: self
updated: 2026-08-26
---

# Python 插件

Sakura 使用 Plugin API v3。插件运行在独立 Plugin Worker 中；Worker 出错时，桌宠窗口和 Core 控制仍可响应。

Plugin Worker 不是安全沙箱。插件代码以当前用户权限访问文件和网络，只安装你信任的来源。

## 安装和卸载

1. 打开“设置 → 插件”。
2. 选择“安装 ZIP”或“安装文件夹”。
3. 检查插件名称、作者、说明和申请的服务。
4. 打开启用开关并保存。

Sakura 只安装 `api: 3` 插件。安装包不能包含符号链接、路径逃逸、特殊文件或跨平台非法文件名，也不会自动安装第三方 Python 依赖。

```text
data/user_plugins/<plugin_id>/   用户插件代码
data/plugins/<plugin_id>/        插件配置、数据库和缓存
```

卸载会移除用户插件代码，但保留插件数据。内置插件不能卸载。

## 启停与设置

插件只能向设置页提供 Sakura 支持的声明式字段、状态卡、资源进度和动作，不能加载自己的网页或脚本。保存结果分为：

- `applied`：当前 Worker 已应用；
- `restart_required`：配置已保存，需要重新加载插件；
- `error`：配置已保存，但运行对象没有应用。

启停、安装、卸载和重新加载会重建 Plugin Worker，不会重启桌面应用。失败的操作不会自动重放。

## 状态

- `disabled`：已安装但未启用；
- `waiting`：依赖的 Service 尚不可用；
- `active`：插件已经发布自己的 Service 和 Effect；
- `failed`：manifest、导入或 `setup()` 失败；
- `conflict`：多个插件提供同名 Service；
- `degraded`：Worker 可响应，但部分插件不可用。

常见原因码包括 `API_VERSION_UNSUPPORTED`、`MISSING_SERVICE`、`SERVICE_CONFLICT`、`DEPENDENCY_CYCLE`、`PLUGIN_CALL_TIMEOUT` 和 `PLUGIN_ID_CONFLICT`。安装事务或 Worker 恢复失败时，先重启 Sakura，再查看插件页和[运行日志](RUNTIME_LOG_TROUBLESHOOTING.md)。不要手工移动事务隔离目录。

插件作者请看 [Plugin API v3 开发指南](../devdocs/SAKURA_PLUGIN_SDK.md)。
