---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-22
---

# Runtime v2 发行与存储合同

## 两个所有权根

Runtime v2 只接受 `distribution_root` 与 `user_root`。Shell 必须通过
`--distribution-root` 和 `--user-root` 把两者传给 Core；生产启动合同不存在 `--app-root`。

`distribution_root` 包含 `VERSION`、`runtime-manifest.json`、`python/`、`core/` 和
`plugins/builtin/` 和 `plugins/dependencies/`，由安装器和更新器拥有，运行时只读。`user_root` 包含 `config/`、`data/`、
`characters/`、`plugins/user/` 和默认 `tts/`，由用户拥有，不进入发行 staging。

平台解析固定为：

- Windows Setup/Portable：两根均为用户选择的 Sakura 安装目录；
- macOS：发行根为 `.app/Contents/Resources`，用户根为
  `~/Library/Application Support/Sakura`；
- Linux（仅保留编译）：用户根为 `${XDG_DATA_HOME:-~/.local/share}/Sakura`。

0.9.x 与 1.0.x 是两套独立安装，发行根和用户根都不得复用同一个物理目录。1.0.x 正常启动、安装器和
Updater 不扫描、不读取也不复用 0.9.x 目录；旧数据只允许用户从首次导航或设置页显式选择后，由只读
legacy import 流程导入。源与目标发生相同、包含或被包含关系时必须拒绝，不为 0.9.x 目录建立安装前
snapshot，也不提供 0.9.x → 1.0.x 覆盖安装。

macOS `.app` 是不可写且可整体替换的签名资产。Updater 不得修改 Application Support；首版不提供 macOS
Portable，也不把 `data/cache` 分拆到 `~/Library/Caches`。由于 Memory 发行依赖 `onnxruntime 1.28` 的
arm64 wheel，首版最低系统版本冻结为 macOS 14.0。

## 干净首次启动与角色

发行包不包含角色。`config/ui.json.settings.first_run_guide_completed` 缺失或为 `false` 时，桌宠保持隐藏，
设置窗口先显示首次启动导航页，只提供“第一次使用”和“迁移0.9.x旧版本数据”两条路径。“第一次使用”进入
真实设置页上的角色导入、供应商和模型三步指路教程；每一步都可以直接继续或跳过，不以完成配置为门禁，
进入真实设置页前必须等待 Core 发布可用代际，不能让设置页自行撞上尚未建立的设置通道。结束后留在普通
设置页。中途关窗不写完成标记，“设置 → 系统 → 使用帮助”可以重播且不重置标记。macOS 首次启动导航页另有
“应用打开遇到问题？”次级入口，不增加第三条配置路径；普通设置页可再次打开同一说明。该入口只展示 Apple
官方的单应用例外流程，不执行全局 Gatekeeper 修改。

“迁移0.9.x旧版本数据”打开显式目录选择、检查和事务化导入流程；正常启动不会扫描旧目录，迁移期间源目录
保持只读，完整合同见 [0.9.x 数据迁移](legacy-0.9-import.md)。缺少角色仍是受支持的 `CHARACTER_REQUIRED` 状态：Core 和设置可用，桌宠隐藏，
托盘点击重新打开设置。角色导入使用已有 `.char` 原子 importer，通过类型化命令完成；逻辑角色 ID 与物理目录名
分离，目录名必须经过跨平台安全编码，重复判断以清单中的逻辑 ID 为准，不能让 Windows 尾部点/空格归一化产生
不可寻址目录或覆盖已有角色。Core 启动时会把历史遗留的 Windows 尾部点/空格角色目录改名到安全目录；若逻辑
ID 已被其他可访问角色占用，则为遗留副本分配带序号的新 ID，清单写回保留备份。导入时主题颜色按包内配置
保留，`theme.source` 仅为来源元数据并统一规范化为当前 `package`，不得因旧版内部来源标记拒绝角色。只有旧式
`voice`、没有插件资源 `extensions` 的角色补齐语音资源扩展；已有资源字段保留。此过程不启用语音或选择引擎，
运行选择仅保存在应用用户根的 `data/plugins/sakura.tts/config.json` 中。
不存在默认 `sakura` 角色、首角色 fallback 或默认角色 prompt。

