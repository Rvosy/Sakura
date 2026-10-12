# 参与 Sakura 开发

开发约定见 [AGENTS.md](../AGENTS.md)，产品行为与扩展接口见[文档总览](../docs/README.md)。先阅读相关调用链，修改解决实际问题所需的部分。

从[架构与模块关系](../docs/development/architecture.md)找到涉及的模块，再读它的 [Spec](../docs/specs/README.md)、关联 [ADR](../docs/adr/README.md) 和公开接口。改变业务要求时更新模块规格；改变重要架构选择时更新对应决策及理由。内部重构不改写业务规则，普通修复也不需要另建决策文档。

## 开发环境

从仓库根目录执行安装、开发依赖准备和启动。使用项目自带 Python；源码检出缺少 `runtime/` 时，安装脚本会下载指定版本。Rust 与 Node.js 需预先安装，Linux 系统依赖见[安装指南](../docs/guide/setup.md#linux)。

Windows：

```powershell
.\scripts\install.bat
.\runtime\python.exe -m pip install -r tools\requirements-dev.txt
.\scripts\start.bat
```

macOS / Linux：

```bash
bash scripts/install.sh
./runtime/bin/python -m pip install -r tools/requirements-dev.txt
bash scripts/start.sh
```

启动脚本增量编译开发版。正式打包见[发行指南](../docs/development/release.md)。

## 仓库入口

| 目录 | 用途 |
|---|---|
| `desktop/` | 桌面窗口、前端与系统资源 |
| `app/` | 后台服务、插件运行、存储和 SDK |
| `plugins/` | 内置及可选插件源码 |
| `services/sakura/` | 在线服务、控制台和官网 |
| `tests/`、`harness/` | 测试与验证入口 |
| `tools/`、`scripts/`、`packaging/` | 开发、启动与发行工具 |

用户数据、模型、日志、凭据、测试缓存和构建产物不入库。测试使用隔离数据，不读写已有角色和配置。

## 验证

测试应能发现具体业务错误，先复用已有覆盖。按修改选择相关测试或 [Harness](../harness/README.md) profile，通过后不重复扩大范围。下面的 Python 路径为 Windows，其他平台换成 `runtime/bin/python`。

```powershell
runtime\python.exe -m harness list
runtime\python.exe -m harness run <profile>
runtime\python.exe -m pytest -q <相关测试文件>
npm test --prefix desktop/frontend
cargo test --manifest-path desktop/src-tauri/Cargo.toml <相关模块>
```

文档链接使用 `runtime\python.exe -m harness run docs` 检查。原生窗口、麦克风和跨平台行为按对应环境验证，未验证部分如实说明。

## 提交与 PR

新贡献通常从 `dev` 建立分支，已有任务继续使用当前工作区。提交标题使用 `<type>: <中文说明>`，PR 面向 `dev`，使用中文说明问题、修改结果、验证和实际剩余问题。UI 变化按需附截图。

提交内容按 [MIT License](../LICENSE) 发布，并须有权分发所含代码与资源。
