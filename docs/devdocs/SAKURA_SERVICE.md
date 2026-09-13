---
kind: devdoc
status: current
audience: maintainer
source_of_truth: self
updated: 2026-09-13
---

# Sakura Service 运维与发布

## 架构与所有权

Sakura 在线服务由公开清单、遥测接收和私人控制台组成。公开清单保持静态读取：

```text
release.yml / publish-portable
  -> 生成 releases.json，读取指定 tag 的最终 latest.json
  -> 受限 SSH forced command 校验完整提交，保存私人草稿
  -> 维护者登录控制台核对并发布，依次原子替换两个文件
  -> Nginx 从 /www/wwwroot/Sakura/service/v1/ 公开读取
  -> Sakura 客户端按需 GET；安装资产仍从 GitHub Release 下载
```

公共端点：

| 地址 | 所有者 | 当前内容 |
|---|---|---|
| `https://api.sakura.cialloo.cn/service/v1/releases.json` | 私人控制台 | 稳定版和 GitHub 下载入口 |
| `https://api.sakura.cialloo.cn/service/v1/latest.json` | 私人控制台 | 国内 Tauri 清单，下载仍指向 GitHub |
| `https://api.sakura.cialloo.cn/service/v1/announcements.json` | 维护者 | schema 1 空集合；项合同尚未启用 |
| `https://api.sakura.cialloo.cn/service/v1/known-issues.json` | 维护者 | schema 1 空集合；项合同尚未启用 |

不要把安装包复制到 VPS，也不要让静态端点进入 Python Core、数据库或 Docker 容器。国内清单不重定向或实时
代理 GitHub；GitHub 不可达时检查仍可读取已经发布的清单，下载仍可能失败。

## 私人控制台

入口为 `https://adm.sakura.cialloo.cn/admin/`，沿用旧后台账号；新域名首次访问需要重新输入原账号。
旧 `https://admin.cialloo.cn/admin/` 已指向同一个控制台，书签和诊断链接保持可用。
运行总览汇总线上版本与诊断数据；版本发布支持导入固定 GitHub 稳定版本、保存说明、预览文件、确认发布、丢弃草稿。
导入完成不等于发布。已发布记录不可编辑，同版本修复须重新导入；不支持自动降级。

旧 `sakura.cialloo.cn/service/v1/` 与 api 域名读取同一目录；旧遥测 CDN 与带源站保护的入口继续使用原配置。
api 同时接受既有遥测路由，方法和 body 大小受限，每 IP 10 请求/秒、允许 20 个突发请求，超出返回 429。
客户端当前遥测默认地址仍为 `telemetry.cialloo.cn`；新的打包配置已采用 api 更新清单地址，尚未发行新客户端。

| 服务 | 身份与端口 | 数据权限 |
|---|---|---|
| 遥测接收 `app:app` | www，127.0.0.1:8765，原 BaoTa 启动脚本 | 写故障库，无清单发布权限 |
| 控制台 `console_app:app` | sakura-console，127.0.0.1:8766，systemd | 只读故障库，写自己的 SQLite、导出目录与清单 |
| CI forced command | sakura-release，原受限 SSH | 只调用固定草稿导入程序，无公开目录写权限 |

控制台代码位于 `/opt/sakura-service/releases/<timestamp>/`，`current` 指向当前版本；Python 环境在
`/opt/sakura-console-venv`。`/var/lib/sakura-console/console.db` 保存草稿和后台操作，exports 子目录存放短期分析包。
控制台使用 `/var/lib/sakura-console/publish.lock`。公开目录由 sakura-console 持有；CI 与 www 均无写权限。
故障目录保持 www 所有，sakura-diagnostics 组只读，目录 setgid 保证重建的 WAL/SHM 继承该组。
控制台 systemd 再以 ReadOnlyPaths 限制故障目录。不要把数据库复制到公网目录，也不要裸复制正在使用的 WAL 主文件。

部署配置模板在 `services/sakura/deploy/`。adm 站点使用独立 Nginx server，旧 admin 的目录认证片段改为同一 8766 代理。
两者继续引用原密码文件，Python 不接收 Authorization。公开 api 只代理列出的遥测路径，永不代理整个 Python 根路径。
两个 Python 端口都只绑定 loopback，不开放安全组端口。

证书覆盖 adm 与 api，位置为 `/etc/letsencrypt/live/sakura-console/`。`sakura-cert-renew.timer` 每天检查两次，
由 `/opt/sakura-acme-venv/bin/certbot` 续期并在 Nginx 配置通过后 reload。HTTP challenge 根为 `/var/lib/sakura-acme`；
保留 80 端口的 challenge location，不让全站跳转或目录认证拦截挑战。

