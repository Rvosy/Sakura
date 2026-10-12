# 构建与发布

本页维护发行操作入口。程序与用户数据的归属、更新和兼容规则见[发行与存储规格](../specs/release-storage.md)，发布端约定见[在线服务规格](../specs/services.md)。

## 准备版本

版本只维护在 [VERSION](../../VERSION)，当前用户更新说明维护在 [CHANGELOG](../CHANGELOG.md)。先确认两者对应，再检查正文：

```powershell
runtime\python.exe -m tools.release.release_notes --check
```

发布正文从[模板](../../packaging/release-body.md)生成，包含实际下载链接和本版本更新内容。GitHub 工作流另附自动生成的变更列表。updater notes 只使用版本更新内容，不包含下载表和 PR 清单。

## 本地 Windows 包

在仓库根目录执行：

```powershell
scripts\package.bat
```

输出在 `artifacts/local/`。需要 updater 产物时：

```powershell
powershell -File scripts\package_windows.ps1 -Updater
```

普通本地构建和可正式更新的产物分开处理。签名、endpoint 和打包参数见[Windows 打包脚本](../../scripts/package_windows.ps1)，不把签名私钥写入配置文件或仓库。

macOS arm64、Linux x64 与正式 Windows 构建使用 [Release 工作流](../../.github/workflows/release.yml)中的平台步骤。各平台基础 Python 和插件依赖必须与目标平台一致，不能用开发机的环境目录代替发行环境。

## 构建工具分工

| 入口 | 用途 |
|---|---|
| [prepare_python_runtime.py](../../tools/release/prepare_python_runtime.py) | 准备平台 Python 及其依赖 |
| [stage_distribution.py](../../tools/release/stage_distribution.py) | 整理程序、内置插件、依赖及迁移材料 |
| [tauri_release_config.py](../../tools/release/tauri_release_config.py) | 生成本次打包配置 |
| [updater_manifest.py](../../tools/release/updater_manifest.py) | 从实际资产生成更新清单 |
| [diagnostic_build.py](../../tools/release/diagnostic_build.py) | 生成与构建身份对应的诊断映射 |
| [package_optional_plugin.py](../../tools/release/package_optional_plugin.py) | 独立打包可选插件 |

Tauri 更新签名使用 `SAKURA_UPDATER_PUBLIC_KEY`、`TAURI_SIGNING_PRIVATE_KEY` 及可选密码。平台代码签名按工作流配置 Apple 凭据或 Windows Authenticode 证书；它们与 Tauri 更新签名用途不同。

## 发布流程

1. 主平台构建准备安装、更新与签名资产。
2. 发布步骤读取实际资产，生成下载表、版本说明和更新清单，发布 GitHub Release。
3. Portable 独立构建完成后追加资产，刷新同一 Release 的下载表和清单。
4. 稳定版最终清单由 CI 导入私人控制台草稿，维护者确认后发布国内更新入口。

发布中的每个状态以该步骤的结果为准。Portable 失败不撤回已经发布的主安装包；预发布版不更新稳定清单。macOS 的离线打开说明作为独立文件提供，不在签名后修改安装资产。

发布前使用隔离用户根核对升级的数据保留。具体测试入口见 [Harness](../../harness/README.md)，真实安装和平台设备行为在对应系统验证。

## GitCode 镜像

[镜像工作流](../../.github/workflows/mirror-gitcode-release.yml)复用 GitHub 已发布资产和签名，只替换下载地址。配置变量为 `GITCODE_REPOSITORY`、`GITCODE_USERNAME`，凭据使用 `GITCODE_ACCESS_TOKEN` secret。

```text
gh workflow run mirror-gitcode-release.yml --ref <branch> -f tag=v<version> -f mode=rehearsal
```

`rehearsal` 检查发布配置，`upload-probe` 检查上传能力，`publish` 正式发布。正式发布核对可公开下载的产物；失败保留具体原因和 GitHub 已发布结果。镜像凭据、临时上传地址不进入 Release 正文。
