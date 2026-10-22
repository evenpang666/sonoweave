# Linux 安装与使用

支持 x86_64、glibc 桌面环境。建议 Ubuntu 24.04 或 Debian 12；安装器自动准备 uv、Python 3.12、Python 依赖及这些发行版上的系统库，无需预装 Python。其他发行版需提供等价系统依赖。声音通过 PulseAudio 或 PipeWire 的 `pipewire-pulse` 兼容服务采集；仅有 ALSA 或 JACK 不够。以登录桌面的普通用户运行程序。

## 安装与启动

直接在项目目录运行，环境未就绪时会先安装，安装成功才启动：

```bash
bash Start.sh
```

可用 `bash Install.sh` 仅准备环境。安装器需要 curl 或 wget，自动安装系统库时可能要求 sudo 密码。保留发行版已有的音频服务：PipeWire 桌面需要已运行 `pipewire-pulse`；PulseAudio 桌面沿用 PulseAudio，无需同时安装或启动两套服务。

以下仅为无法自动安装时的 Ubuntu / Debian 系统库手动补装命令：

```bash
sudo apt update
sudo apt install curl libpulse0 libpulse-dev pulseaudio-utils \
  libgl1 libegl1 libvulkan1 mesa-vulkan-drivers libxcb-cursor0 libxkbcommon-x11-0 \
  libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0 \
  libxcb-xinerama0 xwayland fonts-noto-cjk
```

安装器创建 `.venv-linux`，与 Windows 的 `.venv` 分开，Python 由 uv 下载到项目 `.runtime/linux/python`，无需设置 `PYTHON` 或激活环境。脚本支持包含空格的项目路径，可从其他工作目录调用。详细流程与网络配置见 [自动安装](installation.md)。

默认安装固定版本的 Vulkan 推理轮子，支持相应驱动可用时的 GPU 翻译；识别端自动尝试 NVIDIA CUDA，失败时退回 CPU。没有兼容 GPU、Vulkan 库加载失败，或希望完全使用 CPU 时：

```bash
bash Install.sh --cpu
SONOWEAVE_ASR_DEVICE=cpu SONOWEAVE_TRANSLATE_DEVICE=cpu bash Start.sh
```

`--cpu` 选择 CPU 翻译轮子；再次运行不带该选项的安装器可切回 Vulkan。安装器不会下载模型。首次启动按需下载 Whisper small 和默认 4B；切换 2B 时自动下载，已有完整模型复用。也可以提前准备：

```bash
bash PrepareModels.sh --model both
```

右键字幕设置目标语言、4B / 2B 或重新连接，操作与 Windows 相同。

## 音频与桌面

监听默认输出设备对应的 **monitor** 源，自动跟随默认输出设备切换。通过音频服务提供的 sink → monitor 关系查找源，校验精确 ID 和回环属性；不会因模糊匹配而录入实体麦克风。单独路由到其他设备、绕过 PulseAudio / PipeWire、独占或受保护的声音不保证可采集。

X11 可直接运行。Wayland 桌面若提供 XWayland，会默认使用 Qt `xcb` 后端以支持字幕定位；原生 Wayland 可通过 `QT_QPA_PLATFORM=wayland bash Start.sh` 显式选择。窗口定位、置顶和跨全屏显示最终由窗口管理器决定；原生 Wayland 下可能需设置窗口规则，建议优先使用 XWayland 或 X11。

## 数据与离线使用

- 设置、日志：`${XDG_DATA_HOME:-$HOME/.local/share}/sonoweave`；`XDG_DATA_HOME` 必须是绝对路径，否则回退到默认目录。
- 源码模型：项目的 `models/`；Linux 打包程序：上述数据目录的 `models/`。
- `SONOWEAVE_HOME` 可覆盖设置与日志目录，`SONOWEAVE_MODELS_DIR` 可覆盖模型目录，其他变量见 [开发说明](development.md)。
- 下载完成后无需网络，不上传声音或字幕，不保存录音和字幕历史。模型、虚拟环境及验证产物都被 Git 忽略。

## 问题解决

- **音频服务不可用**：运行 `pactl info`，应能连接服务；PipeWire 桌面再检查 `systemctl --user status pipewire-pulse`。修复服务后右键重新连接。不要使用 `sudo bash Start.sh`，也不要随意同时启动 PulseAudio 和 pipewire-pulse。
- **有声音但没有字幕**：运行 `pactl get-default-sink`、`pactl list short sinks`、`pactl list short sources`；确认播放流输出到默认设备，并有对应 monitor。可用 `pavucontrol` 的“播放”页查看流路由。沙箱或远程桌面需能访问当前用户的音频服务。
- **提示 `libpulse` 缺失**：安装上述 `libpulse0` / `libpulse-dev`；其他发行版使用相应 PulseAudio 客户端库包。`pulsectl` 由安装器处理。
- **Qt `xcb` 插件无法加载**：先补齐系统依赖；用 `QT_DEBUG_PLUGINS=1 bash Start.sh` 查找缺少的共享库。Wayland 桌面还需 XWayland；纯 Wayland 可显式指定 `QT_QPA_PLATFORM=wayland`。
- **字幕无法置顶或拖动**：在桌面的窗口规则中允许定位与置顶，或使用 X11 会话。合成器可能限制全屏应用上方的窗口。
- **中文方框**：安装 `fonts-noto-cjk` 后重启程序。
- **模型加载失败或过慢**：更新显卡驱动，或用 `bash Install.sh --cpu`；右键改用 2B 可降低资源占用。CPU 模式延迟取决于硬件。
- **下载失败**：检查网络、空间和代理，右键重试会复用断点。下载支持 `HF_ENDPOINT`、`HTTPS_PROXY`；也可在联网环境准备模型后复制到指定模型目录。

## 开发与打包

```bash
.venv-linux/bin/python -m ensurepip
.venv-linux/bin/python -m pip install 'pytest>=8,<10'
.venv-linux/bin/python -m pytest -q
bash Start.sh --preview
bash Start.sh --screenshot artifacts/linux-preview.png
bash Build.sh
.venv-linux/bin/python tools/verify_bundle.py --preview-only
```

`Build.sh` 在 Linux 本机生成 `dist/SonoWeave/SonoWeave`，保留整个目录分发。打包不包含模型或验证产物；Windows 的 `.exe` 不能作为 Linux 包使用。发行版系统库仍需存在；建议在计划支持的最旧发行版上构建，不能保证向旧版 glibc 兼容。

CI 在 Ubuntu 24.04 使用安装器自动提供的 Python 3.12，检查 CPU / Vulkan 两套依赖、全套单元测试、离屏字幕预览，并在隔离 PulseAudio 服务上播放合成音验证真实 monitor 采集。该集成测试仅在 `SONOWEAVE_TEST_PULSE=1` 时开启，会创建虚拟输出并临时切换服务器默认设备；仅在隔离测试服务中启用。CI 不下载大模型；真实 GPU、PipeWire、Wayland 桌面和长时间字幕效果仍需相应机器验证。

实现依据：[SoundCard Linux / PulseAudio 后端](https://soundcard.readthedocs.io/en/latest/)、[PulseAudio Python 控制接口](https://github.com/mk-fg/python-pulse-control)、[pipewire-pulse](https://docs.pipewire.org/page_man_pipewire-pulse_1.html)、[Qt Linux 依赖](https://doc.qt.io/qt-6/linux-requirements.html)、[llama-cpp-python 安装与后端](https://github.com/abetlen/llama-cpp-python#installation)。
