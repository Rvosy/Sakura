# Sakura Mobile 手机网页端插件

`sakura_mobile` 是 Sakura 的可选手机网页端插件。它让手机浏览器成为桌面端 Sakura 的一个远程聊天入口，而不是再启动一套独立的 Sakura。

> 安全与能力边界：手机请求会进入 Sakura 的受控 Qt 聊天队列，与桌面聊天串行执行；手机端当前不提供宿主工具调用，因此不会出现需要在手机上确认、但无法完成确认的高风险动作。手机端只跟随桌面当前角色，不提供网页内角色切换。

手机端可以查看桌面当前角色的历史消息、发送文字和图片，并收到分段显示的回复。它复用桌面端当前的模型配置、聊天记录和长期记忆，因此电脑与手机看到的是同一段对话和同一套记忆。

## 功能

- 在手机浏览器中与 Sakura 聊天；
- 跟随电脑端当前角色；
- 读取并写入对应角色原有的聊天记录；
- 发送文字、相册图片或直接拍摄的照片；
- 将图片以兼容 OpenAI 的视觉消息格式交给当前模型；
- 将一条回复中的多个句段按聊天气泡依次显示，并在间隔中显示输入状态；
- 通过访问 token 保护网页和 API；
- 可用于局域网访问，也可配合 Tailscale 在外网安全访问；
- 使用桌面端同一个长期记忆库，不会额外打开 Qdrant，避免文件锁冲突。

## 工作原理

手机浏览器只负责界面和上传请求。真正的模型调用、工具调用、历史写入和记忆读写始终在已经启动的 Sakura 主进程内完成：

```text
手机浏览器
  -> Sakura Mobile HTTP 服务
  -> 插件服务接口
  -> Sakura 主窗口回调
  -> MobileChatBridge
  -> AgentRuntime / ChatHistoryStore / MemoryStore
```

插件中的 HTTP 服务使用 Python 标准库 `ThreadingHTTPServer`，不依赖 Flask、FastAPI 或单独的 Node 前端。移动网页的 HTML、CSS、JavaScript 内嵌在 `server.py`，因此部署时不需要额外构建静态资源。

`MobileChatBridge` 只接受桌面当前角色的手机请求。它使用当前角色自己的提示词、聊天历史和记忆作用域；为避免手机端无法处理高风险确认，手机会话不暴露宿主工具。每次手机发消息前，它还会从主程序刷新 API Key、模型和上下文提供者，所以在电脑设置中更换模型后，下一条手机消息会自动使用新配置。

## 对 Sakura 主体的改动

插件目录本身不足以完成集成，因为原始插件接口没有提供“手机聊天”这一类宿主能力。为保持插件不直接操作主窗口、Qdrant 或内部对象，补丁只增加了一条窄桥接链路。

| 文件 | 改动目的 |
| --- | --- |
| `app/core/mobile_chat_bridge.py` | 新增手机请求到 Sakura 运行时的桥接层，处理当前角色校验、聊天历史、图片消息、分段回复与记忆作用域。 |
| `app/plugins/services.py` | 新增 `PluginMobileService`，只暴露 `characters()`、`history()`、`chat()` 三个能力。 |
| `app/plugins/__init__.py` | 导出新增的插件服务类型。 |
| `app/ui/pet_window.py` | 在桌面主窗口准备好后，将真实角色、历史和聊天回调注入 `PluginMobileService`；手机回合完成时接入原有自动记忆整理计数。 |
| `plugins/sakura_mobile/` | 插件清单、生命周期、设置面板、HTTP 服务和手机网页界面。 |

这种设计有两个关键点：

1. 手机端只允许访问桌面当前角色；聊天执行通过宿主队列串行化，避免网页端跨角色记忆串线。
2. 插件只能通过 `PluginMobileService` 调用三项经过限定的能力，避免网页服务直接耦合 Sakura 内部 UI 和存储实现。


## 使用方法

1. 正常启动 Sakura。手机端是主进程的一部分，因此 Sakura 必须保持运行。
2. 打开 Sakura 的设置页面，在“手机端”中启用服务。
3. 设置监听地址、端口和访问 token。
4. 保存设置。插件会自动重启网页服务。
5. 在手机浏览器中打开下面格式的地址：

```text
http://电脑IP:端口/?token=你的访问token
```

