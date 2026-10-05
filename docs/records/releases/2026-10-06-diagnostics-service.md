---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
updated: 2026-10-06
---

# 遥测诊断增量部署记录

2026-10-06 03:38（北京时间），遥测接收服务和私人控制台完成增量部署。基础改动为 `b7276c3d`，另将线上已有的导出异常日志同步回仓库。产品契约见[远程诊断 Spec](../../specs/runtime-v2/remote-diagnostics-telemetry.md)。桌面客户端尚未发布新安装包。

## 实际部署范围

本次主机使用 Ubuntu、Python 3.12.3、FastAPI 0.141.1 和 Pydantic 2.13.5。两个服务均由 systemd 以 `www-data` 运行：

| 服务 | 代码目录 | 监听地址 |
|---|---|---|
| `sakura-telemetry` | `/opt/apps/sakura-telemetry` | `127.0.0.1:8765` |
| `sakura-console` | `/opt/apps/sakura-console` | `127.0.0.1:8766` |

Python 环境为 `/opt/apps/sakura-runtime/venv`，配置来自 `/etc/sakura/runtime.conf`。这与旧部署指南中记录的宝塔主机布局不同。

两个目录同步更新 `app.py`、`admin.py`、`admin_v2.py`、`models.py`、`v2_models.py`、`v3_models.py`、`exports.py`；`v2_db.py` 在各自线上版本上只应用槽位、提供方分组补丁。保留线上数据库连接关闭、仅按 report ID 处理重复写入、导出包布局以及发布控制代码，未整包覆盖。Nginx 已具备 v3 路由和 128 KiB 限制，无需修改。

## 验证与首次失败

初版增量候选的服务端测试为 15 项通过、3 项失败：线上 v1/v2 校验较旧，导出路由缺少仓库已有的 Host 检查。生产入口原有 Nginx 和控制台外层保护仍在；失败来自直接挂载路由的隔离测试。同步相关契约模块后，连同新增的导出异常日志回归，共 19 项通过。

真实 Python 错误经 Rust HTTP 发送器形成的请求体在隔离服务中重放，验证入库、槽位分组、详情及 ZIP 证据一致。真实数据库通过 SQLite backup API 制作副本，在副本上运行初始化及 `PRAGMA quick_check`，结果通过。依赖安装仅写入私人验收目录，没有改变生产 Python 环境。

部署后两个服务均为 `active/running`，遥测健康检查及服务器内管理查询成功。公网结果：

- `telemetry.cialloo.cn/health`：200。
- 遥测与 API 域名的 v3 非法请求、API 域名的 v1/v2 非法请求：400。
- 遥测与 API 域名的 `/admin/`：404。
- 未认证访问 `adm.sakura.cialloo.cn/admin/`：401。
- API 域名的公开版本清单：200。

没有向生产数据库写入有效验收报告；未执行使用管理员密码的公网登录或桌面发布包验收。

## 备份和回退

代码、配置、两个数据库快照、验收日志和部署清单保存在服务器私人目录 `/var/backups/sakura-diagnostics/20261006-b7276c3d/`，父目录权限为 0700，数据库快照为 0600。`deployment.json` 记录文件范围和部署时间；`backup-sakura-telemetry/`、`backup-sakura-console/` 保存替换前代码。

回退时停止这两个服务，按清单恢复对应代码文件并重启，复验健康、查询和公网隔离。保留当前数据库，不能用备份覆盖部署后新收集的数据。后续整包部署前应重新核对上述保留的线上差异。
