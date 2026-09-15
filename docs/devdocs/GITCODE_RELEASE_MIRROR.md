---
kind: devdoc
status: current
audience: maintainer
source_of_truth: self
updated: 2026-09-16
---

# GitCode Release 镜像

GitHub Release 是 Sakura 的权威发行源；GitCode 只保存同一批已签名产物的下载镜像。镜像过程不重新构建、不重新签名，也不经过 Sakura Service/VPS。

## 仓库配置

GitHub 仓库配置：

- Repository variable `GITCODE_REPOSITORY`：GitCode 的 `owner/repo`，例如 `Rvosy/Sakura`。未配置时镜像 workflow 自动跳过。
- Repository secret `GITCODE_ACCESS_TOKEN`：对目标 GitCode 仓库具有代码推送和 Release 写权限的 Personal Access Token。
- Repository variable `GITCODE_USERNAME`：令牌所属账号；省略时使用 `GITCODE_REPOSITORY` 的 owner，组织仓库需显式填写个人账号。

工作流主动推送本次发布提交到对应 tag，不使用 force，不覆盖 GitCode 的其他分支或标签。Pull Mirror 可继续负责日常分支同步，发行镜像不再依赖它先完成。

不要把 GitCode token 写进仓库、Release、日志或客户端。

## 发布行为

`.github/workflows/mirror-gitcode-release.yml` 在 `Release Runtime v2` 完成后运行，也支持手动输入 tag 回填历史版本。
自动触发时检查本次 Release run 对应 attempt 的 `publish-portable` job：它成功才表示最终资产已上传。
后台草稿导入失败仍会让原 Release workflow 报错，但不再阻止镜像；资产发布失败、取消或跳过时仍不执行镜像。

流程固定为：

1. 从已经发布完成的 GitHub Release 下载最终 assets；GitHub 仍是 source of truth。
2. 推送 GitHub tag 对应的提交及其历史到 GitCode 目标 tag，再通过 GitCode API 核对提交。推送被拒绝或已有 tag 指向其他提交时停止，不改用旧提交创建 Release。新 Release 先保持 `pre` 状态。
3. 将 GitHub Release 中的原始文件逐字节上传到 GitCode。失败后重跑时，已经可见的同名附件直接保留，继续补齐缺失附件，不重新构建文件。
4. 读取 GitHub 最终 `latest.json`，保留 `version`、`notes`、`pub_date` 和 Tauri `signature`，把安装包及 Portable 的 artifact URL 改为 GitCode Release 附件下载 API。Portable 条目仅包含 URL。
5. GitCode 版 `latest.json` 最后上传。所有附件上传和读回比对完成前，Release 保持 `pre`。
6. 检查附件集合，通过无凭据的公开地址读回全部文件并逐块比较实际内容；正式镜像验证通过后才提升为 latest，再检查 latest tag。镜像失败不会删除或撤回已经发布的 GitHub Release。

GitCode 版 artifact URL 使用其公开 Release 附件下载接口：

```text
https://api.gitcode.com/api/v5/repos/<owner>/<repo>/releases/<tag>/attach_files/<file>/download
```

因此两个源的 `latest.json` 保留相同版本、签名和发布说明，下载 URL 不同。

## 失败排查

API 返回错误时记录限长、脱敏后的错误码、说明和 trace ID，避免只剩 HTTP 状态码。上传日志记录目标主机、已发送字节数和耗时，不打印令牌或签名 URL。

2026-09-16 排查确认以下故障，应分别处理：

