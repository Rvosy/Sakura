# 增强感知音频推理运行时

本模块提供可选的本机音频推理后端部署路径，用于语音与声音事件增强感知。目标是让普通用户不需要手动编译推理框架，同时保持默认关闭、跨平台、可回退。

## 运行时边界

- 默认不启动、不下载、不采集音频。
- 只有用户在设置页选择“本机运行框架”与 `llama.cpp` 后端，并点击“准备 llama.cpp 音频后端”且确认下载提示时，才会准备本机运行时与推荐模型缓存。
- Sakura 优先复用已存在的 `llama-server`：
  - `SAKURA_LLAMA_SERVER`
  - `data/local_runtimes/llama_cpp/`
  - `PATH`
- 找不到时，Sakura 从 `ggml-org/llama.cpp` 最新 GitHub release 选择当前平台官方预编译包。
- 下载与解压结果写入 `data/local_runtimes/llama_cpp/`，该目录是用户态缓存，不应提交到仓库。
- 发布版或内网镜像可以提供本地 runtime manifest 固定下载源；Sakura 会优先读取 manifest，再回退到 GitHub latest。

## 跨平台选择

当前平台 key：

- macOS arm64: `macos-arm64`，优先选择 Metal 包
- macOS x64: `macos-x64`
- Windows x64: `windows-x64`
- Windows arm64: `windows-arm64`
- Linux x64: `linux-x64`
- Linux arm64: `linux-arm64`

安装器只选择基础 CPU/Metal 官方包；CUDA、ROCm、Vulkan、OpenVINO、SYCL 等加速包先不自动选择，避免驱动与分发复杂度进入默认路径。

## 运行时 manifest

manifest 用于发布版固定 llama.cpp 运行时版本、使用内网镜像、离线附带 archive，或附加 `sha256` 校验。Sakura 会按顺序读取：

1. `SAKURA_LLAMA_CPP_RUNTIME_MANIFEST` 指向的 JSON 文件。这个路径是显式覆盖；如果文件缺失或无效，会直接报错，不会静默回退到公网。
2. `data/local_runtimes/llama_cpp/runtime_manifest.json`
3. `data/local_runtimes/llama_cpp/llama_cpp_runtime_manifest.json`
4. 找不到本地 manifest 时，回退到 `ggml-org/llama.cpp` 最新 GitHub release。

示例：

```json
{
  "packages": [
    {
      "package_id": "b9763-macos-arm64-metal",
      "label": "llama.cpp b9763 macOS arm64 Metal",
      "platform_key": "macos-arm64",
      "url": "archives/llama-b9763-bin-macos-arm64.tar.gz",
      "archive_format": "tar.gz",
      "binary_relpath": "llama-server",
      "version": "b9763",
      "variant": "metal",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "size_bytes": 10978734
    }
  ]
}
```

`platform_key` 当前支持 `macos-arm64`、`macos-x64`、`windows-arm64`、`windows-x64`、`linux-arm64`、`linux-x64`。同一 manifest 可以同时放多个平台包，安装器会按当前平台选择。

`url` 可以是 HTTPS 镜像、`file://` URI，或相对 manifest 文件所在目录的本地 archive 路径。相对路径适合发布包把 archive 放在 `data/local_runtimes/llama_cpp/archives/` 下，安装时不会访问公网。

生成当前官方 release 的 manifest 模板，不下载 archive：

```bash
.venv/bin/python -m app.sensory.audio_runtime_cli runtime-manifest --relative-archive-dir archives --pretty
```

生成镜像 URL 版本并写入默认位置：

```bash
.venv/bin/python -m app.sensory.audio_runtime_cli runtime-manifest \
  --mirror-base-url https://mirror.example/llama.cpp/b9763 \
  --output data/local_runtimes/llama_cpp/runtime_manifest.json \
  --pretty
```

如果 archive 已经下载到本地目录，可以同时写入真实 `sha256` 与 `size_bytes`：

```bash
.venv/bin/python -m app.sensory.audio_runtime_cli runtime-manifest \
  --relative-archive-dir archives \
  --archive-root data/local_runtimes/llama_cpp/archives \
  --output data/local_runtimes/llama_cpp/runtime_manifest.json \
  --pretty
```

`--archive-root` 不会下载文件；它要求目录中存在与官方 release asset 同名的 archive，缺失时会报错。

发布前校验 manifest，不安装、不下载：

```bash
.venv/bin/python -m app.sensory.audio_runtime_cli runtime-manifest-check \
  --manifest data/local_runtimes/llama_cpp/runtime_manifest.json \
  --archive-root data/local_runtimes/llama_cpp/archives \
  --require-known-platforms \
  --pretty
```

校验会检查 package 结构、必需平台、相对路径或 `file://` archive 是否存在、`size_bytes` 和 `sha256` 是否匹配。HTTPS URL 不会联网；如需校验镜像文件，配合 `--archive-root` 指向本地 archive 目录。

## 一键准备与模型默认值

设置页“准备 llama.cpp 音频后端”会按当前音频源执行：

1. 优先复用已存在的 `llama-server`，找不到时按 runtime manifest 或 GitHub latest 安装当前平台包。
2. 检查推荐 GGUF 模型是否已在 `data/sensory_models/<source>/...` 缓存。
3. 检查本地磁盘空间是否足够容纳推荐模型下载，并保留安全余量。
4. 未缓存时，在用户确认后下载推荐文件；有 Hugging Face CLI 时优先使用 `hf`，没有 `hf` 时使用内置 HTTP 直连下载匹配文件。
5. 成功后把模型字段指向本地缓存目录，避免后续 smoke 或真实调用再让 `llama-server -hf` 拉模型。

