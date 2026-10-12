# 手机聊天

通过手机浏览器与电脑上的 Sakura 聊天，发送文字或图片、查看历史，使用桌面端当前角色和同一份会话数据。电脑上的 Sakura 需要保持运行；回复也会进入桌面字幕和语音流程，语音在电脑播放。

## 安装与连接

在“设置 → 插件 → 市场”搜索“手机聊天”并安装、启用，也可以从 ZIP 安装。插件 ID 为 `sakura_mobile`，使用 Plugin API v4，无额外 Python 依赖。

打开插件设置，修改访问令牌，开启网页服务并保存。手机和电脑接入同一局域网，或通过 Tailscale 互通后，用手机浏览器打开设置中显示的地址。地址包含访问令牌，不要公开分享。

| 配置 | 含义 |
|---|---|
| `enabled` | 是否开启网页服务 |
| `host` | 监听地址 |
| `port` | 监听端口，1–65535 |
| `token` | 访问令牌，启用时不能为空 |

保存后服务按新配置重启。停用插件会关闭服务并取消尚未完成的请求，已有聊天记录保留。更新与旧版迁移见[插件指南](../../../docs/guide/plugins.md#更新与修复)。

## HTTP 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/` | 手机网页 |
| `GET` | `/api/status` | 服务状态 |
| `GET` | `/api/characters` | 可用角色 |
| `GET` | `/api/history` | 角色历史 |
| `POST` | `/api/chat` | 发送文字和可选图片 |

所有接口都需要访问令牌。正式使用时修改默认令牌，远程访问使用有独立访问控制的反向代理，避免把监听端口直接暴露到公网。

## 打包

在仓库根目录执行；macOS/Linux 使用 `runtime/bin/python`：

```powershell
runtime\python.exe tools\release\package_optional_plugin.py --source plugins\optional\sakura_mobile --output dist\sakura-mobile.zip
```

插件通过宿主的角色、历史、对话和图像服务工作。开发接口见[插件能力接入](../../../docs/development/plugin-recipes.md)。
