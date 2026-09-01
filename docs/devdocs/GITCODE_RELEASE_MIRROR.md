# GitCode Release 镜像

GitHub Release 是 Sakura 的权威发行源；GitCode 只保存同一批已签名产物的下载镜像。镜像过程不重新构建、不重新签名，也不经过 Sakura Service/VPS。

## 仓库配置

GitCode 仓库先使用 Pull Mirror 同步 GitHub 的提交和 tag。GitHub 仓库再配置：

- Repository variable `GITCODE_REPOSITORY`：GitCode 的 `owner/repo`，例如 `Rvosy/Sakura`。未配置时镜像 workflow 自动跳过。
- Repository secret `GITCODE_ACCESS_TOKEN`：对目标 GitCode 仓库具有 Release 写权限的 Personal Access Token。

不要把 GitCode token 写进仓库、Release、日志或客户端。

## 发布行为

`.github/workflows/mirror-gitcode-release.yml` 在 `Release Runtime v2` 完成后运行，也支持手动输入 tag 回填历史版本。

流程固定为：

1. 从已经发布完成的 GitHub Release 下载最终 assets；GitHub 仍是 source of truth。
2. 用 GitHub tag 解析出的 40 位 commit SHA 创建/校验同 tag 的 GitCode Release，避免镜像延迟时把 tag 建到错误提交。新 Release 先保持 `pre` 状态。
3. 将 GitHub Release 中的原始文件逐字节上传到 GitCode。失败后重跑时，已经可见的同名附件直接保留，继续补齐缺失附件，不重新构建文件。
4. 读取 GitHub 最终 `latest.json`，保留 `version`、`notes`、`pub_date`、Tauri `signature` 和 Portable `sha256`，只把 artifact URL 改为 GitCode Release 附件下载 API。
5. GitCode 版 `latest.json` 最后上传。只有全部预期附件都可见后，Release 才从 `pre` 提升为 `latest`，因此不会先暴露一个指向缺失附件的最新版 manifest。
6. 最终检查 GitCode Release 的附件集合和 latest tag。镜像失败不会删除或撤回已经发布的 GitHub Release。

GitCode 版 artifact URL 使用其公开 Release 附件下载接口：

```text
https://api.gitcode.com/api/v5/repos/<owner>/<repo>/releases/<tag>/attach_files/<file>/download
```

因此两个源的 `latest.json` 内容应该是：版本、签名和 hash 相同，URL 不同。

## 手动回填现有版本

workflow 合入默认分支并配置好 variable/secret 后，在 GitHub Actions 中运行 **Mirror Release to GitCode**，输入例如：

```text
v1.0.2
```

它直接读取已经存在的 GitHub Release，不重新构建 1.0.2。

## 当前边界

这个 workflow 只负责发行镜像。桌面客户端仍按当前 updater 合同使用 GitHub；GitHub 失败后自动切换 GitCode 的客户端 fallback 需要单独实现和验证。
