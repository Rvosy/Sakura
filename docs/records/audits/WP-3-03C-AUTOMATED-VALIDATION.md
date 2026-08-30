---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-3-03C Windows 输入栏实时高斯玻璃自动验证记录

## 2026-08-13 契约插入

项目负责人要求暂停 WP-4-04、保留其已整合候选，并以提交
`1e2f2f9bb57645a964f0a71c417a9de9ae686129` 为固定基线插入 WP-3-03C。新 task 使用五字段 v2
契约且不创建 activation；自动门为 `docs`、`runtime-v2-shell` 与 `runtime-v2-window-surface`。

实现、测试、截图和 `harness verify WP-3-03C` 的实际结果将在发生后追加到本文。自动门通过最多支持
候选进入 `stabilizing`/`manual_pending`，不构成项目负责人人工验收或 `accepted`。

## 2026-08-13 实现与自动验证

生产实现提交为 `8b581b1a`。Appearance publication 升为 v3，UI schema v1 增加全局
`visual_effect_mode`，Windows capability 开放输入栏视觉效果；原生后端只创建 input region，在隐藏窗口
阶段初始化，并以 `8 × scale_factor × content_scale` 更新 Gaussian 标准差。PoC bubble region、环境启用
开关与鲜粉诊断样式已移除；强制失败开关更名为
`SAKURA_WINDOWS_INPUT_GLASS_FORCE_FAILURE`，失败保持纯色且不改写偏好。

定向验证结果：

- Rust `character_appearance`：10 passed；`product_shell`：9 passed；`windows_glass_poc`：8 passed。
- 前端完整 `node --test desktop/frontend/tests/*.test.js`：156 passed。
- 独立 `temp/wp-3-03c-target` Windows debug 构建成功；运行进程路径确认不是仍被占用的旧
  `desktop/src-tauri/target/debug` 产物。
- 首次 `harness verify WP-3-03C` 发现新增模式预览后测试仍断言旧次数，产品用例均未失败；修正测试计数后
  再次执行，报告 `temp/harness/20260813T143531.555475Z-WP-3-03C.json` 为 8/8 case 通过、0 failed、
  0 blocked，正确返回 `manual_pending`。

本机观察截图为被忽略的 `temp/wp-3-03c-screen.png`，不进入 Git，也不作为产品玻璃的数据源。未在本次
会话中执行 150% DPI 人工观察，不倒填该项；自动测试覆盖标准差 8→12 与既有 DPI/窗口几何契约。

## 2026-08-13 项目负责人验收

项目负责人在最新独立候选运行后明确回复：

> 可以,没问题, 切换也正常

该声明确认本次候选的显示与“纯色块 / 高斯模糊”切换正常，并授权完成本 Work Package 的人工验收闭环。
记录只保存负责人实际给出的结论，不扩写成未执行的 150% DPI、动态背景或逐项故障注入证据。Work
Package 当前状态仍只维护在[总计划](../../plans/runtime-v2/work-packages.md)。
