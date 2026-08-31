---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-01
---

# ADR-0041：使用静态 Sakura Service 控制面，并让 GitHub 持有发行数据面

## 背景

Sakura 需要在不发布新客户端的情况下表达稳定版版本信息，后续还需要承载公告和已知问题。现有阿里云 VPS 适合
处理少量控制元数据，但不适合为大型安装包提供下载流量，也不应成为 Assistant、模型或用户数据的远程核心。

客户端是公开源码，任何内置 API Key、固定请求签名秘密或混淆算法都能被提取。现有 GitHub Release 已经生成签名
Updater manifest，并托管 Windows/macOS 正式资产；复制资产或建立第二套更新信任根会增加失败模式。

## 决策

- 在 `sakura.cialloo.cn/service/v1/` 提供 Nginx 直接服务的静态 JSON 控制面。首版只有版本、空公告 envelope 和
  空已知问题 envelope，不引入 Web framework、数据库、账户或后台管理系统。
- GitHub Release 继续持有 Setup、Portable、DMG、updater artifact 和签名 `latest.json`。Sakura Service 的版本
  JSON 只提供发布控制信息和 GitHub URL，不参与签名验证，不代理二进制下载。
- 控制面是可选依赖。它不可用或返回无效数据时，桌面客户端保持本地能力和既有 GitHub Updater 路径。
- 正式稳定版的全部资产完成后，GitHub Actions 生成版本 JSON，并通过专用 SSH key 推送。服务器用 OpenSSH
  `restrict` 与 forced command 把该 key 限制为“校验并原子替换 `releases.json`”；CI 固定 host key。
- 不复用博客部署 key、root key 或可执行通用 shell 的凭据。客户端不携带服务端秘密，也不以
  `installationId`、版本头或可伪造字段作为身份认证。
- 当前不增加 telemetry、诊断上传或 Agent Trace 上传。任何写接口必须以新的规范和隐私/配额设计获得批准。

## 放弃的方案

### 只使用 GitHub Release

它已足够承担签名更新，但无法自然表达与发行解耦的公告和已知问题。保留 GitHub 数据面，同时增加极薄控制面，比让
客户端直接拼接多个 GitHub API 更稳定。

### VPS 托管安装包或代理 GitHub 下载

这会把约百 MB 的单次下载变成 VPS 出站带宽和可用性责任，并制造第二份资产一致性问题，没有产品收益。

### 首版直接部署 FastAPI、SQLite 和管理后台

三个只读 JSON 不需要常驻应用或数据库。动态后端增加内存、升级、备份、鉴权和攻击面；等真实写入需求出现后再按
独立合同引入。

### 在开源客户端中隐藏固定 Secret

秘密最终会被源码阅读或逆向取得，不能证明“官方客户端”。服务端安全必须来自小请求、严格 schema、限额和独立
权限边界，而不是客户端混淆。

### 复用博客 Git 推送账号和部署 key

博客 key 可以推送完整仓库并触发整站 checkout，权限与 Sakura 单文件发布不相称。专用 forced command key 的泄露
半径只覆盖已校验的版本投影。

## 后果

控制面可以由 Nginx 缓存，资源消耗和维护面很小；版本资产仍享有 GitHub Release 和 Tauri 签名链。代价是维护者需要
管理一个域名、Nginx 配置和专用部署 key，并接受控制面元数据可能短暂落后于 GitHub Release。

服务端拒绝降级和错误 URL，CI 只能在最终稳定资产完成后发布。控制面故障不会阻断产品，因此它不能用于远程 kill
switch、强制登录或绕过本地用户决定。将来若新增公告项、已知问题匹配、telemetry 或诊断上传，必须扩展对应 Spec；
写接口还需要新的安全与隐私决策。

规范见 [Sakura Service 静态控制面合同](../specs/runtime-v2/sakura-service.md)。
