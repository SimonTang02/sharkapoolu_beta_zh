# GitHub 发布检查清单

本仓库的设计目标是发布代码和占位配置，同时将所有候选人专属状态留在本地。创建 GitHub remote 前运行以下检查：

```bash
make release-check
git status --short
git remote -v
```

检查将进入首次公开提交的每个文件：

```bash
git ls-files --cached --others --exclude-standard
git diff --check
```

## 现有历史

如果当前历史提交曾包含简历、联系方式、凭据、浏览器导出、数据库或生成的申请材料，不要原样推送这些历史提交。从当前工作树移除文件并不会删除其早期 blob，仍可从 Git 历史下载。

最稳妥的发布方式是根据审计过的工作树创建新仓库，不复制旧 `.git` 目录：

```bash
make public-snapshot DEST=../26fall_intern_public
cd ../26fall_intern_public
make release-check
```

目标目录必须尚不存在。导出器只复制 `git ls-files --cached --others --exclude-standard` 报告的现有文件，初始化空的 `main` 分支，不创建提交。如果现有 Git 作者邮箱需要保密，首次提交时使用 GitHub `noreply` 地址。

若必须保留历史，则使用 `git filter-repo` 重写，检查所有重写后的提交，并轮换任何曾进入提交的凭据。历史重写会改变提交 ID，应在共享 remote 之前完成。

私有档案可用时，`make history-audit` 会提供额外本地检查。它报告路径和标记类型，不会打印私有具体值。

## 仓库设置

公开仓库之前：

1. 选择并添加许可证。尚未添加许可证时，其他人通常没有复制、修改或再分发代码的许可。
2. 添加简短仓库说明，并且只有在符合目标读者时才添加 `job-search`、`resume`、`python` 和 `browser-automation` 等主题标签。
3. 保持 GitHub Actions 启用，使公开审计、测试和共享配置验证在每次 push 和 pull request 时运行。
4. 绝不要上传 `private_data/`、`GPT_CONTEXT.md`、本地浏览器 profile、cookie、SQLite 数据库、渲染后的 PDF 或本地配置覆盖文件。