主程序自带默认浅蓝主题。当前角色携带主题时覆盖它，否则所有窗口都使用主程序默认主题。
角色无主题时也不得从角色名、旧 prompt 或内置角色资源推断默认外观。
第一方 WebView 界面只使用纯色、透明度、边框与中性阴影建立层级，不得定义 CSS 或 SVG 渐变，也不得
使用主题主色或强调色绘制模糊光晕；角色资源、用户导入图片和应用图标中的原始像素内容不在此限制内。
所有第一方 WebView 窗口必须在原生层关闭 Web Inspector，并在页面层拦截 F12 和常见检查器快捷键；
页面可以保留产品定义的右键交互，但不得通过右键菜单暴露开发者工具。

## TTS 存储

`config/storage.json` schema 1 只保存可选的 `ttsRoot`。最终路径为：

```text
configured_tts_root or user_root/tts
```

默认目录可以创建；自定义目录必须已经存在、是绝对路径且可写。自定义目录失联时不得回退或创建替代目录，
TTS 返回 `TTS_STORAGE_UNAVAILABLE`，设置快照通过 `TTS_ROOT_MISSING`、`TTS_ROOT_NOT_DIRECTORY` 或
`TTS_ROOT_NOT_WRITABLE` 给出原因。切换目录不自动移动已有 TTS 文件。

## 发行内容

主安装包预装 Assistant、远程模型提供方、主动屏幕感知、MCP、立绘、联网、TTS Hub 和 ASR Hub。内置插件可停用，不可卸载；这只表示文件归安装器管理，不赋予私有 API 或实现优先级。

手机聊天、SenseVoice、Mem0、GPT-SoVITS、Genie 和 Spine 改为外部插件，新用户按需安装。主包不携带这六个插件，也不携带四个语音与记忆插件的私有 Python 依赖，以缩小 EXE、ZIP 等发行文件。模型、角色和用户数据不随此次迁出移动或删除。

### 内置插件迁出

ZIP、EXE 和其他发行形式共用 Core 启动迁移，迁移发生在插件清单加载之前。老用户优先从旧程序目录恢复代码和可用依赖；只有包含 `plugin.yaml` 的目录才作为本地来源，升级后残留的空目录或 `__pycache__` 不算插件包。开发环境可使用 `plugins/optional` 中的完整插件；发行包文件已被替换时，从 `app/plugins/migration_sources.json` 固定的公开仓库 commit 下载对应版本，并按需安装依赖。新用户不执行这些下载。首次升级可能需要网络，不承诺所有升级方式离线可用。

桌面入口以启动前 `config/` 是否存在识别旧用户。新用户默认配置与 `config/plugin-migrations.json` 一起发布，六个插件记录为 `not_applicable`；原有配置目录不补写此标记。Core 直接使用空用户根时也记录不适用，必须在模型配置迁移创建 `config/` 之前完成判断。该判断沿用既有用户目录初始化边界，不要求用户曾保存插件设置。

迁移按插件 ID 逐项执行。同 ID 用户插件存在时保留该版本和启停状态；其他第三方插件不影响迁移。迁入用户目录时沿用 `plugins.yaml` 的明确选择，没有明确选择则保留旧内置清单默认启用的行为。插件业务配置、已下载模型与聊天历史不改写。用户主动安装外部包时仍默认停用。

旧目录的清单若仍依赖退役的 `sakura.host.model_slots`，不迁入该代码，改用固定的兼容版本。对于迁移记录已完成、却仍使用此旧接口的官方插件，启动时自动修复；原代码保留在用户目录 `plugins/migration-backups/<随机 ID>/`，安装失败恢复原目录。移动旧代码前记录 `repairing`，中途退出后继续修复，成功才改为 `completed`。未被此迁移接管的用户插件不自动替换。

Core 的完整和最小快照增加可选 `pluginMigration`（无迁移时为空），包含 `state`（`running`、`completed`、`failed`）、`completed`、`total` 和 `pluginId`。桌面启动提示及设置页显示当前插件和已完成数量，迁移完成后继续在同一核心中加载插件。迁移耗时不计入普通初始化的 30 秒期限，下载和依赖安装沿用各自已有的超时，不另加总期限。迁移失败或迁移后核心启动失败时，设置页提供“重启核心”，直接使用已有生命周期重启流程，保留已完成记录。

