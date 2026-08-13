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

## 2026-08-14 单管线替代实现（自动检查）

替代实现已按 ADR-0019 落为独立 Windows 后端：只有新的
`SAKURA_WINDOWS_LIQUID_GLASS_SINGLE_PIPELINE=1|true|on` 才创建一个当前显示器 WGC session、一个 D3D11
device、两个中间纹理、一个双缓冲 composition swap chain 和一个 surface visual；旧开关仍只输出退役诊断。
shader 结构等价移植 Liquid Glass Studio 的纵向高斯、横向高斯、圆角矩形 SDF、折射、色散、Fresnel、
glare 和分步诊断，并保留 `Copyright 2024 Charles Yin` 与 MIT 说明。默认路径不创建任何液态资源。

本轮只执行非 GUI 检查，未启动 Sakura 候选：

- `cargo check --manifest-path desktop/src-tauri/Cargo.toml --all-targets`：通过；
- `cargo test --manifest-path desktop/src-tauri/Cargo.toml windows_liquid_glass --no-fail-fast`：12 项通过，包含
  静态禁用 token、三模式配置、截图安全回退、完整扩展纹理 viewport 与清屏测试；
- `cargo test --manifest-path desktop/src-tauri/Cargo.toml windows_glass_poc --no-fail-fast`：8 项通过；
- `cargo test --manifest-path desktop/src-tauri/Cargo.toml character_appearance --no-fail-fast`：10 项通过；
- `npm test --prefix desktop/frontend`：156 项通过；
- `runtime\python.exe -m harness run docs`：2/2 通过；
- Windows SDK `fxc.exe` 分别编译 `vs_main/vs_5_0`、`ps_copy/ps_5_0`、`ps_blur/ps_5_0`、
  `ps_liquid/ps_5_0`：四项通过。

自动证据只证明源码边界、编译、shader 语法、资源预算、坐标数学和既有高斯回归；不证明 WGC 权限、
显示驱动稳定性或视觉正确。遵照负责人“不要启动”的要求，动态桌面、拖动、DPI、跨屏和故障注入仍为
明确未执行的人工 Gate，项目状态不得据此标记为 accepted。

## 2026-08-14 截图兼容性观察与安全回退

项目负责人实机观察到液态候选中间区域显示异常，且系统截图完全看不到桌宠。源码复查确认候选对整个
Sakura 主 HWND 设置 `WDA_EXCLUDEFROMCAPTURE`；该属性是窗口级全局捕获语义，不仅影响内部 WGC，也会
让普通系统截图排除角色与输入栏。WGC 没有“只对本 capture session 排除 Sakura”的接口，因而不能在
同一主 HWND 上同时满足无递归背景与正常截图。

修复候选保留“液态玻璃”设置值，但在安全隔离输入可用前以
`LIQUID_GLASS_CAPTURE_ISOLATION_UNAVAILABLE` 回退现有高斯；不创建 WGC/D3D 液态资源，也不设置主窗口
display affinity。该回退消除了已确认的截图语义破坏和异常液态合成路径，但不构成液态视觉验收。

负责人补充的实机照片显示输入栏中部存在一条未生效细带，边缘保留启动时黑色背景。该表现与整窗捕获
排除返回空/旧窗口区域一致，不能通过提高折射参数修复。后续 shader 候选按负责人提供的 Liquid Glass
Studio 设置采用 thickness 20、factor 1.4、dispersion 7、Fresnel 30/20%/20%、glare
30/20%/90.36%/50%/80%/-46.1°、blur radius 10 和透明白 tint；交换链每帧清透明并继续坚持首张有效帧
后才切换 visual。上述参数仅完成静态接线，因安全回退未做 GUI 视觉声明。

静态复查还发现两个 blur pass 虽已分配带四倍 sigma 边距的纹理，viewport 却仍使用输入栏本体尺寸，
使扩展纹理部分像素从未写入；最终合成采样这些未初始化区域可以直接解释黑色残边与无效果细带。实现已
改为以完整 `blur_size` 绘制并在每个 pass 前清透明，新增静态回归测试锁定该覆盖契约。

提交 `f38c43f9` 后执行 `runtime\python.exe -m harness verify WP-3-03D`：8/8 自动用例通过，报告为
`temp/harness/20260813T170911.936016Z-WP-3-03D.json`，状态 `manual_pending`。按本包规范，尚未运行新的
GUI 候选，也未完成动态背景、截图可见性、拖动、DPI、跨屏及显示系统安全 Gate，因此不能标记 accepted。
