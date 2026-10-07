# 安装

推荐的 Windows + WSL2 + VS Code 环境，以及经过审阅的 Mac/原生 Windows/Linux 支持边界，见 [platforms.md](platforms.md)。原生 Windows 提供 PowerShell bootstrap 入口；Linux 专用 shell/Make 命令不能直接互换。

选择工作流前请阅读[配置责任与就绪指南](getting-started.md)。该指南区分自动生成的空白文件与候选人确认的事实，并说明策略、浏览器、材料和门户仍需完成的设置。

若两台联网的 WSL 机器要访问同一份私有 SQLite 数据，请参照[共享数据库配置指南](shared-database.md)。数据库保存在一台主机上，另一台通过已认证的 SSH 执行数据库操作。

## 系统要求

- Git
- Python 3.10 或更新版本及 `venv`
- 可选：浏览器辅助功能所需的 Chromium 依赖
- 可选：渲染简历 PDF 所需的 TeX Live、TinyTeX 或 Overleaf

## 全新克隆

按本仓库的实际访问权限选择 HTTPS 或 SSH clone；访问权限因用户和仓库设置而异，不能保证匿名用户可克隆。

```bash
git clone https://github.com/SimonTang02/sharkapoolu_beta_zh.git
cd sharkapoolu_beta_zh
./scripts/bootstrap.sh
source .venv/bin/activate
```

`bootstrap.sh` 可安全重复运行，会保留现有私有文件。它会创建虚拟环境、以 editable 模式安装软件包、将缺失模板复制到被忽略的私有目录、验证两层配置并运行测试。

使用 `PYTHON=/path/to/python ./scripts/bootstrap.sh` 可选择其他 Python。只有在同一版本的测试已通过后，才将 `--skip-tests` 用作快速修复选项。

## 填写私有配置

首次运行会创建：

```text
private_data/config/easy_settings.json
private_data/database/manual/jobs.csv
private_data/database/manual/applications.csv
private_data/credentials/passport.env
private_data/profiles/application_profile.json
private_data/cv/profile/evidence_profile.json
private_data/cv/profile/application_keywords.json
```

在本地编辑这些文件，然后运行：

```bash
jobbot-private check
```

检查器报告路径和字段名称，但绝不打印具体值。详情见[配置指南](configuration.md)。

若要将个人资料放在 clone 目录之外，在每条命令运行前设置同一个稳定绝对路径：

```bash
export JOBBOT_PRIVATE_DIR="$HOME/.local/share/sharkapoolu"
./scripts/bootstrap.sh
```

将该目录保存在加密存储或加密的私有备份中。不要将它指向公开 Git 仓库。

bootstrap 创建的 editable 安装默认使用 clone 内的 `private_data/`。在 checkout 外通过 wheel 安装时，默认使用 `$XDG_DATA_HOME/sharkapoolu`，通常为 `~/.local/share/sharkapoolu`。

## 浏览器支持

安装可选浏览器依赖和 Chromium：

```bash
./scripts/bootstrap.sh --with-browser
```

在 Ubuntu、Debian、WSL 和部分服务器上，Playwright 还需要系统库。此命令需要管理员权限：

```bash
sudo .venv/bin/python -m playwright install-deps chromium
```

新的 bootstrap 安装使用 Playwright 标准操作系统缓存，或显式设置的 `PLAYWRIGHT_BROWSERS_PATH`。旧版 `.playwright-browsers/` 仍被忽略，不会删除。安装和后续命令需设置相同覆盖值。

### 通过 CDP 使用专用 Windows Chrome

`windows_cdp` 模式将 WSL 连接到独立的 Windows Chrome profile。端点保存在 `private_data/credentials/passport.env`：

```dotenv
CHROME_CDP_URL=http://<wsl-gateway-address>:9223
```

在私有配置覆盖中设置 `application_browser.mode: "windows_cdp"`。脚本和网络限制说明见 [`job_bot/README.md`](../job_bot/README.md)。绝不要将调试端口暴露到 LAN，也不要复用日常个人 Chrome profile。

## 可选本地配置覆盖

共享入口是 `job_bot/config/jobbot.json`。如需机器专用路径或浏览器选择，将示例复制到默认私有位置：

```bash
cp examples/job_bot.local.json private_data/config/job_bot.local.json
jobbot-config --config private_data/config/job_bot.local.json
```

示例中的相对 include 假设 `private_data/` 位于仓库内默认布局。如果 `JOBBOT_PRIVATE_DIR` 指向其他位置，需将 include 改为此 clone 中 `job_bot/config/jobbot.json` 的绝对路径。

## 渲染简历

候选人维护的 `.tex` 入口文件放在 `private_data/cv/source/` 下，然后运行：

```bash
make check-tools
make current
make visa
```

共享类和样式位于 `cv/latex/`；渲染后的 PDF 保存到 `private_data/cv/build/`。Overleaf 可连同这些共享样式文件编译私有源文件。

## 首次功能运行

先运行只读检查和预览：

```bash
make private-check
make config-check
jobbot auth-status --config job_bot/config/jobbot.json \
  --env-file private_data/credentials/passport.env
make workflow-plan WORKFLOW=http_refresh
```

初始化数据库；确认来源列表适合候选人后再运行选定工作流：

```bash
jobbot init --config job_bot/config/jobbot.json
make workflow WORKFLOW=http_refresh
make weekly
```

## 更新已安装环境

```bash
git pull --ff-only
./scripts/bootstrap.sh
```

私有文件会保留。查看发布说明；Schema 或示例变更后重新运行 `jobbot-private check`。

## 原始简历与手工数据库

如需提取 PDF 文本，使用 `./scripts/bootstrap.sh --with-resume`，然后遵循[候选人资料导入指南](candidate-onboarding.md)。导入器会新建私有审阅包，不会替换现有源文件或 PDF。只使用 CSV 时，请参阅[手工数据库指南](manual-database.md)。
