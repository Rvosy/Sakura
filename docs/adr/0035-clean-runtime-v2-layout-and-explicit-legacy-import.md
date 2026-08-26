---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0003-runtime-v2-data-compatibility
updated: 2026-08-26
---

# ADR-0035：Runtime v2 使用干净布局，旧版数据只通过显式导入迁移

## 背景

Runtime v2 尚未正式发布，当前仓库中的 `data/config`、旧聊天 JSONL、`data/tts_bundles`、旧插件目录和若干
双读路径主要来自开发期对旧 main/Legacy Qt 数据的复用。继续把这些位置当成发行契约，会让旧版目录结构、
schema 和迁移分支永久进入正常启动链，并反过来限制 v2 的发行布局。

旧版用户数据仍有迁移价值，但“能够迁移旧数据”和“新版本持续兼容旧目录”是两个不同问题。Sakura 需要前者，
不接受后者成为 Runtime v2 的长期架构负担。

## 候选方案

### 方案 A：在原目录上继续兼容演进

Runtime v2 继续读取和写入旧版配置、JSONL、TTS 与插件目录，并为每次 schema 变化保留双读、回退和兼容测试。
这能减少一次迁移，但会把旧 main 的数据模型变成 v2 的永久公共接口，因此不采用。

### 方案 B：启动时自动发现和迁移旧目录

应用根据常见安装位置、当前工作目录或遗留文件特征自动判断旧版本并迁移。该方案看似省去用户选择，但容易
误认开发目录、备份、副本或不完整安装，也会让首次启动承担不可预测的大文件复制和数据库转换，因此不采用。

### 方案 C：干净的 v2 根目录，加独立的用户显式导入流程

Runtime v2 只认识自己的发行资源和用户数据格式。需要迁移时，由用户明确选择旧版本目录，再由独立导入流程
只读解析旧数据并写入新的 v2 根目录。采用此方案。

## 决策

### 1. Runtime v2 从全新布局开始

Runtime v2 使用两个明确的所有权根：

```text
distribution_root/          # 安装器和更新器拥有，只读发行资源
├─ runtime-manifest.json
├─ python/
├─ core/
└─ plugins/builtin/         # 官方插件和其他随版本更新的应用资源

user_root/                  # 用户拥有，可备份和整体迁移
├─ config/
├─ data/                    # Timeline、Memory、日志、缓存和其他运行数据
├─ characters/
├─ plugins/user/
└─ tts/
```

- Windows 安装版和 portable 包让两个根位于同一个可见的 Sakura 安装目录内，但代码不得依赖二者物理
  相等；各子树的所有权仍必须互斥。
- portable 包使用同一发行 staging 和相同 v2 用户布局，整个目录可以复制迁移。
- macOS 的发行资源位于 `Sakura.app/Contents/Resources`，用户根固定为
  `~/Library/Application Support/Sakura`。应用不得把 bundle identifier 或当前开发 identifier 暴露为
  长期数据目录合同。
- Linux 首版不发行；保留的路径合同为 `${XDG_DATA_HOME:-~/.local/share}/Sakura`。
- 首版不再把 `data/cache` 拆到平台 Cache 目录。只有 TTS 可以通过设置覆盖为一个已存在、绝对且可写的目录；
  配置、数据、角色和用户插件始终留在 `user_root`。
- 发行包不包含内置角色、默认角色或隐藏 fallback 角色。`user_root/characters` 在干净安装时为空，角色必须
  由用户显式安装。
- 零角色是受支持的首次启动状态：设置窗口和角色导入能力必须可用，但不创建聊天 Session，也不显示依赖角色
  资源的桌宠主界面。用户安装并选中角色后才进入正常运行态。
- 具体文件名和各领域 schema 由对应 Spec 冻结，但不得仅因为当前实现或旧 main 使用某个路径就保留它。

### 2. 正常运行链不兼容旧版目录

