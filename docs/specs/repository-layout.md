---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-08-26
---

# 仓库根目录与发布入口契约

## 范围

本规范约束 Sakura 源码仓库的 Git 跟踪根目录、面向用户的启动入口、Python 依赖清单和更新包内部
布局。目标是让根目录只承载必须在项目级发现或由外部工具直接消费的内容。

本规范不约束被 `.gitignore` 排除的本地运行数据、角色包、Runtime、缓存和构建产物，也不要求把
职责清晰的源码目录合并到单体目录中。

## Git 跟踪根目录

根目录允许且只允许以下 22 个 Git 跟踪入口：

```text
.gitattributes
.github/
.gitignore
AGENTS.md
CHANGELOG.md
LICENSE
README.md
VERSION
app/
desktop/
docs/
harness/
install.bat
main.py
plugins/
pytest.ini
requirements.txt
scripts/
start.bat
tests/
third_party/
tools/
```

新增根入口前必须先说明为什么不能归入既有目录，并同步更新本规范和布局测试。`data/`、
`characters/`、`runtime/`、`runtime-v2-dependencies/`、`temp/` 以及本地 Agent 配置不属于 Git 跟踪
根目录契约，不得因整理仓库而删除。

## 用户入口

- `main.py`、`install.bat` 与 `start.bat` 只服务 Runtime v2 开发启动，不进入正式发行 staging。
- Windows Setup 与 macOS 更新由 Tauri Updater 负责；Windows Portable 仅检查并提示下载新版 ZIP。
- 历史桌面入口已经退役；需要参考旧实现时使用 Git 历史，不在当前源码维持第二套应用。
- 角色工作室只通过 Sakura 应用内的 Tauri Studio 打开；不再提供根级独立工作室入口。
- macOS/Linux 的安装与启动入口继续位于 `scripts/`，根级 `.command` 文件只存在于生成的发布包，
  不作为源码仓库入口。

## 依赖与发布布局

- `requirements.txt` 是 Core 和发行安装工具的通用运行依赖入口，必须留在根目录；插件专属依赖不得加入。
- `tools/requirements-dev.txt` 是开发与 CI 的完整安装入口，通过 `-r ../requirements.txt` 引入 Core 依赖，
  并仅为插件测试显式引入各插件自己的 requirements。
- 正式发行依赖由 `packaging/requirements-windows-x64.lock` 与
  `packaging/requirements-macos-arm64.lock` 以完整 SHA-256 锁定，并安装到发行 Python 的原生
  `site-packages`。
- 完整安装包不得携带用户数据、测试、文档素材、历史桌面实现或仅供仓库维护的工具。

## 验收条件

- 布局测试确认 22 个 Git 跟踪根入口与本规范一致。
- 用户入口、开发依赖命令和更新包内部清单路径都有自动测试或发布工作流断言。
- 历史 Studio、历史入口和废弃工具不再被源码、当前文档或发布白名单引用。
- Runtime v2 发布清单不包含 Qt 桌宠实现或面向用户的历史回退说明。
- 文档、Python、Tauri Studio 和发布相关门禁全部通过。
