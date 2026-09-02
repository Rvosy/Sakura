---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: ../../plans/runtime-v2/work-packages.md
updated: 2026-09-01
---

# WP-3-03A：跨平台桌宠动态表面与精确命中规范

## 必须行为

- 900×1374 只作为规范坐标系；其中立绘与默认控件仍使用原有 900×996 坐标和缩放基准，底部扩展只用于容纳
  0.9.10 合法布局极值与三行输入框。原生窗口必须取立绘可见 alpha、可见气泡、输入框、菜单和其他可见
  控件的动态视觉外接矩形。全局安全边距为 2 逻辑像素，外部 focus outline 额外预留 4 像素。
- 隐藏组件不得占用视觉包络或输入区域。alpha 值大于零的立绘像素参与命中，透明洞和外围必须穿透；
  仅 Windows 缩放预览活跃期间允许按下述性能事务短暂放宽，预览结束后必须恢复精确穿透。全透明
  立绘是合法资源，对视觉包络和立绘拖动区域的贡献为零，但当前可见气泡、输入框和控件仍必须参与。
- 动态控制面提交保留气泡和输入栏的规范矩形，并分别携带 `bubbleVisible`、`inputVisible`。矩形用于恢复
  位置和后续测量；逻辑包络、命中区域、菜单入口及原生视觉层只消费可见组件。共享契约不得包含 HWND、
  AppKit 或 GTK 专用状态。
- Windows 启用 0.9.x 显隐交互。气泡在回复进入 `thinking`/`typing` 时显示，回复稳定后按设置倒计时；悬停
  立绘、可见气泡或输入栏会暂停并重置倒计时，单击立绘可唤回气泡。输入栏在悬停时显示，有焦点、有文本
  或附件工具未完成时保持显示。指针在立绘、气泡和输入栏之间换靶时，整体离开状态延迟 80ms 发布，期间
  进入另一表面必须取消离开。开始原生拖动时输入栏可以收起，拖动结束后按当前悬停和固定条件重算。
- 淡入前先提交包含目标组件的原生 region，再显示 WebView 元素。淡出时先播放 220ms WebView `opacity`
  动画；收到该元素的 `transitionend` 或 `transitioncancel` 后，再等待两个绘制帧，才可提交不含该组件的
  region。动画事件缺失时使用 320ms 超时，减少动态效果时跳过事件等待但仍保留两个绘制帧。气泡和输入栏
  分别使用修订号；旧淡出完成后不得提交或夹带另一组件的目标状态。提交失败保留上一版原生区域并恢复
  与其一致的 WebView 可见性。Windows 的稳定 HWND/WebView backing envelope 不随显隐改变。
- 输入栏淡出开始时，Windows Composition 高斯容器必须与 WebView 元素使用相同的 220ms `opacity` 时长
  和 `ease` 曲线；不得等 WebView 动画结束后再二值关闭原生层。`inputVisible=false` 提交完成时，原生高斯
  容器、Gaussian visual 和液态采样请求必须全部隐藏。再次显示时先以零透明度恢复输入栏几何和采样位置，
  再同时淡入原生层和 WebView 输入控件，不得留下不可见点击区或玻璃残影。
- macOS/Linux 本阶段不开放自动显隐。两端继续提交 `bubbleVisible=true`、`inputVisible=true`；后续迁移
  复用同一显隐控制器和共享包络算法，只补平台窗口事务、输入路由和原生材质验收。
- 立绘底部中心的物理屏幕坐标在表情、缩放、气泡高度、输入高度、菜单和 DPI 更新中保持不变。
- 动态包络只允许改变顶层原生窗口的裁剪范围；900×1374 规范坐标不得随当前 alpha 包络重缩放。
  气泡、输入框及立绘锚点在缩放事务的任何可见中间帧中都不得抖动、闪跳或短暂错位。
