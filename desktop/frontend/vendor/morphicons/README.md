# Morphicons

固定版本 [morphicons 1.7.0](https://www.npmjs.com/package/morphicons/v/1.7.0)，随包保留 [MIT 许可证](LICENSE)。`dom.js` 与两个依赖模块来自 npm 包的 `dist/`，不需要运行时下载。

`dom.js` 的本地补丁在切换动画目标时立即填入起始轮廓，避免同一帧连续切换使图标从原点坍缩后飞入。对应行为见[连续切换回归](../../tests/morph-icon-interruption.test.js)。

状态图标通过 `core/morph-icon.js` 使用，静态图标使用本地 Lucide 资源。
