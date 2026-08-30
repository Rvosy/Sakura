---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# 仓库根目录整理本地验收记录

## 环境与范围

- 日期：2026-07-31
- 环境：macOS 26.5.2（25F84），Python 3.12.8，Node 24.18.0，Rust/Cargo 1.96.0
- 分支：`refactor/tauri-runtime-v2`
- 范围：文档素材、依赖清单、发布删除清单、旧 PySide6 Studio、当前 Tauri Studio 测试和根目录契约
- 授权边界：项目负责人明确允许在 `WP-3S-01` 仍为 `stabilizing` 时执行本独立维护变更；该授权不改变 Runtime v2 Work Package 状态。

## 结果

- 根目录布局测试通过，现有工作树中的 Git 跟踪根入口精确为规范定义的 25 项。
- 旧 Studio 源码、根启动脚本和废弃工具已从当前源码移除；升级删除清单覆盖对应旧安装文件。
- 完整包白名单不再携带旧 Studio 入口和内部删除清单；Windows/macOS 升级包仍在根目录包含
  `update-delete.json`。
- Intel macOS 条件约束可由 PEP 508 解析；开发依赖入口可从 `tools/requirements-dev.txt` 引用根运行依赖。
- 文档检查确认元数据、索引、本地链接和真相源约束有效。

## 自动验证证据

| 检查 | 结果 |
|---|---|
| 定向 Python：布局、更新器、Character Studio、Tauri RPC | 23 passed |
| Tauri Studio 前端 `node --test` | 4 passed |
| Tauri Studio Rust locked tests | 5 passed |
| `python -m harness run docs` | 2/2 cases；`temp/harness/20260731T130116Z-docs.json` |
| `python -m harness run unit` | 546 passed，1 skipped；`temp/harness/20260731T130128Z-unit.json` |
| `python -m harness run legacy-qt-ui` | 24 passed；`temp/harness/20260731T130134Z-legacy-qt-ui.json` |
| `python -m harness run smoke` | 2/2 cases，26 tests；`temp/harness/20260731T130150Z-smoke.json` |
| Workflow YAML、requirements marker、JS 语法、`git diff --check` | 通过 |

## 尚未取得的证据

本地改动尚未提交和推送，因此没有同一候选 SHA 的 Windows/macOS GitHub Actions 发布矩阵结果。
在远端矩阵通过前，实施计划保持 `stabilizing`，不归档、不把本记录解释为跨平台发布验收。
