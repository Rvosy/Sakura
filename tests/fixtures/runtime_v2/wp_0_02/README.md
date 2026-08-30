# WP-0-02 脱敏兼容夹具

本目录只包含人为构造的占位数据，不来自真实 `data/`、角色包、聊天、Memory、notes 或插件数据。

- `dataset/` 映射到一个隔离 Sakura 根目录。
- 所有用户文本和 Memory 文本均使用显式 `REDACTED_FIXTURE` 占位符。
- API endpoint 使用保留域名 `fixture.invalid`；没有可用 API Key、Token 或凭据。
- Qdrant、SQLite、图片、音频、模型、TTS bundle 和插件任意二进制只记录缺失原因，不提交真实样本。
- `data/runtime_v2/` 是 WP-0-02 提议的 v2 专属命名空间，只是契约夹具，不是 Tauri 实现。

夹具只能复制到 `temp/runtime-v2-wp-0-02/` 后执行写入和故障注入。
