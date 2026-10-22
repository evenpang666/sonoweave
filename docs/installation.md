# 自动安装与启动

源码版无需预装 Python 或 uv。Windows 使用系统自带的 Windows PowerShell，Linux 使用 Bash 及 curl / wget。支持 Windows x64 和 Linux x86_64 glibc 桌面。

| 操作 | Windows | Linux |
| --- | --- | --- |
| 安装并启动 | 双击 `Start.bat` | `bash Start.sh` |
| 只安装运行环境 | `Install.bat` | `bash Install.sh` |
| 安装 CPU 翻译后端 | `Install.bat --cpu` | `bash Install.sh --cpu` |
| 安装 CUDA 翻译后端 | `Install.bat --cuda` | `bash Install.sh --cuda` |
| 安装 Vulkan 翻译后端 | `Install.bat --vulkan` | `bash Install.sh --vulkan` |
| 提前下载两种模型 | `PrepareModels.bat --model both` | `bash PrepareModels.sh --model both` |

启动脚本先检查环境标记、依赖声明及实际导入情况，缺少环境、依赖变更或依赖损坏时才运行安装。安装失败会返回错误并停止启动，修复后可再次执行相同命令；环境完整时检查不联网，不因启动而更新依赖。已打包的程序包含 Python 运行时，直接运行对应原生可执行文件即可。

Windows 和 Linux 的启动脚本在存在 `main.py` 时始终运行当前源码，项目内残留的旧 `dist/SonoWeave` 不会覆盖源码更新。需要使用打包版时直接打开 `dist/SonoWeave/SonoWeave.exe`（Windows）或 `dist/SonoWeave/SonoWeave`（Linux）；源码更新后需重新运行对应的 `Build.ps1` / `Build.sh` 才能更新该程序。只有不含 `main.py` 时，启动脚本才回退到已有打包程序。

两端均可右键开启“人声配色”，默认关闭；首次开启自动准备人声分段与声纹模型，约 30 MB。配色仅用于轮流说话，暂不处理多人同时说话。需要提前离线准备时，运行 `PrepareModels.bat --speaker-colors` 或 `bash PrepareModels.sh --speaker-colors`。

## 安装流程

1. 优先复用可用的 uv（至少 0.8.22）；缺少时下载固定版本 0.13.4：Windows 获取 Astral 官方 GitHub 压缩包并核对发布的 SHA-256，Linux 使用官方安装器。放入项目的 `.runtime/<windows|linux>/uv`，不修改全局 PATH。
2. uv 自动下载托管的 Python 3.12，放入 `.runtime/<windows|linux>/python`，不依赖、不替换系统 Python，不写 Python 注册表或全局命令。
3. 创建 Windows `.venv` / Linux `.venv-linux`。不兼容的旧环境移动至 `.cache/previous-environments/` 保留，模型及个人设置不移动。
4. Linux 在 Debian / Ubuntu 上检查系统包，缺少时通过 apt 自动安装音频客户端库、Qt 桌面库、字体和所选后端运行库，可能需要 sudo 密码；沿用已运行的 PulseAudio / pipewire-pulse，不自动替换音频服务。其他发行版缺少系统库时提示手动补齐，详见 [Linux 说明](linux.md)。
5. 使用 `uv pip install` 安装 Python 依赖，精确选择 CPU / Vulkan 的 llama.cpp 轮子；检查主要模块能否加载，通过后才原子写入就绪标记。并发安装通过系统文件锁串行执行。

默认检测 NVIDIA 驱动，兼容时优先安装固定版本的 CUDA 12.4 llama.cpp 轮子；否则使用 Vulkan。`--cuda`、`--vulkan`、`--cpu` 可显式选择翻译后端，后续自动补装保留该选择。早期的 CPU 安装同样保留。Whisper 的设备选择独立于翻译后端；需要强制两个模型都用 CPU 时，同时设置 `SONOWEAVE_ASR_DEVICE=cpu` 和 `SONOWEAVE_TRANSLATE_DEVICE=cpu`。依赖完整并且声明未变化时，重复安装直接复用。程序启动后再按需下载模型；环境安装不下载多 GB 模型。

Whisper 的 CUDA 推理还需要 CUDA 12 的 cuBLAS 和 cuDNN 9。Windows 可复用 CUDA 安装目录及 `PATH` 中的 DLL；程序也会搜索 CUDA 轮子和打包目录内的运行库。Linux 需在启动 Python 前配置相应的系统库或 `LD_LIBRARY_PATH`。库缺少、GPU 不可用或显存不足时自动回退 CPU，字幕仍可使用。分配策略和验证方式见 [GPU 与 CPU 资源分配](resources.md)。

## 网络、空间与排错

首次安装需访问 `astral.sh`、GitHub（uv / Python / llama.cpp 轮子）和 PyPI；首次使用模型需访问 Hugging Face。网络受限时配置 `HTTPS_PROXY` 后重试。可使用 uv 官方支持的 `UV_PYTHON_INSTALL_MIRROR`、`UV_DEFAULT_INDEX` 等设置，但自定义源需要提供所需版本；llama.cpp 后端从专用官方轮子索引选择。

安装缓存位于项目 `.cache/uv`，可用 `UV_CACHE_DIR` 覆盖；uv、Python、环境、缓存和模型都不被 Git 跟踪。完整模型下载后可离线使用。运行环境连同托管 Python 包含绝对路径，移动项目目录后若检查失败，重新执行安装器修复。

- **下载失败**：检查代理、TLS 证书、网络及磁盘空间后重试；uv 会复用已下载缓存。
- **Windows 脚本策略限制**：`.bat` 仅对运行本地引导脚本的 PowerShell 进程使用 `ExecutionPolicy Bypass`，不会修改系统执行策略；组织策略仍可能限制运行，需要按管理员规定配置。
- **Linux 缺少 curl / wget 或 sudo**：先安装对应系统命令；系统库自动安装支持 apt，其他发行版参照 [Linux 说明](linux.md)。
- **CPU / Vulkan 切换后仍加载失败**：重新运行对应安装命令，必要时检查显卡驱动或选择 CPU 后端。
- **需要指定已有 uv**：设置 `SONOWEAVE_UV` 为 uv 可执行文件的完整路径；版本不兼容时明确失败，不会悄悄使用错误解释器。

开发者在已提供系统库的隔离容器中可设置 `SONOWEAVE_SKIP_SYSTEM_DEPS=1` 跳过系统包管理，不用于普通首次安装。

实现参考：[uv 官方安装器](https://docs.astral.sh/uv/getting-started/installation/)、[无人值守安装目录](https://docs.astral.sh/uv/reference/installer/)、[托管 Python](https://docs.astral.sh/uv/guides/install-python/)、[多索引解析规则](https://docs.astral.sh/uv/pip/compatibility/#packages-that-exist-on-multiple-indexes)。
