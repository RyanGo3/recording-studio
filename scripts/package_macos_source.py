"""Create a small macOS build kit on any host; this is not a compiled .app."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import uuid
import zipfile

from build import ROOT, SOURCES


def main() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    name = f"PotatoFactory-v{version}-macos-build-kit"
    release = ROOT / "releases" / (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
    release.mkdir(parents=True, exist_ok=False)
    files = {ROOT / path for path in (
        *SOURCES, "README.md", "VERSION", "THIRD_PARTY_NOTICES.md",
        "requirements.txt", "requirements-dev.txt", "requirements-macos-dev.txt",
        ".gitignore", ".gitattributes", ".github/workflows/build-macos.yml")}
    for folder in ("assets", "docs", "licenses", "scripts", "tests"):
        files.update(path for path in (ROOT / folder).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts
                     and path.suffix not in {".pyc", ".pyo"})
    archive = release / (name + ".zip")
    manifest = {}
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(files):
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            manifest[relative] = hashlib.sha256(data).hexdigest()
            entry = zipfile.ZipInfo(f"{name}/{relative}")
            entry.create_system = 3
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (0o100755 if path.suffix == ".command" else 0o100644) << 16
            output.writestr(entry, data)
        output.writestr(f"{name}/BUILD-KIT.json", json.dumps({
            "kind": "macos-source-build-kit", "compiled_app_included": False,
            "version": version, "source_sha256": manifest,
        }, ensure_ascii=False, indent=2) + "\n")
        output.writestr(f"{name}/先读我.txt",
                        "这是 macOS 源码构建包，不含已编译的 .app。\n"
                        "在 macOS 15 或更新版本解压，在本目录的终端运行：\n"
                        "bash scripts/build-macos.command\n"
                        "需要 Homebrew、网络连接；详细步骤见 docs/MACOS.md。\n"
                        "构建后从 releases 子目录取得对应 Mac 架构的 .app 和 ZIP。\n")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (release / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(json.dumps({"archive": str(archive), "sha256": digest,
                      "files": len(files), "compiled_app_included": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
