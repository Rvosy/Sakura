---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-02
---

# Sakura Service 静态控制面合同

## 目的与边界

Sakura Service 是桌面客户端的可选远程辅助服务。首版只提供公开、只读、可缓存的版本、公告和已知问题元数据；
它不运行 Assistant、模型、Memory、插件或用户数据处理，也不是 Sakura 启动、聊天和本地设置可用的前置条件。

服务根固定为：

```text
https://sakura.cialloo.cn/service/v1/
```

GitHub Release 仍是安装资产与签名 Updater 清单的唯一发行数据面。Sakura Service 只投影控制信息，不托管
Setup、Portable ZIP、DMG 或 updater artifact，不得降低 Tauri Updater 的签名校验和用户确认门禁。

## 访问合同

- 当前端点只接受 HTTPS `GET` 和 `HEAD`；写方法必须在 Nginx 层拒绝。
- JSON 使用 UTF-8，响应 `Content-Type` 为 `application/json`，允许公开跨域读取。
- 正常响应允许公共缓存 5 分钟。客户端不得依赖精确缓存时长或 ETag。
- 网络失败、超时、非 2xx、无效 JSON 或不支持的 schema 必须被视为“辅助信息不可用”，不能阻止 Core、聊天、
  设置、手动检查更新或 GitHub Updater。
- 客户端必须把所有返回字段视为不可信输入。URL 只允许导航到经过产品白名单批准的 HTTPS 目标；文本只能作为
  纯文本展示，不解释 HTML、Markdown 命令、脚本或工具调用。

## `releases.json`

`GET /service/v1/releases.json` 的 schema 1 固定为：

```json
{
  "schema": 1,
  "latest": "1.0.2",
  "minimumSupported": null,
  "releaseUrl": "https://github.com/Rvosy/Sakura/releases/tag/v1.0.2",
  "publishedAt": "2026-08-31T16:43:37Z",
  "urgent": false,
  "downloads": {
    "windowsX64Setup": "https://github.com/Rvosy/Sakura/releases/download/v1.0.2/Sakura-1.0.2-windows-x64-setup.exe",
    "windowsX64Portable": "https://github.com/Rvosy/Sakura/releases/download/v1.0.2/Sakura-1.0.2-windows-x64-portable.zip",
    "macosArm64Dmg": "https://github.com/Rvosy/Sakura/releases/download/v1.0.2/Sakura-1.0.2-macos-arm64.dmg"
  },
  "updaterManifestUrl": "https://github.com/Rvosy/Sakura/releases/latest/download/latest.json"
}
```

字段合同：

| 字段 | 合同 |
|---|---|
| `schema` | 当前必须为整数 `1`；未知 schema 整体忽略。 |
| `latest` | 不带 `v` 的稳定 SemVer；prerelease 不发布到此端点。 |
| `minimumSupported` | `null` 表示尚无最低支持策略；非空时必须是不晚于 `latest` 的稳定 SemVer。它只能驱动说明，不得绕过签名或用户确认强制安装。 |
| `releaseUrl` | 与 `latest` 对应的 `Rvosy/Sakura` GitHub Release HTTPS 页面。 |
| `publishedAt` | GitHub Release 的 RFC 3339 发布时间。 |
| `urgent` | 维护者提示位；不得自动下载、安装、退出或重启。 |
| `downloads` | 三个固定平台资产的 GitHub Release HTTPS URL；文件本体不经过 Sakura Service。 |
| `updaterManifestUrl` | 固定为 GitHub 稳定版 `latest.json`；只有该签名清单参与 installed updater。 |

服务端发布命令必须拒绝缺失/额外字段、无效类型、非稳定版本、非 GitHub 资产 URL、版本与 URL 不一致和自动降级。
相同版本重新发布用于幂等修复，允许覆盖。

## `announcements.json` 与 `known-issues.json`

首版只冻结空集合 envelope：

```json
{
  "schema": 1,
  "updatedAt": "2026-09-01T00:00:00Z",
  "announcements": []
}
```

```json
{
  "schema": 1,
  "updatedAt": "2026-09-01T00:00:00Z",
  "issues": []
}
```

在公告项、版本/平台匹配规则、已知问题项和客户端展示/消重合同另行冻结之前，两个数组必须保持为空。不得先向
生产 JSON 添加自由结构，再让客户端猜测字段。

## 正式发布

稳定版发行必须先完成 Windows Setup、macOS 资产、Portable ZIP 和最终签名 `latest.json` 的 GitHub Release
发布，再生成并推送 `releases.json`。这样控制面不会宣告一个尚未具备全部正式资产的版本。

发布使用仓库专用 SSH 身份。服务器必须把该公钥限制为单一 forced command；它不能获得交互 shell、端口转发、
任意远程命令或站点其他路径写权限。CI 必须固定服务器 host key，不得在每次运行时用 `ssh-keyscan` 无条件信任
网络返回值。控制面发布失败必须让 job 明确失败，但不得撤回已经发布的 GitHub Release；修复连接或服务端校验后
可以幂等重跑失败 job。

## 隐私与后续写接口

当前静态服务不接收 telemetry、诊断包、反馈、installation ID、Agent Trace 或任何用户内容。远程诊断由独立的
Cloudflare Telemetry Edge 接收，不进入 `sakura.cialloo.cn/service/v1/` 的请求路径；其 schema、默认设置、保留期和
删除合同见 [远程诊断与匿名统计](remote-diagnostics-telemetry.md)。

普通运行日志与私密 Agent Trace 继续遵循
[人类可读运行日志与 Prompt Trace](WP-4L-02-human-readable-runtime-log-agent-trace.md) 的分离边界；Agent Trace
不得因静态控制面或 Telemetry Edge 存在而自动上传。静态控制面以后若要增加自己的写接口，仍需另行冻结 schema、
体积、配额、保留期和故障降级合同。

## 验证

- 三个端点返回 `200 application/json`、CORS 与公共缓存头；POST 等写方法被拒绝。
- `releases.json` 中所有资产存在于同一稳定 GitHub Release，版本与文件名一致。
- 受限发布密钥可以提交合法元数据；尝试执行 `id` 等任意命令时只进入 forced command 并失败。
- 服务不可达、返回损坏 JSON 或未知 schema 时，桌面产品的本地能力与 GitHub Updater 不受影响。
- CI 的 prerelease 路径不得更新稳定版控制面。

相关决策见 [ADR-0041](../../adr/0041-static-sakura-service-control-plane.md)，维护入口见
[Sakura Service 运维与发布](../../devdocs/SAKURA_SERVICE.md)。
