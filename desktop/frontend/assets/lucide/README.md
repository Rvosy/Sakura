# 图标与动效资源

功能图标按需收录 [Lucide 1.41.0](https://github.com/lucide-icons/lucide/releases/tag/1.41.0) 的原始 SVG，随包保留[许可证](LICENSE)。图标在本地加载并跟随主题颜色，按钮提供可读文字或无障碍名称。

新增图标同步 `core/icons.js` 的目录和 `core/icons.css` 的资源引用。插件的 `presentation.icon` 使用目录中的名称，未收录时按分类回退。品牌标识和角色图片使用自己的资源与许可。

描边和呼吸动效参考 [Lucide Animated](https://github.com/pqoqubbw/icons)，保留其 [MIT 许可证](ANIMATED-LICENSE)。状态形变由 [Morphicons](../../vendor/morphicons/README.md) 提供。

动画表达实际操作状态，不延迟发送、停止或保存；同一状态不反复播放，状态切换和销毁时结束旧动画。
