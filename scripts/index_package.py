"""Register an existing portable build in the small, versioned GitHub package index."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/RyanGo3/recording-studio"


def register(release: Path):
    result = json.loads((release / "build-result.json").read_text(encoding="utf-8"))
    bundle, archive = Path(result["bundle"]), Path(result["archive"])
    info = json.loads((bundle / "build-info.json").read_text(encoding="utf-8"))
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != result["sha256"]:
        raise ValueError("安装包 SHA-256 与构建记录不一致，未更新索引。")
    version, build_id = info["version"], info["build_id"]
    # Metadata must stay under packages/, even if a build record is malformed.
    if not all(part and part not in {".", ".."} and
               all(char.isalnum() or char in "._-" for char in part)
               for part in (version, build_id)):
        raise ValueError("版本号或构建编号无效。")
    tag = "v" + version
    release_url = f"{REPOSITORY}/releases/tag/{tag}"
    download_url = f"{REPOSITORY}/releases/download/{tag}/{archive.name}"
    folder = ROOT / "packages" / tag / build_id
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "app_name": info["app_name"], "author": info["author"], "version": version,
        "platform": "windows-x64", "build_id": build_id, "built_at_utc": info["built_at_utc"],
        "source_base_commit": info["git_commit"], "source_sha256": info["source_sha256"],
        "release_tag": tag, "release_url": release_url,
        "assets": [{"name": archive.name, "bytes": archive.stat().st_size,
                    "sha256": digest, "download_url": download_url}],
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    (folder / "README.md").write_text(
        f"# 土豆工厂 {version} · Windows x64\n\n"
        f"作者：大土豆|Ryan · 构建：`{build_id}`\n\n"
        f"[下载安装包]({download_url}) · [发行说明]({release_url})\n\n"
        f"- 文件：`{archive.name}`\n"
        f"- 大小：{archive.stat().st_size:,} 字节（{archive.stat().st_size/1024**2:.2f} MiB）\n"
        f"- 构建时间（UTC）：{info['built_at_utc']}\n"
        f"- SHA-256：`{digest}`\n\n"
        "完整解压后运行 `PotatoFactory.exe`，保留同目录 FFmpeg / FFprobe。\n\n"
        "`manifest.json` 记录实际打包的 11 个运行模块散列；`source_base_commit` "
        "为构建时的 Git 基线，可能另含由散列标识的未提交修改。\n\n"
        "下载入口在对应 GitHub Release 附件上传后可用。重复构建保留独立目录；"
        "同版本发布新构建时请先更新版本号，避免覆盖已发布附件。\n",
        encoding="utf-8")
    records = []
    for path in (ROOT / "packages").glob("v*/*/manifest.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        asset = item["assets"][0]
        relative = path.parent.relative_to(ROOT / "packages").as_posix()
        records.append((item["built_at_utc"],
                        f"| {item['version']} | Windows x64 | `{item['build_id']}` | "
                        f"[下载]({asset['download_url']}) | [详情与校验]({relative}/) |\n"))
    table = "".join(row for _, row in sorted(records, reverse=True))
    (ROOT / "packages" / "README.md").write_text(
        "# 土豆工厂安装包\n\n"
        "此目录按版本和构建编号管理安装包索引、说明与 SHA-256 校验信息。"
        f"实际 ZIP 存放于同仓库的 [GitHub Releases]({REPOSITORY}/releases)，下载不需要 Git LFS。\n\n"
        "| 版本 | 平台 | 构建 | 安装包 | 记录 |\n"
        "| --- | --- | --- | --- | --- |\n" + table +
        "\n## 目录约定\n\n"
        "```text\npackages/\n  README.md\n  v<版本>/\n    <构建编号>/\n      README.md\n      manifest.json\n      SHA256SUMS.txt\n```\n\n"
        "GitHub 普通 Git 仓库阻止超过 100 MiB 的单个文件；当前完整包大于该限制，"
        "因此二进制作为 Release 附件存放。本目录只提交体积很小的索引，旧版记录保留。\n\n"
        "## 构建与发布\n\n"
        "1. 修改版本号，运行 `python scripts/build.py`；新包生成于 `releases/<构建编号>/`，"
        "构建完成后自动生成本目录索引。\n"
        "2. 提交代码与索引，在 GitHub 为该提交创建 `v<版本>` Release，"
        "上传完整 ZIP 以及对应的 `SHA256SUMS.txt`。\n"
        "3. 核对附件文件名、大小和 SHA-256，再发布 Release。索引生成不会自动上传附件。\n\n"
        "对已有本地构建可运行 "
        "`python scripts/index_package.py releases/<构建编号>`，只登记并校验，不重新打包。\n\n"
        "[GitHub 大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)\n",
        encoding="utf-8")
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    args = parser.parse_args()
    print(register(args.release.resolve()))
