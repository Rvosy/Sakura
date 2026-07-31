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
