---
kind: spec
status: normative
audience: maintainer
source_of_truth: self
status_source: docs/plans/runtime-v2/work-packages.md
updated: 2026-07-31
---

# WP-1P-02：三平台 RuntimeLocator 与 bundled Python 布局

> 执行状态：仅见 `docs/plans/runtime-v2/work-packages.md` 第 2 节
> 日期：2026-07-22
> 前置：WP-1P-01 accepted，提交 `21c2aaf9`
> 规范来源：ADR-0004、`WP-1P-01-platform-contract.md`

## 1. 结果边界

本 Work Package 把 Python/Core 的位置和来源从调用方参数、`.exe` 字符串和仓库构建目录假设中分离出来。Tauri/Core 启动链只消费 `RuntimeLayout`；`FilesystemRuntimeLocator` 根据显式模式、当前正式 target 和受控 manifest 返回唯一布局。

它不下载、安装、更新或修复用户机器上的 Runtime。仓库中的 `scripts/runtime_v2_archive.py` 只供 CI/build 阶段下载并校验固定上游归档，应用不会导入或执行它。

以下仍不属于 WP-1P-02：

- POSIX shared lock、Windows mutex 适配或 legacy Qt 锁改造。
- macOS/Linux process group、Windows Job 适配或 Supervisor 修改。
- 窗口、IME、原生诊断和最终 Tauri bundle 生成。
- system Python、PATH、Conda、Homebrew、pyenv 或在线 Runtime repair 回退。
- Python Core 协议、Snapshot、Assistant 领域服务或用户数据 schema 修改。

## 2. 精确 source manifest

权威 manifest 位于 `desktop/src-tauri/runtime-layouts/<platform-id>/runtime-manifest.json`。三个 manifest 都固定 CPython 3.12.8、精确 asset、长度、SHA-256、归档根和 extraction strip count。

| Platform | Archive | Bytes | SHA-256 |
|---|---|---:|---|
| `windows-x64` | `python-3.12.8-embed-amd64.zip` | 11,094,114 | `8d3f33be9eb810f23c102f08475af2854e50484b8e4e06275e937be61ce3d2fb` |
| `macos-arm64` | `cpython-3.12.8+20250106-aarch64-apple-darwin-install_only.tar.gz` | 15,676,873 | `5dfd4d81ad8ea0407e6153ed998a5fba332275c60ece81c6db2b58e443de60b9` |
| `linux-x64` | `cpython-3.12.8+20250106-x86_64-unknown-linux-gnu-install_only.tar.gz` | 67,062,562 | `c8032747c8e44ce0164236fa70a6b767a43ef778dc51b99bd18f25984f8cba3b` |

校验值来自 2026-07-22 对三个固定 HTTPS 工件的完整下载和 SHA-256 计算；临时归档计算后已删除，没有进入工作区。build/CI 下载器要求 HTTPS、精确 byte length 和 SHA-256 同时匹配，使用 `.partial-<pid>` 后再原子改名；失败不留下可被误用的目标归档。

Assistant Core 既有的 PyYAML 依赖不安装进上述冻结 CPython，也不另建 requirements manifest。三个 target 的 `runtime-manifest.json` 另行固定 PyYAML 6.0.2 原生 wheel 的 PyPI HTTPS URL、文件名、byte length、SHA-256，以及 development/packaged 相对路径。build/CI 只下载并校验该不可变 import artifact；`RuntimeLocator` 在 Core spawn 前再次校验 regular-file、size 和 SHA-256，并把 canonical wheel 路径作为只读 Python path entry 交给 bootstrap。packaged staging 复制同一已校验 wheel，验收阶段不得执行 pip、改写 Runtime site-packages 或 `_pth`。

禁止用 release API 中“名称包含 `cpython-3.12`”的第一个结果、`latest` tag 或可漂移 URL 替代 manifest。更新任一 source 必须单独审查三个 target，并同时更新 manifest、本规范和 CI evidence。

## 3. 两种唯一布局

### 3.1 ExplicitDevelopment

调用方必须传绝对的 repository root；没有默认当前工作目录，也不从 executable 反向猜仓库。

```text
<explicit repository root>/
├─ app/core_host/__main__.py
└─ runtime/
   ├─ python.exe          # windows-x64
   └─ bin/python3         # macos-arm64 / linux-x64
```

`application_root` 是显式 repository root，Core module 固定为 `app.core_host`。这个模式只允许开发和验收调用方明确选择，不允许 packaged 启动静默进入。

### 3.2 Packaged

调用方必须传 Tauri 提供的绝对 resource directory；locator 不使用当前工作目录。

```text
<resource directory>/runtime-v2/<platform-id>/
├─ runtime-manifest.json
├─ python/
│  ├─ python.exe          # windows-x64
│  └─ bin/python3         # macos-arm64 / linux-x64
└─ core/
   └─ app/core_host/__main__.py
```

`runtime-manifest.json` 必须与编译进二进制的 target manifest 结构完全相等。`application_root` 是 `core/`，不暴露 resource directory 之外的任意路径。

## 4. 定位与验证顺序

`FilesystemRuntimeLocator` 固定按以下顺序失败关闭：

