---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-02
---

# WP-3-06 本地自动验证记录

## 环境与范围

- 日期：2026-08-02（Asia/Shanghai）
- 分支：`refactor/tauri-runtime-v2`
- 候选：本地 `WORKTREE`；尚未创建候选提交或推送远端
- Work Package 基线：`93c75ba9803618d2d9fde6e99ebb152ffc176a6b`
- 真实数据边界：仓库 `data/**`、`characters/**`、`third_party/**` 无变化；往返只使用系统临时目录中的
  WP-0-02 脱敏 fixture

## 实现与安全边界

Core readiness 在创建可写聊天会话前，使用现有全局版本门并逐行验证当前角色全部 JSONL 历史段。损坏、
截断、类型错误或目标/祖先符号链接与联接点进入稳定 `HISTORY_COMPATIBILITY_READ_ONLY` 状态；真实聊天边界
在调用 Provider 和追加 user turn 前再次校验。兼容检查不调用 legacy 尾行修复，不截断、不备份、不创建
默认历史。

真实验收入口只存在于 debug/test 路径，并要求系统临时目录的单层 `sakura-wp-3-06-*` 根、canonical
非链接 `app-root` 与固定脱敏 marker；release 构建显式拒绝覆盖。验收驱动将 legacy、Tauri 与 Core 后代
置于 kill-on-close Windows Job 中，父编排进程异常终止时由操作系统回收该轮完整进程树。

## 真实 Windows 往返结果

项目负责人在仓库根目录执行：

```powershell
.\desktop\tests\windows_wp_3_06_data_compatibility_acceptance.ps1
```

脚本成功构建当前 debug Tauri 候选并输出：

```json
{"acceptance_root_removed":true,"changed_paths":["data/chat_history/fixture.jsonl","data/runtime_v2/config/ui.json"],"fixture_files":28,"lock_conflicts":["legacy-holds-tauri","tauri-holds-legacy"],"provider_requests":1,"status":"passed"}
```

这证明真实 `legacy_qt_main.py` 参考进程写入、真实 Tauri/Shell lifecycle、bundled Core、本机确定性
Provider、冻结 oracle 回读以及生产共享锁双向冲突均走通。最终只改变批准的 history/UI 两个相对路径，临时根已删除；结束后只读
进程与窗口核对未发现 Sakura、Tauri 或该轮 Core 残留，既有无关 Python 进程未被终止。

## 本地自动结果

```text
runtime\python.exe -m pytest tests\integration\test_wp_3_06_data_compatibility.py tests\unit\test_core_host_config_reader.py -q
-> 83 passed / 2 skipped（仅当前 Windows 未开放符号链接权限）

npm test  # desktop/frontend
-> 105 passed / 0 failed

cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked -- --test-threads=1
-> 234 passed / 24 ignored / 0 failed

cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check
-> passed

runtime\python.exe -m harness run core-host
-> 109 unit + 26 integration passed

runtime\python.exe -m harness run python-full
-> 581 unit + 35 integration + 24 legacy Qt UI passed；8 environment skips

runtime\python.exe -m harness run runtime-v2-shell
-> 7/7 suites passed

runtime\python.exe -m harness run docs
runtime\python.exe -m harness run smoke
runtime\python.exe -m harness check WP-3-06
git diff --check
-> passed
```

`runtime\python.exe -m harness verify WP-3-06` 报告为
`temp/harness/20260802T150200Z-WP-3-06.json`：五个 required profiles 全部通过，23 项通过、0 项失败、
3 项人工验收 pending。Harness 的验收描述映射不单独替代真实进程证据；本记录以上述真实 Windows 脚本
输出作为 Qt → Tauri → Qt 自动门的实际证据。

## 结论与待验收项

本地自动门与真实 Windows 迁移 oracle 往返门通过。按 2026-08-02 产品方向修订，Legacy Qt 只作为迁移
参考，不要求可见 UI，也不是用户回退入口。等待项目负责人确认可见 Runtime v2 的基础聊天；复核未来/
损坏只读诊断、保存失败、强杀重获与零残留；审查同一候选 SHA 的三平台证据、最终脱敏 manifest、
Runtime v2 回退边界和 Phase 7 Legacy Qt 删除边界。

本文不填写上述人工结果，不把 WP-3-06 标记为 `accepted`；当前状态唯一见
[`work-packages.md`](../../plans/runtime-v2/work-packages.md)。
