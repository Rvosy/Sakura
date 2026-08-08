---
kind: plan
status: stabilizing
audience: maintainer
source_of_truth: self
status_source: work-packages.md
updated: 2026-08-08
---

# WP-3-03A 跨平台桌宠动态表面实施计划

1. 冻结 schema v3、逻辑 surface snapshot、动态包络和锚点换算，以纯前端/Rust 测试锁定。
2. 合并布局、alpha、缩放、菜单和表情过渡为单一 revision transaction，前端只在原生成功后提交画面。
3. 保留并加固 Windows region，新增 macOS 光标路由和 Linux GTK/GDK input shape。
4. 在 stabilizing 阶段阻止 WKWebView 对立绘图片的默认拖拽和选择，只给气泡、输入框保留主题化文本
   选区；该修正不改变原生命中区域、拖动授权或窗口几何。
5. 修正缩放稳定化回归：固定按完整 900×996 视口计算 `content_scale`；同一立绘的缩放预览固定使用
   150% alpha 动态包络，几何相同的 revision 只提交实时精确命中，不调用原生 bounds、WebView offset
   或桥接区域。真实几何变化仍由 Rust 预提交 `active_bounds`；指针读取同一 offset，旧 revision 返回
   空结果。设置窗口用 pointer/keyboard 手势显式控制稳定包络生命周期，Rust 在手势活跃期间拒绝收口；
   松手、取消或失焦后只允许最新 revision 以当前倍率真实包络收口，消除顶部透明空间及 50%–55%
   慢速拖动时由时间防抖误判造成的向上闪动；
   macOS 不采用被 WRY 忽略的独立 WebView bounds，也不依赖 eval 与 placement 的排队顺序。
6. 扩展 Harness profile 与 Windows 门，补 macOS、X11/XWayland、native Wayland 分列验收和诊断证据。
7. 自动门通过后写验证 record 并进入 stabilizing；负责人实机验收前不标记 accepted。

每阶段失败均保留上一版窗口和命中 surface，前端不提交失败 revision。整体回退恢复此前动态表面
实现，不触碰用户数据和角色资源。
