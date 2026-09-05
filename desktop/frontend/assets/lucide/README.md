# Lucide 图标与动效

Sakura 的功能图标统一使用 [Lucide 1.41.0](https://github.com/lucide-icons/lucide/releases/tag/1.41.0)。
本目录按需收录该版本 `icons/` 下的原始 SVG，保留 24 × 24 网格、2px 线宽及完整 [许可证](LICENSE)。
Sakura 标识、服务商品牌标识和角色图片保留原资源。设置页的图标只显示轮廓，不单独添加圆角底座。

TTS 提供方统一使用 `audio-lines`（声波），包括 Genie 和 GPT-SoVITS；`speech`（侧脸声波）预留给语音输入插件。

静态图标由 `core/icons.css` 以 CSS mask 呈现，颜色跟随 `currentColor`，无需联网或额外构建。
HTML 使用 `sakura-icon icon-brain` 等类；动态节点使用 `core/icons.js` 的 `createIcon` 或 `iconMarkup`。
图标本身对辅助技术隐藏，按钮保留可读文字或 `aria-label`。

插件可声明 `presentation.icon`，从 `core/icons.js` 的目录选择名称。未声明或未收录的名称按分类回退，
不加载插件提供的图片、URL 或 SVG。新增资源时，从固定版本复制原文件，并同步 `icons.js` 和 `icons.css`。

## 动效

记忆准备提示采用 [Lucide Animated](https://github.com/pqoqubbw/icons) 的 BrainIcon 描边与呼吸动画。
发送图标的短促位移、设置图标的旋转等也参考该项目；其 [MIT 许可证](ANIMATED-LICENSE) 随包保留。
采用的是原生 CSS / Web Animations 适配，未引入 React/Motion。对应实现为 `core/animated-icons.js`、
`core/icon-motion.css` 和 `chat/composer-action-indicator.js`。

- 发送使用垂直重心居中的 `send-horizontal`。进入既有忙碌状态时，纸飞机向右飞出，接续旋转指示；悬停或键盘聚焦显示停止图标。动画不延迟发送或停止。
- 导航、设置、下载、刷新等图标在悬停或键盘聚焦时反馈一次，离开后复位。展开箭头继续跟随控件真实状态。
- 记忆动画只在现有准备状态播放，就绪或失败时随原有状态提示替换，不虚构处理步骤或百分比。
- 系统选择减少动态效果时，保留图标与状态文字，关闭图标动画。

这些动效只表达交互和已有状态，不改变插件设置、保存、下载任务或记忆业务逻辑。