迁移状态由 Core 生成，桌面端只解析展示需要的字段，不重复限制插件 ID 格式、进度范围或插件数量。快照允许增加字段，仍保留核心代次隔离、版本顺序和用户数据保护。

设置页在角色表现尚未发布时暂时禁用自定义控件，持续观察当前核心的角色表现；就绪后自动初始化并启用控件，无需关闭设置或重启。已存在的外观草稿沿用原有跨核心恢复规则。

安装成功或确认已有同 ID 用户副本后，才原子记录 `completed`。失败保留原错误并报告恢复失败，不标记完成；用户修复网络或磁盘问题后可重新启动。插件运行时初始化失败且尚未发布时，插件设置显示失败，已启用插件以 `PLUGIN_APPLICATION_FAILED` 标记，不继续显示正在启动。已发布代码但尚未记录完成时，按同 ID 用户副本续接。完成后用户主动卸载的插件不会自动恢复。损坏的迁移记录报错，不按新用户处理。

插件发现与安装冲突检查都忽略迁出清单中的旧内置副本，包括 ZIP 覆盖解压的残留文件；不删除旧程序目录，也不影响用户目录中的同 ID 插件。

旧数据导入 worker 按需使用外部 Mem0 与 TTS 插件的兼容工具。缺失时先安装对应固定版本，原启停选择保持不变；仅为导入安装的插件默认停用。该步骤也可能需要网络。

`playwright_browser` 是用户按需安装的可选插件，不进入主安装包。
发行流程把它另行生成一个可由普通本地插件安装入口处理的 `.sakplugin.zip`，安装和启用仍使用与第三方插件
相同的 user plugin 与 dependency root 路径。

Plugin Runtime v4 的发行 Python 只携带 Core 必需依赖、Plugin SDK 和安装工具；官方插件依赖进入
各自独立 dependency root，不进入主 Runtime 的全局 `site-packages`。预装插件可以携带已解析环境或
wheelhouse，避免加载插件时安装依赖；普通第三方插件不强制携带完整 wheelhouse。默认对话仍使用远程 API，发行包不携带本地推理模型。`uv`、`uvx`、`7zz` 位于
`python/tools/`，共享下载缓存只做物理去重，不改变插件 import 隔离。具体过渡合同见
[Plugin Runtime v4](sakura-plugin-runtime-v4.md)。

当前物理路径固定为：预装插件使用只读的 `distribution_root/plugins/dependencies/<plugin-id>/`，普通用户插件
使用可写的 `user_root/data/plugin-runtime/dependencies/<plugin-id>/`。两者使用相同的依赖就绪标记和
Runner 校验；普通启动只读取并验证，不把预装环境复制到 user root，也不自动安装或修复。

主 Python 运行时只读且不执行 pip；OpenAI SDK、HTTPX 与 SOCKS 传输依赖只进入远程模型插件环境，Assistant 无这些私有依赖。
Memory 不携带约 91 MB 模型，Genie/GPT-SoVITS 不携带本体、环境或
模型；Playwright 的 Python 包和浏览器资源都随可选插件流程取得。

依赖隔离缩小并稳定的是 Core Runtime 依赖闭包，不等于预装插件的依赖从安装包消失。完整下载体积是否
下降取决于预装插件集合；当前直接减少来自 Playwright 可选化，后续收益是增删插件不再改变 Core 依赖集合。

Windows 生成 Setup 与带 `portable.flag` 的 ZIP；前者使用 Tauri Updater，后者只检查并下载新版 ZIP。
发行资源清单 `release-inventory.json` 使用 schemaVersion 2，记录路径、大小与汇总，不生成逐文件内容摘要。
Windows 签名的 `digestAlgorithm: sha256`、Tauri updater 签名算法和 pip/uv 上游锁文件哈希继续保留。
macOS 生成 `.app`、DMG 与 updater artifact。正式公开产物必须签名，开发 staging 可以无签名。macOS Release
还发布独立的 `Sakura-<version>-macos-open-help.html`，并把同一说明放在 `.app.zip` 根目录，与 `.app` 并列。
Tauri 生成的 DMG 不在签名或公证后重打包；外部说明不得改变 `.app`、DMG 或 updater artifact 的签名字节。
Windows 安装版、Portable 和开发构建的主程序文件名统一为 `sakura.exe`；不得把 Cargo 内部架构名称暴露为
用户可见的可执行文件名。