- Runtime v2 不扫描、不猜测、不自动打开旧 main 的安装目录或数据目录。
- 正常启动不执行旧 schema parser、原地 migration、fallback read、dual read、dual write 或 shadow copy。
- 缺少 v2 配置时进入 v2 首次设置；不得用发现的旧配置静默补齐。
- 新聊天从 v2 的 Timeline/数据库格式开始，不为旧版继续写兼容 JSONL。
- TTS、角色、插件和配置只写 v2 的用户目录，不因旧路径存在而改变目标位置。
- 配置的自定义 TTS 目录丢失或不可写时不得静默回退到 `user_root/tts`；Shell 和非 TTS 功能保持可用，
  TTS 操作明确返回 `TTS_STORAGE_UNAVAILABLE`。
- 旧 main、Legacy Qt 或兼容模块不得进入 Core、Plugin Worker 和 Tauri Shell 的正常 import/启动闭包。
- 开发期的自动 `.env`/旧配置迁移器与 TTS Plugin cutover 已删除；不能为了维持旧测试重新引入默认
  `sakura` 角色。未来 importer 必须在独立模块和用户显式操作边界内重新实现所需转换。

现有开发期兼容代码、双读路径和兼容 fixture 不构成发布契约。实现新布局时可以删除它们，不要求先维持一次
旧目录往返兼容。

### 3. 旧版迁移是未来独立、显式的导入能力

迁移流程以后单独设计和实现，并至少遵守以下边界：

- 由用户在 UI 或命令中明确选择旧版本目录；不自动发现或静默执行。
- 旧目录始终作为只读来源，不移动、不删除、不原地升级。
- 导入结果写入新的 v2 `user_root`，成功校验前不得替换已有 v2 数据。
- TTS 采用受校验的复制，API/角色等配置映射到 v2 schema，旧聊天记录转换为 v2 Timeline 数据库；Memory、
  插件及其他领域是否支持导入由各自的迁移 Spec 决定。
- 不支持、损坏或版本未知的数据明确报告并保持源目录不变，不用空默认值伪装成功。
- 旧格式 parser、样本和转换器放在隔离的 importer/migration 边界内；正常运行模块不得依赖它们。

本 ADR 不承诺当前版本已经具备上述迁移能力，也不冻结迁移 UI、字段映射、数据库事务或大文件复制策略。

### 4. v2 自身仍允许有界的 schema 演进

拒绝旧 main 兼容不等于拒绝未来 v2 升级。正式发布后的 v2 schema 变化必须使用明确版本、备份、校验和失败
恢复；这些迁移只处理受支持的 v2 版本，不重新引入旧版目录探测或长期双写。

## 与既有决策的关系

- 本 ADR supersede ADR-0003 对 Runtime v2 直接复用、兼容写入和持续验证旧 main 数据布局的剩余产品约束；
  ADR-0003 中保护源数据、避免危险原地改写的历史安全证据仍然有效。
- 本 ADR 延续 ADR-0034 对 Legacy Qt 运行时的退役。ADR-0034 所说的旧数据 parser 和 migration 仅可存在于
  未来的显式导入边界，不进入正常 Runtime v2 生命周期。
- ADR-0033 的类型化 SQLite Timeline 继续是 v2 聊天数据方向；其中从旧 JSONL 导入的动作移到本 ADR 定义的
  显式迁移流程，不在干净 v2 首次启动时自动执行。

## 后果

Runtime v2 可以按照发行产品本身设计直观目录、schema 和测试，不再为旧 main 保留分支、路径别名和双写状态。
安装器、Updater 和 portable 包也能围绕明确的发行资源/用户资产边界构建。

代价是旧版不能原地升级，也不会在安装 v2 后自动出现历史数据。迁移能力完成前，用户只能使用新的 v2 数据根；
完成后也必须主动选择旧目录并执行一次导入。这个显式步骤是为了换取长期更简单、可理解的 Runtime v2。
