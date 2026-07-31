---
kind: plan
status: stabilizing
audience: maintainer
source_of_truth: self
updated: 2026-07-31
---

# 仓库根目录中度整理实施计划

## 范围与前置

本计划把 Git 跟踪根入口从 30 个收敛到 25 个，迁移文档素材和开发依赖，内聚更新删除清单，
并退役已由 Tauri Studio 取代的旧 PySide6 Studio。`WP-3S-01` 仍处于 `stabilizing`；项目负责人于
2026-07-31 明确授权本维护变更作为独立工作执行，不改变或冒充 Runtime v2 Work Package 状态。

不触碰用户 `data/`、角色包、Runtime、本地 Agent 配置、构建缓存和 Runtime v2 双入口契约。

## 实施步骤

1. 先建立根目录与发布入口规范及布局回归测试。
2. 把文档图片和接话示例迁入对应 userdoc/devdoc 目录并修复全部链接。
3. 将开发依赖移入 `tools/`，把 Intel macOS 条件约束合并到运行依赖清单并同步 CI。
4. 把更新删除清单源移入 `.github/release/`，发布时只注入升级包，并扩充旧文件删除项。
5. 删除旧 Studio、旧入口与废弃工具，以当前 Character Studio 服务、RPC、前端和 Rust 测试替代。
6. 同步用户文档、开发文档和 CHANGELOG，运行全量门禁并写入审计 record。

## 退出条件与回退

退出条件为根入口精确等于规范中的 25 项、所有旧路径引用清零、更新包协议保持兼容、当前 Tauri
Studio 测试覆盖通过，并完成 docs、unit、legacy Qt UI、Studio 前端/Rust 与差异检查。

回退时整体 revert 本维护变更，恢复旧根文件和 PySide6 Studio；不得删除或恢复任何用户数据。若只需
恢复独立 Studio，应同时恢复其脚本、实现、测试、发布白名单和用户文档，不能只恢复一个入口文件。

本地实现与自动门禁已完成，证据见
[`docs/records/audits/REPOSITORY_ROOT_CLEANUP.md`](../records/audits/REPOSITORY_ROOT_CLEANUP.md)。当前只等待
同一候选 SHA 的 Windows/macOS 发布矩阵；取得证据前保持 `stabilizing`，不移入 archive。
