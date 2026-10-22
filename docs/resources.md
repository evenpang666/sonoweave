# GPU 与 CPU 资源分配

Windows 和 Linux 使用同一套加载与回退逻辑。默认优先使用 CUDA；没有 NVIDIA GPU 时，可沿用 Vulkan 翻译或 CPU。安装器根据驱动选择 CUDA 12.4 / Vulkan 轮子，也可通过 `Install.bat --cuda` 或 `bash Install.sh --cuda` 显式切换。

## 分配顺序

1. 主 Whisper 先加载并预热，两个模型的加载由同一把锁串行处理，避免同时根据旧的空闲显存作决定。
2. Whisper 优先 CUDA FP16；空间较紧时使用 CUDA INT8/FP16 混合计算；无法容纳时使用 CPU INT8。只有显存分配错误才进一步尝试较小的 CUDA 精度；库或设备不可用时直接回退 CPU。
3. Qwen 根据此时的可用显存、GGUF 实际大小和层数、上下文长度估算可放入 GPU 的层数。空间充足时全层 GPU，空间不足时部分层 GPU、其余 CPU，空间很少时全 CPU。加载失败会减少 GPU 层数，最终回退 CPU。
4. 估算额外保留 768 MiB 显存，并为 Qwen 的上下文和临时计算缓冲预留空间。CUDA 加载选择可见设备中空闲显存最多的 GPU；尊重 `CUDA_VISIBLE_DEVICES`。显存查询不可用时仍可尝试 GPU，以实际加载结果决定回退。

显存估算是保守启发式，不代表所有模型、上下文和驱动的精确峰值，也不保证全局最优延迟。其他程序可随时占用显存，因此真实分配错误仍是最终依据。库初始化失败的模型不会被标记为已就绪。

## 运行中显存不足

Whisper 会完整取出惰性的转写结果后再发布；如果在中途发生 CUDA 显存不足，释放 GPU 模型并在 CPU 上重试同一份音频，不发布一半的转写。Qwen 同样先关闭生成流和 GPU 模型，再用相同模型在 CPU 上重试当前文本。两端保留语言、字幕上下文与缓存，后续任务继续使用 CPU。

为避免每句话都重新加载模型，回退后不会自动来回迁移；重新连接或切换模型时重新评估。人声分段与声纹沿用 CPU；当前仅处理轮流说话，不启动重叠分离或额外的 Whisper 实例。

实际分配写入应用日志：Whisper 的设备与精度、Qwen 的设备与 GPU 层数。全 GPU 为 `CUDA`，混合为 `CUDA+CPU`，CPU 为 `CPU`。显存查询仅发生在加载或恢复阶段，不在逐 token 路径中调用外部命令。

## 验证

`tests/test_resources.py` 覆盖低显存规划、可见 GPU 映射、惰性转写中途 OOM、Qwen 降低 GPU 层数、运行中 OOM 重试和显式 CPU 设置。`tests/test_engine.py` 验证 Whisper 预热前不会加载 Qwen，以及等待加载时可以退出。

`python main.py --check-devices artifacts/devices.json` 可用本地模型验证两种设备的真实推理并输出 JSON；不采集声音、不下载模型、不启动字幕窗口。打包版可直接给 `SonoWeave.exe` / `SonoWeave` 传入同样的参数。CUDA 打包会收集动态加载的 CUDA 12 / cuDNN 9 库；`tools/verify_bundle.py --preview-only --require-cuda` 同时检查界面、真实模型推理和 Windows CUDA DLL 是否来自打包目录。打包验证可通过 `SONOWEAVE_MODELS_DIR` 指定已有的本地模型目录。

可用 `tools/benchmark.py` 和 `tools/benchmark.py --cpu` 对同一公开音频依次测试流式延迟；脚本不播放音频，不采集系统声音，并阻止 Python 网络连接。模型启动时间与流式处理延迟分别记录。真实吞吐取决于硬件、后台负载和字幕内容，不能由单个样本推断总体准确率。

实现参考：[faster-whisper 的设备、精度及 CUDA 库要求](https://github.com/SYSTRAN/faster-whisper#gpu)、[官方 CUDA 12.4 轮子](https://abetlen.github.io/llama-cpp-python/whl/cu124/llama-cpp-python/)、[llama-cpp-python 加载选项](https://llama-cpp-python.readthedocs.io/en/latest/api-reference/#llama_cpp.Llama)。
