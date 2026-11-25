# 开发、测试与发布

## 环境

- Windows 10 / 11 x64；本工具依赖 WASAPI、Windows 原生光标和 FFmpeg `gdigrab`，不支持 macOS / Linux。
- 已验证 CPython 3.13.9 x64，安装 Tcl/Tk。
- 普通运行依赖见 `requirements.txt`；PyInstaller 及其构建依赖见 `requirements-dev.txt`，均固定版本。
- Git 仅用于克隆和提交；运行 EXE 不需要 Git。
- FFmpeg 固定使用 Gyan `8.1.2 essentials_build`，含本项目所需编码器与过滤器。

## 工具准备

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare-tools.ps1
```

脚本优先从 Gyan 官方 GitHub Release 下载固定版本 ZIP，失败后重试并回退到其官网，使用已核对的上游 SHA-256 固定值校验，提取 `ffmpeg.exe`、`ffprobe.exe`、许可和说明。保留下载缓存，不删除现有文件；已有工具必须与固定 ZIP 中的文件一致才可复用。

也可自行安装 FFmpeg 和 FFprobe，放到 `tools\ffmpeg.exe`、`tools\ffprobe.exe`。自备版本至少应提供 `gdigrab`、`libx264`、`aac`、`libmp3lame`、PNG、`afftdn`、`alimiter` 及通用音视频拼接 / 缩放过滤器。替换后应重新运行测试。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

默认包括 Tkinter 控件、命名冲突、模拟音源、采样连续性、WAV / RF64、真实 FFmpeg 编码、降噪、音轨对齐和效果导出测试。需要可创建 Tkinter 窗口的 Windows 会话；不要求真实麦克风。

视频工具测试另行生成带两条音轨的无损测试视频，检查四种音频格式、第一音轨选择、H.264 / H.265 实际缩小比例、SSIM、时长 / 分辨率 / 帧率、音轨语言、兼容音轨逐包复制、静音视频、取消、重名保护及界面任务互斥。

以下两项真实桌面测试默认跳过，在交互式桌面显式开启：

```powershell
$env:STUDIO_DESKTOP_TESTS = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

屏幕集成测试录制自己创建的测试窗口，并使用模拟声音及光标轨迹；原生光标测试读取 Windows 实际光标状态。测试产物写入带随机编号的 `test-artifacts/`，不会清理旧测试产物；正常录制流程仍会自动清理本次成功保存的临时会话。

## 构建 EXE

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\build.py
```

脚本使用正在执行它的 Python 环境调用 PyInstaller `--onefile --windowed`，自动包含运行依赖，生成 `PotatoFactory.exe`。EXE 产品名为「土豆工厂」、作者字段为「大土豆|Ryan」。Windows 文件拖放直接调用系统 API，无需打包额外拖放扩展。每次都创建全新的输出目录，不调用清理命令。

柔化只使用 NumPy 数组；构建显式排除 SciPy 可选的 PyTorch / TensorFlow / JAX / CuPy 后端，以及绘图、交互和测试包，避免本机其他项目的环境增大发行包。

输出：

```text
builds/<时间_编号>/             # PyInstaller 工作目录与 spec
releases/<时间_编号>/
  PotatoFactory-v2.1.0-windows-x64/
    PotatoFactory.exe
    ffmpeg.exe
    ffprobe.exe
    README.txt
    docs/
    licenses/
    THIRD_PARTY_NOTICES.md
    build-info.json
    SHA256SUMS.txt
  PotatoFactory-v2.1.0-windows-x64.zip
```

`build-info.json` 记录产品名、作者、Python、Windows、依赖版本、Git 提交（若存在）、构建时间、FFmpeg 版本与散列，以及 11 个运行源码的 SHA-256。构建完成不代表已完成真实录音验证，请结合 `docs/VALIDATION.md` 查看验收范围。

## 离线开发包

在 CPython 3.13 x64 的 Windows 环境中下载 wheel：

```powershell
python -m pip download --only-binary=:all: -r requirements-dev.txt -d wheelhouse
```

离线包只需包含 Git 管理的源码文档、`tools\ffmpeg.exe`、`tools\ffprobe.exe` 和 `wheelhouse\*.whl`，不应加入 `.git`、`.venv`、历史构建和测试录制素材。Python 官方安装程序由使用者自行安装，包内不附带系统安装器。

## GitHub 分发

仓库保存文本源码、图标、测试和文档。免安装包、离线源码依赖包、包级 SHA-256 清单通过版本对应的 GitHub Release 分发。

仓库中的 `packages/` 专门保存安装包目录，按 `v<版本>/<构建编号>/` 记录下载入口、`manifest.json` 和 `SHA256SUMS.txt`。构建脚本生成 ZIP 后自动登记索引；已有构建可运行 `python scripts/index_package.py releases/<构建编号>` 登记。ZIP 实体作为 GitHub Release 附件上传，避免超过普通 Git 单文件大小限制。

发布时先提交源码和索引，再为对应提交创建 `v<版本>` Release，上传 ZIP、清单及校验文件，核对后发布。保留历史构建；发布新构建应先更新版本号，避免覆盖已有附件。索引登记不会自动上传或发布。

本次迁移新建私有仓库，不修改原目录，也不把 GitHub 凭证写入脚本、Git remote、配置或压缩包。默认不替所有者选择开源许可证。调整仓库可见性或再次对外分发时，应自行决定项目授权并遵守第三方许可。