所有 1.0.x 正式形态共用同一安装身份：`productName = Sakura`、bundle identifier
`com.rvosy.sakura`、Windows NSIS `installMode = currentUser`、Windows Updater
`installMode = passive`。这些字段是覆盖升级身份，不得按补丁版本或分发渠道变化。

1.0.x 覆盖升级只拥有并替换程序域：`VERSION`、`runtime-manifest.json`、Windows 的 `sakura.exe`、
`python/`、`core/`、`plugins/builtin/` 和 `plugins/dependencies/`。Setup 直接覆盖和内置 Updater 都必须
先清理旧程序目录中的运行期缓存及已经退役的 builtin/dependency 文件，再安装新程序域。Updater 的卸载
阶段不得进入用户域。

以下用户域必须逐字节保留：`config/`、`data/`、`characters/`、`plugins/user/`、默认 `tts/`，以及
`config/storage.json` 指向的安装目录外 TTS。`config/ui.json.settings.first_run_guide_completed` 也属于
用户域；1.0.x 升级后不得重置首次设置、重新进入首次导航，或自动触发 0.9.x 迁移。

Windows Portable 不增加后台替换器：客户端只下载新版 ZIP，由用户在原 1.0.x Portable 目录中覆盖解压。
ZIP 只含程序域、`portable.flag` 和当前 `sakura.exe`，不得携带任何用户域。覆盖解压必须更新 ZIP 中的程序
文件并原样保留用户域；需要完全清除不在新版 ZIP 中的未知旧程序残留时，发布说明应要求先替换上述程序
目录，不得把删除范围扩大到整个 Portable 目录。macOS Updater 只整体替换 `.app`，不得触碰
`Application Support/Sakura` 或外置 TTS。

正式发行不等待 Windows Portable 打包：Windows Setup 与 macOS 安装类资产完成后立即创建 Release，并先发布
不含 `portable` 字段的 `latest.json`；独立 Portable job 复用已经编译和签名链路验证过的 Windows Shell，完成后
把 ZIP 追加到同一 Release，并用包含 Portable URL 的最终 `latest.json` 覆盖初始清单。Portable 条目不生成内容摘要。Portable 失败
不得撤回已经发布的安装版资产；失败必须在 workflow 中明确可见，维护者修复后重新运行完整发行流程。

稳定版的 Portable 与最终 `latest.json` 发布完成后，发行 workflow 必须把版本资料与完整清单导入私人控制台草稿。
维护者确认后，控制台更新 `https://api.sakura.cialloo.cn/service/v1/releases.json` 与同目录的 `latest.json`。`releases.json`
供公告、兼容性和下载入口使用，不由客户端据此安装更新。schema 1 固定包含 `latest`、可空的
`minimumSupported`、`releaseUrl`、`publishedAt`、`urgent`、三个公开下载 URL 和
`updaterManifestUrl`，后者指向国内 `latest.json`；下载文件及原始安装包签名仍由 GitHub Release 托管。
prerelease 不更新该接口，服务端拒绝格式错误和版本
降级。CI 凭据只能调用服务器端受限草稿导入命令，不得获得通用 shell 或站点其他文件的写权限。完整接口 schema、
失败降级和部署权限合同见 [Sakura Service 与私人控制台合同](sakura-service.md)。

Windows Setup 卸载器无论是否勾选“删除应用数据”，都必须递归删除安装器拥有的 `core/`、`python/`、
`plugins/builtin/` 和 `plugins/dependencies/` 发行根，包括运行期间在其中产生的字节码缓存；大量小文件的删除
不得逐文件刷新卸载详情。未勾选时保留安装目录内的用户数据。勾选后必须额外递归删除 Runtime v2 拥有的
`config/`、`data/`、`characters/`、`plugins/user/` 和默认 `tts/`，并在目录为空时移除安装目录；不得递归
删除安装目录中的未知文件，也不得删除 `config/storage.json` 指向的安装目录外自定义 TTS 路径。Updater
触发的卸载阶段始终保留用户数据。

## 启动更新检测与用户操作

