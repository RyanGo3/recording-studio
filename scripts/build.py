"""Build a fresh Windows portable release without removing previous outputs."""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("app.py", "apple_ui.py", "audio_sources.py", "audio_processing.py",
           "recording_v2.py", "cinematic.py", "audio_softening.py", "softening_ui.py",
           "media_conversion.py", "conversion_ui.py", "path_inputs.py")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("请在 Windows x64 上构建。")
    ffmpeg = ROOT / "tools" / "ffmpeg.exe"
    ffprobe = ROOT / "tools" / "ffprobe.exe"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        raise SystemExit("请先运行 scripts\\prepare-tools.ps1 准备 FFmpeg 和 FFprobe。")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    build_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    work = ROOT / "builds" / build_id
    release = ROOT / "releases" / build_id
    bundle_name = f"PotatoFactory-v{version}-windows-x64"
    bundle = release / bundle_name
    work.mkdir(parents=True, exist_ok=False)
    bundle.mkdir(parents=True, exist_ok=False)
    version_file = work / "version-info.txt"
    version_tuple = tuple(int(part) for part in version.split(".")) + (0,)
    version_file.write_text(
        "VSVersionInfo(ffi=FixedFileInfo(filevers={0!r}, prodvers={0!r}, "
        "mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), "
        "kids=[StringFileInfo([StringTable('080404B0', ["
        "StringStruct('CompanyName', '大土豆|Ryan'), "
        "StringStruct('FileDescription', '土豆工厂'), "
        "StringStruct('ProductName', '土豆工厂'), "
        "StringStruct('FileVersion', '{1}'), StringStruct('ProductVersion', '{1}'), "
        "StringStruct('OriginalFilename', 'PotatoFactory.exe')])]), "
        "VarFileInfo([VarStruct('Translation', [2052, 1200])])])".format(version_tuple, version),
        encoding="utf-8")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm",
        "--name", "PotatoFactory", "--onefile", "--windowed",
        "--version-file", str(version_file),
        "--icon", str(ROOT / "assets" / "studio.ico"),
        "--collect-all", "soundcard", "--hidden-import", "lameenc",
        # SciPy supports optional array backends; this app processes NumPy arrays only.
        "--exclude-module", "torch", "--exclude-module", "tensorflow",
        "--exclude-module", "jax", "--exclude-module", "cupy",
        "--exclude-module", "matplotlib", "--exclude-module", "IPython",
        "--exclude-module", "pytest",
        "--distpath", str(bundle), "--workpath", str(work / "work"),
        "--specpath", str(work), str(ROOT / "app.py"),
    ], cwd=ROOT, check=True)
    shutil.copy2(ffmpeg, bundle / "ffmpeg.exe")
    shutil.copy2(ffprobe, bundle / "ffprobe.exe")
    for name in ("docs", "licenses"):
        shutil.copytree(ROOT / name, bundle / name)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", bundle / "THIRD_PARTY_NOTICES.md")
    (bundle / "README.txt").write_text(
        "土豆工厂\n作者：大土豆|Ryan\n\n"
        "完整解压后，双击 PotatoFactory.exe。\n"
        "请保留同目录 ffmpeg.exe、ffprobe.exe，以及 docs 和 licenses。\n"
        "无需安装 Python。适用于 Windows 10 / 11 x64。\n"
        "完整使用说明：docs/USER_GUIDE.md\n"
        "默认保存到 D:\\土豆工厂，无 D 盘时使用用户 Videos\\土豆工厂；已有旧目录时继续使用。\n"
        "添加文件支持粘贴路径、文件或文件夹拖入；目录仅导入本层支持的文件。\n"
        "录制可暂停 / 继续，停止后请等待保存及效果处理完成。\n"
        "SHA256SUMS.txt 可校验包内文件。\n"
        "本版本未进行商业代码签名。\n",
        encoding="utf-8-sig")
    kwargs = {"creationflags": 0x08000000}
    ffmpeg_version = subprocess.run(
        [str(ffmpeg), "-version"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True, **kwargs).stdout.splitlines()[0]
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True) if shutil.which("git") else None
    info = {
        "app_name": "土豆工厂", "author": "大土豆|Ryan",
        "version": version, "build_id": build_id,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "platform": platform.platform(),
        "git_commit": git.stdout.strip() if git and git.returncode == 0 else None,
        "dependencies": {name: metadata.version(name) for name in
                         ("soundcard", "numpy", "scipy", "lameenc", "pygame", "mutagen",
                          "Pillow", "cffi", "pycparser", "pyinstaller",
                          "pyinstaller-hooks-contrib", "altgraph", "packaging",
                          "pefile", "pywin32-ctypes", "setuptools")},
        "source_sha256": {name: sha256(ROOT / name) for name in SOURCES},
        "ffmpeg_version": ffmpeg_version, "ffmpeg_sha256": sha256(ffmpeg),
        "ffprobe_sha256": sha256(ffprobe),
    }
    (bundle / "build-info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = sorted(path for path in bundle.rglob("*") if path.is_file())
    (bundle / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(bundle).as_posix()}\n" for path in files),
        encoding="utf-8")
    archive = release / f"{bundle_name}.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                output.write(path, f"{bundle_name}/{path.relative_to(bundle).as_posix()}")
    (release / "build-result.json").write_text(json.dumps({
        "bundle": str(bundle), "archive": str(archive), "sha256": sha256(archive)
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "index_package.py"), str(release)],
                   cwd=ROOT, check=True)
    print(json.dumps({"bundle": str(bundle), "archive": str(archive),
                      "sha256": sha256(archive)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
