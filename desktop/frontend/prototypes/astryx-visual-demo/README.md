# Sakura Astryx 审美方向 Demo

这是一个只用于视觉评审的原型。

它不引入 Astryx、React 或新的组件系统，也不改动正式设置页面。Demo 直接读取当前分支的
`desktop/frontend/settings/index.html`、`settings.js` 和正式 CSS，再额外覆盖一层
`visual.css`，因此“视觉 Demo / main 对照”使用的是同一套页面结构和模拟数据。

## 打开

在仓库根目录运行：

```sh
runtime/bin/python -m http.server 8770 --bind 127.0.0.1 --directory desktop/frontend
```

Windows 可改为：

```powershell
runtime\python.exe -m http.server 8770 --bind 127.0.0.1 --directory desktop/frontend
```

浏览器打开：

```text
http://127.0.0.1:8770/prototypes/astryx-visual-demo/
```

## 怎么看

顶部可以直接切换：

- **视觉 Demo**：在当前 main 设置页上叠加 Astryx-inspired 的视觉处理。
- **main 对照**：去掉视觉覆盖，回到当前 main 的正式样式。
- **场景**：沿用现有 settings-demo 的角色/表现插件模拟数据，方便看普通立绘、多形态和缺插件状态。

建议先看“角色与布局”，再依次查看“模型服务 / 语音 / 插件 / 关于”，确认这套视觉语言能否覆盖
普通表单、管理台、状态卡和弹窗，而不是只对首页好看。

## 这一版刻意做了什么

视觉目标不是复制 Astryx，而是借它的审美方向给 Sakura 做一次产品化收敛：

- 页面背景从“浅蓝配置面板”改为中性暖灰白画布。
- 主文字改为接近黑色，Sakura Blue 只承担选中、主操作和状态强调。
- 去掉“左卡片 + 右大卡片 + 内层小卡片”的多层套框感。
- 左侧导航变轻，当前项使用白色浮层而不是大面积蓝色。
- 右侧只有真正的设置分组成为白色 Card。
- 增加页面标题、说明和分组留白，降低工程参数面板感。
- Input / Select / Button 使用更中性的表面和更柔和圆角。
- 保留 Sakura Pink，但只用于错误、缺失等少量强调。
- 插件页、弹窗、资源卡同步使用同一套视觉语言，避免 Demo 只优化角色页。

## 没有做什么

- 没有改变正式 DOM 与设置控制器。
- 没有改变任何业务逻辑、保存逻辑或插件合同。
- 没有引入 Astryx 源码或依赖。
- 没有把 Sakura 改成 Meta 风格后台；立绘、桌宠气泡和 Studio 仍应继续保留自己的角色感。

如果这个方向通过评审，再把其中有效的颜色、留白、卡片、按钮和排版规则逐项迁回正式样式即可。
