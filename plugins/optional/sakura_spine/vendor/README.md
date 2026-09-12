# Spine 3.6 WebGL 运行库

来源为 [EsotericSoftware/spine-runtimes](https://github.com/EsotericSoftware/spine-runtimes/tree/654c20e5b0e523040b6366bbd1042510d2645134)，
提交 `654c20e5b0e523040b6366bbd1042510d2645134`，分支 `3.6`。

- `spine-webgl.mjs` 来自上游 `spine-ts/build/spine-webgl.js`，内容保留，仅在末尾添加 `export { spine };`，避免向 WebView 全局注入变量。
- `SPINE-LICENSE` 原样保留上游根目录 `LICENSE`，版本为 Spine Runtimes Software License v2.5。

该许可证包含开发、修改和分发的授权条件，不能按 MIT 等宽松许可证处理。对外发布此插件前需确认相应 Spine 授权，
分发时必须保留完整许可证。角色美术资源不随插件分发，来源于用户选择的本地文件。

上游提交仅用于锁定依赖版本。资源校验不读取或重算骨骼 JSON 中的 `hash` 字段。
