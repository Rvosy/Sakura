---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-07-31
---

# WP-H-01 实现与本地验证记录

## 环境与候选

- 日期：2026-07-31（Asia/Shanghai）
- 分支：`refactor/tauri-runtime-v2`
- 激活提交：`895adb987bab5c3a4adf26e43794addde12ae342`
- 核心提交：`eac7890eadd08309d91c0edbe2ccc65b303d9b3c`
- CI/文档提交：`6ce3a46a6022e7db2bd062534a022420f32d73e1`
- Python：仓库 Runtime 3.12.8；macOS 本地主机；额外使用全新 `/private/tmp` clone 验证 Git 范围

## TDD 与自动结果

新增 Harness v1 测试首次执行在收集阶段因 `harness.git_state` 不存在退出 2。最小实现后，runner 兼容、
任务契约、Work Package、四类 Git 变化、依赖、冻结、报告和 docs 定向矩阵为 39 passed。

干净 clone 执行：

```text
python -m harness current --json                         -> WP-H-01 / stabilizing 前的 active 候选解析通过
python -m harness preflight WP-H-01                      -> 12/12 checks passed
python -m harness check WP-H-01                          -> scope passed，全部失败 bucket 为空
python -m harness verify WP-H-01 --report .../WP-H-01.json
  docs                                                   -> 2/2 cases passed
  smoke                                                  -> 3/3 cases passed
  unit                                                   -> 575 passed, 1 skipped
  final                                                   -> exit 3, manual_pending, 19 passed / 0 failed / 1 pending
```

报告使用完整 base/head SHA、UTC 时间、UTF-8 和原子替换。新增 fault tests 证明范围外、禁止、受保护、
依赖文件、删除/重命名、staged/unstaged/untracked/committed、契约放宽、profile 失败、前置失败短路、
报告 replace 失败清场、空格与非 ASCII 路径。

扩展 `runtime-v2-shell` 为 7/7 cases：frontend 68 passed、Provider/模型 25 passed；Rust 定向分别为角色
外观 8、角色表现 8、产品窗口 7、窗口几何 16、窗口交互 15 passed。

## 本地工作树与 CI 边界

原工作树在任务开始前已有未跟踪 `.codex/environments/environment.toml`。实际
`verify --active --report temp/harness/WP-H-01-worktree.json` 将其列为 out-of-scope，0.223 秒退出 1，
且 `profiles` 为空，证明前置失败不会继续昂贵测试。该文件未被删除、修改、忽略或加入允许列表。

`.github/workflows/test.yml` 已为 `refactor/tauri-runtime-v2` push 增加 Harness v1 job，执行定向 pytest、
current/preflight/check、docs 和 smoke。远端 Actions 尚未因本地提交自动运行；本记录不把 workflow 配置
或本地测试表述为远端 CI 已通过。

## 当前结论

自动实现门在干净仓库通过，人工审查仍 pending，因此 WP-H-01 进入 stabilizing，不标记 accepted，
WP-3-04 继续保持 planned。

## 2026-07-31 稳定化约束修复

审查后的治理修订与实现候选如下：

- `e327e1488dd3f813535b5b23a1f402b351f05756`：修订激活锚点、状态源、路径和 CI 契约，新增独立 `0001` 锚点。
- `fe18b6e77fda5cac75350e64c17a6b0dbe196083`：精确补入原激活提交中的 WP-3S-01 依赖验收 spec，新增 `0002` 锚点。
- `7382da498ea0e3aaa3922eb2ea2a442602d33dd0`：实现完整 SHA 与锚点锁定、状态源 owner gate、通用
  `--active`、受限路径语义、CI 泛化和 Windows 文档路径修复。
- `48e3f643551c3d0d715230c6633bbcdc91e54317`：修正任务契约中残留的旧 CI 验收描述，新增 `0003` 锚点。

Windows 原工作树的 `preflight --active` 和 `check --active` 仍按设计拒绝任务开始前已有的未跟踪
`.codex/environments/environment.toml` 与受保护 `data/runtime_v2/config/ui.json`。两者未删除、未修改、
未暂存，也未加入允许列表。为隔离这些用户运行时文件，使用无 hardlink 的本地干净 clone 验证最终候选；
仓库嵌入式 Python 固定包含原仓库路径，因此验证入口显式把 clone 路径放在模块搜索首位，报告路径也位于
clone 内，避免误用原工作树模块。

最终候选 `48e3f643...` 的自动结果：

```text
current --json                 -> WP-H-01 / stabilizing
preflight --active             -> 12/12 checks passed
check --active                 -> passed; base=642c1b...; all failure buckets empty
harness-v1                     -> 2/2 cases; 45 tests passed
docs                           -> 2/2 cases; Windows path comparison passed
smoke                          -> 3/3 cases
verify --active
  docs                         -> 2/2 cases passed
  smoke                        -> 3/3 cases passed
  unit                         -> 580 passed / 6 skipped
  final report                 -> manual_pending; 19 passed / 0 failed / 1 pending
  duration                     -> 112.922 seconds
```

故障矩阵另行证明：`base_ref` 为 `HEAD`、当前 HEAD SHA 或后移 SHA 均失败；锚点提交夹带实现、已提交锚点
覆写、非法 `src/*.py` glob 均失败；状态源或冻结治理文件变化进入 `owner_review_required`，退出 3 且跳过
昂贵 profiles。远端 GitHub Actions 尚未由这些本地提交触发，本记录不把本地 workflow 检查写成远端 CI
成功。

因此当前只能结论为：实现与本地自动门通过，等待项目负责人审查独立锚点和人工验收。WP-H-01 继续为
`stabilizing`，不得标记 `accepted`，也不得开始 WP-3-04。
