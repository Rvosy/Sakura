---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
updated: 2026-09-01
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
设置页。中途关窗不写完成标记，“设置 → 系统 → 使用帮助”可以重播且不重置标记。

“迁移0.9.x旧版本数据”打开显式目录选择、检查和事务化导入流程；正常启动不会扫描旧目录，迁移期间源目录
保持只读，完整合同见 [0.9.x 数据迁移](legacy-0.9-import.md)。缺少角色仍是受支持的 `CHARACTER_REQUIRED` 状态：Core 和设置可用，桌宠隐藏，
托盘点击重新打开设置。角色导入使用已有 `.char` 原子 importer，通过类型化命令完成；逻辑角色 ID 与物理目录名
分离，目录名必须经过跨平台安全编码，重复判断以清单中的逻辑 ID 为准，不能让 Windows 尾部点/空格归一化产生
不可寻址目录或覆盖已有角色。Core 启动时会把历史遗留的 Windows 尾部点/空格角色目录改名到安全目录；若逻辑
ID 已被其他可访问角色占用，则为遗留副本分配带序号的新 ID，清单写回保留备份。导入时主题颜色按包内配置
保留，`theme.source` 仅为来源元数据并统一规范化为当前 `package`，不得因旧版内部来源标记拒绝角色。只有旧式
`voice`、没有插件 `extensions` 的角色按旧版语义补齐并启用 GPT-SoVITS 兼容扩展；已有扩展始终原样保留。
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

随主安装包预装的五个官方默认插件为 `sakura_mem0`、`sakura_mobile`、`sakura_tts_hub`、
`sakura_genie` 和 `sakura_gpt_sovits`。它们默认启用、允许禁用、不可卸载；不可卸载只表示文件由安装器
拥有，不赋予私有 API 或实现优先级。`playwright_browser` 是用户按需安装的可选插件，不进入主安装包。
发行流程把它另行生成一个可由普通本地插件安装入口处理的 `.sakplugin.zip`，安装和启用仍使用与第三方插件
相同的 user plugin 与 dependency root 路径。

Plugin Runtime v4 的发行 Python 只携带 Core 必需依赖、Plugin SDK 和安装工具；官方插件依赖进入
各自独立 dependency root，不进入主 Runtime 的全局 `site-packages`。预装插件可以携带已解析环境或
wheelhouse 以保证首次启动离线可用；普通第三方插件不强制携带完整 wheelhouse。`uv`、`uvx`、`7zz` 位于
`python/tools/`，共享下载缓存只做物理去重，不改变插件 import 隔离。具体过渡合同见
[Plugin Runtime v4](sakura-plugin-runtime-v4.md)。

当前物理路径固定为：预装插件使用只读的 `distribution_root/plugins/dependencies/<plugin-id>/`，普通用户插件
使用可写的 `user_root/data/plugin-runtime/dependencies/<plugin-id>/`。两者使用同一 marker、fingerprint 和
Runner 校验；普通启动只读取并验证，不把预装环境复制到 user root，也不自动安装或修复。

主 Python 运行时只读且不执行 pip。Memory 不携带约 91 MB 模型，Genie/GPT-SoVITS 不携带本体、环境或
模型；Playwright 的 Python 包和浏览器资源都随可选插件流程取得。

依赖隔离缩小并稳定的是 Core Runtime 依赖闭包，不等于五个预装插件的依赖从安装包消失。完整下载体积是否
下降取决于预装插件集合；当前直接减少来自 Playwright 可选化，后续收益是增删插件不再改变 Core 依赖集合。

Windows 生成 Setup 与带 `portable.flag` 的 ZIP；前者使用 Tauri Updater，后者只检查并下载新版 ZIP。
macOS 生成 `.app`、DMG 与 updater artifact。正式公开产物必须签名，开发 staging 可以无签名。
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
把 ZIP 追加到同一 Release，并用包含 Portable URL 与 SHA-256 的最终 `latest.json` 覆盖初始清单。Portable 失败
不得撤回已经发布的安装版资产；失败必须在 workflow 中明确可见，维护者修复后重新运行完整发行流程。

稳定版的 Portable 与最终 `latest.json` 发布完成后，发行 workflow 必须把控制面版本元数据推送到
`https://sakura.cialloo.cn/service/v1/releases.json`。该接口是公告、兼容性和下载入口使用的只读控制面，不替代
Tauri Updater 的签名清单，也不由客户端据此安装更新。schema 1 固定包含 `latest`、可空的
`minimumSupported`、`releaseUrl`、`publishedAt`、`urgent`、三个公开下载 URL 和
`updaterManifestUrl`；下载文件仍由 GitHub Release 托管。prerelease 不更新该接口，服务端拒绝格式错误和版本
降级。部署凭据只能调用服务器端受限发布命令，不得获得通用 shell 或站点其他文件的写权限。完整接口 schema、
失败降级和部署权限合同见 [Sakura Service 静态控制面合同](sakura-service.md)。

Windows Setup 卸载器无论是否勾选“删除应用数据”，都必须递归删除安装器拥有的 `core/`、`python/`、
`plugins/builtin/` 和 `plugins/dependencies/` 发行根，包括运行期间在其中产生的字节码缓存；大量小文件的删除
不得逐文件刷新卸载详情。未勾选时保留安装目录内的用户数据。勾选后必须额外递归删除 Runtime v2 拥有的
`config/`、`data/`、`characters/`、`plugins/user/` 和默认 `tts/`，并在目录为空时移除安装目录；不得递归
删除安装目录中的未知文件，也不得删除 `config/storage.json` 指向的安装目录外自定义 TTS 路径。Updater
触发的卸载阶段始终保留用户数据。

## 启动更新检测与用户操作

正式安装包的 Tauri Updater endpoint 固定为主仓库的 GitHub 稳定版 Release：
`https://github.com/Rvosy/Sakura/releases/latest/download/latest.json`。`releases/latest` 不包含 draft 和
prerelease；客户端不调用 GitHub Releases API，也不自行比较版本。开发配置没有 endpoint 时直接跳过。
Updater 负责 SemVer 比较、签名下载包选择和安装前验签。

Updater 网络请求同时遵循 Windows/macOS 系统代理和标准 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、
`NO_PROXY` 环境变量。检查、下载、验签和安装的开始、完成及失败阶段写入 `sakura-runtime.log`；失败记录保留
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
  更新前后对全部用户域 marker/hash，确认首次设置标记不变，应用可启动，旧 Python 缓存和退役 builtin 已清除。
- Windows Portable 在 1.0.0 原目录覆盖解压新版 ZIP；确认 ZIP 内程序文件更新，全部用户域及默认/外置 TTS
  marker/hash 不变，应用可启动。
- macOS 从已签名的 1.0.0 `.app` 经 Updater 替换；确认 codesign/notarization、退出和替换完成，
  `Application Support/Sakura` 与外置 TTS marker/hash 不变，应用可重新启动。
