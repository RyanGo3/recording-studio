# 土豆工厂 · Potato Factory

作者：**大土豆|Ryan**

Windows / macOS 桌面录音、录屏与简单剪辑工具。支持系统声音、麦克风混录、自动点击缩放、平滑光标和电影感画面；录音导出 MP3，录像导出 H.264 / AAC MP4。

## 下载与使用

当前成品版本为 **v2.1.1**，从 [GitHub Release](https://github.com/RyanGo3/recording-studio/releases/tag/v2.1.1) 下载：

| 电脑 | 安装包 | 使用说明 |
| --- | --- | --- |
| Windows 10 / 11，64 位 | [Windows x64](https://github.com/RyanGo3/recording-studio/releases/download/v2.1.1/PotatoFactory-v2.1.1-windows-x64.zip) | [Windows 说明](docs/USER_GUIDE.md) |
| Mac，苹果 M 系列芯片，macOS 15+ | [Mac 苹果 M 系列](https://github.com/RyanGo3/recording-studio/releases/download/v2.1.1/PotatoFactory-v2.1.1-macos-arm64.zip) | [Mac 简易说明](docs/MACOS_QUICK_START.md) |
| Mac，Intel 芯片，macOS 15+ | [Mac Intel](https://github.com/RyanGo3/recording-studio/releases/download/v2.1.1/PotatoFactory-v2.1.1-macos-x86_64.zip) | [Mac 简易说明](docs/MACOS_QUICK_START.md) |

Mac 包完整解压后，将 `PotatoFactory.app` 拖入「应用程序」即可，无需自行构建或安装 Python、FFmpeg。
录电脑声音需额外配置 BlackHole 2ch，详见 [Mac 详细说明](docs/MACOS_USER_GUIDE.md)。
当前 Mac 实验版已通过两种架构的云端构建、界面启动和编码检查，实际录屏与音频设备仍需人工验收。

全部版本、构建记录和 SHA-256 校验见 [安装包目录](packages/README.md)。文档入口见 [说明目录](docs/README.md)。

本仓库从原项目的 v2 整理而来。运行代码位于根目录的 Python 模块，不依赖旧版本、历史构建或原电脑的项目路径。新增工具与文件操作优化需使用当前源码或基于当前源码重新构建的版本。

## Windows 安装

1. 在 [安装包目录](packages/README.md) 按版本下载便携包；当前版本为 `PotatoFactory-v2.1.1-windows-x64.zip`。本地构建输出在 `releases/<构建编号>/`，GitHub 上的历史 `RecordingStudio` 包不包含本轮更新。
2. **完整解压**到可写入目录。
3. 双击 `PotatoFactory.exe`。同目录的 `ffmpeg.exe` 和 `ffprobe.exe` 必须保留。

免安装包自带 Python 运行时、所需 Python 库和 FFmpeg，无须安装 Python、Git 或额外的 pip 依赖。适用 Windows 10 / 11 64 位；本次实际验证环境和结果见 [验收记录](docs/VALIDATION.md)。

若要修改代码或重建新版，请使用当前源码，按开发说明准备依赖与工具。历史 Release 的 `RecordingStudio-v2.0.0-source-tools-win64.zip` 是旧版离线开发包；Python 解释器需另行安装。

## 功能

| 功能 | 行为 |
| --- | --- |
| 文件与目录输入 | 输入或粘贴完整路径、带引号路径、多个复制的文件路径；支持文件 / 文件夹拖入，批量工具导入目录本层支持的文件 |
| 系统录音 | 录制 Windows 默认播放设备中的声音，128 kbps、48 kHz、双声道 MP3 |
| 全屏 / 区域录屏 | 30 fps MP4；区域模式拖动框选，Enter 确认、Esc 取消 |
| 录屏音源 | 系统声音、系统 + 外部声音、仅外部声音；外部声音采用默认输入设备 |
| 暂停和继续 | 暂停时段从画面、声音与光标时间轴中排除 |
| 自定义名称 | 录制中可改名，保存时锁定；同名文件自动编号，避免覆盖 |
| 自动点击缩放 | 鼠标点击后推近、停留、拉远；倍率 1.35 / 1.65 / 2.0 |
| 平滑光标 / 电影感画面 | 轨迹平滑、圆角背景、阴影和柔和缓动，停止后生成效果 |
| 轻度降噪 | 可关闭；系统声音与麦克风采用不同参数，不是 AI 人声分离 |
| 裁剪与导出 | 添加时间切分点、导出多个片段、从录像分离 MP3 |
| 声音柔化 | 高音角色 / 整段动态柔化，按需抑制刺耳声和齿音，强度 15–100，MP3 / 24 位 WAV / FLAC，平均音量匹配的对比试听 |
| 视频转音频 | 批量提取为高品质 MP3、AAC M4A、24 位 WAV 或 FLAC，可独立选择保存目录 |
| 视频压缩 | H.265 / H.264、清晰优先 / 均衡 / 体积优先，保留原分辨率与帧率，显示前后大小和缩小比例 |
| 预览定位 | 点击 / 拖动时间轴；方向键前后 5 秒，Home / End 跳转 |
| 录制限制 | 单次最长 12 小时，到时自动保存 |

**纯录音模式只录系统声音**；麦克风及混合音源选项用于录屏。系统录音会收集默认播放设备中的所有程序声音，尚不支持按应用选择音轨。

## 从源码运行

已验证环境：**Windows x64 + CPython 3.13.9**。建议使用完整的 Python 官方安装包并安装 Tcl/Tk；嵌入式 Python 包不适合直接运行本界面。

```powershell
git clone https://github.com/RyanGo3/recording-studio.git
cd recording-studio
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare-tools.ps1
.\.venv\Scripts\python.exe app.py
```

私有仓库克隆需要具有仓库访问权限的 GitHub 账号。FFmpeg 查找顺序为程序同目录、`tools\ffmpeg.exe`、Python 所在目录、PyInstaller 解包目录，最后查找系统 PATH。

**离线源码包**完整解压后，在该目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse -r requirements-dev.txt
.\.venv\Scripts\python.exe app.py
```

离线 wheel 只适用于 Windows x64 / CPython 3.13；不包含 Python 安装程序。普通 EXE 包没有这一安装要求。

## 测试和构建

```powershell
# 自动化测试：界面、模拟音源、真实 FFmpeg 编码、降噪、命名与导出。
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 在有交互式桌面的 Windows 上，额外验证原生光标和真实屏幕录制。
$env:STUDIO_DESKTOP_TESTS = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 构建前安装构建依赖；离线包可使用上面的 --no-index 方式。
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\build.py
```

每次构建创建独立的 `builds\<时间_编号>`、`releases\<时间_编号>`，不清理或覆盖以前的构建。构建结果包括 EXE、FFmpeg、说明文档、第三方许可、版本与源码散列清单，以及可直接分发的 ZIP。详见 [开发与构建](docs/DEVELOPMENT.md)。

## 目录

```text
recording-studio/
├── app.py                   # 程序入口、录音、预览和剪辑界面
├── apple_ui.py              # 主题和 Tkinter 控件
├── audio_sources.py         # Windows / Mac 音源采集、WAV / RF64 写入
├── audio_processing.py      # 降噪和峰值限制参数
├── audio_softening.py       # 高音片段识别、音色与音量柔化及导出
├── softening_ui.py          # 原生声音柔化页面、批量任务和对比试听
├── media_conversion.py      # 视频转音频、视频压缩、进度和取消、成品校验
├── conversion_ui.py         # 两个视频工具页面、批量队列和大小对比
├── recording_v2.py          # 录屏、暂停、音轨对齐和成片封装
├── cinematic.py             # 光标采集、自动缩放和效果渲染
├── macos_capture.py         # macOS 主屏采集、权限和光标接口
├── assets/studio.ico        # EXE 图标
├── docs/                    # 使用、架构、构建和验收说明
├── packages/                # 按版本和构建编号保存的下载索引、清单与校验
├── scripts/                 # FFmpeg 准备和打包脚本
├── tests/                   # 自动化测试
├── .github/workflows/       # 自动化测试和 Mac 云端构建
├── tools/                   # FFmpeg；二进制仅在 Release 中分发
├── licenses/                # 第三方原始许可文本
├── requirements.txt         # 锁定运行依赖
├── requirements-dev.txt     # 锁定构建依赖
└── VERSION
```

`wheelhouse/`、`builds/`、`releases/`、`test-artifacts/` 为本地生成目录，不提交 Git。大体积 EXE、FFmpeg 和离线包通过 GitHub Release 分发。

## 保存与恢复

Windows 默认保存到 `D:\土豆工厂`；没有 D 盘时保存到用户的 `Videos\土豆工厂`。若对应位置已有旧的「录制工坊」目录，则继续使用旧目录。Mac 默认保存到 `~/Movies/土豆工厂`。可在软件中更改保存位置；Mac 文件输入方式见对应说明。

录制期间会在保存目录创建隐藏的 `.文件名_session_随机编号`，用于保存分段、音源、光标轨迹与处理日志。**成功保存后自动清理本次临时目录**；异常或处理失败时保留，并提示恢复位置。录制前请为长时间、高分辨率录屏预留足够磁盘空间。

开启画面效果时，恢复用的 `original.mp4` 不含系统光标；光标由轨迹重新绘制。突然碰撞声、回声、严重爆音、采集丢失无法保证修复；DRM / 受保护内容可能无法录制。

完整操作、限制与常见问题见 [使用说明](docs/USER_GUIDE.md)。本工具处理本地文件；正常录制和导出流程不需要网络、云端账号或模型 API。

## 许可与依赖来源

本次整理不替项目作者选择新的开源许可证；仓库可见性和对外授权由所有者决定。FFmpeg、Python 及各 Python 库保留各自许可，见 [第三方依赖说明](THIRD_PARTY_NOTICES.md) 和 `licenses/`。向其他人分发时应同时保留这些文件。
