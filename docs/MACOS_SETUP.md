# 在 macOS 上运行 Sakura

Sakura 基于 PySide6（Qt）开发，本身是跨平台的，因此可以在 macOS 上从源码运行。
仓库自带的 `install.bat` / `start.bat` 仅适用于 Windows；本文档说明 macOS 的运行路径
以及我们踩到的平台相关问题。

> 在 Apple Silicon（M2 Pro）Mac 上测试。多数说明同样适用于 Intel Mac。

## 速查（TL;DR）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-macos-intel.txt   # 仅当 venv 为 x86 / Rosetta 时需要，见 §1
# 修复 python.org 版 Python 的 SSL 证书（见 §2）
# 在 data/config/api.yaml 填入你的 LLM API Key
python main.py
```

---

## 1. 架构：Apple Silicon vs. Rosetta（重要）

先确认你的 Python 实际是哪种架构：

```bash
python3 -c "import platform; print(platform.machine())"
```

- **`arm64`** —— 原生 Apple Silicon。新版 PyTorch 可用，**不需要**额外的 pin 文件。
- **`x86_64`** —— 你正运行在 **Rosetta** 下（即使是 Apple Silicon，只要终端/Python 是 x86
  就会这样）。在 x86 macOS 下，PyTorch 最高只到 **2.2.2**，与 NumPy 2 和新版
  `transformers` 不兼容。请套用 pin：

  ```bash
  pip install -r requirements-macos-intel.txt
  ```

  该文件锁定 `numpy<2` 与 `transformers>=4.41,<4.45`。**每次执行
  `pip install -r requirements.txt` 之后都要重新套用一次**，否则会被重新拉回不兼容
  的版本，导致长期记忆功能（mem0 本地向量）失效。

  若不套用，典型症状：`Failed to initialize NumPy: _ARRAY_API not found`，或
  `transformers` 输出「Disabling PyTorch because PyTorch >= 2.4 is required」。

> 预编译的 macOS 发布包是 **arm64-only**，所以若你处于 x86/Rosetta 环境，请按本文档
> 从源码运行，而不是用发布包。

---

## 2. SSL 证书（python.org 版 Python）

python.org 的 macOS 安装包**不会**安装根证书，导致 app 基于 `urllib` 的 API 请求报错：

```
[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate
```

执行一次即可永久修复（对该 Python 全系统生效）：

```bash
/Applications/Python\ 3.12/Install\ Certificates.command
```

（版本目录请按你的 Python 版本调整。）此命令会安装 `certifi` 并链接框架的 `cert.pem`。
Homebrew / conda 版的 Python 通常已自带证书。

---

## 3. macOS 上的角色包（`.char`）

角色包扩展名为 `.char`，但其实是 **ZIP 压缩包**（`sakura.character.archive` 格式）。
需把内部的 `character/` 目录解压成 `characters/<id>/`。

macOS 自带的 CLI `unzip` 会**弄乱压缩包里的 UTF-8（中文/日文）文件名**（报
「Illegal byte sequence」），因为压缩包未设置 UTF-8 标志位，`unzip` 退回到本地代码页。
请改用 Python 解压并修正文件名编码：

```python
import zipfile, shutil
from pathlib import Path

z = zipfile.ZipFile("YourPack.char")
dst = Path("characters/yourpack")
for info in z.infolist():
    if info.is_dir() or not info.filename.startswith("character/"):
        continue
    name = info.filename
    if not (info.flag_bits & 0x800):          # 未设置 UTF-8 标志位
        name = name.encode("cp437").decode("utf-8")
    target = dst / name[len("character/"):]
    target.parent.mkdir(parents=True, exist_ok=True)
    with z.open(info) as src, open(target, "wb") as out:
        shutil.copyfileobj(src, out)
```

最终结构必须是 `characters/<id>/character.json`（外加 `card.md`、`portraits/`、可选的 `voice/`）。

---

## 4. 语音 / TTS（GPT-SoVITS）在 macOS 上

TTS 是**可选**功能（不开也能用，只是显示字幕没有声音）。若想要语音：

### app 无法在 macOS 上自动安装 TTS 服务器
内置的 TTS bundle 下载器附带的是 **Windows 运行时**（`runtime/python.exe`），
所以 app 内的「自动启动 GPT-SoVITS」在 macOS 上无效。你需要**自己运行一个 GPT-SoVITS
服务器**，并用 **`custom-gpt-sovits`** provider 让 app 指向它。

### 推荐：用 conda 原生安装 arm64 版（Apple Silicon）
让服务器原生运行（而非 Rosetta）速度更快、兼容性更好：

```bash
conda create -n GPTSoVITS python=3.10 -y      # miniforge = arm64
conda activate GPTSoVITS
git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS.git
cd GPT-SoVITS
bash install.sh --device MPS --source HF       # 安装 torch 并下载底模
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

### 我们踩到的坑

- **`opencc` 编译失败**（`ld: symbol(s) not found for architecture x86_64`）。
  `requirements.txt` 里有 `--no-binary=opencc`，强制源码编译，而在 Rosetta 下会误判架构。
  删掉那一行，改用预编译 wheel：`pip install opencc`。

- **源码编译的 wheel 被缓存成 x86。** 如果任何 `pip install` 是在 Rosetta（x86）shell 里
  执行的，像 `jieba_fast` 这类包会被编译/缓存成 x86_64 并被复用，运行时报
  `incompatible architecture (have 'x86_64', need 'arm64')`。解决：清缓存并在 arm64 下重装：
  ```bash
  arch -arm64 python -m pip cache purge
  arch -arm64 python -m pip install --force-reinstall --no-cache-dir <pkg>
  ```

- **MPS 撞到硬性限制。** GPT-SoVITS v2 的解码器会触发
  `Output channels > 65536 not supported at the MPS device`。这是硬性约束，
  连 `PYTORCH_ENABLE_MPS_FALLBACK=1` 也救不了。请在
  `GPT_SoVITS/configs/tts_infer.yaml`（`custom` 配置段）把设备改成 **`cpu`**，
  并设 `is_half: false`。在 M2 Pro 上，一句话用 CPU 约 7 秒合成完成 —— 对桌宠完全够用。

### 让 app 指向服务器
在 `data/config/api.yaml` 中：

```yaml
tts:
  provider: custom-gpt-sovits
  enabled: true
  gpt_sovits:
    api_url: http://127.0.0.1:9880/tts
    ref_lang: ja      # 与角色语音语言一致
    text_lang: ja
    timeout_seconds: 120
```

**启动 Sakura 之前先启动 GPT-SoVITS 服务器。** app 会推送角色微调后的
`.ckpt`/`.pth`（通过 `/set_gpt_weights` + `/set_sovits_weights`）并调用 `/tts`。

---

## 5. macOS 上的 MCP 工具与插件

- **`web` MCP 服务器**（网页搜索 / 抓取）—— 可用（纯标准库）。
- **`windows` MCP 服务器** —— 仅 Windows（`pywin32`）；默认关闭，保持关闭即可。
- **`playwright_browser` 插件** —— 执行 `playwright install chromium` 后可用。

---

## 6. 快速参考

```bash
# 一次性
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-macos-intel.txt        # 仅 x86/Rosetta 需要
/Applications/Python\ 3.12/Install\ Certificates.command

# 每次运行
source .venv/bin/activate && python main.py
# （可选）先在另一个终端启动 GPT-SoVITS 语音服务器
```