例如局域网访问：

```text
http://192.168.1.23:8765/?token=请替换为你的token
```

进入网页后会显示桌面当前角色，输入文字或选择/拍摄图片发送。

## 网络配置建议

### 仅本机或通过 Tailscale Serve 访问

推荐将监听地址设为：

```text
127.0.0.1
```

然后在电脑上运行：

```powershell
tailscale serve --bg --http=8766 127.0.0.1:8765
```

请在手机端使用 Tailscale 输出的 MagicDNS 地址访问，例如：

```text
http://你的设备名.你的tailnet.ts.net:8766/?token=你的访问token
```

这会让 Sakura 的服务只监听本机，再由 Tailscale 负责远程访问控制，适合长期使用。

### 局域网直接访问

将监听地址设为：

```text
0.0.0.0
```

再使用电脑的局域网 IP 访问，例如 `192.168.x.x`。这种方式会向局域网暴露网页服务，务必设置强 token，且不要将端口直接映射到公网。

## 配置文件

插件自带默认配置：

```text
plugins/sakura_mobile/config.json
```

实际由用户修改、应当保留在本机的配置位于：

```text
data/plugins/sakura_mobile/config.json
```


## HTTP 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/` | 返回手机网页。 |
| `GET` | `/api/status` | 检查服务状态。 |
| `GET` | `/api/characters` | 获取桌面当前角色。 |
| `GET` | `/api/history` | 获取角色历史消息。 |
| `POST` | `/api/chat` | 发送文字和可选图片，获得回复分段。 |

所有接口都需要 token。网页会从 URL 中读取 token，API 也接受查询参数、JSON 请求体或 `X-Sakura-Mobile-Token` 请求头中的 token。

单次上传的请求体上限为 12 MiB。插件默认关闭；启用后的默认监听地址为 `0.0.0.0`，可供同一局域网设备访问。服务还会使用 30 秒连接超时、最多 8 个并发请求以及每客户端每分钟 60 次请求的限制。图片会作为 `data:image/...` 数据传给桌面端，再转为模型兼容的 `image_url` 消息；是否能够理解图片取决于当前选择的模型是否支持视觉输入。

## 日志与排查

手机网页服务的访问日志写入：

```text
data/logs/mobile-server.log
```

日志会记录连接来源、请求方法、路径、Host、Origin 和 User-Agent，适合排查手机无法连接、Tailscale 代理或 token 验证问题。

常见问题：

- **手机无法打开网页**：确认 Sakura 正在运行、插件已启用、地址和端口正确，并检查 `mobile-server.log` 是否收到请求。
- **端口已被占用**：在设置中换用未占用端口，例如 `8766`，然后重新保存设置。
- **远程访问失败但局域网正常**：优先使用 `tailscale serve` 给 `127.0.0.1:8765` 建立代理，并使用其 MagicDNS 地址，不要直接依赖 Tailscale IP 加端口。
- **手机端没有同步新模型**：电脑端修改设置后，再发送一条新的手机消息；桥接层会在每回合开始前刷新配置。
- **相机按钮不可用**：直接拍照由移动浏览器决定，部分浏览器要求 HTTPS 或明确的相机权限。可尝试使用 Chrome/Edge，并通过 Tailscale HTTPS 访问。

## 安全说明

- 默认 token `sakura` 仅用于本地测试，正式使用前请改为足够长的随机字符串。
- token 等同于进入手机端的密码，不要分享截图中包含 token 的链接。
- 不要将 `8765` 端口暴露到公共互联网。
- 推荐使用 Tailscale Serve + MagicDNS；它比直接暴露局域网监听更适合在外出时使用。
- 浏览器无法读取电脑屏幕、记忆数据库或本地文件；它只能访问插件显式提供的 HTTP 接口。

## 开发与验证

修改后可先进行语法检查：

```powershell
.\runtime\python.exe -m py_compile `
  app\plugins\services.py `
  app\core\mobile_chat_bridge.py `
  app\ui\pet_window.py `
  plugins\sakura_mobile\server.py `
  plugins\sakura_mobile\plugin.py `
  plugins\sakura_mobile\settings_panel.py
```

随后启动 Sakura，依次验证：打开 `/api/status`、发送文本、上传或拍摄图片、切换角色，以及检查手机和桌面端是否出现相同的聊天历史。
