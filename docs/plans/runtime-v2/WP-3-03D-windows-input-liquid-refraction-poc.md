---
kind: plan
status: active
audience: maintainer
source_of_truth: ../../plans/runtime-v2/work-packages.md
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-13
---

# WP-3-03D Windows HostBackdrop 输入栏液态折射 PoC 计划

## 实施阶段

1. 固定 task v2、ADR、规范、计划和初始验证记录；暂停 WP-4-04 但不回滚其代码或证据。
2. 在现有 Windows input glass backend 内建立液态 PoC 开关、Snell 风格离散曲线和 12 条边缘带。
3. 为每条带建立方向 sector、HostBackdrop 采样变换和鲜粉诊断覆盖，保持中心现有高斯不变。
4. 加入正常模式 Fresnel/glare 视觉层和液态失败回退高斯，不改变设置或 Appearance 契约。
5. 完成 Rust/前端边界测试、独立候选构建、required profiles 和 `harness verify WP-3-03D`。
6. 自动门全绿后进入 `stabilizing`，由项目负责人完成动态桌面、DPI、拖动和边缘覆盖视觉 Gate。

## 退出条件

- 输入栏边缘能在高对比动态桌面上产生可辨认的弯折，中心保持稳定高斯和文本可读性。
- 鲜粉诊断证明外边界、12 条带和 sector 无漏带；关闭诊断后无可见分层裂缝。
- 拖动期间效果持续可见，松手不闪回旧位置；无黑块、轮廓或桌面递归捕获。
- 环境变量关闭时与 WP-3-03C 候选一致，液态失败时现有高斯仍可用。

## 回退

运行时移除 `SAKURA_WINDOWS_LIQUID_GLASS_POC` 即回到已验收高斯。代码回退只移除液态 visual、曲线和
诊断接线，保留 WP-3-03C HostBackdrop 后端。不得用截图或辅助 HWND 作为临时回退。

## 边界

- 不修改 `data/**`、`characters/**`、`third_party/**`、Legacy Qt、插件、Python Core 或设置契约。
- 不纳入用户已有的 `desktop/frontend/index.html` 换行修改。
- 参考实现只用于算法理解，产品代码独立重写，不复制其 WebGL renderer 或 shader 文件。
