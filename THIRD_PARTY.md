# 第三方组件

本程序使用下列开源组件。打包文件中的相关库仍遵循其各自许可证；分发修改版时应保留所需版权和许可信息。

- [PySide6 / Qt](https://doc.qt.io/qtforpython-6/licenses.html)：LGPLv3/GPLv3 或商业许可。此分发使用动态链接的未修改 Qt 库，库文件位于 `_internal/PySide6`。许可证与第三方通知见 `licenses`；源代码与许可信息见官方链接及 [Qt 6.8.3 源码](https://download.qt.io/archive/qt/6.8/6.8.3/single/)。
- [SoundCard](https://github.com/bastibe/SoundCard)：BSD-3-Clause。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)：MIT。
- [CTranslate2](https://github.com/OpenNMT/CTranslate2)：MIT。
- [Whisper](https://github.com/openai/whisper)：代码及模型权重为 MIT；自动下载 [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) 的 CTranslate2 转换版本，版本 536b0662742c02347bc0e980a01041f333bce120。
- [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)：Apache-2.0；默认下载 [Unsloth Q4_K_M 量化版](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF)，版本 e87f176479d0855a907a41277aca2f8ee7a09523。
- [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B)：Apache-2.0；可选下载 [Unsloth Q4_K_M 量化版](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF)，版本 f6d5376be1edb4d416d56da11e5397a961aca8ae。两种模型均仅加载语言权重，不包含视觉投影模型；发布包不附带模型文件。
- [llama.cpp](https://github.com/ggml-org/llama.cpp) 及 [llama-cpp-python](https://github.com/abetlen/llama-cpp-python)：MIT，Vulkan 后端来自上游预编译轮子；运行时动态加载本机显卡驱动。
- [NumPy](https://numpy.org/doc/stable/license.html)：BSD-3-Clause。
- [Requests](https://requests.readthedocs.io/en/latest/community/faq/#what-is-the-license)：Apache-2.0。
- [ONNX Runtime](https://github.com/microsoft/onnxruntime)：MIT。
- [PyAV](https://github.com/PyAV-Org/PyAV)：BSD-3-Clause；其 FFmpeg 库遵循各自许可证，详情见打包的 PyAV 许可信息。
- [PyInstaller](https://pyinstaller.org/en/stable/license.html)：GPL 及允许生成非 GPL 应用的引导程序例外。

完整组件版本可参见 `requirements-installed.txt`，许可原文位于 `licenses`。此版本不使用在线翻译服务。
