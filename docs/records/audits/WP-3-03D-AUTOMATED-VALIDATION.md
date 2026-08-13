---
kind: record
status: recorded
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-08-14
---

# WP-3-03D Windows HostBackdrop 输入栏液态折射 PoC 自动验证记录

## 插入基线

2026-08-13，项目负责人要求开始液态玻璃实现。以
`f7e970e4e9961c8ed1362ba2340050148e3d1171` 为固定基线插入 WP-3-03D，并暂停 WP-4-04；其代码、测试
和既有证据未回滚。本包使用五字段 task v2，不创建 activation。

实现参考公开 MIT 项目 [Liquid Glass Studio](https://github.com/iyinchao/liquid-glass-studio) 的四阶段管线、
边缘 SDF、折射曲线和分步诊断思想，并在 HLSL 移植中保留上游版权与许可证说明；不复制其 React 编辑器、
演示资产或 WebGL 宿主。实现提交、自动命令、报告路径、候选路径和实际结果将在发生后追加。自动门通过
最多支持 `manual_pending`，人工视觉结论只记录负责人真实声明。

## 2026-08-13 DWM 安全事故

离散 Composition 候选在两次启动中均造成可复现的 DWM 崩溃风暴，因此该候选未进入视觉验收，且不得
再次启动。候选启动时间分别为 `23:26:30`、`23:39:54`，Windows Application 日志中对应的首次
`dwm.exe` 崩溃均发生在约 1.5 秒后。两轮共记录 26 次崩溃，签名保持一致：

- 故障模块：`dwmcore.dll` `10.0.22621.4830`；
- 异常：`0x8000000b` / `E_BOUNDS`；
- 偏移：`0x0000000000149127`；
- DWM 在 NVIDIA GeForce RTX 5060 上重启 18 次，随后降级到 Microsoft Basic Display Driver 并
  再重启 8 次；
- NVIDIA 与 AMD 显示适配器一度进入设备管理器错误码 43，NVIDIA 刷新率被错误报告为 1 Hz；正常
  重启 Windows 后由项目负责人确认显示恢复。

候选自身日志只记录到 12×8 个液态 sector 创建成功，随后系统合成进程开始崩溃。关闭候选后没有新的
DWM 崩溃。由时间相关性、重复签名和停止条件判断，包含 96 个全窗口
`HostBackdrop -> Border -> GaussianBlur -> 2D AffineTransform` 图的设计是直接触发因素。由于 WER
dump 受系统权限保护，本记录不把具体 `dwmcore.dll` 内部函数作为已证实事实。

结论：否决离散多 brush 架构，不得以降低模糊半径、减少 tint 或继续实机试错的方式恢复。危险实现已从
源码移除，旧环境开关只记录退役诊断，不能恢复该路径。后续实现必须把 Windows API 限制为单一动态背景
输入，并在一个应用侧 GPU 管线中完成参考项目的 SDF、模糊、折射、色散、Fresnel 和 glare；任何 GUI
实机运行都需要先通过资源预算和离线测试 Gate。
