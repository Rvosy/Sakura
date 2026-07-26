# WP-3U-02：角色包可见能力与外观设置联动

```text
状态：planned
前置依赖：WP-3U-01 accepted
主要结果：优先迁移当前角色包中用户可直接看到的表现能力，并让同 App 设置窗口的角色外观页真实可用
数据边界：只允许批准的角色外观/ui 配置兼容写入；characters/** 只读
回退边界：关闭角色外观保存命令，设置窗口退回能力门控壳，保留当前角色只读展示
```

## 目标

在真实聊天进入 UI 前，先完成角色包的可见表现闭环：角色名、初始消息、主题、默认立绘、表情资源映射、
立绘切换、布局预览和外观配置。真实聊天随后只需把回复段中的 portrait/tone 投影到已经接受的表现层。

本 WP 不实现运行中角色切换、历史分页或完整 Session 重建。设置页中的角色选择控件必须隐藏或明确
禁用，并说明将在 WP-5-03 接入；不得保存 `current_character_id`。

## 能力范围与优先级

第一优先级：

- `display_name` 和 `initial_message`。
- 当前角色 `theme`。
- `portrait.default`。
- `portrait.expressions` 的逻辑键、真实图片和 fallback。
- Fake Core 多段回复中的 portrait key 驱动真实立绘切换。

第二优先级：

- 当前角色立绘缩放和受约束的布局预览。
- 气泡、输入框、字体和主题的当前 Runtime v2 支持子集。
- 应用、保存、取消、失败回滚和重新打开一致性。

不因“角色包相关”提前迁移：

- 角色卡人格/Prompt 对话语义；它由已存在的 Assistant Adapter 和真实聊天 WP 消费。
- TTS 模型、参考音频和 tone refs。
- 角色导入/导出、Studio、草稿、发布和回滚。
- 插件 renderer、Live2D、Canvas 或高级动画系统。

## 所有权

- Python Core：当前角色身份、角色 manifest 校验、公开表现 DTO、兼容角色配置读取。
- Rust/Tauri：`ui.*` 配置仓库、设置窗口命令入口、原子保存协调、受控资源 URL 和当前 generation 缓存。
- WebView：表单草稿、即时预览、立绘交叉淡入、打字机和未提交 dirty state。
- legacy Qt：保留兼容读取者；本 WP 不删除其设置工具或改变无法回读的 schema。

`characters/**` 始终是角色资源真相源且只读。WebView 不得直接写角色包、拼接本地路径或持久化角色业务
对象。预览状态不进入 Python 领域真相源，取消或窗口失败时必须恢复打开设置前的 UI。

## 窄设置契约

旧 `app/ui/tauri_settings.py` 同时包含纯 DTO/校验和 PySide6 进程/线程代码。本 WP 只允许把当前消费者
需要的纯逻辑抽到无 Qt 模块，legacy Qt wrapper 与 Runtime v2 Core Adapter 共同调用；不得整体搬迁该文件。

建议最小命令：

```text
settings.characterAppearance.get
settings.characterAppearance.preview
settings.characterAppearance.save
settings.characterAppearance.cancelPreview
```

约束：

- command allowlist 固定；Rust 注入 request/generation identity，WebView 不能伪造。
- `get` 返回当前角色公开表现、允许的 ui 字段、范围和 capability，不返回 Credential 或裸路径。
- `preview` 只更新 WebView/Rust 短期表现，不写磁盘。
- `save` 逐字段校验并使用现有兼容 schema 的原子写入；失败时旧文件和当前 UI 保持有效。
- `cancelPreview` 恢复设置窗口打开时的基线；重复调用幂等。
- 设置窗口关闭、WebView 崩溃、Core generation 变化或主应用退出都会自动取消未提交预览。
- 不建立跨 `core.*`/`ui.*` 的分布式事务；本 WP 只保存已明确属于角色外观/ui 的窄字段。

## 多角色真实验收

至少使用仓库中的 Sakura 与 N.A.V.I 角色包作为只读验收输入：

- 覆盖横向较宽和纵向较高的立绘宽高比。
- 验证默认立绘、全部 manifest 表情键、缺失/非法资源 fallback。
- 验证角色主题、初始消息和显示名称。
- 验证 portrait 快速切换、旧 decode callback、旧 generation resource 和 reduced motion。
- 验证不同角色资产不会导致窗口包络、气泡或输入框移动。

这只是同一实现对多个真实角色包的验收矩阵，不是产品角色选择功能。

## 设置窗口行为

- WP-3U-01 的设置窗口开放“角色与外观”页面的当前角色信息和已迁移字段。
- 未迁移控件必须删除、隐藏或禁用并附稳定说明；不能发送空操作。
- 预览期间桌宠保持可交互，并使用同一固定窗口包络。
- “应用”保存且保持窗口；“保存”保存并关闭；“取消”恢复未提交预览。
- 保存成功后重新打开设置，值与桌宠实际表现一致。
- 保存失败显示字段/域级错误，不关闭窗口、不留下部分文件或半应用 UI。

## 实施白名单

允许修改：

- `app/core_host/**` 中当前角色公开表现与窄外观设置 Adapter。
- 为去 Qt 复用而新增的纯设置 DTO/校验模块，以及 legacy Qt wrapper 的最小适配。
- `desktop/src-tauri/src/**` 中角色外观 Gateway、ui repository 和受控资源的窄实现。
- `desktop/frontend/settings/**`、`desktop/frontend/pet/**`、共享 DTO/theme/portrait 模块。
- `tools/settings-tauri/**` 中继续消费 canonical frontend/纯契约所需的兼容改造。
- 相关测试、fixture 和规范文档；`characters/**` 只读取证。

明确禁止：

- 修改角色包源资源或角色卡业务语义。
- 保存当前角色选择、运行中 Session 切换或历史分页。
- TTS、Memory、Tools、MCP、插件、截图、主动互动、完整首次设置、Studio、导入/导出。
- WebView 直接写 `data/**`，或 Rust 复制/修改 Python Assistant 业务对象。
- 通用 resource token、完整配置平台或跨域事务抽象。

## 验收门禁

自动测试：

- 两个真实角色 manifest 与立绘矩阵、资源安全和 generation 失效。
- portrait key 正常/缺失/非法/快速切换、decode 失败和 fallback。
- theme 与外观字段 validate、preview、save、cancel、窗口崩溃回滚和重复打开。
- 原子写失败、权限失败、未来 schema、Qt 可读兼容和凭据脱敏。
- PySide6 import guard：bundled Core 执行本能力时不得加载 Qt。
- 固定窗口、命中区域、IME、Fake Core/typewriter/close 回归。

真实 Windows 候选验证 Sakura/N.A.V.I、100%/150% DPI、立绘切换、外观预览、应用/保存/取消、
失败恢复、设置窗口关闭和主程序退出。公共代码须通过三平台构建；macOS/Linux 真实 WebView 留至 WP-7-02。

## 状态与回退

只有 WP-3U-01 accepted 后才能激活。本 WP 完成后进入 `stabilizing`；真实角色、设置联动、兼容写入、
回滚、Qt-free 和资源安全门全部通过且无 P0/P1 后才能 accepted。

回退时禁用/移除角色外观设置命令和保存入口，取消全部预览并恢复持久化基线；设置窗口退回 WP-3U-01
能力门控壳，桌宠保留 WP-3-03 当前角色只读表现。不得删除、恢复或改写角色包和无关用户数据。
