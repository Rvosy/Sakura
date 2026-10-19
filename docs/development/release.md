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
| [stage_distribution.py](../../tools/release/stage_distribution.py) | 整理程序、内置插件及对应依赖 |
| [tauri_release_config.py](../../tools/release/tauri_release_config.py) | 生成本次打包配置 |
| [updater_manifest.py](../../tools/release/updater_manifest.py) | 从实际资产生成更新清单 |
| [diagnostic_build.py](../../tools/release/diagnostic_build.py) | 生成与构建身份对应的诊断映射 |
| [package_optional_plugin.py](../../tools/release/package_optional_plugin.py) | 独立打包可选插件 |

Tauri 更新签名使用 `SAKURA_UPDATER_PUBLIC_KEY`、`TAURI_SIGNING_PRIVATE_KEY` 及可选密码。macOS 代码签名使用 Apple 凭据，Windows 发布使用 SignPath；它们与 Tauri 更新签名用途不同。Windows CI 不再使用 `WINDOWS_CERTIFICATE_BASE64`、`WINDOWS_CERTIFICATE_PASSWORD`。

## SignPath 配置

组织 ID 为 `4071764e-c1ca-4688-9f22-3bf5d059e005`，项目 slug 为 `Sakura`。项目仓库关联 `https://github.com/Rvosy/Sakura.git`，可信构建系统关联 GitHub.com。所有签名前置任务使用 GitHub 托管 runner。GitHub App 的安装与权限按[官方接入文档](https://docs.signpath.io/trusted-build-systems/github)配置。

在 SignPath 项目中添加以下产物配置，slug 与表格一致，内容使用对应 XML。上传到 GitHub 的 artifact 默认是 ZIP，因此 XML 的根文件类型也是 ZIP。规则只匹配指定的自有文件，并要求产品名为 Sakura、产品版本等于本次 `VERSION`；不匹配第三方运行库。

| Artifact configuration slug | 配置文件 | 签名对象 |
|---|---|---|
| `windows-app` | [windows-app.xml](../../packaging/signpath/windows-app.xml) | `sakura.exe` |
| `windows-installer` | [windows-installer.xml](../../packaging/signpath/windows-installer.xml) | `Sakura-setup.exe` |

在 GitHub 仓库的 Settings → Secrets and variables → Actions 中设置：

| 类型 | 名称 | 内容 |
|---|---|---|
| Secret | `SIGNPATH_API_TOKEN` | SignPath 中具有提交权限的 CI 用户 API Token |
| Variable | `SIGNPATH_TEST_CERTIFICATE_THUMBPRINT` | `test-signing` 所绑定测试证书的 Windows Thumbprint，连续十六进制字符，不带空格 |

测试证书指纹可从 SignPath 下载该公开证书后，用 `Get-PfxCertificate -FilePath <证书.cer>` 查看；不要下载或上传私钥。Thumbprint 仅用于核对证书身份，不额外计算文件摘要。

`test-signing` 和 `release-signing` 使用各自的证书。正式策略保留来源验证、可信构建系统验证和人工审批，允许的分支应覆盖实际运行工作流的分支。准备正式签名时将范围收窄到实际发布分支，不关闭验证来绕过失败。

## 测试签名与正式发布

[Release 工作流](../../.github/workflows/release.yml)手动运行时，`mode` 默认为 `test-signing`：

```text
gh workflow run release.yml --ref <branch> -f mode=test-signing
```

测试模式只构建 Windows，依次签名程序和安装包，再为最终安装包生成并验证 Tauri `.sig`。更新密钥在 runner 内临时生成，更新地址为不可用的测试地址，不使用正式更新服务。结果保存为 `TEST-ONLY-…` artifact，包含测试说明，不创建 Release、不打 Portable，也不导入发布控制台。测试签名核对配置的证书指纹，允许自签名证书的 `NotTrusted` 状态，但拒绝内容被修改、未签名等错误；它不代表公共证书链受信任。

首次接入须在 GitHub 托管 runner 上完成真实测试签名。工作流摘要提供两次 SignPath 请求链接；将它们与成功的 Actions 构建链接一并回复给 SignPath，请其审核并完成正式证书配置。正式证书状态与适用范围见[签名政策](../guide/code-signing.md)。

正式证书就绪后，选择 `mode=publish` 执行现有三平台发布：

```text
gh workflow run release.yml --ref <release-branch> -f mode=publish
```

Windows 正式构建固定使用 `release-signing`。程序与安装包分别提交请求，每次最多等待一小时供维护者审批。取回产物后检查 Windows 签名、时间戳和 SignPath Foundation 发布者；任一签名失败都会停止主发布，不回退到未签名安装包。Portable 复用已签名主程序。最终安装包的 Tauri 更新签名另行生成，不能复用代码签名前的 `.sig`。

## 发布流程

1. 主平台构建准备安装、更新与签名资产。
2. 发布步骤读取实际资产，生成下载表、版本说明和更新清单，发布 GitHub Release。
3. Portable 独立构建完成后追加资产，刷新同一 Release 的下载表和清单。
4. 稳定版最终清单由 CI 导入私人控制台草稿，维护者确认后发布国内更新入口。

发布中的每个状态以该步骤的结果为准。Portable 失败不撤回已经发布的主安装包；预发布版不更新稳定清单。macOS 的离线打开说明作为独立文件提供，不在签名后修改安装资产。

发布前使用隔离用户根核对升级的数据保留。具体测试入口见 [Harness](../../harness/README.md)，真实安装和平台设备行为在对应系统验证。

可选插件独立发布。使用 `package_optional_plugin.py` 从对应源码目录生成 ZIP，包内版本以 `plugin.yaml` 为准。插件更新测试使用同一打包器生成安装包，覆盖安装、切换失败、恢复和重试。主程序发布不会更新用户已安装的可选插件；插件包发布后还需更新市场目录，并核对目录版本对应实际可下载的包。

## GitCode 镜像

[镜像工作流](../../.github/workflows/mirror-gitcode-release.yml)复用 GitHub 已发布资产和签名，只替换下载地址。配置变量为 `GITCODE_REPOSITORY`、`GITCODE_USERNAME`，凭据使用 `GITCODE_ACCESS_TOKEN` secret。

```text
gh workflow run mirror-gitcode-release.yml --ref <branch> -f tag=v<version> -f mode=rehearsal
```

`rehearsal` 检查发布配置，`upload-probe` 检查上传能力，`publish` 正式发布。正式发布核对可公开下载的产物；失败保留具体原因和 GitHub 已发布结果。镜像凭据、临时上传地址不进入 Release 正文。