- 同一立绘在 50%–150% 缩放预览中必须使用其 150% alpha 外接矩形与当前控件的并集作为稳定动态
  包络。Windows 一旦取得有效立绘资源，底层 HWND/WebView 必须常驻覆盖完整规范立绘槽、全部合法缩放和
  控件布局极值的稳定包络；该矩形不得依赖当前表情或角色的 alpha 外接范围。表情淡入和角色切换只允许
  更新精确 window region，不得 resize/reposition HWND 或改变 WebView surface offset。静止态仍由当前倍率精确 window region
  裁出真实视觉和点击范围。手势开始只允许清除一次旧复杂 region，不得 resize/reposition HWND 或改变
  WebView surface offset。手势期每个数值刻度必须经独立轻量帧通道直接更新 WebView 合成 transform，
  不得进入完整外观预览、原生 bounds、alpha 行段构建或 `SetWindowRgn`。手势活跃期间任何精确 region
  提交都必须被拒绝。松手、取消或失焦后，最新 revision 只恢复一次当前倍率的精确 region；从放宽状态
  恢复时不得先提交过渡桥接 region，也不得改变稳定 HWND placement。旧 revision 不得生效。若下一轮
  pointer/key 手势在上一轮预览队列排空前开始，两轮必须共享已开启的原生 guard，不得在中间发布
  `active=false` 或产生无 guard 刻度。轻量帧允许丢弃旧值并有界追赶最新值，单帧失败不得显示连接报警；
  最终完整外观预览仍须可靠提交最新值。macOS 只在缩放手势活跃期间使用当前控件布局与 150% 立绘的
  稳定缩放包络；手势刻度不得调用原生 bounds 或 WebView offset，但必须按当前倍率更新精确光标路由，
  透明余量和 alpha 洞始终穿透。松手、取消或失焦后由最新 revision 一次把原生窗口收紧到最终倍率
  立绘 alpha、气泡、输入框和其他当前可见控件的并集，同时提交最终精确路由。静止态不得保留 150%
  顶部余量；控制面板布局变化仍按真实需要更新包络，不得套用 Windows 的全部布局极值包络。Linux
  必须使用相同的手势临时包络生命周期，但通过 GTK/GDK 实现：开始前先预提交目标 stage offset、
  `active_bounds` 和 revision，再只调整一次原生 bounds；刻度不得 resize/reposition 或改变 WebView
  offset，必须立即显示合成 transform，并以 latest-wins 单槽队列把 surface-local 精确 input region
  追到最新倍率。结束时必须清空待执行旧帧，以新 revision 一次收紧到当前倍率立绘 alpha 与所有当前
  可见控件的真实并集，并恢复最终精确 region。短暂帧失败不得显示内部连接错误；旧 revision、旧手势
  的结束回调或 configure 结果不得覆盖新手势。
- 普通立绘切换的 CSS 交叉淡入在 macOS 上必须使用旧、新立绘 150% alpha 包络与当前控件的并集作为
  临时原生包络；原生窗口 frame 不得随单个表情的可见边界在过渡中反复收缩或移动。过渡期间只更新
  WebView stage offset 和精确命中快照；新立绘完成视觉提交并至少获得一帧绘制机会后，才按最新 revision
  更新最终命中区域和真实动态包络。过期或取消的 transition 不得提交最终 geometry，也不得让缩放手势
  重新使用该 pending transition。macOS 普通静止态和动态布局不得常驻该临时包络，也不得使用 Windows
  的全部控件布局极值。
- 右键产品菜单完成 WebView 测量后，必须把当前动态 `active_bounds` 与菜单矩形求并集，并以一次
  可逆的 native surface 事务扩展窗口；菜单矩形不得被裁到打开前的动态窗口边界。Windows 必须在扩窗
  前清除复杂 window region，并在菜单打开的整个事务中保持整窗 region，禁止按调整前或调整后的
  surface-local alpha 坐标重建复杂 region；macOS/Linux 继续随扩展表面提交精确平台命中区域。菜单
  关闭或动作执行时，必须恢复最新基础布局的窗口 placement、`active_bounds` 和精确命中区域。菜单打开
  期间发生普通布局或组件显隐更新时，只更新基础布局快照，并继续保留菜单矩形和菜单 region；不得收紧
  region 或主动关闭菜单。Windows 恢复精确 region 失败时必须清除 region，退回整窗可交互状态，不能保留
  与当前 HWND 尺寸不一致的旧复杂 region。菜单扩展只允许沿当前 surface 的右侧/底部增长，窗口左上角、物理立绘锚点、canonical
  stage offset 与 `content_scale` 保持不变；异步原生拖动完成后，菜单事务必须使用移动事件同步后的
  placement。原生扩展开始前，若输入框仍有焦点，WebView 必须先主动清理焦点，避免 AppKit 的窗口尺寸
  事务与输入控件 focus/blur 同时发生。以左键或中键在菜单外按下关闭菜单时，该次 `pointerdown` 只用于
  关闭菜单，必须在捕获阶段消费，不得继续触发桌宠原生拖动或控件动作。若菜单无法在规范 viewport 内
  表达，必须拒绝事务并保持上一版表面。
