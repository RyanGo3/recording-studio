# macOS 实验版

本分支已加入 macOS 适配和原生构建入口。Windows 环境只能检查通用逻辑，
尚未完成 Mac 真机验收；构建脚本成功也不代表录制功能已通过真机验收。
请先测试下方验收项目，再正式分发。

## 使用应用包

GitHub 工作流在 macOS 15 构建，目标为 macOS 15 及以上。若自行在更高版本系统构建，
最低系统版本会保守地设为构建机的系统主版本，以避免随包动态库不支持旧系统。
Apple Silicon（M 系列）使用 `macos-arm64` 包，
Intel Mac 使用 `macos-x86_64` 包。两个包分别构建，不是通用二进制。

完整解压 ZIP，将 `PotatoFactory.app` 拖到“应用程序”后打开。
成品自带 Python、FFmpeg 和 FFprobe。默认保存到 `~/Movies/土豆工厂`。

当前构建使用临时签名，没有 Developer ID 签名及 Apple 公证。
从可信来源取得程序后，可按系统提示在“系统设置 → 隐私与安全性”中允许打开。
不要关闭系统整体的安全检查。

首次录屏需要允许“屏幕录制”（系统版本不同也可能显示“屏幕与系统音频录制”）。
修改授权后退出并重新打开应用。录音还需要允许麦克风访问；
源码运行时授权对象可能是终端或 Python。

## 系统声音

macOS 版通过 **BlackHole 2ch** 虚拟音频设备采集系统声音，需要用户另行安装；
BlackHole 驱动不随应用分发。没有该设备时，系统录音会提示配置，不会用麦克风代替。
仅录麦克风可在录屏音源中选择“仅外部声音”。

1. 安装 BlackHole 2ch。使用 Homebrew 时运行 `brew install --cask blackhole-2ch`，
   如安装器要求重启，请先重启。
2. 打开“音频 MIDI 设置”，创建“多输出设备”，包含实际扬声器/耳机和 BlackHole 2ch。
3. 将相关设备采样率设为 48,000 Hz；按 BlackHole 官方说明设置时钟源与漂移校正。
4. 在系统声音设置中，将输出切换到该多输出设备。这样可以同时听到声音并录入 BlackHole。
5. 默认输入仍使用实际麦克风；混录时程序分别采集 BlackHole 和默认输入。

若录出的系统轨道无声，先确认应用声音实际输出到这个多输出设备。
程序能发现 BlackHole 并不等于系统输出路由已配置正确。

## 当前差异

- 全屏和区域录制仅支持主显示器；不支持跨屏区域。
- Retina 屏幕采集先缩放到与界面一致的逻辑分辨率再裁剪，输出不是 Retina 原始像素分辨率。
- 光标效果使用箭头形状，不区分文本光标；点击检测可能受系统输入监控权限影响。
- macOS 不支持现有 Windows 窗口排除接口，悬浮录制条可能被录入画面。
- 文件和目录支持选择器、输入路径及粘贴；Finder 原生文件拖入尚未接入。
- 支持媒体转音频、视频压缩、裁剪、声音柔化等通用模块，实际效果仍需真机验收。

## 在 Mac 构建

需要 macOS 15 或更新版本、Homebrew 和网络连接。首次构建安装 Python、Tcl/Tk、FFmpeg 和 Python 依赖。
在项目目录的终端运行：

```bash
bash scripts/build-macos.command
```

若从 Windows 交接，可先运行 `python scripts/package_macos_source.py`，
把生成的 `macos-build-kit.zip` 复制到 Mac 解压，再执行上述命令。
构建包只包含源码、图标、许可和构建入口，不包含已编译应用。

或手动构建：

```bash
brew install python@3.13 python-tk@3.13 ffmpeg
"$(brew --prefix python@3.13)/bin/python3.13" -m venv .venv-macos
.venv-macos/bin/python -m pip install -r requirements-macos-dev.txt
.venv-macos/bin/python scripts/build.py
```

Python 和 FFmpeg 必须与目标架构一致。在 Apple Silicon 的原生终端构建 arm64 包；
Intel 包在 Intel Mac 构建。脚本会检查 FFmpeg 架构和编码器，
由 PyInstaller 收集 FFmpeg 的动态库，再检查签名、完整 GUI 创建和随包工具运行。
每次输出到新的 `releases/<构建编号>/`，保留旧包。
ZIP 内有 `.app`、中文说明、构建信息和校验值。

GitHub Actions 提供手动触发的 `Build macOS packages` 工作流，
分别在 Apple Silicon 和 Intel runner 构建，成功后从该次运行的 Artifacts 下载。
该工作流不会自动发布 GitHub Release。工作流需要先提交并推送到 GitHub 才能触发。

## 真机验收

构建自检不会请求隐私权限，也不会操作实际音频设备。正式使用前请检查：

1. 在没有 Python / Homebrew 的同架构 Mac 上打开 `.app`，检查工具页面和媒体转换。
2. 首次拒绝屏幕或麦克风权限时有明确提示；授权并重启后能录制。
3. 主屏全屏和区域录制、Retina 裁剪位置、光标和自动缩放正确。
4. BlackHole 系统录音、仅麦克风和混录分别能听到预期声音。
5. 多次暂停/继续后，画面和声音同步；停止后正常导出。
6. 默认目录、中文路径和带空格路径可保存，已有文件不会被覆盖。
