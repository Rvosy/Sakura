# SenseVoice provider sources

The Python runtime dependency is `sherpa-onnx==1.13.7` (Apache-2.0), including
the matching `sherpa-onnx-core` binary wheel. NumPy 2.2.6 is BSD-3-Clause.
Dependencies are installed into the provider's isolated dependency root, not Core.

SenseVoiceSmall INT8 and its vocabulary are converted ONNX artifacts published by
the sherpa-onnx maintainer `csukuangfj`:

- [Pinned conversion repository](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tree/2365baeacb507f821a0c8120fcee3d484dba7a07)
- [Upstream SenseVoice code and model information](https://github.com/FunAudioLLM/SenseVoice)
- [Conversion repository license notice](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/blob/2365baeacb507f821a0c8120fcee3d484dba7a07/LICENSE)

The conversion's license notice delegates to FunASR's license information. The
[upstream SenseVoiceSmall model card](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)
identifies its weights as `model-license` and links the
[FunASR Model Open Source License Agreement](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE).
Those terms apply to the model weights and their conversion; they are separate
from the MIT SenseVoice code and Apache-2.0 sherpa-onnx runtime. Retain the model
license and conversion notice with any offline model distribution.

Silero VAD is MIT licensed by the Silero Team. This provider uses the
[sherpa-onnx ONNX release artifact](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx).
See the [Silero source and license](https://github.com/snakers4/silero-vad).

The resource version and required file sizes are fixed in `_resources.py`.
Installation is an explicit Settings action. It checks download lengths before
atomically publishing the complete directory. Startup/status/warmup never download.
An offline preinstallation needs the matching files and a `complete.json` marker
with the resource version. Legacy digest fields are ignored; native inference
validates model loading without a separate content scan. The existing version
label and cache directory remain unchanged for installed resources.

The five upstream `test_wavs` files were used as public validation inputs. They
are not bundled with this plugin. No user recordings are included in tests.