- `bubbleMaxHeight` 持久化字段继续兼容既有配置。默认固定模式把它解释为对话框的精确高度；用户开启
  `bubbleAutoExpand` 后，它改为最低高度，回复逐字输出、历史切换或语言切换可按当前内容向上平滑扩展，
  只以当前规范窗口上沿作为硬边界，自动模式始终禁用正文内部滚动。每次高度变化必须连续插值并保持气泡
  底边、输入栏屏幕坐标与立绘锚点不动；macOS/Linux 必须在开启自动扩展时一次预留当前布局可达到的
  透明原生包络，不得随逐字高度变化反复 resize/reposition 原生窗口；
  `prefers-reduced-motion` 下直接提交最终高度。输入框仍可按输入行数在契约范围内自适应。设置页拖动
  `controlPanelWidth`、`bubbleMaxHeight`、`controlPanelVerticalOffset` 或
  `inputBarOffset` 时，沿用 0.9.10 的调整范围：控制组宽度 420–860、气泡固定/最低高度 96–260、控制组
  垂直偏移 -200–200、输入栏偏移 0–200。Windows 的底层 HWND/WebView 包络必须同时覆盖这些布局极值与
  50%–150% 立绘倍率。Windows 每个数值帧必须立即更新 WebView 布局，不得等待 region 放宽、完整 appearance
  publication 或原生布局回包。每轮手势只允许一次最终原生布局提交和一次精确 region 恢复。
  每个合法偏移值必须产生对应的真实控件位移；不得因为三行输入框预留或规范 viewport 底边而在
  -27 等中间值提前钳住气泡位置。输入框需要的额外底部空间应由动态原生包络承担。
  macOS 不具备 Windows 的全部布局极值包络；macOS/Linux 的布局手势不使用立绘缩放专用的稳定包络，
  布局轻量事件仍必须逐帧提交对应命中模型，真实控件布局超出当前包络时必须更新原生表面，不得永久
  停留在仅 DOM 预览状态。
- Windows 的动态气泡收缩必须在 WebView 动画结束后再收紧精确 window region，并由同一 layout revision
  守卫延迟提交；扩展时先提交包含目标气泡的 region，再播放 WebView 动画。不得因 region 提前收紧裁掉
  仍在收缩的旧气泡上沿。
- 立绘有效 alpha 像素与气泡的非交互空白可启动拖动。气泡中实际渲染的回复文字、
  输入框和控件不得启动拖动。WebView 必须先按 DOM 目标区分文字/滚动条/控件与空白，Rust 再按当前
  revision 和规范起点复核立绘或可见气泡区域。
- 立绘当前帧和过渡帧不得触发 WebView 图片拖拽或元素选择。只有气泡内实际渲染的回复文字和输入框
  文本可选择；气泡 padding、正文剩余空白和空行外区域不得进入文本选择。两类文字的选择高亮必须
  使用当前角色主题色，不得回退为平台默认颜色。
- 一次 revision 必须包含布局、可见性、alpha、顶层窗口、规范舞台偏移和平台应用结果；舞台偏移
  必须在对应窗口 bounds 前预提交，失败时与窗口、命中区域一起恢复上一版。旧 revision 不得返回
  可重新提交的几何，指针分类必须读取已预提交的 surface offset。
- 原生窗口 bounds 提交可能同步产生窗口移动事件；该事件不得等待或重入当前几何事务。程序化移动由
  提交方写入 session，只有未被事务占用的拖动移动事件才观察 deferred drag 位置。
- 精确命中区域必须携带同 revision 的目标物理 envelope；Windows、macOS、Linux 应使用该值裁剪和
  路由，不得以 resize 后的即时窗口 readback 代替，否则首次扩大窗口可能按旧尺寸截空控件。

## 平台契约

