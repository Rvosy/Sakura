---
kind: plan
status: active
audience: maintainer
source_of_truth: ../../plans/runtime-v2/work-packages.md
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-14
---

# WP-3-03D Windows 输入栏单管线液态折射 PoC 计划

## 实施阶段

1. 固定 task v2、ADR、规范、计划和验证记录；暂停 WP-4-04 但不回滚其代码或证据。
2. 删除并熔断造成 DWM 事故的离散多 brush 实现，记录事件日志、设备影响和恢复结果。
3. 建立单一 Windows Graphics Capture 输入、单 D3D11 device 和单 composition surface 的资源边界。
4. 等价移植 Liquid Glass Studio 四阶段 shader、SDF 折射、色散、Fresnel/glare 和鲜粉诊断阶段。
5. 加入捕获排除生命周期、设备错误永久熔断、帧丢弃、跨显示器重建和不冒充其他模式的显式故障状态。
6. 将 `liquid_glass` 作为第三种输入栏外观贯通配置、Appearance v3、设置即时预览/取消/保存和后端选择；
   高斯与纯色保持独立。
7. 因整窗 `WDA_EXCLUDEFROMCAPTURE` 破坏系统截图，先将捕获执行 fail closed；保留液态有效模式并关闭
   HostBackdrop 高斯层。评估能只隔离内部背景源且不改变系统截图的替代输入方案，独立决策后再开放
   液态执行。
8. 完成 HLSL 离线编译、固定纹理快照、Rust/前端测试、required profiles 和
   `harness verify WP-3-03D`。
9. 自动门全绿后进入 `stabilizing`；只有项目负责人再次显式批准，才可启动独立候选做系统安全和视觉 Gate。

## 退出条件

- 输入栏边缘能在高对比动态桌面上产生连续弯折，中心保持稳定高斯和文本可读性。
- 鲜粉诊断证明输入栏外边界无漏带；最终连续 SDF 结果无离散分层裂缝。
- 拖动期间效果持续可见，松手不闪回旧位置；无递归、黑块、轮廓或捕获指示边框。
- 未选择液态玻璃时与 WP-3-03C 候选一致；液态失败时不偷换高斯，保存偏好和有效模式不被改写，并返回
  可诊断的受限状态。
- DWM 不接收自定义液态 effect graph，资源计数不超过规范预算，事件日志无新的 DWM 或显示驱动错误。

## 回退

运行时在设置中选择“高斯模糊”即可回到已验收高斯；旧的 `SAKURA_WINDOWS_LIQUID_GLASS_POC` 不得恢复。
代码回退只移除 `liquid_glass` 设置值、单一捕获会话、D3D11 renderer、surface visual 和诊断接线，保留
WP-3-03C HostBackdrop 后端。不得恢复离散多 brush 图、GDI 截图或辅助 HWND。

## 边界

- 不修改 `data/**`、`characters/**`、`third_party/**`、Legacy Qt、插件或 Python Core；设置只扩展既有
  `visual_effect_mode` 枚举，不升级 schema。
- 不纳入用户已有的 `desktop/frontend/index.html` 换行修改。
- 参考实现是 MIT；复用 shader 数学与结构时保留上游版权说明，不复制其演示资产、React 编辑器或控制面板。
- 未经项目负责人新的明确批准，本包不得启动 GUI 候选；编译、HLSL 离线验证和非 GUI 测试不在此限制内。
