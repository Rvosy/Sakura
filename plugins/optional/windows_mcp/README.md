# Windows 操作插件

将 CursorTouch 的 [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) 封装为 Sakura 可选插件。
需要 Windows 10/11 和已启用的 `sakura.mcp` 基础组件。

插件通过基础组件启动官方 PyPI 包、发现工具并注册为 `windows_mcp_*`，不自行实现 MCP 客户端。
没有服务器管理设置页。停用插件会撤回工具并回收服务器；连接失败后可停用再启用。

当前固定 Windows-MCP 0.8.5、FastMCP 4.0.3，使用插件私有依赖和独立服务进程。
上游匿名遥测默认关闭，stdio 使用 UTF-8，使用插件自己的配置路径，不读取用户全局 Windows-MCP 配置。
命令、鼠标、键盘和文件工具会实际操作当前 Windows 用户环境，启用后按任务需要调用。

## 安装与接口

使用 Sakura 的本地插件安装入口导入 ZIP；安装时准备私有依赖，安装后启用“Windows 操作”。
核心组件不增加任何管理入口。此插件发现 20 个上游工具，包括 Snapshot、Screenshot、Click、PowerShell 等。
截图通过宿主图像 artifact 传回；大文本或多图结果返回操作编号，可用 `windows_mcp_result` 分段读取并释放。

另提供 `sakura.windows-mcp` Service：`status`、`catalog`、`begin`、`inspect`、`readResult`、`cancel`、`release`。
基础组件的句柄归此插件所有，插件退出时统一回收。

## 构建和验证

```text
runtime\python.exe tools/release/package_optional_plugin.py --source plugins/optional/windows_mcp --output artifacts/plugins/windows-mcp-0.1.0.zip
runtime\python.exe -m pytest -q tests/unit/test_windows_mcp_plugin.py
```

普通测试使用隔离服务器，验证动态工具注册、大结果和停用回收。真实桌面验证显式设置
`SAKURA_TEST_WINDOWS_MCP_LIVE=1` 与 `SAKURA_WINDOWS_MCP_TEST_DEPS`（已安装上游依赖的独立目录）。
真实测试只发现工具、读取无图快照和验证进程退出，不点击、输入或执行命令，也不输出桌面内容。

2026-09-15 在 Windows 实测：20 个工具，Snapshot 成功，停用后两个服务端进程回收。
这一结果不代表已逐个验证写入工具。PyPI 0.8.5 支持当前 Python 3.12；GitHub 主分支的 Python 要求不同，
更新时应重新核对发行包及依赖，不直接跟随 main。

上游 Windows-MCP 使用 MIT License，作者 JEOMON GEORGE；上游代码通过包管理器获取，未复制到此包装插件。
