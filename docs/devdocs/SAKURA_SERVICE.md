---
kind: devdoc
status: current
audience: maintainer
source_of_truth: self
updated: 2026-09-01
---

# Sakura Service 运维与发布

## 架构与所有权

Sakura Service 当前是静态控制面，不是应用后端：

```text
release.yml / publish-portable
  -> 生成 releases.json
  -> 受限 SSH forced command 校验并原子替换
  -> Nginx 从 /www/wwwroot/Sakura/service/v1/ 公开读取
  -> Sakura 客户端按需 GET；安装资产仍从 GitHub Release 下载
```

公共端点：

| 地址 | 所有者 | 当前内容 |
|---|---|---|
| `https://sakura.cialloo.cn/service/v1/releases.json` | 正式 Release CI | 稳定版和 GitHub 下载入口 |
| `https://sakura.cialloo.cn/service/v1/announcements.json` | 维护者 | schema 1 空集合；项合同尚未启用 |
| `https://sakura.cialloo.cn/service/v1/known-issues.json` | 维护者 | schema 1 空集合；项合同尚未启用 |

不要把安装包复制到 VPS，也不要让三个静态端点进入 Python Core、数据库或 Docker 容器。

## GitHub Actions

正式流程位于 `.github/workflows/release.yml`。`publish-service-metadata` 必须依赖 `publish-portable`，并只处理稳定
版本。它先从 GitHub Release 读取真实 `published_at`，再构造固定 schema，最后把 JSON 送到
`sakura-release@sakura.cialloo.cn`。

仓库需要以下 Secret；这里只记录名称，不记录值：

- `SAKURA_SERVICE_DEPLOY_KEY`：专用 ED25519 私钥；不得使用 root、博客或个人登录密钥。
- `SAKURA_SERVICE_KNOWN_HOSTS`：预先验证的 `sakura.cialloo.cn` host key；不得在 workflow 中用裸
  `ssh-keyscan` 替代信任确认。

Secret 更新后不需要把值写入 issue、日志、artifact、文档或仓库文件。CI 发布失败时先看 forced command 的明确拒绝
原因；修复后重跑失败 job，不删除已经完成的 GitHub Release。

## 服务器布局

| 路径 | 用途 |
|---|---|
| `/www/wwwroot/Sakura/service/v1/` | 三个公开 JSON |
| `/usr/local/sbin/sakura-release-deploy` | schema、URL、版本和降级校验；原子发布 |
| `/var/lib/sakura-release/.ssh/authorized_keys` | 带 `restrict` 和 forced command 的 CI 公钥 |
| `/www/server/panel/vhost/nginx/sakura.cialloo.cn.conf` | 站点配置 |
| `/www/server/panel/vhost/nginx/snippets/sakura-service-location.conf` | GET/HEAD、CORS、缓存和方法限制 |
| `/var/backups/sakura-service/<timestamp>/` | 修改站点前的配置与静态文件备份 |

Nginx 片段必须位于 `snippets/` 子目录。宝塔会把 `vhost/nginx/*.conf` 当作顶层配置全局加载，把 `location` 文件直接
放在该目录会导致 `nginx -t` 报错。

## 日常验证

公开面检查：

```bash
curl -fsS -D - https://sakura.cialloo.cn/service/v1/releases.json
curl -fsS https://sakura.cialloo.cn/service/v1/announcements.json
curl -fsS https://sakura.cialloo.cn/service/v1/known-issues.json
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST https://sakura.cialloo.cn/service/v1/releases.json
```

期望三个 GET 为 `200 application/json`，带 CORS 和公共缓存头；POST 为 `403`。服务器修改必须遵循：备份目标文件、
写入候选、执行 `nginx -t`、验证通过后 reload，再检查 Nginx 状态、端口、相关站点和近期错误日志。不要因静态 JSON
变更重启 Docker、`new-api` 或数据库。

版本元数据还应核对：

- `latest`、tag 与三个文件名一致；
- GitHub Release 为非 draft、非 prerelease；
- Setup、Portable ZIP、DMG 和最终 `latest.json` 均已存在；
- `updaterManifestUrl` 仍指向主仓库稳定版 manifest；
- `minimumSupported` 和 `urgent` 的策略变更经过产品规范更新。

## 密钥轮换

轮换时生成新的专用 ED25519 key，把新公钥以相同 `restrict,command="/usr/local/sbin/sakura-release-deploy"`
约束加入 `sakura-release` 的 `authorized_keys`，固定并更新 host key Secret，然后直接把私钥写入 GitHub Secret。
先用合法版本 JSON 验证新 key，再删除旧公钥；临时私钥文件随后立即清理。不得为了方便改成普通 shell key。

## 回退与故障

- Nginx 候选配置未通过 `nginx -t`：不得 reload，恢复最近备份后再次校验。
- `releases.json` 被拒绝：根据错误修复 schema、版本或 URL；不要绕过服务端校验直接覆盖。
- 控制面暂时不可用：保持 GitHub Release 和签名 Updater 运行，客户端按可选依赖降级。
- 部署 key 疑似泄漏：先从 `authorized_keys` 撤销对应公钥，再轮换仓库 Secret；检查版本 JSON 是否有异常等版本覆盖。
- 需要回滚 Nginx 或静态文件：从 `/var/backups/sakura-service/<timestamp>/` 恢复，校验后 reload，并重新检查三个端点。

产品合同见 [Sakura Service 静态控制面合同](../specs/runtime-v2/sakura-service.md)，选择理由见
[ADR-0041](../adr/0041-static-sakura-service-control-plane.md)。
