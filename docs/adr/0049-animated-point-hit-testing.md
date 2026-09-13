---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-13
---

# ADR-0049：动画表现使用单点命中，宿主管理原生穿透

Windows 的静态立绘使用窗口区域同时处理显示裁剪和鼠标命中。Spine 的轮廓持续变化，逐帧沿用此路径需要生成整幅蒙版、合并区域并调用 SetWindowRgn，过期轮廓还会裁掉正在移动的部件。

动态表现保留稳定的显示表面，通过可选的宿主 `setHitTest` 服务回答鼠标位置是否可交互。Spine 缓存贴图 Alpha，直接采样正常绘制时的最终三角形；宿主负责坐标转换、控件优先级、请求寿命及原生事件归属。静态立绘路径继续使用现有蒙版。

首个原生后端为 Windows。原生层在穿透期间继续观察鼠标，只在角色范围内请求插件检测；一个请求在途，超时使用矩形命中。Tauri 的忽略鼠标接口只在结果变化时调用，不逐帧重建窗口区域。按键保持期间冻结事件归属，避免抢走底层应用的拖动或中断角色拖动。

此方案接受异步命中的短暂旧状态窗口。快速移动后立即点击可能遇到旧结果；不阻塞系统输入线程等待 WebView，也不重放点击。当前不采用整帧 GPU 读回或全局鼠标钩子。若真实使用中的误点不可接受，需要重新评估原生输入架构。

接口与边界见[表现插件契约](../specs/runtime-v2/visual-plugin-boundary.md)及 [Spine 契约](../specs/runtime-v2/spine-visual-plugin.md)。验证入口为 `spine-hit-test.journey.py` 的 GPU 输出对照，以及 `spine-native.journey.py` 的真实 WebView2、跨进程点击和性能采样；后者使用隔离数据和专用底层窗口。
