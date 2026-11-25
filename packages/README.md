# 土豆工厂安装包

此目录按版本和构建编号管理安装包索引、说明与 SHA-256 校验信息。实际 ZIP 存放于同仓库的 [GitHub Releases](https://github.com/RyanGo3/recording-studio/releases)，下载不需要 Git LFS。

| 版本 | 平台 | 构建 | 安装包 | 记录 |
| --- | --- | --- | --- | --- |
| 2.1.1 | Windows x64 | `20261125_145337_d13224f6` | [下载](https://github.com/RyanGo3/recording-studio/releases/download/v2.1.1/PotatoFactory-v2.1.1-windows-x64.zip) | [详情与校验](v2.1.1/20261125_145337_d13224f6/) |
| 2.1.0 | Windows x64 | `20261124_175605_99515761` | [下载](https://github.com/RyanGo3/recording-studio/releases/download/v2.1.0/PotatoFactory-v2.1.0-windows-x64.zip) | [详情与校验](v2.1.0/20261124_175605_99515761/) |

## 目录约定

```text
packages/
  README.md
  v<版本>/
    <构建编号>/
      README.md
      manifest.json
      SHA256SUMS.txt
```

GitHub 普通 Git 仓库阻止超过 100 MiB 的单个文件；当前完整包大于该限制，因此二进制作为 Release 附件存放。本目录只提交体积很小的索引，旧版记录保留。

## 构建与发布

1. 修改版本号，运行 `python scripts/build.py`；新包生成于 `releases/<构建编号>/`，构建完成后自动生成本目录索引。
2. 提交代码与索引，在 GitHub 为该提交创建 `v<版本>` Release，上传完整 ZIP 以及对应的 `SHA256SUMS.txt`。
3. 核对附件文件名、大小和 SHA-256，再发布 Release。索引生成不会自动上传附件。

对已有本地构建可运行 `python scripts/index_package.py releases/<构建编号>`，只登记并校验，不重新打包。

[GitHub 大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)
