---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-13
---

# Sakura Service 与私人控制台合同

## 目的与边界

Sakura Service 是桌面客户端的可选远程辅助服务。公开清单提供只读、可缓存的版本、公告和已知问题元数据；
它不运行 Assistant、模型、Memory、插件或用户数据处理，也不是 Sakura 启动、聊天和本地设置可用的前置条件。

服务根固定为：

```text
https://api.sakura.cialloo.cn/service/v1/
```

GitHub Release 仍托管安装资产并保留 Updater 清单。Sakura Service 同时提供国内 `latest.json`，其中下载 URL
仍指向固定版本 GitHub Release，安装包签名字段保持不变。服务器不托管 Setup、Portable ZIP、DMG 或 updater
artifact，不得降低 Tauri Updater 的安装包签名校验和用户确认门禁。清单 JSON 本身不是安装包签名。

## 访问合同

- 当前端点只接受 HTTPS `GET` 和 `HEAD`；写方法必须在 Nginx 层拒绝。
- JSON 使用 UTF-8，响应 `Content-Type` 为 `application/json`，允许公开跨域读取。
- 正常响应允许公共缓存 5 分钟。客户端不得依赖精确缓存时长或 ETag。
- 网络失败、超时、非 2xx、无效 JSON 或不支持的 schema 必须被视为“辅助信息不可用”，不能阻止 Core、聊天、
  设置等本地能力；更新清单不可用时报告检查失败，保留手动检查入口，不显示“已是最新版”。
- 客户端必须把所有返回字段视为不可信输入。URL 只允许导航到经过产品白名单批准的 HTTPS 目标；文本只能作为
  纯文本展示，不解释 HTML、Markdown 命令、脚本或工具调用。

## 私人控制台与域名边界

- `adm.sakura.cialloo.cn/admin/` 为唯一维护者使用的控制台；旧 `admin.cialloo.cn/admin/` 保持同一后台和认证。
- `api.sakura.cialloo.cn` 提供公开清单及既有 `/v1`、`/v2`、`/v3/errors` 遥测协议。只开放已有路由和方法，
  不提供 `/admin/`、数据库、分析包静态目录或 Python 健康检查。
- `sakura.cialloo.cn/service/v1/` 与新清单入口读取相同文件，保留 GET/HEAD，不通过重定向依赖新客户端行为。
  `telemetry.cialloo.cn` 及其源站继续服务已安装客户端；POST 转发保持原路径，不重定向。当前客户端遥测默认地址未切换。
- `sakura.cialloo.cn` 继续承载产品站；`hub.sakura.cialloo.cn`、`dl.sakura.cialloo.cn` 仅预留命名，尚未启用。
- 后台和遥测接收端运行在不同的 loopback 监听进程。接收端不能挂载管理路由，也没有清单发布权限。
  控制台只读访问故障数据库，独立存储版本草稿、操作记录和临时分析包。
- Nginx 对后台执行现有 Basic Auth。后台拒绝其他 Host；所有写请求要求与当前 HTTPS Host 一致的 Origin 和
  `application/json`。后台内容禁止缓存，密码不转发给 Python。第一版不新增注册、第三方登录或多角色权限。

## 版本管理合同

私有前缀为 `/admin/api/control`：`GET /status` 返回实际文件状态、最近 30 个草稿和最近 50 个控制台操作；
`GET /drafts/{id}` 返回预览。状态仅证明本机文件与字段可读取、可校验，不等同于公网健康探测。

`POST /drafts` 接受 `{version}`，从固定 `Rvosy/Sakura` 的稳定 GitHub Release 导入完整发行资料。
版本页和五个必要资产齐全、清单符合签名字段与平台合同后才保存草稿；导入不会改动公开文件。
最多保留 30 个待处理草稿，已发布和已丢弃记录不计入该上限。

`POST /drafts/{id}/save` 接受 `notes`、`urgent`、`minimumSupported` 与 `expectedPayload`。
只允许编辑上述三项，保存前直接比较草稿实际字段，阻止过期页面覆盖。URL、平台和签名不可手工改写。
`POST /drafts/{id}/discard` 标记未发布草稿为已丢弃并保留记录。

`POST /drafts/{id}/publish` 接受 `expectedRevision` 与 `expectedPayload`。
前者是上次预览的线上清单文件修订值，后者是已保存草稿的实际内容；任一变化都拒绝发布。
控制台写入使用独立发布锁；CI 只通过 SQLite 事务导入草稿，复用清单校验器。已发布草稿重复请求幂等返回，不重新覆盖线上版本；
同版本修复须重新导入。写入失败记为 failed，重启发现 publishing 记为 interrupted，均可在核对后重试。
两份清单逐个原子替换，不宣称跨文件事务；拒绝自动降级。

