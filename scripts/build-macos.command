#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "此脚本只能在 macOS 上运行。"
  exit 1
fi
if ! command -v brew >/dev/null; then
  echo "请先安装 Homebrew，再运行此脚本。详见 docs/MACOS.md。"
  exit 1
fi
brew install python@3.13 python-tk@3.13 ffmpeg
python_bin="$(brew --prefix python@3.13)/bin/python3.13"
export PATH="$(brew --prefix)/bin:$PATH"
venv=".venv-macos-$(uname -m)"
"$python_bin" -m venv "$venv"
"$venv/bin/python" -m pip install -r requirements-macos-dev.txt
"$venv/bin/python" scripts/build.py
open releases
echo "构建完成。按回车退出。"
read -r
