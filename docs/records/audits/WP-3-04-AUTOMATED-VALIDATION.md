---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-01
---

# WP-3-04 本地自动验证记录

## 环境与范围

- 日期：2026-08-01（Asia/Shanghai）
- 分支：`refactor/tauri-runtime-v2`
- 候选：本地 `WORKTREE`；尚未创建候选提交或推送远端
- Work Package 基线：`4d3e34fc10ad770847694c9203f8e562c182d9f2`
- 用户运行时隔离：预检前已有的 `.codex/` 与 `data/runtime_v2/config/ui.json` 被可恢复地暂存，未纳入
  WP 变更、测试输入或证据

## 实现与定向验证

本地实现把真实 `chat.send`/`chat.cancel` Gateway 接入冻结桌宠表现，并增加共享 `ui.json` 仓库上的
`chat.presentation_timing` 设置切片。产品 DOM、样式、窗口几何、Python Assistant、Provider、history、
角色资源和依赖清单均未修改。

定向结果：

```text
cargo test --locked                         -> 251 tests：227 passed / 24 ignored
npm test                                    -> 73 passed
cargo fmt --all -- --check                  -> passed
git diff --check                            -> passed
runtime\python.exe -m harness run docs      -> 2/2 cases passed
runtime\python.exe -m harness check WP-3-04 -> scope passed，全部失败 bucket 为空
```

新增回归覆盖 started/send-response 先后竞态、唯一终态、取消 handle、单 active interaction、额外 command
payload、非 main 窗口、secret/path-shaped 错误投影、generation 丢弃、timing 边界、保存失败恢复、重新读取
以及下一条回复才采用新 timing。既有固定 DOM、布局、DPI、portrait、IME、长文本内部滚动与 CSP 回归继续
通过。

## Harness 最终报告

`runtime\python.exe -m harness verify WP-3-04` 完整运行 182.797 秒，机器报告写入
`temp/harness/20260731T164230Z-WP-3-04.json`。结果为：

```text
preflight       -> passed；12/12 checks passed
docs            -> 2/2 cases passed
smoke           -> 3/3 cases passed
core-host       -> 2/2 cases passed
runtime-v2-shell -> 7/7 cases passed
python-full     -> 3/3 cases passed
acceptance      -> 22 passed / 0 failed / 3 manual pending
final status    -> manual_pending
```

第一次同命令运行因外层 120 秒命令时限在 `python-full` 阶段被中断，没有形成最终结论；随后以 600 秒
时限原样重跑并生成上述完整报告。该中断不是测试失败，也未被计入最终证据。

## 结论与剩余门禁

本地自动门通过，当前只能等待项目负责人完成三项人工验收：Windows 真实配置下的正常回复、错误、取消、
连续第二轮与打字机跳过；Sakura/N.A.V.I. 在 100%/150% DPI 下的冻结几何；同一候选 SHA 的三平台证据
与回退边界复核。

当前尚无候选提交、远端 CI 或人工验收证据，因此本记录不把 WP-3-04 标记为 `accepted`，也不改变
[`work-packages.md`](../../plans/runtime-v2/work-packages.md) 中的唯一状态。

## 人工反馈后的取消控件修复

2026-08-01 项目负责人在 Windows 实机聊天中确认正常回复可用，同时发现生成期间主按钮没有持续显示
取消态，再次点击表现为无响应。定位结果是同 generation 的 `ready` 生命周期刷新保留了 `thinking` 阶段，
却错误清除了 `canCancel`；真实客户端仍正确持有活动 operation，因此新的 send 被拒绝。

修复后，同 generation 的 `ready` 刷新会保留活动 operation 的取消或打字机跳过动作；generation 变化或
非 ready 生命周期仍会使旧动作失效。按负责人反馈，活动态图标在原按钮尺寸和命中区域内显示为可点击
取消的环形旋转条；减少动态效果模式下显示静态圆环。新增回归先复现 `false !== true`，修复后定向测试
9/9 通过，前端全量测试 76/76、docs 2/2、smoke 3/3、runtime-v2-shell 7/7 通过。该结果只证明自动回归，
Windows 真实取消仍等待负责人重新验证。