操作记录包含后台导入、发布、丢弃和 GitHub Actions 导入，显示来源；顶部状态始终读取实际文件。
草稿的编辑和发布不会自动触发 GitHub Release，也不会把插件或第三方代码运行在服务器上。

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
  "updaterManifestUrl": "https://api.sakura.cialloo.cn/service/v1/latest.json"
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
| `updaterManifestUrl` | 新发布指向国内 `latest.json`；旧发行流程的 GitHub 清单地址仍可读。该字段不在运行时覆盖客户端配置的 Updater endpoint。 |

服务端发布命令必须拒绝缺失/额外字段、无效类型、非稳定版本、非 GitHub 资产 URL、版本与 URL 不一致和自动降级。
相同版本重新发布用于幂等修复，允许覆盖。

## `latest.json`

`GET /service/v1/latest.json` 使用现有 Tauri v2 静态更新清单，字段为 `version`、`notes`、`pub_date`、
`platforms`、`portable`。`platforms` 必须包含 `windows-x86_64` 和 `darwin-aarch64`，各自具有非空
`signature` 和固定版本资产 `url`；Portable 保持 `portable.windows-x86_64.url` 合同，不新增签名或摘要字段。
下载 URL 仅接受该版本 `Rvosy/Sakura` 的既有正式文件名。客户端仍由 Tauri 比较版本和校验安装包签名。

CI 在指定 tag 的最终 `latest.json` 发布后推送草稿，由维护者登录控制台确认发布。服务器不在用户请求时向 GitHub 转发或重定向，也不
定时拉取 GitHub。GitHub 不可达时，国内检查可以成功，但下载仍可能失败；这一步不承诺下载加速。

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
发布，再生成并推送 `{schema: 1, release: releases.json, updater: latest.json}`。
受限 SSH 入口只校验并保存草稿，不能直接修改公开清单。提交必须包含完整 updater；旧 CI 的单份版本资料被明确拒绝，
不会继续自动发布。GitHub Release 仍正常上线，因此使用 GitHub 更新入口的旧客户端仍可能先收到更新。

同一稳定版本的首次 CI 导入建立版本到草稿的持久映射，并保存原始导入内容。重跑时直接比较原始字段：相同提交返回
原草稿，包括已编辑、已发布或已丢弃状态；内容不同则失败，要求维护者从后台重新导入核对。不得覆盖编辑或重复创建草稿。
导入映射、草稿与操作记录在一个 SQLite 事务提交；配额检查在写事务内完成。CI 导入不重置正在发布的状态。

`import-console-draft` 只依赖完成正式资产上传的 `publish-portable`，跳过 prerelease，将 GitHub Release 正文写入草稿
更新说明，保留 URL 和签名。Action Summary 给出版本、实际草稿状态和控制台入口。维护者核对后才能改变国内公开清单。

服务器保持原仓库 SSH key 与固定 host key。forced command 拒绝任意远程命令，只通过精确 sudo 规则执行无参数的
`/usr/local/sbin/sakura-ci-import`，以控制台身份写草稿。CI 身份不能读控制台库或写公开目录，不获得交互 shell、端口转发
或一般 sudo 权限。导入失败使 job 失败，不撤回 GitHub Release；修复后可重跑导入 job。

## 隐私与后续写接口

当前静态服务不接收 telemetry、诊断包、反馈、installation ID、Agent Trace 或任何用户内容。远程诊断由独立的
Telemetry Edge 接收：公网请求经过多吉云 CDN 后进入独立的 VPS Nginx vhost、FastAPI 进程和 SQLite 数据库，不进入
`sakura.cialloo.cn/service/v1/` 的静态请求路径。其 schema、默认设置、保留期和删除合同见
[远程诊断与匿名统计](remote-diagnostics-telemetry.md)。

普通运行日志与私密 Agent Trace 继续遵循
[人类可读运行日志与 Prompt Trace](WP-4L-02-human-readable-runtime-log-agent-trace.md) 的分离边界；Agent Trace
不得因静态控制面或 Telemetry Edge 存在而自动上传。静态控制面以后若要增加自己的写接口，仍需另行冻结 schema、
体积、配额、保留期和故障降级合同。

## 验证

- 四个端点返回 `200 application/json`、CORS 与公共缓存头；POST 等写方法被拒绝。
- `releases.json` 中所有资产存在于同一稳定 GitHub Release，版本与文件名一致。
- 受限导入密钥可以提交合法完整草稿；尝试执行 `id` 等任意命令时只进入 forced command 并失败。
- 国内清单的 GitHub URL、签名与 Portable 字段保持；无效、跨版本或降级提交不能覆盖已发布文档。
- 国内清单故障不能影响本地功能；隔离测试中阻断 GitHub 后仍能从国内清单检测更新，但不能冒充下载成功。
- CI 的 prerelease 路径不得更新稳定版控制面。

相关决策见 [ADR-0049](../../adr/0049-domestic-updater-manifest.md)，维护入口见
[Sakura Service 运维与发布](../../devdocs/SAKURA_SERVICE.md)。
