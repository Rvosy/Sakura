# Sakura Mobile 手机网页端插件

`sakura_mobile` 是 Plugin API v3 插件，用于把手机浏览器接入桌面端 Sakura 的同一条聊天、历史和角色链。

## Runtime v2 当前状态

当前 Runtime v2 尚未提供移动聊天平台 Service，因此插件稳定显示：

```text
waiting / MISSING_SERVICE
missingServices: [sakura.mobile]
```

waiting 期间不会导入 HTTP 实现、打开端口、创建设置 contribution 或写入插件配置。后续移动平台切片提供
普通 Worker-local `sakura.mobile` Service 后，Kernel 会按 required dependency 生命周期自动激活插件。

这里刻意没有新增 `sakura.host.mobile` 或 Mobile 专用 Bridge。Mobile HTTP 请求发生在插件线程，而当前
`host.call` 只能由 Worker dispatch owner thread 发起；在移动平台的队列、取消和 generation 语义冻结前，
不能用一个表面可调用、实际跨线程失效的 Host proxy 冒充完整能力。

## 激活后的能力边界

插件激活后会：

- 使用 Python 标准库 `ThreadingHTTPServer` 提供手机网页和 JSON API；
- 通过 `sakura.mobile` 读取当前角色、历史和提交聊天，不直接访问 Core/UI/Qdrant 内部对象；
- 通过 root Effect 关闭 HTTP server 并等待监听线程退出；
- 使用 `sakura.host.settings` 注册声明式设置、状态和刷新 action；
- 保存设置后在当前插件生命周期内重启 server，并返回 `applied` 或 `error`；
- 继续限制 12 MiB 请求体、8 个并发请求、每客户端每分钟 60 次请求和 30 秒 socket timeout。

`sakura.mobile` 必须是能安全承接插件 HTTP 线程调用的 Worker-local Service。它怎样把请求排队到 Core、绑定
当前 generation、执行取消并返回聊天结果，属于后续移动平台切片，不由本插件或 Generic Bridge 猜测。

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