- [Release 34997366224](https://github.com/Rvosy/Sakura/actions/runs/34997366224)，提交 `cb804c58`、attempt 1：GitHub 资产发布成功，`publish-service-metadata` 返回 `SERVICE_FIELDS_INVALID`。服务端已切换草稿协议，主分支仍发送旧清单；需要将本分支的 `import-console-draft` 工作流合入实际发布分支。此前镜像依赖整个 Release workflow 成功，因而被连带跳过。
- [镜像 34380600347](https://github.com/Rvosy/Sakura/actions/runs/34380600347)，提交 `87406364`、attempt 1、Ubuntu：创建 Release 时返回 HTTP 400，尚未进入文件上传。排查时 GitCode `main` 仍为 9 月 1 日的 `ee62f9bc`，tags 只到 `v1.0.2`，发布提交的 API 查询返回 `404 Not Found Commit`。必须先恢复代码同步；原日志没有响应正文，不能断言 400 没有其他原因。
- [镜像 33501174309](https://github.com/Rvosy/Sakura/actions/runs/33501174309)，提交 `e75d39bb`、attempt 1、Ubuntu：`v1.0.2` 创建 Release 成功，但上传 `Sakura-1.0.2-macos-arm64.app.tar.gz` 时出现 `The write operation timed out`。这是另一处尚未闭环的上传故障，不能用后续版本创建 Release 的 400 解释，也不能靠增加超时宣称解决。

恢复时先确认目标提交在 GitCode 可读，再对已存在的 GitHub Release 执行一次手动镜像验证，保留上传结果。未完成真实附件上传和公开下载验证前，不把 GitCode 地址写入已发布的国内清单。

### 预演验证

- [35001855469](https://github.com/Rvosy/Sakura/actions/runs/35001855469)，提交 `7f19d27f`、attempt 1：成功将 `v1.1.2` 的提交 `cb804c58` 推送到独立测试 tag，GitCode API 校验通过。随后读取正式 latest 基线收到 HTTP 400、`No latest release found`，未进入附件上传。已针对这一确切空状态补充处理和回归测试，其他 HTTP 400 仍报错。
- [35002242781](https://github.com/Rvosy/Sakura/actions/runs/35002242781)，提交 `2c5095c6`、attempt 1：代码同步、空基线处理及 `pre` Release 创建均成功。第一个 165332642 字节的 macOS 包上传数分钟后仍未输出 16 MiB 进度；为诊断手动取消，结果不能记为上传成功或自然超时。后续改用 curl 做对照，读取提前返回的 HTTP 响应，并记录状态、字节数、速度和耗时，不增加自动重试。
- [35003762172](https://github.com/Rvosy/Sakura/actions/runs/35003762172)，提交 `0c578ee5`、attempt 1：404 字节签名文件的代码同步、Release 创建、上传和公开读回全部通过，上传 HTTP 200。它只证明小文件连通性。
- [35004098026](https://github.com/Rvosy/Sakura/actions/runs/35004098026)，提交 `bf3ae877`、attempt 1：扩展探测的 6 个附件全部上传并逐字节读回通过，包括两个约 1.2 MB 的诊断文件（各约 2.6–2.8 秒）、插件包、安装帮助页及签名。另从杭州服务器无凭据下载测试插件包，HTTP 200，9235 字节约 0.83 秒；这项国内访问检查没有单独进行内容比对。
- [35002885446](https://github.com/Rvosy/Sakura/actions/runs/35002885446)，提交 `8ec7d7c7`、attempt 1：完整预演通过，12 个附件（含 Windows 安装包/Portable、macOS 包、插件包、签名和改写后的 `latest.json`）全部上传并公开读回逐字节比对通过，代码 tag 对应 `cb804c58`，正式 latest 基线未改变。全过程约 28 分钟，测试 Release 保持 `pre`。随后从杭州服务器完整下载 107088597 字节的 Windows 安装包，用时 7.86 秒，大小匹配；完整内容比对证据来自本次 CI。
- [35005911149](https://github.com/Rvosy/Sakura/actions/runs/35005911149)，提交 `cb614c27`、attempt 1：针对新增的实时日志传输执行一次连通性探测，6 个附件的上传与读回再次通过；日志可见 HTTP 100/200，未输出凭据请求头或签名 URL。这次验证覆盖日志实现变更，没有重复整套大文件测试。

完整预演显示上传速度波动很大：165 MB 的 macOS tar 包耗时 1006.6 秒（约 164 KB/s），167 MB 的 macOS ZIP 耗时 19.6 秒（约 8.5 MB/s），181 MB 的 Windows Portable 耗时 307.7 秒。一次通过证明本次全链路可用，不能证明历史写入超时已经消失；目前没有足够证据把速度差异归因到具体网络节点或服务端机制。实时查询期间附件列表未及时反映进度，最终结果以完整 Actions 日志及读回比对为准。

## 不发新版本的预演

在 GitHub Actions 中运行 **Mirror Release to GitCode**，选择待测分支，填写已有正式 GitHub Release 的 tag，`mode` 保持默认的 `rehearsal`。也可使用 CLI：

```text
gh workflow run mirror-gitcode-release.yml --ref codex/sakura-service-console -f tag=v1.1.2 -f mode=rehearsal
```

预演使用 `mirror-test-<源tag>-<run_id>-<attempt>` 独立标签，实际同步该版本代码、创建 `pre` Release、上传现有全部附件并从公开地址读回比对。它不会创建新的 GitHub Release、提升 GitCode latest 或修改国内清单；最后检查正式 latest 标签未改变。测试 Release 保留供检查，入口见 Actions Summary。

这是有真实写入的分发预演，需要代码及 Release 写权限，不是仅打印计划的 dry-run。测试旧版本的大文件可以暴露上传链路问题，但不覆盖新代码编译或签名生成。每次失败先根据日志定位原因，再决定是否有必要继续验证。

`mode=upload-probe` 只传输源 Release 中不超过 2 MiB 的附件，并读回比较；它不上传 `latest.json`，不表示完整安装包通过。连通性探测使用独立测试 tag，可与完整预演同时运行。完整预演和正式镜像仍串行执行。

## 手动回填正式镜像

选择已有 GitHub Release 的 tag，将 `mode` 改为 `publish`。它复用现有安装包，验证通过后会提升 GitCode 正式 latest。自动触发的发行镜像也使用 `publish`。工作流串行执行镜像任务，避免预演与正式发布互相干扰。

## 当前边界

这个 workflow 只负责发行镜像。桌面客户端仍按当前 updater 合同使用 GitHub；GitHub 失败后自动切换 GitCode 的客户端 fallback 需要单独实现和验证。
