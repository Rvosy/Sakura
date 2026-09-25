# 手机聊天插件

通过手机浏览器与电脑上的 Sakura 聊天，发送文字或图片、查看聊天历史，使用桌面端当前角色和同一份会话数据。插件 ID 为 `sakura_mobile`，使用 Plugin API v4。使用时需要保持电脑上的 Sakura 运行。

## 安装与升级

此插件单独分发，新用户不预装。在“设置 → 插件 → 市场”搜索“手机聊天”并安装，也可以从“更多 → 从 ZIP 安装…”导入插件包，然后启用插件。
插件只依赖 Python 标准库和宿主公开服务，无需额外 Python 依赖。

```text
runtime/bin/python tools/release/package_optional_plugin.py --source plugins/optional/sakura_mobile --output artifacts/plugins/sakura-mobile-1.0.0.zip
```

Windows 将上述 Python 路径替换为 `runtime\python.exe`。

从内置版本升级时，Sakura 使用本地文件和发行包中的兼容材料离线迁移，保留配置、数据和启停选择。已有其他版本的用户插件不会自动降级，迁移完成后主动卸载也不会自动恢复。原 `data/plugins/sakura_mobile/config.json` 和聊天历史继续使用。

迁移在启动时执行，安装版和便携版使用同一流程；便携版需要沿用原用户目录。失败时可从市场重新安装，详见[插件升级指南](../../../docs/userdocs/RUNTIME_V2_PLUGINS.md#升级已有安装)。

## Runtime v2 当前状态

Runtime v2 通过普通 `sakura.host.mobile` Host Service 提供当前角色、Timeline 和聊天入口。聊天使用显式
`begin/poll/cancel`，避免让一次模型回合占住短时 Plugin RPC；图片先写入现有
`sakura.host.artifacts`，跨进程只传有界 descriptor。

## 激活后的能力边界

插件激活后会：

- 使用 Python 标准库 `ThreadingHTTPServer` 提供手机网页和 JSON API；
- 通过 `sakura.host.mobile` 读取当前角色、历史和提交聊天，不直接访问 Core/UI/Qdrant 内部对象；
- 通过 `sakura.host.artifacts` 传递图片，不把大型 data URL 塞进 Plugin IPC；
- 通过 root Effect 关闭 HTTP server 并等待监听线程退出；
- 使用 `sakura.host.settings` 注册声明式设置、状态和刷新 action；
- 保存设置后在当前插件生命周期内重启 server，并返回 `applied` 或 `error`；
- 继续限制 12 MiB 请求体、8 个并发请求、每客户端每分钟 60 次请求和 30 秒 socket timeout。

Host Service 把任务绑定当前 generation 和插件 scope；插件停止时 Runtime 只取消该 scope 尚未完成的任务。

## 配置

打开插件设置，修改访问 token，开启手机网页服务并保存。将手机和电脑接入同一局域网，或通过 Tailscale 互通，再用手机浏览器打开设置中显示的访问地址。地址包含访问令牌，不要公开分享。

插件自带默认配置：

```text
plugins/optional/sakura_mobile/config.json
```

用户覆盖配置：

```text
data/plugins/sakura_mobile/config.json
```

字段包括：

- `enabled`：是否启动手机网页服务；
- `host`：监听地址；
- `port`：1–65535；
- `token`：访问 token，启用时不能为空。

## HTTP 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/` | 返回手机网页 |
| `GET` | `/api/status` | 检查服务状态 |
| `GET` | `/api/characters` | 获取当前可用角色 |
| `GET` | `/api/history` | 获取角色历史 |
| `POST` | `/api/chat` | 发送文字和可选图片 |

所有接口都需要 token。正式使用时不要保留默认 token，不要把监听端口直接暴露到公共互联网；远程访问优先
使用 Tailscale Serve 等有独立访问控制的反向代理。