```sh
systemctl status sakura-console
systemctl list-timers sakura-cert-renew.timer
journalctl -u sakura-console --since today
/opt/sakura-acme-venv/bin/certbot renew --cert-name sakura-console --dry-run
```

## 部署验证与回退

本次迁移前的代码、Nginx 配置、发布程序、公开 JSON、权限清单和 SQLite backup API 快照保存在
`/var/backups/sakura-console/20260913T124921Z/`；当前备份位置亦写入 `/opt/sakura-service/last-backup`。
控制台上线后的草稿数据库应通过 SQLite backup API 单独备份；故障接收持续运行时也使用该 API。

后续代码部署先在隔离 Linux 目录跑 `services/sakura/tests`，前端构建后跑浏览器测试，再用
`package_release.py --output <new-archive>` 打包。更新 current 后重启控制台；接收端保留原 BaoTa 启动环境。
必要的发布资源映射放在部署版本的 builds/ 中，升级时一并保留。

回退应用时恢复旧 Nginx 认证片段和接收端代码，按原 BaoTa 入口重启接收端，验证 nginx -t 后 reload。
若需恢复旧发布校验器，还要核对其允许的域名与客户端合同；新 api 已被客户端使用后不能直接移除。
故障库和控制台库保留现状，不以迁移前快照覆盖新增数据。清单修复只能同版本或更高版本，不能回填旧版造成降级。

验证应包括：未登录后台 401、公开管理路由 404、新旧清单 GET/HEAD、写方法拒绝、接收端无后台路由、
原故障库只读查询可用、签名和固定版本下载 URL 保持一致。使用隔离数据验证写入和导出，勿向生产发送故障验收样本。

## GitHub Actions

正式流程位于 `.github/workflows/release.yml`。`import-console-draft` 必须依赖 `publish-portable`，并只处理稳定
版本。它先从指定 tag 读取真实 `published_at` 和最终 `latest.json`，构造版本资料，再由
`services/sakura/releases.py build` 生成 `{schema: 1, release, updater}`，最后把提交送到
`sakura-release@sakura.cialloo.cn`。程序把版本资料里的 `updaterManifestUrl` 设为国内地址，不修改下载 URL 和签名。SSH 导入只创建草稿；同版本重跑返回原记录，不覆盖后台编辑。Action Summary 提供后台入口。

仓库需要以下 Secret；这里只记录名称，不记录值：

- `SAKURA_SERVICE_DEPLOY_KEY`：专用 ED25519 私钥；不得使用 root、博客或个人登录密钥。
- `SAKURA_SERVICE_KNOWN_HOSTS`：预先验证的 `sakura.cialloo.cn` host key；不得在 workflow 中用裸
  `ssh-keyscan` 替代信任确认。

Secret 更新后不需要把值写入 issue、日志、artifact、文档或仓库文件。CI 导入失败时先看 forced command 的明确拒绝
原因；修复后重跑失败 job，不删除已经完成的 GitHub Release。

## 服务器布局

| 路径 | 用途 |
|---|---|
| `/www/wwwroot/Sakura/service/v1/` | 四个公开 JSON |
| `/usr/local/sbin/sakura-release-deploy` | forced command 入口，只经 sudo 调用固定导入程序 |
| `/usr/local/sbin/sakura-ci-import` | 固定无参数程序，以控制台身份调用当前版本 ci_import.py |
| `/etc/sudoers.d/sakura-ci-import` | 只允许 CI 身份执行上述精确命令 |
| `/var/lib/sakura-console/publish.lock` | 控制台发布锁，覆盖版本检查及两份文件的写入 |
| `/var/lib/sakura-release/.ssh/authorized_keys` | 带 `restrict` 和 forced command 的 CI 公钥 |
| `/www/server/panel/vhost/nginx/sakura.cialloo.cn.conf` | 站点配置 |
| `/www/server/panel/vhost/nginx/snippets/sakura-service-location.conf` | GET/HEAD、CORS、缓存和方法限制 |
| `/var/backups/sakura-service/<timestamp>/` | 修改站点前的配置与静态文件备份 |

Nginx 片段必须位于 `snippets/` 子目录。宝塔会把 `vhost/nginx/*.conf` 当作顶层配置全局加载，把 `location` 文件直接
放在该目录会导致 `nginx -t` 报错。

## 国内清单首次接入

仓库改动不等于服务器已部署。先核对线上 forced command、Python 版本及 Nginx 片段，再部署候选；服务端 Python
使用独立的 Python 3.12 环境。操作顺序如下：

