# 增强感知音频推理运行时

本模块提供可选的本机音频推理后端部署路径，用于语音与声音事件增强感知。目标是让普通用户不需要手动编译推理框架，同时保持默认关闭、跨平台、可回退。

## 运行时边界

- 默认不启动、不下载、不采集音频。
- 只有用户在设置页选择“本机运行框架”与 `llama.cpp` 后端，并点击“配置 llama.cpp 运行时”时，才会准备本机运行时。
- Sakura 优先复用已存在的 `llama-server`：
  - `SAKURA_LLAMA_SERVER`
  - `data/local_runtimes/llama_cpp/`
  - `PATH`
- 找不到时，Sakura 从 `ggml-org/llama.cpp` 最新 GitHub release 选择当前平台官方预编译包。
- 下载与解压结果写入 `data/local_runtimes/llama_cpp/`，该目录是用户态缓存，不应提交到仓库。

## 跨平台选择

当前平台 key：

- macOS arm64: `macos-arm64`，优先选择 Metal 包
- macOS x64: `macos-x64`
- Windows x64: `windows-x64`
- Windows arm64: `windows-arm64`
- Linux x64: `linux-x64`
- Linux arm64: `linux-arm64`

安装器只选择基础 CPU/Metal 官方包；CUDA、ROCm、Vulkan、OpenVINO、SYCL 等加速包先不自动选择，避免驱动与分发复杂度进入默认路径。

## 模型默认值

配置 llama.cpp 运行时成功后，如果当前音频源没有模型，设置页会填入带量化后缀的 llama.cpp `-hf` 推荐值：

- 语音：`ggml-org/Qwen3-ASR-0.6B-GGUF:Q8_0`
- 声音事件：`ggml-org/ultravox-v0_5-llama-3_2-1b-GGUF:Q4_K_M`

这些是推荐值，不覆盖用户已填写的模型。用户也可以从 Hugging Face 下载模型到本地；在本机 llama.cpp 模式下，模型字段会优先使用下载后的本地目录。

截至 2026-06-23，推荐 smoke 下载量约为：

- Qwen3-ASR Q8_0 + mmproj：约 1.0 GB
- Ultravox Q4_K_M + mmproj：约 2.1 GB

设置页“测试模型”遇到这些推荐远端模型时，会先弹窗确认下载量；用户拒绝时不会启动 sidecar 或下载模型。

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
.venv/bin/python -m pytest tests/unit/test_sensory.py tests/unit/test_sensory_llama_cpp_runtime.py tests/ui/test_pet_window.py -q
```

本机运行时验证：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from app.sensory.llama_cpp_runtime import (
    fetch_latest_llama_cpp_runtime_packages,
    select_llama_cpp_runtime_package,
    install_llama_cpp_runtime_package,
    llama_cpp_platform_key,
    discover_llama_server_binary,
)

base_dir = Path(".")
packages = fetch_latest_llama_cpp_runtime_packages(timeout_seconds=20)
package = select_llama_cpp_runtime_package(packages, platform_key=llama_cpp_platform_key())
result = install_llama_cpp_runtime_package(base_dir, package, timeout_seconds=300)
print(result.to_mapping())
print(discover_llama_server_binary(base_dir))
PY
```

不下载模型的配置 dry-run：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from app.sensory.audio_smoke import build_sensory_audio_smoke_plan
from app.sensory.models import SensoryProviderMode, SensorySource
from app.sensory.settings import SensoryProviderConfig

plan = build_sensory_audio_smoke_plan(
    SensoryProviderConfig(
        provider_id="speech_local",
        source=SensorySource.SPEECH,
        mode=SensoryProviderMode.LOCAL,
        endpoint="http://127.0.0.1:18080/v1",
        model="ggml-org/Qwen3-ASR-0.6B-GGUF:Q8_0",
        extra={"backend": "llama", "managed_runtime": "llama.cpp"},
    ),
    base_dir=Path("."),
    source=SensorySource.SPEECH,
)
print(plan.to_mapping())
PY
```

真实音频模型 smoke 会下载 GGUF 模型，可能占用数百 MB 到数 GB。需要用户确认后再运行。
