# Sakura Harness

这是 Sakura 的仓库级验证入口，面向开发者、Codex 和 CI。它不替代 `pytest`；它把已有检查组织成稳定的 profile，并为每次运行生成统一 JSON 报告。

## 为什么放在这里

`harness/` 与 `app/`、`tests/`、`scripts/` 同级：

- `app/` 只保留产品代码；
- `tests/` 继续保存测试实现；
- `scripts/` 保存构建、安装和运维脚本；
- `harness/` 只负责选择场景、执行检查和汇总证据。

这样后续可以加入 Tauri、桌宠 UI、真实 Core lifecycle 或离线对话评测，而不需要改变现有测试布局。

## 使用

项目运行环境：

```powershell
runtime\python.exe -m harness list
runtime\python.exe -m harness run harness-v1
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness run docs
runtime\python.exe -m harness run unit
runtime\python.exe -m harness run core-host
runtime\python.exe -m harness run legacy-qt-ui
runtime\python.exe -m harness run python-full
runtime\python.exe -m harness run runtime-v2-shell
runtime\python.exe -m harness run runtime-v2-windows-interaction
```

也可以指定报告位置：

```powershell
runtime\python.exe -m harness run smoke --report temp\harness\smoke.json
```

默认报告写入 `temp/harness/`。进程退出码为 `0` 表示全部通过，`1` 表示至少一个 case 失败，`2` 表示调用或清单错误。

`docs` 会检查 `docs/` 的职责目录、YAML 元数据、Markdown 本地链接、索引覆盖、废弃路径和
Runtime v2 Work Package 真相源，并运行对应单元测试。

`runtime-v2-shell` 会运行 `desktop/frontend` 的完整 Node 测试，以及近期桌面壳改动涉及的角色外观、角色表现、产品窗口、窗口几何和原生交互 Rust 模块测试。该 profile 保持离线，并避开会与正在运行的 Sakura 实例争用共享锁的完整 Rust 生命周期测试。

`runtime-v2-windows-interaction` 是 Windows 专用实机门禁。它会先构建 debug 桌面壳，再启动桌宠和独立背景接收窗口，验证透明点不属于桌宠、可见立绘仍属于桌宠，并确认透明点击实际跨进程到达背景窗口。运行期间会短暂显示窗口并移动鼠标，证据写入 `temp/harness/windows-transparent-clickthrough/`。

Python profile 按用途分层：

- `unit`：完整 `tests/unit`，适合 Python 业务代码的常规回归；
- `core-host`：Core Host 单元、真实本地子进程集成测试，以及 WP-3V-01 脱敏 manifest、Provider 消息
  分类、Legacy oracle 基线和环境隔离行为测试；不访问公网或真实 Provider；
- `legacy-qt-ui`：完整 `tests/ui`，在 offscreen Qt 平台冻结迁移期 Legacy Qt 行为参考；它不是受支持产品入口；
- `python-full`：依次运行 unit、integration 和迁移参考 Qt UI，适合合并前完整回归。

## Agent Development Harness v1

任务级命令已经可用：

- `run`：当前已可用，运行一个验证 profile。
- `current`：从唯一真相源查询 active/stabilizing Work Package；`--json` 输出稳定 JSON。
- `preflight`：修改前校验任务契约、当前状态、依赖、文档、profile、base ref 和工作树。
- `check`：检查 committed、staged、unstaged、untracked 变化及依赖、受保护路径和冻结契约。
- `verify`：按 preflight、scope、required profiles、自动验收、人工汇总顺序生成任务报告。

Test 是单个行为断言；Test Harness 运行并汇总测试；Agent Development Harness 还约束任务、Git 范围和
依赖。Task Contract 是 `harness/tasks/<WP-ID>.json` 的机器可读任务边界；Work Package 的当前状态仍只在
`docs/plans/runtime-v2/work-packages.md`。自动验收由命令确定结果；人工验收由项目负责人执行，Harness
只汇总状态，Agent 不得代填。

完整示例：

```powershell
runtime\python.exe -m harness current
runtime\python.exe -m harness preflight --active
runtime\python.exe -m harness check --active
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness verify --active
```

退出码设计为 `0` 自动门通过且没有人工待办，`1` 验证失败，`2` 调用/契约/清单错误，`3` 没有自动失败
但仍需负责人处理。退出 3 的报告可能是 `manual_pending`，也可能是冻结治理文件变化导致的
`owner_review_required`；后一种情况尚未完成 required profiles。两者都不表示 Work Package 已
`accepted`，Agent 不得代填人工结果。

`preflight/check/verify --active` 使用当前 Work Package；也可传 `<ID>` 显式指定任务。前置门
失败时不会运行 required profiles。任务报告使用 UTF-8、UTC 时间和同目录临时文件原子替换；不会枚举
环境变量或读取密钥。所有 Git 命令使用 argv、仓库根 cwd 和 10 秒 timeout。

契约根对象和嵌套对象拒绝未知字段。路径只接受精确文件或 `directory/**`；相同或明确父子冲突的
allowed/forbidden/protected 规则失败。依赖文件默认禁止，只有 `allowlisted` 契约中的显式文件可变化。
`documents` 的 specs/adrs/plans 三类字段必须存在、各类可以为空，但合计至少引用一份权威文档，避免
普通修复被迫机械创建三类文档。
完整 40 位 `base_ref` 必须与最新独立激活锚点一致。契约、引用的 Spec/ADR/Plan 和状态源与锚点提交比较；
实施中变化会进入 `owner_review_files`，不能由普通自动门直接通过。

WP-3V-01 的真实 Windows 组合进程门不混入离线 profile。它直接构建并启动 debug Runtime v2 EXE，
使用系统临时目录中的脱敏数据和本地 Provider，精确强杀该 EXE 进程树内的 bundled Python Core，随后
运行无 UI Legacy oracle 回读：

```powershell
.\desktop\tests\windows_wp_3v_01_assistant_architecture_acceptance.ps1
```

脚本的成功 JSON 必须报告回复/取消、新 generation 水合、Legacy oracle、敏感证据和进程残留结果；
不能用普通 pytest 或静态源码断言替代这条真实进程证据。

Harness 只注册可执行行为、协议或生命周期检查。仅依赖源码字符串、函数排列或历史实现 token 的检查不作为 profile 门禁；对应意图应由 Python 行为测试、Node 测试、Rust 测试或独立真实验收覆盖。

## 扩展

在 `suites.json` 的 `cases` 中增加命令，再把 case id 放进相应 `profiles` 即可。命令以 argv 数组执行，不经过 shell；`{python}` 会替换为当前 Python，`{repo}` 会替换为仓库绝对路径。

最小版本刻意保持离线、无第三方依赖，也不会读取 API Key。需要真实模型或网络的评测应建立独立 profile，并显式标注和隔离数据目录。
