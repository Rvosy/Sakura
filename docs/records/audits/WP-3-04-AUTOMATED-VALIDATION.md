---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-08-02
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

## 2026-08-02 聊天表现一致性纠正

用户复核真实聊天后，WP-3-04 追加了五项以旧 Qt 为行为基准的纠正：启动问候 reveal 后逐字播放、等待
终态保持当前立绘、完整回复逐段清屏显示、右键菜单恢复中/日字幕选择，以及约 300ms、80% 重叠的双层
立绘交叉淡入。实现未修改 Python Core、legacy Qt、角色包、固定窗口几何、依赖清单或真实用户数据。

基线先取消 Git 对 `data/runtime_v2/config/ui.json` 的误跟踪，并保留用户本地文件；该路径与 `.codex/`
本地环境目录均已加入忽略规则。Windows 历史失败用例
`spawn_failures_and_repeated_release_do_not_leak_process_handles` 连续运行三次，结果均为 1/1 passed，因此
没有重新打开或修改进程树前置工作包。

纠正实现候选为 `989fc852a053d6c6d6f37a090acd54e94a2122ca`。定向与完整自动结果：

```text
runtime\python.exe -m harness run docs              -> 2/2 cases passed
runtime\python.exe -m harness run smoke             -> 3/3 cases passed
runtime\python.exe -m harness run unit              -> 580 passed / 6 skipped
runtime\python.exe -m harness run runtime-v2-shell  -> 7/7 cases passed；frontend 88/88
cargo test subtitle_language                        -> 3/3 passed
cargo test product_menu                             -> 4/4 passed
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check -> passed
runtime\python.exe -m harness verify WP-3-04        -> exit 3 / manual_pending
```

`verify` 机器报告为 `temp/harness/20260802T044917Z-WP-3-04.json`，运行 149.843 秒；preflight、scope、
依赖与 required profiles 全部通过，汇总为 22 passed / 0 failed / 3 manual pending。该自动结果包含三平台
workflow 契约的静态/测试门，不代表本纠正候选已经取得新的远端三平台同 SHA 运行证据。

剩余门禁仍由项目负责人完成：Windows 真实 Provider 下复核启动问候、等待立绘、多段中/日字幕、当前段
“立即显示”、取消/错误/连续第二轮与快速立绘切换；Sakura 和 N.A.V.I. 在 100%/150% DPI 下复核固定
几何与冷热缓存过渡；最后审查同一最终候选 SHA 的 Windows、macOS、Linux CI 和独立回退边界。本记录
不填写上述人工结果，不把 WP-3-04 标记为 `accepted`。

## 2026-08-02 等待与错误表现二次纠正

项目负责人在上一候选实机复核后追加三项反馈：等待回复不显示“正在组织完整回复”，恢复旧 Qt 点号动效
并在输入框显示角色名思考状态；Provider 400/429 等错误必须提供可诊断信息；移除“立即显示”按钮。
第三次契约修订继续沿用原始 `base_ref`，只窄开放 `app/core_host/real_chat.py` 的错误公开投影，没有改变
Provider 请求、重试、模型选择、history、legacy Qt、固定窗口几何、依赖或真实用户数据。

回归测试先稳定复现：前端捕获旧 skip DOM、缺失角色 placeholder 和缺失等待控制器；Core 400/401/429/500
四组用例均只得到 `Provider request failed`，坏 JSON 与坏回复结构均只得到
`Provider response was invalid`。实现后，等待气泡按旧 Qt 的七帧序列每 360ms 循环，reduced motion 固定为
`...`；输入框在 thinking 阶段显示“`{角色名}正在思考中…`”；skip DOM、状态与事件接线均已移除。
Provider HTTP 错误现在公开状态码以及 JSON `error.message/code/type/status` 白名单，经过 360 字符上限和
凭据、token、URL、绝对路径等敏感模式过滤；非 JSON 或无安全字段时只回退为含 HTTP 状态的稳定文案。

本地候选实现提交为 `20c862a8`（等待/无 skip）和 `f3bb8d75`（Provider 安全诊断）。自动结果：

```text
desktop/frontend npm test                            -> 91/91 passed
tests/integration/test_core_host_real_chat_integration.py -> 13/13 passed
runtime\python.exe -m harness check WP-3-04          -> passed / 无越界
runtime\python.exe -m harness verify WP-3-04         -> exit 3 / manual_pending
cargo test --locked --quiet                          -> 230 passed / 24 ignored
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml -- --check -> passed
runtime\python.exe -m py_compile app/core_host/real_chat.py -> passed
```

`verify` 报告为 `temp/harness/20260802T052916Z-WP-3-04.json`，运行 165.172 秒；preflight、scope、依赖与
required profiles 全部通过，汇总为 22 passed / 0 failed / 3 manual pending。其中 frontend 91/91、
Python unit 580 passed/6 skipped、Core Host integration 26/26、legacy Qt UI 24/24。

