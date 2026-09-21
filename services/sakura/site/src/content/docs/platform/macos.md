---
title: macOS 指南
description: Apple Silicon、Rosetta、SSL 证书、角色包和 GPT-SoVITS 的 macOS 注意事项。
---

# macOS 指南

Sakura 基于 PySide6，本身是跨平台的。仓库自带的 `install.bat` 和 `start.bat` 仅适用于 Windows；macOS 用户建议按源码路径运行。

## 快速路径

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-macos-intel.txt
# 仅当 venv 为 x86/Rosetta 时需要
python main.py
```

启动前记得在 `data/config/api.yaml` 中填入 LLM API 配置。

## Apple Silicon 与 Rosetta

先确认 Python 实际架构：

```bash
python3 -c "import platform; print(platform.machine())"
```

| 输出 | 说明 | 建议 |
|---|---|---|
| `arm64` | 原生 Apple Silicon | 新版 PyTorch 可用，不需要额外 pin 文件 |
| `x86_64` | 正在 Rosetta 下运行 | 需要 `pip install -r requirements-macos-intel.txt` |

在 x86/Rosetta 环境中，PyTorch 最高只到 2.2.2，与 NumPy 2 和新版 `transformers` 不兼容。每次 `pip install -r requirements.txt` 后，都要重新套用 `requirements-macos-intel.txt`。

## SSL 证书

python.org 的 macOS 安装包不会自动安装根证书，可能导致 API 请求报错：

```text
[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate
```

执行一次证书安装命令即可：

```bash
/Applications/Python\ 3.12/Install\ Certificates.command
```

版本目录按你的 Python 版本调整。Homebrew 或 conda 版 Python 通常已自带证书。

## 角色包

`.char` 角色包本质是 ZIP 压缩包。macOS 自带 `unzip` 可能弄乱包内 UTF-8 中文或日文文件名，建议用 Python 解压并修正编码。

最终结构必须是：

```text
characters/<id>/character.json
characters/<id>/card.md
characters/<id>/portraits/
characters/<id>/voice/
```

## GPT-SoVITS 语音

TTS 是可选功能。要在 macOS 上用语音，建议自己运行 GPT-SoVITS 服务器，然后让 Sakura 用 `custom-gpt-sovits` provider 指向它。

Apple Silicon 推荐 conda 原生安装 arm64 版：

```bash
conda create -n GPTSoVITS python=3.10 -y
conda activate GPTSoVITS
git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS.git
cd GPT-SoVITS
bash install.sh --device MPS --source HF
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

然后在 Sakura 的 `data/config/api.yaml` 中配置：

```yaml
tts:
  provider: custom-gpt-sovits
  enabled: true
  gpt_sovits:
    api_url: http://127.0.0.1:9880/tts
    ref_lang: ja
    text_lang: ja
    timeout_seconds: 120
```

先启动 GPT-SoVITS 服务器再启动 Sakura。Sakura 会推送角色微调后的权重并调用 `/tts`。

## MCP 与插件

| 能力 | macOS 状态 |
|---|---|
| Web MCP 服务器 | 可用，纯标准库 |
| Windows MCP 服务器 | 仅 Windows，macOS 保持关闭即可 |
| `playwright_browser` 插件 | 执行 `playwright install chromium` 后可用 |