- Windows 静止态和非缩放预览使用精确 Win32 window region，不得把复杂 alpha 退成 bbox。只有受
  revision 和显式手势约束的缩放预览，以及右键自绘菜单从打开到关闭的完整 resize 事务，可以临时
  清除 region。缩放预览必须由最新 revision 在手势结束时恢复；菜单必须在恢复打开前 HWND placement
  后恢复打开前的精确 region，恢复失败则保留整窗可交互安全回退并显式报错。桌宠在混合 DPI 显示器
  之间实时拖动时，`WM_DPICHANGED` 必须在 WebView/窗口按新 DPI 调整的同一消息链中同步变换当前精确
  region；不得保留旧物理坐标裁剪到松手后的最终布局提交。松手提交必须使用目标 DPI 下的本地锚点
  反推全局锚点并保持释放时 HWND 的物理左上角，不得用源显示器的本地锚点造成左上或右下瞬移。
- macOS 根据当前逻辑命中模型切换 `NSWindow.ignoresMouseEvents`，透明点必须交给下层窗口；缩放手势
  的稳定包络不得整体变为可交互，必须随当前倍率替换精确路由，结束后再随真实包络提交最终路由。
- Linux X11/XWayland 使用 GTK/GDK input shape 并满足完整契约；需要同时改变位置与尺寸时，必须优先
  使用现有 GDK 单次 `move_resize`，不得退回分离 `set_size`/`set_position` 或新增底层 X11 依赖。
  native Wayland 同样应用 surface-local input region，但因无全局 surface 坐标，只允许手势开始和结束
  各至多一次必要 resize，位置由 compositor 管理，并发布 `wayland_degraded_anchor` 诊断。GTK cairo
  region 必须由同一目标 snapshot 按 GDK scale 换算，不得在异步 configure 后立即读取 `inner_size`。

## 验收

- 外围和内部透明洞点击到达背景窗口；有效 alpha、输入框和菜单由桌宠接收。
- 冷启动的首次 bounds 提交不得阻塞窗口事件循环；主窗口必须在 15 秒内可见并保持响应。
- 有效 alpha 与气泡非交互空白可拖动，实际回复文字、滚动条、输入框和控件不能拖动；可见立绘顶部
  可距工作区顶部不超过 2 逻辑像素。
- 拖动立绘或气泡空白不得出现矩形选择层或图片拖拽预览；气泡实际回复文字和输入框文本仍可选择、
  复制且高亮跟随主题。
- 连续拖动缩放滑块 50%→150%→50%，气泡与输入框的全局物理坐标必须保持不变且无中间错位帧。
- 对话回复从空文本增长到超过可视范围时，对话框外框高度必须保持设置值；连续拖动四个布局滑块时
  第一帧即可见、高频刻度不闪回，最终 DOM、原生表面和精确命中均等于最后一个值。Windows 首次拖动
  与后续拖动的事件路径相同，不得要求重复拖动后才响应。
- 以超过 120ms 的慢速刻度间隔往返拖动 50%↔55% 时，手势中途不得误恢复精确 region 或向上闪动；
  只在真实 pointer/key 手势结束后恢复一次。
- 上述缩放循环的活动预览中 `active_bounds`、物理窗口 placement、本地立绘锚点和 `content_scale`
  必须逐次相等；Windows 不得逐刻度重建 region 或以旧 region 裁剪新视觉帧，macOS 不得逐刻度更新
  原生 bounds 或 offset，但精确光标路由必须随每个实时倍率变化；Linux 同样不得逐刻度更新原生
  bounds 或 offset，精确 GTK input region 必须 latest-wins 追随实时倍率。停止后 Windows 继续使用
  稳定包络，macOS 与 Linux 必须收紧到最终倍率立绘和当前控件的真实并集。物理立绘锚点保持不变，
  可见顶部仍可贴近工作区上沿。连续快速点拖与一次轻量帧失败都不得显示连接错误或回放旧倍率。
- 各状态循环 20 次，支持混合 DPI、负坐标多屏且完整平台路径的物理锚点漂移为零。
- Windows 现有真实透明穿透门保持通过；macOS、X11/XWayland 分别提供实机证据，native Wayland 单列，
  且不得把 compositor 管理的位置能力记录为全局锚定通过。自动回归由
  `window_surface_regression` Rust tests 和 `runtime-v2-window-surface` Harness profile 覆盖。

## 非目标

不修改角色资源、用户布局配置、聊天协议、设置业务或 Legacy Qt 实现。