验证期间曾错误并行启动 Harness 与第二份 locked Rust 全量，两个测试进程争用同名 Windows mutex，造成
3 个 shared-instance/lifecycle 用例失败；确认测试进程退出后，未修改代码、未放宽或忽略测试，串行原命令
重跑为 230 passed/24 ignored。该次并行资源冲突不计作最终产品结论，但作为验证编排问题保留记录。

自动门通过后仍等待项目负责人在 Windows 实机验证：点号节奏、角色名思考 placeholder、无“立即显示”
控件、真实 Provider 400/429 安全诊断、取消与连续第二轮；Sakura/N.A.V.I. 的 100%/150% DPI 固定几何；
以及最终候选同一 SHA 的三平台 CI 和回退边界。本记录不代填人工验收，不改变 WP-3-04 的 `active` 状态。

## 2026-08-02 字幕即时刷新与回复导航纠正

项目负责人继续复核后发现：非播放状态切换中/日字幕不会立即改变气泡；气泡右上角仍有不需要的关闭
按钮；旧 Qt 的回复上、下翻阅及对应立绘同步尚未恢复；鼠标右键打开菜单时，“隐藏至托盘”因自动获得
焦点而持续呈现深色高亮。

第四次契约修订继续使用原始 `base_ref`，将回复翻阅冻结为当前 WebView 运行会话内的前端历史，不读取
持久聊天 JSONL，也不新增 Core history API。实现提交 `59132821` 在 reducer 中保存不可变回复段历史；
播放结束后启用 30px 主题化垂直回复轨道，翻阅时同时提交完整段文本和对应立绘；切换字幕时，活动段
从头重播，已稳定显示或正在回看的段立即完整刷新。取消提示和 Provider 错误不会被语言切换错误替换为
旧回复。气泡关闭 DOM 与点击接线已移除，原生窗口关闭协调保持不变。鼠标右键不再聚焦菜单首项，键盘
打开菜单仍聚焦首个可用动作。

实现后的阶段性结果：

```text
desktop/frontend node --test                       -> 97/97 passed
runtime\python.exe -m harness check WP-3-04        -> passed / 无越界
```

完整 Harness、Rust、Core integration 与文档门禁将在最终候选上串行重跑并追加到本节。回复翻阅的跨重启
恢复、旧聊天记录读取、TTS 重放和 Core history 协议均不属于本次纠正。本记录不代填人工验收，不改变
WP-3-04 的 `active` 状态。

## 2026-08-02 控制面板高度与撕裂纠正

项目负责人提供三张 Windows 实机截图，确认加入回复轨道后同类字幕的气泡高度发生变化；同时指出既有
气泡/输入栏自适应会出现表面撕裂，输入栏向上扩展时发送按钮先下沉到可见区域外再随外框展开。

定位到两个直接原因：回复 DOM 重组后，旧测量代码仍从 `bubble-copy` 读取已经迁移到 `reply-body` 的
8px 内容间距；textarea 测量把目标高度立即留在可见 DOM 中，但外层面板要等待原生精确区域确认，期间
网格先被撑开并受 `overflow: hidden` 裁切。原生区域确认后，bubble/composer 的 `top`/`height` 仍继续
160ms CSS 过渡，形成 DOM 与 Win32 精确区域的尾帧错位。

第五次契约修订未修改固定 layout contract。实现提交 `21c00467` 恢复 reply body 的原 flex 高度语义并
从该容器读取内容间距；测量后立即恢复屏幕上的 textarea，把目标 height/overflow 作为布局候选；原生确认
后在同一同步提交阶段更新子项与外层面板；普通自适应不再保留 top/height CSS 尾帧。overflow-only 变化
也进入候选 key，避免达到最大高度后漏开或漏关内部滚动。

阶段性自动结果：

```text
desktop/frontend node --test                       -> 100/100 passed
runtime\python.exe -m harness check WP-3-04        -> passed / 无越界
```

最终自动候选为 `24ce52d0b4e4232510af04986a0b08e25e12678f`。全部命令串行执行，避免 Rust/Harness
共享 Windows mutex 争用：

```text
desktop/frontend node --test                                      -> 100/100 passed
tests/integration/test_core_host_real_chat_integration.py          -> 13/13 passed
cargo test --locked --quiet                                        -> 230 passed / 24 ignored
cargo fmt --all -- --check                                         -> passed
runtime\python.exe -m harness run docs                             -> 2/2 passed
runtime\python.exe -m harness run smoke                            -> 3/3 passed
runtime\python.exe -m harness run unit                             -> 580 passed / 6 skipped
runtime\python.exe -m harness verify WP-3-04                       -> exit 3 / manual_pending
```

`verify` 报告为 `temp/harness/20260802T061114Z-WP-3-04.json`，运行 164.953 秒；preflight、scope、依赖
和五个 required profiles 全部通过，汇总为 23 passed / 0 failed / 3 manual pending。Harness 的
`EXIT_MANUAL_PENDING` 固定为 3；自动门没有失败项。

真实 WebView2 下的同文本气泡高度，以及输入栏 1→4→1 行时按钮底部锚定、无裁切/撕裂，仍必须由项目
负责人完成实机视觉验收；本记录不以 Node 几何测试代替该结果。