新构建正式安装包的 Tauri Updater endpoint 为国内静态清单：
`https://api.sakura.cialloo.cn/service/v1/latest.json`。CI 把已完成的稳定版导入控制台草稿，维护者确认后更新此入口；保留 GitHub 清单供旧客户端。
客户端不调用 GitHub Releases API，也不自行比较版本。开发配置没有 endpoint 时直接跳过。
Updater 负责 SemVer 比较、签名下载包选择和安装前验签。

Updater 网络请求同时遵循 Windows/macOS 系统代理和标准 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、
`NO_PROXY` 环境变量。每次检查更新和开始下载安装包分别创建客户端，读取当时的代理；已开始的下载不换连接。
诊断上报每个新批次也重新创建客户端，不缓存启动时的代理。检查、下载、验签和安装的开始、完成及失败阶段写入 `sakura-runtime.log`；失败记录保留
稳定错误码、脱敏后的底层诊断和代理来源是否已配置，但不得记录代理地址、凭据、签名密钥或带查询参数的下载
地址。版本清单检查整体超时为 10 秒；用户明确开始安装后，签名安装包下载整体超时为 30 分钟，不能把清单
检查的短超时复用于大文件下载。

主窗口显示后每次启动最多执行一次后台检查，单次超时 10 秒且不自动重试。检查、配置读取或网络失败不影响
Core、聊天、启动问候或设置页的手动检查。自动检测只缓存已通过 Updater 解析的候选，不下载、不安装；手动
“检查更新”始终可用，也不受每日主动播报门禁影响。

`config/ui.json` schema 1 的 `settings.update` 为：

```json
{
  "auto_check_enabled": true,
  "last_announced_version": null,
  "last_announced_local_date": null
}
```

缺失 `auto_check_enabled` 等同 `true`。同一版本在同一本地自然日只成功主动播报一次；新版本即使同日也可播报。
只有对应 `operationId` 的 `chat.completed` 才原子写入版本和日期，失败、取消、Core generation 变化和持久化
失败均不写成功标记。关闭自动检测立即丢弃未发送候选，不取消已开始的回复，也不清除成功标记；重新开启立即
触发本次启动的受控检查入口。“自动检测更新”开关位于“设置 → 系统 → 应用更新”。

“设置 → 关于”是唯一手动更新操作入口。installed 模式显示“下载并安装”，明确点击后调用 Tauri Updater 的
签名下载与安装接口；Windows 在安装器接管退出前有界等待 Core 受控关闭完成，macOS 成功后提示用户重启。
Portable 模式只显示清单中固定 HTTPS 资产的“下载新版 ZIP”。任何自动检查或模型播报都不得触发下载、安装、
退出或重启。Updater 只替换 `distribution_root`，不得读取、迁移或覆盖 `user_root`。
启动检查已经缓存候选版本时，“设置 → 关于”直接显示该候选和对应的用户操作，不重复发起网络请求；手动“重新
检查”仍始终可用。缓存为空时保持初始“检查更新”状态。

真实升级门禁必须在发布机上使用签名产物验收；单元测试或开发包不能替代：

- Windows 在 1920×1080、125% 与 150% DPI 下，从 1.0.0 分别执行同身份 Setup 直接覆盖和内置 Updater；
  更新前后直接比较隔离用户域夹具内容，确认首次设置标记不变，应用可启动，旧 Python 缓存和退役 builtin 已清除。
- Windows Portable 在 1.0.0 原目录覆盖解压新版 ZIP；确认 ZIP 内程序文件更新，全部用户域及默认/外置 TTS
  夹具内容不变，应用可启动。
- macOS 从已签名的 1.0.0 `.app` 经 Updater 替换；确认 codesign/notarization、退出和替换完成，
  `Application Support/Sakura` 与外置 TTS 夹具内容不变，应用可重新启动。

## GitHub 下载镜像

Shell 的下载源配置保存在 `config/ui.json` 的 `settings.download_sources`，与插件市场共用。
对发行配置已有的 GitHub 更新清单端点按源顺序展开，非 GitHub 地址保持原样；不覆盖发行构建提供的公钥和端点。
安装版更新包仍由 Tauri updater 下载并验证签名，只有网络、HTTP 和超时错误切换下一源；签名失败立即终止。
下载阶段在设置页显示当前源。便携版继续使用原有手动下载流程，镜像不改写浏览器中的人工下载事务。
