# WP-3-03：使用 Fake Core 的桌宠聊天表现层

```text
状态：active
激活日期：2026-07-26
前置依赖：WP-3-02 accepted
唯一产品边界：WebView 表现层 + 确定性进程内 Fake Core；不调用真实 chat Gateway、Provider 或 Python Assistant
回退边界：回退本 WP 的 frontend 表现模块、markup、styles 和测试，保留 Phase 1A Shell、窗口技术门与既有 Core lifecycle
```

## 目标与设计冻结

本 WP 在 Tauri WebView 内形成一条完全确定性的桌宠聊天表现链：初始问候、composer、思考、取消、
错误、重连、完整回复打字机、跳过动画、立绘表情和气泡收起。Fake Core 只发布与 WP-3-02 已冻结
终态形状相符的测试事件；它不读取用户配置、角色包、history 或网络，不产生产品数据写入。

视觉方向固定为“夜樱通信札”：透明桌面上的窄纸签气泡、墨紫正文和单一樱粉状态脉冲。立绘使用
WebView 自带的测试 SVG，不复制或改写 `characters/**`。唯一强调动作是回复期间从立绘到气泡的
状态脉冲；其余位移和淡入保持克制，并在 `prefers-reduced-motion` 下关闭。

## 所有权与状态机

- `fake-chat-core.js` 是测试 generation、operation identity、唯一终态和确定性场景计时器的唯一所有者。
  支持 normal、slow、error、cancel 和 restart；dispose/restart 必须清除计时器并丢弃旧 generation 回调。
- `chat-presentation.js` 只接受当前 generation/operation 的 started/completed/failed/cancelled 事件，
  投影为 composer、thinking、typing、settled、error/reconnect 表现；不保存业务 history。
- `typewriter.js` 只消费完整 `segments`，不消费 token/delta。skip 立即展示完整回复并只结束动画，
  不调用 Fake Core cancel；新回复、cancel、restart 和 dispose 使旧 timer callback 失效。
- `portrait-controller.js` 只负责受控同源 SVG 的预加载、generation token、decode/load failure fallback 与
  简单交叉淡入；tone/portrait 仅映射到白名单资源。
- composer 的 Enter 发送与 Shift+Enter 换行继续复用既有 IME composition 门禁。等待 Fake Core 终态时
  主按钮是“取消”；完整终态到达并进入打字机后，气泡内独立“立即显示”按钮只控制动画。

## 确定性场景

| 输入 | Fake Core 行为 | 必须可见的结果 |
|---|---|---|
| 普通文本 | 短延迟 completed | thinking → typing → settled；回显脱敏后的本地测试文本 |
| `/slow` | 长延迟 completed | 等待期间可输入、取消、拖动和关闭 |
| `/error` | retryable failed | 稳定错误文案；可重新发送 |
| `/long` | completed 长段 | expanded 气泡有界滚动，不逃逸窗口 |
| `/multi` | completed 多段 | 完整回复逐字显示，并随段切换 tone/portrait |
| `/restart` | 当前 operation cancelled，Core crashed → restarting → ready | 旧 generation 晚回调丢弃；重连后可再次发送 |

这些命令只存在于 WP-3-03 Fake Core，不进入 WP-3-04 的真实产品协议。

## 实施白名单与明确禁止

允许修改：`desktop/frontend/index.html`、`desktop/frontend/app.js`、`desktop/frontend/styles.css`、
`desktop/frontend/pet/**`、`desktop/frontend/chat/**`、`desktop/frontend/core/**`、
`desktop/frontend/assets/**`、`desktop/frontend/tests/**`、本文件、Work Package 总计划及仅用于接入新增
确定性前端门禁的 platform workflow/测试断言。

明确禁止：真实 `chat.send/chat.cancel` Gateway 接线、Python Assistant/Provider/history、Rust 通用
Operation、streaming、TTS、Tools/确认、Memory、MCP、插件、截图、Live2D/Canvas、高级动画引擎、
设置/Studio、`characters/**`、`data/**`、`runtime/**`、第三方目录和新增依赖。

## 自动验收与实机门禁

自动测试必须覆盖 Fake Core 五类场景、唯一终态、旧 generation/operation 丢弃、取消竞态、skip 与
Core cancel 分离、打字机晚 callback、portrait 快速切换/失败、theme 非法值 fallback、bubble timer、
IME、layout/hit-region/lifecycle 回归及 frontend source/markup 安全边界。

Windows 本机候选至少验证真实 Tauri WebView 启动、透明窗口、拖动、焦点、中文 IME、100%/150% DPI、
长文本、reduced motion、动画期间取消/关闭和 Fake Core restart。macOS 与 Linux 的真实 WebView、IME、
多屏和 compositor 体验仍是 WP-7-02 的发布硬门禁，不因本 WP 提前宣称完成。

## 状态迁移与回退

实现和自动矩阵完成后迁为 `stabilizing`；只有本机候选实机门禁通过、无 P0/P1 且改动/非目标/回退
证据完整时才能迁为 `accepted`。回退时先关闭窗口并清除 Fake Core/typewriter/bubble/portrait timer，
再逆序 revert 本 WP 提交；不得删除或改写角色资源、history 或任何用户数据。