1. 验证 mode/root 组合：development 必须有显式 root；packaged 禁止 development root；exe/resource/root 必须是绝对路径。
2. 验证请求 target 等于当前 Rust build 的正式 target；另一个平台立即返回 `incompatible_architecture`。
3. 加载编译时 target manifest；packaged 再读取工件内 manifest 并要求完全相等。
4. canonicalize runtime root、Python、application root 和 Core entry；manifest path 必须为安全相对路径，canonical child 不得逃离受控 root。
5. 验证 Python/Core 是预期文件、application root 是目录；Unix Python 必须有 executable bit。
6. 只读检查 Python executable header：Windows PE machine `0x8664`、macOS Mach-O CPU `0x0100000c`、Linux ELF64 little-endian machine `0x003e`。
7. 返回 canonical `RuntimeLayout`：target、mode、runtime root、Python、application root、Core module 和 source ID。

locator 不执行 Python 来决定是否可信，不解析用户 PATH，不联网，也不自动更换 source。实际 Python 版本和 Core lifecycle 在 WP-1P-06 继续由真实 hello/diagnostics 记录。

## 5. 错误和故障矩阵

| 故障 | 稳定 category | 处置 |
|---|---|---|
| mode/root 混用、相对 root | `invalid_input` | 调用方修正；不重试 |
| runtime root、manifest、Python 或 Core entry 缺失 | `not_found` | diagnostics/repair；不查 PATH |
| Unix Python 无 executable bit、原生权限拒绝 | `permission_denied` | 用户/安装器修复权限 |
| manifest JSON 损坏、source/hash/path 被改、binary header 无效、canonical path 逃逸 | `integrity_mismatch` | 不执行工件 |
| 请求 target 不等于 build、PE/Mach-O/ELF machine 不匹配 | `incompatible_architecture` | 使用正确 target 工件 |
| 其他无法安全细分的文件系统错误 | `native_failure` | 保留 Win32/errno，默认不自动重试 |

已执行的 golden/fault contract 覆盖：三个 source manifest 唯一；三套 packaged layout；资源根整体移动；development/packaged 混用；manifest 缺失、非法 JSON、source identity 被改；Python 缺失、损坏、错误 CPU/格式；公共 locator 拒绝另一个 target；当前 Windows development Runtime 真实 PE header。

验收时由 native CI 强制的故障：macOS/Linux 真实归档 executable permission、实际 Mach-O/ELF header、原生 canonical path 和 Tauri native compile。不能用模拟 header 测试替代这些证据。

## 6. 生产/验收接线

- `CoreHostRuntime::launch` 现在只接受 `RuntimeLayout` 和 generation ID；program、Core module 和 cwd 均来自 locator 结果。
- Phase 1C Windows 真实验收只传显式 repository root；Rust 自行选择当前正式 target 并调用 locator。`SAKURA_PHASE_1C_PYTHON` 已删除。
- Core Host 的 framing、initialize、Snapshot、deadline、Job Object 和 shutdown 逻辑未改。
- `shared_instance.rs` 只把 Win32 测试 module 收窄为 `cfg(all(test, windows))`，使 POSIX native test build 不错误引用 Windows-only dependency；其生产 fallback 和锁语义未改，正式 POSIX backend 仍属于 WP-1P-03。

## 7. 原生 CI 门禁

`.github/workflows/runtime-v2-platform-foundation.yml` 建立三个独立 native job：

| Platform | Runner | 硬断言 |
|---|---|---|
| Windows x64 | `windows-2025` | `PROCESSOR_ARCHITECTURE=AMD64` |
| macOS arm64 | `macos-15` | `uname -m=arm64`，使用标准 Apple Silicon runner，不接受 Intel cross-compile |
| Linux x64 | `ubuntu-24.04` | `uname -m=x86_64`，安装 WebKitGTK 4.1 build prerequisites |

每个 job 从 target manifest 的精确 URL 下载并验证归档，在 runner 的临时 checkout 中替换开发 `runtime/`，然后执行：

- Rust/Cargo/target/native OS 信息记录。
- `cargo fmt --check`。
- 全部 `platform::` contract/golden tests。
- 被显式标为 staged-runtime integration 的真实归档 locator test。
- native Tauri debug build。

workflow 不启动 Assistant、不读取真实用户 data、不生成发布工件。macOS runner 必须实际可用且为 arm64；`macos-15` 由 workflow 内的 `uname -m=arm64` 断言约束，不能改用 Intel 或 cross-compile 结果接受。

## 8. 验收证据与 accepted 条件

Windows 本机已完成：三个归档上游元数据核对和 SHA-256、8 项 locator 定向测试、真实仓库 Runtime 显式定位、完整 Rust 回归、Python archive verifier 测试、Debug/Release build、PowerShell/YAML 语法和 diff 门禁。精确数量以 Work Package accepted 记录为准。

WP-1P-02 已由提交 `5c0ef64b6c25f5554ceb4dc4072ab98a8e29f369` 的 GitHub Actions run [`30018844932`](https://github.com/Rvosy/Sakura/actions/runs/30018844932) 验收。Windows x64、macOS arm64、Linux x64 三个 native job 对同一提交全部通过，并分别完成真实架构断言、精确归档下载与校验、staged RuntimeLocator 集成测试和 native Tauri Shell 编译。

这些证据满足总表登记所需门禁；后续仍不得删除 non-Windows 测试、降低 runner architecture 断言或把 fake binary header 当作真实归档证据。当前状态和后续启动点只见 Work Package 总表。

独立回退：整体 revert WP-1P-02 实现提交，恢复 WP-1P-01 的 compile-only `RuntimeLocator` trait 和 WP-1C-02 的显式 Windows Python 参数；不回退平台契约、Supervisor/Core Host 能力或用户 Runtime/data。
