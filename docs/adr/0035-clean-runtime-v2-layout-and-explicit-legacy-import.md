---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
supersedes: 0003-runtime-v2-data-compatibility
updated: 2026-08-26
---

# ADR-0035：Runtime v2 使用干净布局和单一 v1 契约

> 2026-08-30：ADR-0038 仅 supersede 本文“不提供旧数据导入”的决定。干净 v2 布局、正常启动不扫描、
> 不双读/双写、不运行旧源码和不原地修改旧数据的约束继续有效。

## 背景

Runtime v2 尚未正式发布，当前仓库中的 `data/config`、旧聊天 JSONL、`data/tts_bundles`、旧插件目录和若干
双读路径主要来自开发期对旧 main/Legacy Qt 数据的复用。继续把这些位置当成发行契约，会让旧版目录结构、
schema 和迁移分支永久进入正常启动链，并反过来限制 v2 的发行布局。

该分支与旧 main 的数据契约明确不兼容。当前不规划、不保留也不预留旧数据导入边界。

## 候选方案

### 方案 A：在原目录上继续兼容演进

Runtime v2 继续读取和写入旧版配置、JSONL、TTS 与插件目录，并为每次 schema 变化保留双读、回退和兼容测试。
这能减少一次迁移，但会把旧 main 的数据模型变成 v2 的永久公共接口，因此不采用。

### 方案 B：启动时自动发现和迁移旧目录

应用根据常见安装位置、当前工作目录或遗留文件特征自动判断旧版本并迁移。该方案看似省去用户选择，但容易
误认开发目录、备份、副本或不完整安装，也会让首次启动承担不可预测的大文件复制和数据库转换，因此不采用。

### 方案 C：干净的 v2 根目录，不提供旧数据导入

Runtime v2 只认识自己的发行资源和用户数据格式。旧数据如需处理，将来必须作为新需求重新设计，
不在当前代码、fixture 或文档中预留边界。采用此方案。

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
  `sakura` 角色或旧格式转换器。

现有开发期兼容代码、双读路径和兼容 fixture 不构成发布契约。实现新布局时可以删除它们，不要求先维持一次
旧目录往返兼容。

### 3. 首次正式发布前所有内部 schema 固定为 v1

Runtime v2 尚未发布，因此开发过程中新增字段不提升内部 schema 版本。Sakura 自有的配置、Snapshot、
manifest、publication、layout 与日志记录均固定为 v1，只接受当前字段结构；不保留开发期旧结构的 parser、
normalize、migration、dual read 或 dual write。正式发布后若确有升级需求，再单独设计迁移契约。

## 与既有决策的关系

- 本 ADR supersede ADR-0003 对 Runtime v2 直接复用、兼容写入和持续验证旧 main 数据布局的剩余产品约束；
  ADR-0003 中保护源数据、避免危险原地改写的历史安全证据仍然有效。
- 本 ADR 延续 ADR-0034 对 Legacy Qt 运行时的退役，不保留旧数据 parser 或 migration。
- ADR-0033 的类型化 SQLite Timeline 继续是 v2 聊天数据方向；当前不提供旧 JSONL 导入。

## 后果

Runtime v2 可以按照发行产品本身设计直观目录、schema 和测试，不再为旧 main 保留分支、路径别名和双写状态。
安装器、Updater 和 portable 包也能围绕明确的发行资源/用户资产边界构建。

代价是旧版不能原地升级，也不会在安装 v2 后出现历史数据。用户只能使用新的 v2 数据根。
