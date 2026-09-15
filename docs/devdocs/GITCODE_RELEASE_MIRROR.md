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
5. GitCode 版 `latest.json` 最后上传。只有全部预期附件都可见后，Release 才从 `pre` 提升为 `latest`，因此不会先暴露一个指向缺失附件的最新版 manifest。
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

## 不发新版本的预演

在 GitHub Actions 中运行 **Mirror Release to GitCode**，选择待测分支，填写已有正式 GitHub Release 的 tag，`mode` 保持默认的 `rehearsal`。也可使用 CLI：

```text
gh workflow run mirror-gitcode-release.yml --ref codex/sakura-service-console -f tag=v1.1.2 -f mode=rehearsal
```

预演使用 `mirror-test-<源tag>-<run_id>-<attempt>` 独立标签，实际同步该版本代码、创建 `pre` Release、上传现有全部附件并从公开地址读回比对。它不会创建新的 GitHub Release、提升 GitCode latest 或修改国内清单；最后检查正式 latest 标签未改变。测试 Release 保留供检查，入口见 Actions Summary。

这是有真实写入的分发预演，需要代码及 Release 写权限，不是仅打印计划的 dry-run。测试旧版本的大文件可以暴露上传链路问题，但不覆盖新代码编译或签名生成。每次失败先根据日志定位原因，再决定是否有必要继续验证。

## 手动回填正式镜像

选择已有 GitHub Release 的 tag，将 `mode` 改为 `publish`。它复用现有安装包，验证通过后会提升 GitCode 正式 latest。自动触发的发行镜像也使用 `publish`。工作流串行执行镜像任务，避免预演与正式发布互相干扰。

## 当前边界

这个 workflow 只负责发行镜像。桌面客户端仍按当前 updater 合同使用 GitHub；GitHub 失败后自动切换 GitCode 的客户端 fallback 需要单独实现和验证。
