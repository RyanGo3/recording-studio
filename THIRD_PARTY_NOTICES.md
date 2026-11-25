# 第三方软件、许可与来源

本项目使用下列独立工具及 Python 组件。各组件保留原作者版权、许可及第三方声明；`licenses/` 保存本次构建所用发行包携带的原始许可文本。此文件不替代这些原始许可。

本次整理不为项目自身新增开源许可证。仓库以私有方式备份；如果后续向其他接收者分发，应结合各组件原始许可处理源码提供、修改和重新链接等要求。

## 外部工具与解释器

| 组件 | 本次版本 | 来源与许可资料 |
| --- | --- | --- |
| FFmpeg | 8.1.2 essentials_build，Windows x64，Gyan 静态构建 | [上游构建](https://www.gyan.dev/ffmpeg/builds/)、[FFmpeg 源码](https://github.com/FFmpeg/FFmpeg/tree/n8.1.2)、[构建仓库](https://github.com/GyanD/codexffmpeg)，GPLv3；原始说明含准确源码提交 `38b88335f9` 及启用库清单 |
| CPython | 3.13.9 x64 | [源码与许可](https://github.com/python/cpython/tree/v3.13.9)，PSF License 及附带历史声明；免安装 EXE 内含运行时，源码工具包不含系统安装程序 |
| Tcl/Tk | Python 官方安装环境携带的版本，EXE 构建时由 PyInstaller 收集 | [Tcl/Tk](https://www.tcl.tk/)，原始许可随本次 Python 环境收集到 `licenses/tcl-tk/` |

FFmpeg ZIP 固定来源：

```text
https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip
ZIP SHA-256: db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec
EXE SHA-256: 1326dde4c84ff1f96fe6b8916c5bed29e163e9b5dccf995f6f3db069d143ec5e
```

`tools/ffmpeg-provenance.json` 记录工具来源和散列。免安装包中的 `build-info.json` 记录实际采用的 FFmpeg 版本和散列。随包提供 `ffmpeg.exe` 和声音柔化所需的 `ffprobe.exe`，不附带 `ffplay.exe`。

## Python 运行组件

以下许可名称依据本次安装包元数据记录；以 `licenses/` 内的完整原文为准。

| 包 | 版本 | 许可 | 源码 |
| --- | --- | --- | --- |
| soundcard | 0.4.6 | BSD 3-Clause | https://github.com/bastibe/SoundCard |
| numpy | 2.5.4 | BSD-3-Clause 及其附带组件许可 | https://github.com/numpy/numpy |
| scipy | 1.16.3 | BSD-3-Clause 及其附带组件许可，见 `licenses/scipy/` | https://github.com/scipy/scipy |
| lameenc | 1.8.4 | LGPL-3.0-or-later，含 LAME 编码器 | https://github.com/chrisstaite/lameenc |
| pygame | 2.6.1 | LGPL；底层 SDL 等组件另见其原始声明 | https://github.com/pygame/pygame/tree/2.6.1 |
| mutagen | 1.48.1 | GPL-2.0-or-later | https://github.com/quodlibet/mutagen |
| Pillow | 12.2.0 | MIT-CMU 及其附带组件许可 | https://github.com/python-pillow/Pillow |
| cffi | 2.1.1 | MIT-0，依安装包原文 | https://foss.heptapod.net/pypy/cffi |
| pycparser | 3.0 | BSD-3-Clause | https://github.com/eliben/pycparser |

pygame 的 LGPL 原文从该项目 2.6.1 标签的 `docs/LGPL.txt` 获取；Python 许可从 CPython 3.13.9 标签获取。其他许可从实际安装的 wheel 复制，保留各包目录结构与子组件声明。

## 构建组件

PyInstaller 6.22.2、pyinstaller-hooks-contrib 2026.7、altgraph 0.17.5、packaging 25.0、pefile 2024.8.26、pywin32-ctypes 0.2.3、setuptools 80.9.0 的原始许可也保存在 `licenses/`。

PyInstaller 的许可包含 bootloader 特别例外，见其 `COPYING.txt`。运行 EXE 无须在系统中安装这些构建组件。
