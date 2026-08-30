# Sakura Mobile 手机网页端插件

`sakura_mobile` 是 Plugin API v4 插件，用于把手机浏览器接入桌面端 Sakura 的同一条聊天、历史和角色链。

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

插件自带默认配置：

```text
plugins/sakura_mobile/config.json
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
