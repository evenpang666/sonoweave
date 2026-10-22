# 开发、配置与验证

## 从源码安装

无需提前安装 Python。安装器自动准备 uv、托管 Python 3.12 和独立环境，详见 [自动安装](installation.md)。

以下命令用于 Windows。Linux 的依赖、安装、测试和打包见 [Linux 安装与使用](linux.md)，使用独立的 `.venv-linux` 环境。

1. 双击 `Start.bat`，缺少或损坏的运行环境会先自动安装，再启动字幕。也可先运行 `Install.bat` 仅安装环境；首次需要网络。
2. 程序自动下载缺少的识别模型和当前选择的翻译模型；完成后可断网使用。
3. 可选：提前运行 `PrepareModels.bat` 下载默认组合；`PrepareModels.bat --model 2b` 下载 2B 组合，`PrepareModels.bat --model both` 提前准备两种翻译模型。

```powershell
.venv\Scripts\python.exe main.py
.venv\Scripts\python.exe -m ensurepip
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tools\smoke.py
.venv\Scripts\python.exe tools\quality_check.py
.venv\Scripts\python.exe tools\benchmark.py
.venv\Scripts\python.exe tools\benchmark.py --cpu
.venv\Scripts\python.exe tools\live_test.py --seconds 90
.venv\Scripts\python.exe main.py --preview
.venv\Scripts\python.exe main.py --screenshot artifacts\preview.png
```

`smoke.py` 会阻止 Python 网络连接，使用本地模型和公开测试语音验证真实离线识别、翻译及上下文词义消歧。若缺少测试语音，先执行 `tools/prepare_models.py --test-sample`。`benchmark.py` 按真实时间将该语音输入流式处理链路，不会向扬声器播放声音。`live_test.py` 则实际监听默认输出设备，验证两个缓存持续增长；不保存音频，会将测试时的原文、译文与界面诊断保存在被 Git 忽略的 `.cache/<label>-test` 下，发布程序不包含这些内容。`--preview` 与 `--screenshot` 显示示例字幕，不代表现场识别结果。

`quality_check.py` 使用公开短句做翻译抽查，输出原文、译文、耗时及关键词命中；关键词命中不能证明语义正确。可先运行 `powershell -File tools/make_asr_fixture.ps1`，再执行 `quality_check.py --compare-asr`，比较本地 SAPI 合成的固定语音；该小样本与真实后台声音测试分别记录，不能推导所有语音的识别准确率。候选模型可通过 `--model` 指定，报告可通过 `--output` 指定。

打包：运行 `powershell -ExecutionPolicy Bypass -File .\Build.ps1`，生成 `dist/SonoWeave`；不复制模型文件，在首次使用所选模型时自动下载。

可选环境变量：

| 变量 | 用途 |
| --- | --- |
| `SONOWEAVE_HOME` | 设置与日志目录 |
| `SONOWEAVE_MODELS_DIR` | 下载及读取模型的目录；便于提前配置离线模型 |
| `SONOWEAVE_MODEL` | 本地 CTranslate2 Whisper 模型目录 |
| `SONOWEAVE_TRANSLATE_MODEL` | 诊断脚本的本地 GGUF 覆盖路径；桌面菜单按所选 4B/2B 读取 |
| `SONOWEAVE_ASR_DEVICE` | `auto`（默认）或 `cpu` |
| `SONOWEAVE_TRANSLATE_DEVICE` | `auto`（默认）或 `cpu` |

自动下载及准备脚本可使用 `HF_ENDPOINT` 和 `HTTPS_PROXY`；它们仅用于获取模型文件。技术参考：[SoundCard](https://soundcard.readthedocs.io/en/latest/)、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[llama-cpp-python](https://github.com/abetlen/llama-cpp-python)。

## 仓库与产物

- 提交源码、自动化测试、依赖声明和维护文档。`tests/`、`tools/` 保留跟踪。
- 不提交模型、缓存、虚拟环境、构建目录、运行日志、测试截图、验证数据或本机 `pip freeze` 快照。`.venv/`、`.venv-linux/`、`artifacts/`、`.cache/`、`.runtime/`、`models/`、`build/`、`dist/` 均由 `.gitignore` 排除。
- 历史验证文档和清理前的 README 仅作本地归档，位于 `docs/archive/verification/`，该目录同样忽略。测试脚本按需创建输出目录，干净检出不需要携带历史产物。
- 打包复制 README、维护文档及许可通知，不复制验证产物和模型权重。打包目录内的 `requirements-installed.txt` 由构建时生成。
- `.gitignore` 不会自动取消已有跟踪；排除已跟踪产物需使用 `git rm --cached`，保留本地文件。历史提交不会因此被改写。

测试脚本的成功输出不代表翻译语义全部正确。当前仍有历史事件序数、专名和习语错译，也可能出现较长的无新字幕间隔；识别未提供新原文的停顿不计入“原文增长但译文停滞”指标。未保存并标注参照音频时，不推断总体准确率或把无新字幕一概当作软件排队延迟。

## 异常输出与停滞恢复（0.5.1）

`n-` 不是程序的状态码。异常短输出此前可能通过校验并进入译文缓存；现在会拦截此类格式残片，简化提示后重试，不缓存无效结果。合法的 `n-gram`、`n-1` 等文字不会被当作格式残片删除。

生成在 token 检查点受时间预算约束：GPU 每次约 5 秒、CPU 约 20 秒，预热另设约 60 秒预算。超时丢弃未完成输出，关闭生成流并继续下一项任务。普通任务异常会被记录并隔离，不再导致整个翻译线程直接退出。

界面每秒检查识别和翻译工作状态；运行中 GPU 约 8 秒、CPU 约 30 秒没有进展时给出提示，线程已退出也会提示。“重新连接”始终显示在右键菜单，重连最多等待旧任务约 8 秒。底层显卡/本地库的单次调用如果完全不返回，Python 的 token 停止条件无法强制中断；此时会提示完全退出程序后重开，不会无限停在重连等待中。

这些防护不证明历史现场卡顿一定由 `n-` 引起；只有异常输出被错误接收的问题已通过受控输入复现。诊断日志不记录字幕文本，无法据此还原现场模型输出。

## 上下文预算验证（0.5.2）

```powershell
.venv\Scripts\python.exe tools\check_context_budget.py
```

此脚本依次使用本地 4B 和 2B，阻断 Python socket 连接。验证默认 2048-token 窗口中的生僻字边界、JSON 转义后的重试预算、正常翻译及异常输出后的跨语言重试；再以实际 512-token 小窗口强制触发拆分，检查原文完整性与逐段进度。512 仅用于边界验证，桌面默认值仍为 2048。所有生成调用都会审计输入长度、256-token 输出预留和 8-token 余量。

结果写入被忽略的 `artifacts/context-budget-v052.json`，不采集系统声音，也不把模型或验证数据纳入版本控制。上下文检查通过不代表所有翻译都语义正确。
