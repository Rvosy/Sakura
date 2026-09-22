# Sakura 服务部署配置

本目录与 `../site/`、`../dashboard/` 及上级 Python 模块一起维护官网、私人控制台和公开接口。
这些文件是可审阅的配置与入口，添加到 Git 不会改变服务器。

## 线上对应关系

2026-09-16 经 SSH 别名 `macmini` 只读核对：

| 服务 | 公网入口 | 当前服务器位置与管理方式 |
|---|---|---|
| 官网 | `sakura.cialloo.cn` | 宝塔 Nginx 静态站，目录 `/www/wwwroot/Sakura`；源码在 `../site/` |
| 国内清单 | 官网及 `api.sakura.cialloo.cn/service/v1/` | 共用 `/www/wwwroot/Sakura/service/v1/`；由控制台发布 |
| 私人控制台 | `adm.sakura.cialloo.cn/admin/` | systemd `sakura-console`，127.0.0.1:8766，代码 `/opt/sakura-service/current` |
| 遥测接收 | 现有遥测入口及 api 协议路由 | 既有宝塔 Python 项目，用户 www，127.0.0.1:8765，代码 `/opt/sakura-telemetry` |

当次检查中，首页、安装指南与两域名清单返回 200，未认证控制台返回 401，api 的 `/admin/` 返回 404。
两份公开版本 JSON 均为 1.1.0；这是检查时的在线状态，不是后续部署的默认版本。

控制台 current 指向 `/opt/sakura-service/releases/20260913T130911Z-ci-drafts`。其 22 个 Python/依赖文件与
此次迁入代码一致，仅 `http_input.py` 末尾空行不同。遥测目录还保留旧版 `control.py`、`releases.py` 副本，
实际监听入口是 `app:app`，不是控制台。维护源码统一使用上级目录，部署时仍尊重两个现有进程的独立位置；
不从旧遥测副本覆盖控制台，也不增加第二个遥测 systemd 服务。

## 配置归属

- `sakura-site.conf`：从现有宝塔官网配置取回的基线，对应 `vhost/nginx/sakura.cialloo.cn.conf`。
  保留现有证书路径与面板 include；证书文件、站点隐藏配置和日志不入库，不直接用其他域名的 server 配置覆盖官网。
- `sakura-admin.conf`、`sakura-api.conf`：独立子域名配置。
- `sakura-admin-locations.conf`、`sakura-service-location.conf`：放在宝塔 `vhost/nginx/snippets/`，
  不能直接放进会被作为顶层配置读取的 `vhost/nginx/*.conf`。公开清单片段由官网和 api 共用。
- `sakura-console.service`：控制台进程及目录权限。遥测继续由宝塔管理。
- `sakura-release-deploy`、`sakura-ci-import`、`sakura-ci-import.sudoers`：CI 固定草稿导入入口。
- `sakura-console-acme.conf`、证书续期 service/timer：adm/api 证书配置；官网仍使用自己的宝塔证书。

管理员密码文件、SSH 私钥、公钥授权清单、数据库、导出文件和在线版本 JSON 保留在服务器，不能作为部署包源文件。
Nginx 的配置引用可以记录，认证内容不能复制。修改 Nginx 前备份实际配置，执行 `nginx -t` 后再按授权 reload。

## 分开发布

官网用 [site/deploy_site.py](../site/deploy_site.py) 预览及同步构建结果，保护共享根目录中的 `service/` 和隐藏项。
服务端用 `package_release.py` 生成包含后台静态资源和本目录的独立 Python 包。
CI 发布流程仅导入草稿，不代替部署服务或维护者发布公开清单。
具体备份、迁移、验收与回退顺序见 [服务运维文档](../../../docs/devdocs/SAKURA_SERVICE.md)。
