# SenseVoice 资源来源

运行依赖为 Apache-2.0 许可的 `sherpa-onnx==1.13.7` 及对应 `sherpa-onnx-core`，NumPy 2.2.6 使用 BSD-3-Clause。依赖安装在插件自己的环境中。

## 识别模型

SenseVoiceSmall INT8 模型和词表来自 sherpa-onnx 维护者 `csukuangfj` 发布的 ONNX 转换：

- [转换仓库与固定版本](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tree/2365baeacb507f821a0c8120fcee3d484dba7a07)
- [ModelScope 镜像](https://www.modelscope.cn/models/gomodels/sherpa/files?Revision=590473aaa26eed19b270a424cb641972ae56482d)：目录 `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/` 下的 `model.int8.onnx` 和 `tokens.txt`
- [SenseVoice 上游](https://github.com/FunAudioLLM/SenseVoice)
- [转换仓库许可说明](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/blob/2365baeacb507f821a0c8120fcee3d484dba7a07/LICENSE)

[SenseVoiceSmall 模型卡](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)将权重标为 `model-license`，具体条款见 [FunASR 模型开源许可](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)。模型及转换产物遵循该许可，与 SenseVoice 代码的 MIT 许可、sherpa-onnx 运行库的 Apache-2.0 许可分别适用。离线分发模型时保留模型许可和转换说明。

## 语音活动检测

Silero VAD 来自 [sherpa-onnx 发布的 ONNX 文件](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx)，使用 Silero Team 的 MIT 许可，见[上游代码与许可证](https://github.com/snakers4/silero-vad)。

安装和资源管理方式见[插件说明](README.md)。
