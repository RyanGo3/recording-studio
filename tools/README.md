# FFmpeg 工具

源码运行需要 `tools\ffmpeg.exe`，声音柔化还需要 `tools\ffprobe.exe`；免安装包中两者均放在 EXE 同目录。

运行 `scripts\prepare-tools.ps1` 可下载并校验固定版本 Gyan FFmpeg 8.1.2 essentials Windows x64 构建。优先使用官方 GitHub Release，支持重试和官网回退，校验值固定在脚本中。下载缓存和二进制不提交 Git，随 GitHub Release 的源码工具包一起提供。

该程序通过独立进程使用 FFmpeg，声音柔化使用 FFprobe 校验音频；不需要 `ffplay.exe`。两个 EXE 均从相同的固定版本 ZIP 提取，已有文件先验证散列，避免覆盖。上游构建为 GPLv3，详见 `THIRD_PARTY_NOTICES.md` 和 `licenses/ffmpeg/`。