1. 备份控制台代码、配置、公开清单，并用 SQLite backup API 备份控制台库。部署新代码并重启控制台，创建 ci_imports 表。
2. 安装 deploy/ 中的两个 root 所有的固定入口和 sudoers 模板，运行 `visudo -cf` 验证。
   `authorized_keys` 保持 `restrict,command="/usr/local/sbin/sakura-release-deploy"`。
   CI 只能无参数执行固定导入程序；控制台库保持 600，公开目录改由 sakura-console 持有，CI 无写权限。
3. 部署更新后的控制台 unit，发布锁使用其私人目录。公开 Nginx GET/HEAD、CORS、缓存与旧客户端入口保持现状。
4. 用当前真实版本的完整资料验证受限身份导入和幂等返回，直接比较导入前后两份清单内容，确认没有公开写入。
   无效字段、缺失签名、旧单份元数据、任意远程命令和额外 sudo 参数都应失败。
5. 将工作流改动提交到实际运行 Release 的分支后，在 GitHub 运行 Action。正式资产全部上传后，最后一个 job 导入草稿；
   登录后台点击发布才切换国内清单。本地修改工作流不等于 GitHub 已启用，也不代表真实 Action 已验证。

导入通道不再支持旧 CI 自动发布。回退时保留“CI 只导入”的边界，不恢复自动写公开清单。
公开文件仍逐个原子替换；第二次写入失败后，由维护者核对并同版本修复。

完全无法连接 GitHub 的旧客户端无法自行获得新 endpoint，需要通过可访问下载入口手动安装新版。首次部署验收
应在隔离客户端中阻断 GitHub，确认能从国内清单发现更新；该测试不能宣称 GitHub 下载已加速或可用。

## 日常验证

公开面检查：

```bash
curl -fsS -D - https://api.sakura.cialloo.cn/service/v1/releases.json
curl -fsS -D - https://api.sakura.cialloo.cn/service/v1/latest.json
curl -fsS https://api.sakura.cialloo.cn/service/v1/announcements.json
curl -fsS https://api.sakura.cialloo.cn/service/v1/known-issues.json
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST https://api.sakura.cialloo.cn/service/v1/releases.json
```

期望四个 GET 为 `200 application/json`，带 CORS 和公共缓存头；POST 为 `403`。服务器修改必须遵循：备份目标文件、
写入候选、执行 `nginx -t`、验证通过后 reload，再检查 Nginx 状态、端口、相关站点和近期错误日志。不要因静态 JSON
变更重启 Docker、`new-api` 或数据库。

版本元数据还应核对：

- `latest`、tag 与三个文件名一致；
- GitHub Release 为非 draft、非 prerelease；
- Setup、Portable ZIP、DMG 和最终 `latest.json` 均已存在；
- `updaterManifestUrl` 指向国内 manifest；国内清单的下载 URL 仍为同版本 GitHub 资产，签名与原清单一致；
- `minimumSupported` 和 `urgent` 的策略变更经过产品规范更新。

## 密钥轮换

轮换时生成新的专用 ED25519 key，把新公钥以相同 `restrict,command="/usr/local/sbin/sakura-release-deploy"`
约束加入 `sakura-release` 的 `authorized_keys`，固定并更新 host key Secret，然后直接把私钥写入 GitHub Secret。
先用合法版本 JSON 验证新 key，再删除旧公钥；临时私钥文件随后立即清理。不得为了方便改成普通 shell key。

## 回退与故障

- Nginx 候选配置未通过 `nginx -t`：不得 reload，恢复最近备份后再次校验。
- `releases.json` 被拒绝：根据错误修复 schema、版本或 URL；不要绕过服务端校验直接覆盖。
- 国内清单暂时不可用：客户端报告检查失败，本地功能继续运行；修复静态服务后可手动检查，不将网络故障当成无更新。
- 部署 key 疑似泄漏：先从 `authorized_keys` 撤销对应公钥，再轮换仓库 Secret；检查版本 JSON 是否有异常等版本覆盖。
- 需要回滚 Nginx 或静态文件：从 `/var/backups/sakura-service/<timestamp>/` 恢复，校验后 reload，并重新检查四个端点。
  已有新客户端发布后，不要删除国内 `latest.json`；回退导入程序前先暂停导入 job，不恢复 CI 自动发布。

产品合同见 [Sakura Service 静态控制面合同](../specs/runtime-v2/sakura-service.md)，选择理由见
[ADR-0049](../adr/0049-domestic-updater-manifest.md)。
