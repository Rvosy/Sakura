---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-29
---

# WP-5-06 Runtime v2 运行日志查看器

## 1. 范围

本规范交付 WP-5-06 中独立可验收的日志查看器切片。它提供一个由原生托盘打开的同 App WebView 窗口，
展示本次 Sakura 进程启动以来的用户可观察事件和完整 warning/error。查看器用于让普通用户直接阅读状态，
并通过截图或复制已脱敏摘要协助维护者排查。

本切片不读取历史或轮转日志，不提供搜索、导出、上传、目录按钮、清空日志、Repair、自动修复、自动重试或
更新前置检查。WP-5-06 的其余能力保持 `planned`。

## 2. 数据边界

- `RuntimeLogService` 继续执行 ADR-0012/0013 的 Rust 单写者、固定消息、属性白名单和凭据清洗；查看器不得
  形成第二个文件 writer，也不得回读 `sakura-runtime.log`。
- 已规范化事件在进入 writer 队列时同步投影到最多 400 条的内存环形缓冲。普通 info 只保留启动、聊天、
  模型请求、工具、截图、TTS、插件和 MCP 等用户能感知的阶段；warning/error 无条件保留。
- 展示 DTO 只能包含序号、`HH:MM:SS`、软件/TTS scope、等级、固定频道、稳定事件代码、固定中文消息、
  中文标签的安全详情和最多一个 8 字符关联编号。IPC 事件可将稳定的 `command` 作为“请求”详情展示，
  使同类完成记录能够区分具体操作；不得包含正文、Prompt、Memory、工具参数/结果、绝对路径、环境变量、
  凭据或原始异常对象。
- 窗口只通过 `runtime_log_viewer_bootstrap` 和带 `afterSequence` 的
  `runtime_log_viewer_snapshot` 增量读取。游标落后于已淘汰记录时返回完整当前缓冲及 `resetRequired=true`。
  两个 command 必须校验调用窗口标签为 `runtime-log`。

## 3. 窗口与展示

- 原生托盘提供“运行日志…”；重复打开时恢复、聚焦并刷新同一个非模态窗口。窗口不依赖 Core readiness，
  因此 Core 启动失败、重启或不可用时仍可查看 Shell 侧记录。
- 运行日志 WebView 关闭开发者工具与默认右键菜单；右键、F12 和常见检查器快捷键不得打开前端调试界面。
- 窗口保留“软件”和“TTS”页。`tts.service.*` 只进入 TTS；其余 TTS 用户阶段同时进入软件与 TTS。
- info 保持单行；warning 可展开；error 默认展开并优先显示诊断、错误代码、原因代码、阶段、错误类型、
  状态、耗时和关联编号。相邻完全相同记录合并为 `×N`。
- 增量轮询和同一次运行内的手动刷新只更新有变化的记录；必须保留既有记录的展开、选中、键盘焦点和滚动
  状态，不得通过整表重建重播动效或覆盖用户操作。
- 提供自动滚动、刷新、复制选中和关闭。用户向上滚动时自动滚动暂停；不提供清空操作。复制结果与界面使用
  同一展示 DTO，不重新读取内部属性。

## 4. 主题、动效与失败语义

- 页面使用当前角色的 11 个公开主题 token；Core/角色尚未就绪时使用同形默认 token。外观预览、保存、取消
  和重新发布通过现有 `sakura://character-appearance-changed` 事件热更新。
- 新建窗口在主题 token 与运行时字体状态应用完成前保持原生隐藏；成功或失败状态都只在对应主题成为 WebView
  首帧后显示，重复打开不得提前暴露主程序默认主题。
- 新记录、重复计数和详情展开只使用 120–240 ms 一次性动效；`prefers-reduced-motion` 必须关闭
  动效。主题色不得形成渐变或模糊辉光，warning/error 使用与主题混合的稳定语义色。
- 快照读取或事件订阅失败只在窗口内显示明确状态，不得改变日志写入、Core health、聊天、取消或退出结果。

## 5. 验收

- Rust 测试覆盖可见事件目录、异常保留、详情顺序、脱敏、400 条边界、游标重置和 TTS scope。
- WebView 测试覆盖 DTO 严格校验、增量合并、重复折叠、复制文本、模块入口、主题约束和 reduced motion。
- `journey-observability` 同时运行既有单写者/bridge 测试与日志查看器 Rust/WebView 测试。
