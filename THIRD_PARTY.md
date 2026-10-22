# 第三方组件

本项目原创代码采用 [MIT 许可证](LICENSE)。本程序使用下列开源组件，相关库和模型仍遵循其各自许可证；分发修改版时应保留所需版权和许可信息。

- [PySide6 / Qt](https://doc.qt.io/qtforpython-6/licenses.html)：LGPLv3/GPLv3 或商业许可。此分发使用动态链接的未修改 Qt 库，库文件位于 `_internal/PySide6`。许可证与第三方通知见 `licenses`；源代码与许可信息见官方链接及 [Qt 6.8.3 源码](https://download.qt.io/archive/qt/6.8/6.8.3/single/)。
- [SoundCard](https://github.com/bastibe/SoundCard)：BSD-3-Clause。
- [pulsectl](https://github.com/mk-fg/python-pulse-control)：MIT，Linux 上查询默认输出对应的 monitor 源；通过动态链接的系统 libpulse 访问 PulseAudio / pipewire-pulse，系统组件遵循各自许可证。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)：MIT。
- [CTranslate2](https://github.com/OpenNMT/CTranslate2)：MIT。
- [Whisper](https://github.com/openai/whisper)：代码及模型权重为 MIT；自动下载 [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) 的 CTranslate2 转换版本，版本 536b0662742c02347bc0e980a01041f333bce120。
- [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)：Apache-2.0；默认下载 [Unsloth Q4_K_M 量化版](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF)，版本 e87f176479d0855a907a41277aca2f8ee7a09523。
- [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B)：Apache-2.0；可选下载 [Unsloth Q4_K_M 量化版](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF)，版本 f6d5376be1edb4d416d56da11e5397a961aca8ae。两种模型均仅加载语言权重，不包含视觉投影模型；发布包不附带模型文件。
- [llama.cpp](https://github.com/ggml-org/llama.cpp) 及 [llama-cpp-python](https://github.com/abetlen/llama-cpp-python)：MIT，CUDA / Vulkan 后端来自上游预编译轮子；运行时动态加载本机显卡驱动。
- [NVIDIA CUDA](https://docs.nvidia.com/cuda/eula/index.html) / [cuDNN](https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/eula.html)：CUDA 构建可能附带本机安装的可再分发推理运行库，各自遵循 NVIDIA 的许可；不附带显示驱动，使用机器仍需安装兼容驱动。
- [NumPy](https://numpy.org/doc/stable/license.html)：BSD-3-Clause。
- [Requests](https://requests.readthedocs.io/en/latest/community/faq/#what-is-the-license)：Apache-2.0。
- [ONNX Runtime](https://github.com/microsoft/onnxruntime)：MIT。
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)：Apache-2.0，用于本地人声分段与特征提取。
- [pyannote segmentation 3.0](https://huggingface.co/pyannote/segmentation-3.0)：MIT；下载 [sherpa-onnx ONNX int8 转换版](https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0)，固定版本 9403a6902bb58e3d5ae8c7e77c3422de279db2e0。
- [3D-Speaker](https://github.com/modelscope/3D-Speaker)：Apache-2.0；下载 [CAMPPlus 中英文 ONNX 声纹模型](https://huggingface.co/csukuangfj/speaker-embedding-models)，固定版本 0743f301363dec56491a490f6d6cbc9d67f9a3bf，文件 `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`。人声模型不随发布包附带，首次准备时下载。
- [Conv-TasNet Libri2Mix sepclean 16 kHz](https://huggingface.co/JorisCos/ConvTasNet_Libri2Mix_sepclean_16k)：保留的离线分离实验工具使用此模型，未接入当前字幕流程。原模型由 Joris Cosentino 等提供，权重采用 CC-BY-SA-4.0；[welcomyou ONNX 转换版](https://huggingface.co/welcomyou/convtasnet-libri2mix-16k-onnx)固定版本 da50e0fa7789356790994bc898290134fef5d42d，文件 `convtasnet_16k.onnx`。当前程序不自动下载或加载此模型，不随发布包附带。
- [PyAV](https://github.com/PyAV-Org/PyAV)：BSD-3-Clause；其 FFmpeg 库遵循各自许可证，详情见打包的 PyAV 许可信息。
- [PyInstaller](https://pyinstaller.org/en/stable/license.html)：GPL 及允许生成非 GPL 应用的引导程序例外。
- [uv](https://github.com/astral-sh/uv)：MIT / Apache-2.0，源码安装时按需下载；托管 Python 来自 [python-build-standalone](https://github.com/astral-sh/python-build-standalone)，CPython 及所含组件遵循各自许可证。安装器不把这些运行环境文件纳入 Git。

完整组件版本可参见 `requirements-installed.txt`，许可原文位于 `licenses`。此版本不使用在线翻译服务。
