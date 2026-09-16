# Morphicons

固定版本 [morphicons 1.7.0](https://www.npmjs.com/package/morphicons/v/1.7.0)，随包保留 [MIT 许可证](LICENSE)。
`dom.js`、`normalize-CYnN3Npw.js` 和 `spring-CFHloqPP.js` 原样取自 npm 包的 `dist/`，没有运行时依赖或构建步骤。

下载地址：<https://registry.npmjs.org/morphicons/-/morphicons-1.7.0.tgz>。已校验 npm registry 的 SHA-512 integrity：

```text
sha512-MOqSK+O5RdxynER5016vUvvqQaHkqqWYmNpocsk9TEszUZ9PB/K52Yqq8AGTRK1NUO5dj8znGSEVf9slqEIQaw==
```

Sakura 通过 `core/morph-icon.js` 驱动少量 inline SVG 状态图标，显式设置 `reducedMotion: "never"`，不跟随操作系统减少动态效果设置。静态图标继续使用本地 Lucide CSS mask。