如果只安装运行时或命令行使用远端 `-hf`，推荐值为：

- 语音：`ggml-org/Qwen3-ASR-0.6B-GGUF:Q8_0`
- 声音事件：`ggml-org/ultravox-v0_5-llama-3_2-1b-GGUF:Q4_K_M`

推荐模型下载会限制文件范围，避免拉取完整仓库：

- 语音：`--include Qwen3-ASR-0.6B-Q8_0.gguf --include mmproj-Qwen3-ASR-0.6B-Q8_0.gguf`
- 声音事件：`--include Llama-3.2-1B-Instruct-Q4_K_M.gguf --include mmproj-*.gguf`

这些是推荐值，不覆盖用户已填写的非一键模型。用户也可以手动从 Hugging Face 下载模型到本地；手动下载任意仓库仍需要 Hugging Face CLI。在本机 llama.cpp 模式下，模型字段会优先使用下载后的本地目录。

截至 2026-06-23，推荐 smoke 下载量约为：

- Qwen3-ASR Q8_0 + mmproj：约 1.0 GB
- Ultravox Q4_K_M + mmproj：约 2.1 GB

设置页“测试模型”遇到这些推荐远端模型时，会先弹窗确认下载量；用户拒绝时不会启动 sidecar 或下载模型。若已通过“一键准备”缓存到本地目录，测试会直接使用本地路径。

命令行也提供同一套一键准备入口。没有 `--yes` 时只做只读检查，发现需要下载运行时或模型会返回 `ok=false`，不会触网下载：

```bash
.venv/bin/python -m app.sensory.audio_runtime_cli prepare-backend --source speech --pretty
```

确认下载后再执行：

```bash
.venv/bin/python -m app.sensory.audio_runtime_cli prepare-backend --source speech --yes --pretty
```

## 调用链

1. 设置页保存 provider extra：
   - `backend=llama`
   - `managed_runtime=llama.cpp`
   - `llama_binary_path`
   - `llama_runtime_install_dir`
   - `llama_runtime_package_id`
2. `build_provider_registry(..., base_dir, resource_registry)` 创建 `ManagedLlamaCppSensoryProvider`。
3. 第一次音频感知调用前，provider 启动 `llama-server` sidecar。
4. sidecar 通过 OpenAI-compatible `/v1/chat/completions` 接收 `input_audio`。
5. 进程通过 `ResourceRegistry` 接管，随 Sakura 生命周期清理。

## 日志

Sakura 管理的 `llama-server` stdout/stderr 写入：

```text
data/logs/sensory-llama-server.log
```

模型下载、GGUF 加载、端口占用、Metal/CUDA/CPU 初始化等问题优先看这个文件。启动超时或进程立即退出时，错误消息会带上该日志路径。

## 验证

轻量验证：

```bash
.venv/bin/python -m pytest tests/unit/test_sensory.py tests/unit/test_sensory_llama_cpp_runtime.py tests/unit/test_sensory_huggingface.py tests/ui/test_pet_window.py -q
```

命令行 dry-run，不下载模型、不启动 sidecar：

```bash
.venv/bin/python -m app.sensory.audio_runtime_cli plan --source speech --managed-llama-defaults --pretty
```

整体诊断，不下载、不安装、不启动 sidecar：

```bash
.venv/bin/python -m app.sensory.audio_runtime_cli doctor --pretty
```

`doctor` 会汇总当前平台、`llama-server` 是否可用、Hugging Face CLI 是否可用、内置推荐文件下载是否可用、本地 runtime manifest 候选、本地推荐模型缓存、推荐模型磁盘空间预检、语音/声音默认模型 smoke plan、以及下一步动作建议。

设置页在“本机运行框架 + llama.cpp”后端下也提供“诊断 llama.cpp”按钮，使用同一套检查逻辑，不下载、不安装、不启动 sidecar。

`plan` 输出中几个字段用于发布预检：

- `runtime_requirement`: `cached` 表示已找到本机 `llama-server`，`download_required` 表示需要安装官方运行时，`external_service` 表示该 provider 依赖外部 LM Studio/Ollama/API 服务。
- `requires_runtime_download`: 是否需要 Sakura 下载官方 llama.cpp 运行时。
- `model_location`: `local` 表示本地 GGUF，`huggingface` 表示 managed llama.cpp 首次运行可能通过 `-hf` 拉取模型，`provider` 表示模型由外部服务管理。
- `requires_model_download`: 真实 smoke 是否可能触发模型下载。

底层本机运行时安装验证。没有可用 `llama-server` 时，必须显式传入 `--yes` 才会下载官方 llama.cpp 运行时；普通用户优先使用上面的 `prepare-backend`：

```bash
.venv/bin/python -m app.sensory.audio_runtime_cli install-runtime --yes --pretty
```

真实音频模型 smoke 会下载 GGUF 模型，可能占用数百 MB 到数 GB。命令行同样默认拒绝这一步；确认后需要显式传入：

```bash
.venv/bin/python -m app.sensory.audio_runtime_cli smoke --source speech --managed-llama-defaults --allow-model-download --pretty
```
