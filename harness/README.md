# Sakura Harness

Sakura 的仓库级验证入口。它不替代 `pytest`、Node 或 Rust 测试；它把已有检查组织成稳定 profile，
执行 Work Package changed-set 门，并生成机器可读 JSON 报告。产品代码仍在 `app/`/`desktop/`，行为
断言仍在各测试目录，`harness/` 只负责选择、执行和汇总。

## Profile

```powershell
runtime\python.exe -m harness list
runtime\python.exe -m harness run harness
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness run docs
runtime\python.exe -m harness run unit
runtime\python.exe -m harness run core-host
runtime\python.exe -m harness run legacy-qt-ui
runtime\python.exe -m harness run python-full
runtime\python.exe -m harness run runtime-v2-shell
runtime\python.exe -m harness run runtime-v2-windows-interaction
```

也可用 `--report temp\harness\name.json` 指定报告。profile 退出码：`0` 全部通过，`1` 至少一个 case
失败，`2` 调用或 manifest 错误。

- `smoke`：Harness 自测和核心协议的快速反馈。
- `docs`：目录职责、元数据、本地链接、索引和 Runtime v2 状态真相源。
- `unit`：完整 `tests/unit`。
- `core-host`：Core Host unit/integration，以及 Provider/Memory Python 边界。
- `legacy-qt-ui`：offscreen Qt 迁移参考回归，不是受支持产品入口。
- `python-full`：完整 unit、integration 和迁移参考 UI。
- `runtime-v2-shell`：Node 前端与 Rust 角色/产品窗口/原生交互检查，不再重复 Provider/Memory Python case。
- `runtime-v2-windows-interaction`：Windows 真实透明点击穿透门，会短暂显示窗口并移动鼠标。

## Work Package 命令

```powershell
runtime\python.exe -m harness current
runtime\python.exe -m harness check --active
runtime\python.exe -m harness verify --active
```

- `current` 从 `docs/plans/runtime-v2/work-packages.md` 查询唯一当前任务。
- `check` 一次检查当前 WP、表中依赖、固定 base、committed/staged/unstaged/untracked、重命名、allowlist、
  依赖变化、测试删除和全局保护路径。
- `verify` 只在硬门通过且 task 没有工作树修订时运行 required profiles；profile 先展开为有序唯一 case
  集，同一 case ID 只运行一次，再派生各 profile 状态。

独立 `preflight` 已删除。`check/verify --active` 使用当前 WP，也可以传显式 `<ID>`。

task v2 位于 `harness/tasks/<WP-ID>.json`，只包含：

```json
{
  "schema_version": 2,
  "id": "WP-X-01",
  "base_ref": "<full-40-character-sha>",
  "allowed_paths": ["app/example/**", "tests/**"],
  "required_profiles": ["docs", "unit"]
}
```

`base_ref` 与 task 第一次提交中的值一致，之后不得移动。已提交的 allowlist/profile 修订会在报告中列出；
未提交或 staged 的 task 修订返回 `3`/`owner_review_required` 并跳过 case。历史 v1 task/activation 只作
Git 证据，loader 不读取；WP-H-02 的 `0001` 是最后一个 activation。

未命中 allowlist 的路径直接失败。`data/**`、`characters/**`、`third_party/**` 是不可覆盖的全局保护
边界；`tests/**` 删除继续失败。允许的 manifest/lock 变化会被突出显示并继续测试，未允许时按越界失败。

task 退出码：`1` 自动失败，`2` 调用/契约/状态错误，`3` 自动全绿等待人工验收或 task 修订等待审查。
只有状态 `manual_pending` 表示自动门已通过；人工步骤来自对应 Spec，Harness 不复制也不代填结果。

## 报告与临时目录

每次 `run` 或 `verify` 都创建唯一的
`temp/harness/runtime-tmp/<run-id>`，默认注入 `TMPDIR`、`TMP`、`TEMP`；case 显式 `env` 可以覆盖。
临时根写入报告，避免 macOS `/var` 与 `/private/var` 一类平台路径别名误判。

报告使用 UTF-8、UTC 时间和同目录原子替换，不枚举环境变量或读取密钥。task report schema v2 保存
scope、依赖变化、契约修订字段、case ID/结果和派生 profile，不复制人工验收散文。

case timeout 是硬 deadline：到期后 case 立即失败并终止子进程，不增加解释器启动宽限期或自动重试。
报告保留终止并排空 pipe 后实际返回的 stdout/stderr，并按 UTF-8 解码；如果子进程在 deadline 前尚未
产生输出，报告保持为空，不推断或补写预期文本。短 timeout 回归与 UTF-8 timeout 输出解码分别测试，
避免把平台解释器启动耗时误判为 Runner 丢失输出。

## 扩展

在 `suites.json` 的 `cases` 中注册窄命令，再把 case ID 放入 profile。命令使用 argv 数组执行，不经过
shell；`{python}` 替换为当前 Python，`{repo}` 替换为仓库绝对路径。新增业务 WP 默认不修改 Harness
Python；Journey 随真实产品能力渐进增加，且不得被 broad Python profile 重复收集。
