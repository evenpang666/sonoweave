# 自动安装与启动

源码版无需预装 Python 或 uv。Windows 使用系统自带的 Windows PowerShell，Linux 使用 Bash 及 curl / wget。支持 Windows x64 和 Linux x86_64 glibc 桌面。

| 操作 | Windows | Linux |
| --- | --- | --- |
| 安装并启动 | 双击 `Start.bat` | `bash Start.sh` |
| 只安装运行环境 | `Install.bat` | `bash Install.sh` |
| 安装 CPU 翻译后端 | `Install.bat --cpu` | `bash Install.sh --cpu` |
| 提前下载两种模型 | `PrepareModels.bat --model both` | `bash PrepareModels.sh --model both` |

启动脚本先检查环境标记、依赖声明及实际导入情况，缺少环境、依赖变更或依赖损坏时才运行安装。安装失败会返回错误并停止启动，修复后可再次执行相同命令；环境完整时检查不联网，不因启动而更新依赖。已打包的程序包含 Python 运行时，直接运行对应原生可执行文件即可。

## 安装流程

1. 优先复用可用的 uv（至少 0.8.22）；缺少时下载固定版本 0.13.4：Windows 获取 Astral 官方 GitHub 压缩包并核对发布的 SHA-256，Linux 使用官方安装器。放入项目的 `.runtime/<windows|linux>/uv`，不修改全局 PATH。
2. uv 自动下载托管的 Python 3.12，放入 `.runtime/<windows|linux>/python`，不依赖、不替换系统 Python，不写 Python 注册表或全局命令。
3. 创建 Windows `.venv` / Linux `.venv-linux`。不兼容的旧环境移动至 `.cache/previous-environments/` 保留，模型及个人设置不移动。
4. Linux 在 Debian / Ubuntu 上检查系统包，缺少时通过 apt 自动安装音频客户端库、Qt 桌面库、字体和所选后端运行库，可能需要 sudo 密码；沿用已运行的 PulseAudio / pipewire-pulse，不自动替换音频服务。其他发行版缺少系统库时提示手动补齐，详见 [Linux 说明](linux.md)。
5. 使用 `uv pip install` 安装 Python 依赖，精确选择 CPU / Vulkan 的 llama.cpp 轮子；检查主要模块能否加载，通过后才原子写入就绪标记。并发安装通过系统文件锁串行执行。

默认安装 Vulkan 后端；`--cpu` 可切到 CPU，再运行不带参数的安装器可切回 Vulkan。启动自动补装时会保留之前的后端选择。依赖完整并且声明未变化时，重复安装直接复用。程序启动后再按需下载模型；环境安装不下载多 GB 模型。

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
