# 输入栏动效预览

用于评审语音、发送、附件菜单、输入栏展开和回复气泡的连续过渡。只模拟前端状态，不启用麦克风，不调用后端，不持久化或发送消息。正式前端已接入状态图标与语音内容过渡；附件旋转恢复原版 225° / 160ms。

在仓库根目录运行：

```sh
runtime/bin/python -m http.server 8768 --bind 127.0.0.1 --directory desktop/frontend
```

打开 <http://127.0.0.1:8768/prototypes/icon-motion/>。直接操作输入栏，或点击右侧演示按钮。语音演示会自动停止录音并回填固定文字；直接点击麦克风时，由用户结束录音。Esc 取消语音或停止回复。慢放和深色承载背景仅控制预览。

- 语音使用麦克风 → 加载环 → 实心停止方块 → 加载环 → 完成勾 → 麦克风的形变，同时过渡草稿、状态文字与模拟波形。取消和失败不显示完成勾，草稿与附件保留。
- 发送以短促位移接入加载环。鼠标停在刚点击的位置时继续显示加载环；移开再进入或键盘重新聚焦时，形变为 ×，点击始终可以停止。回复完成显示短暂勾，再恢复纸飞机；中途停止直接恢复纸飞机。
- 多行草稿与附件触发输入栏展开，按钮位置随高度调整。附件菜单、反馈和气泡采用短暂位移与透明度过渡。
- 全部动效不受操作系统减少动态效果设置影响。

图标形变实际使用 [Morphicons 1.7.0](https://www.npmjs.com/package/morphicons/v/1.7.0) 的纯 DOM 入口，采用 inline SVG，显式设置 `reducedMotion: "never"`。共享的 `../../vendor/morphicons/` 保留 npm 包的 `dom.js`、两个依赖 chunk 和 MIT LICENSE，未改写上游源码。包下载后按 npm registry 的 SHA-512 integrity 校验：

```text
sha512-MOqSK+O5RdxynER5016vUvvqQaHkqqWYmNpocsk9TEszUZ9PB/K52Yqq8AGTRK1NUO5dj8znGSEVf9slqEIQaw==
```

`../../core/morph-icon-data.js` 中的 Lucide 几何来自仓库 `assets/lucide/`，许可证随原资源保留。立绘复用此前语音原型中的 N.A.V.I. 图片。浏览器预览不能代替原生 WebView 的透明窗口、系统模糊和点击区域验收。
