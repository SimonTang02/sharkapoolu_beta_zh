# Sharkapoolu 中文发布版

> **第一次用？** 先看[逐步上手指南](docs/foolproof_guide_zh.md)：从Windows＋WSL2＋VS Code安装到简历导入，每一步都有命令和可复制给Agent的消息。

这是 Sharkapoolu 的简体中文发布仓库。Sharkapoolu 是由 Agent 驱动的求职工具集，用于职位发现、定制简历和申请跟踪。主开发与功能开发在[英文上游仓库](https://github.com/SimonTang02/sharkapoolu_beta)进行；本仓库用于维护与上游兼容的中文翻译和发布内容。此版本适配的上游提交为 `bd90331c16d9f128f985928ae7752b49e8e758c9`。翻译对应关系见[本地化说明](docs/localization.md)，简体中文翻译许可说明见[中文许可说明](docs/license-zh-CN.md)。

Sharkapoolu 是一个本地优先的求职工具集，可发现和排序职位、定制简历，并辅助准备申请材料。项目最初围绕硬件和数字设计岗位构建，但来源与评分层可配置。候选人资料保存在公开 Git 历史之外；最终申请提交始终由用户本人完成。

coding agent 请先阅读[完整操作与交接手册](AGENT_HANDOFF.md)及[配置责任与就绪指南](docs/getting-started.md)，再查看[带注释的公开模板](examples/README.md)，其中包括虚构的 Mike Malon / NYU 计算机科学简历示例和共享数据库设置。

## 主要功能

- **找岗位。** 告诉Agent岗位方向、地区和毕业时间，它配置支持的来源与排序，再把岗位收进数据库。默认偏硬件方向，其他专业可能需要适配。
- **准备简历材料。** 提供PDF或LaTeX简历，核实提取的事实；Agent整理可编辑LaTeX和逐岗材料。安装TeX后生成PDF，每份都要审阅再使用。
- **帮你填表。** 手动HTML投递包提供答案和附件，支持的浏览器适配器可以准备字段。登录、验证码、审阅和最终提交由你完成。
- **记录投递。** 使用本机数据库，也可手工填UTF-8 CSV。只有真实回执或你明确确认成功才登记已提交；跨机器SSH共享是可选项。
- **按需开关、接着做。** 一份配置选择功能、地区、审查轮数和准备模式。私有任务与交接文件帮助新对话续接，浏览器进度导出和回执用于核实实际进度。

先跑通一个岗位。[上手指南](docs/foolproof_guide_zh.md)说明安装脚本会做什么、哪些仍需要你或Agent完成。浏览器、报告和共享等进阶功能见[安装指南](docs/installation.md)、[功能开关](docs/beginner-settings.md)及[共享数据库](docs/shared-database.md)。

## 快速开始

请按仓库实际访问权限选择 HTTPS 或 SSH clone；仓库访问配置不同，不能保证匿名用户可以克隆。英文开发仓库与本中文版请使用独立 clone 和虚拟环境，避免相同 CLI 入口相互覆盖。

需要 Python 3.10 或更新版本。

```bash
git clone https://github.com/SimonTang02/sharkapoolu_beta_zh.git
cd sharkapoolu_beta_zh
./scripts/bootstrap.sh --with-resume
source .venv/bin/activate
```

bootstrap 脚本会创建 `.venv`、安装软件包、从安全示例生成被忽略的私有配置文件、验证配置并运行测试。英文开发仓库与本中文发布仓库请使用彼此独立的 clone 和虚拟环境，避免覆盖相同的命令行入口。空白模板检查通过并不代表已具备申请条件。请填写 `private_data/` 下生成的文件，再运行验证：

```bash
jobbot-private check
jobbot-config --config job_bot/config.china_hk_ic_foreign.json
```

如需浏览器辅助采集和表单准备：

```bash
./scripts/bootstrap.sh --with-browser --with-resume
sudo .venv/bin/python -m playwright install-deps chromium  # 仅 Linux，必要时运行
```

Windows CDP、TeX、加密数据和更新说明见[安装指南](docs/installation.md)。开始采集或申请之前，请阅读[各项配置由谁负责](docs/getting-started.md)：bootstrap 不会填写候选人事实、选择来源、创建 LaTeX 简历、登录招聘门户或组装手动投递包。

## 仓库结构

```text
application_bot/   浏览器会话检查与人工审阅辅助表单适配器
cv/                简历证据、关键词与文档生成代码
docs/              安装、架构、配置与接口说明
examples/          复制到私有数据目录的脱敏模板
job_bot/           职位发现、规范化、评分、数据库与报告
schemas/           候选人维护的私有文件 JSON Schema
scripts/           bootstrap、隐私审计与发布工具
private_data/      被忽略的身份资料、凭据、会话、数据库和输出
```

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `make private-init` | 创建缺失的私有模板，不覆盖已有文件 |
| `make private-check` | 检查私有目录结构、跨文件一致性和权限 |
| `make config-check` | 解析 includes 并验证最终生效的公开配置 |
| `make daily` | 采集、重新评分并生成每日和每周报告 |
| `make weekly` | 根据 SQLite 重建每周报告 |
| `make session-audit` | 检查已配置的登录会话，不暴露凭据 |
| `applybot manual-kit --manifest <private-manifest> --output <new-private-directory> --progress-key <stable-prefix>` | 根据已审阅 manifest 渲染离线手动申请包，并附带新对话交接说明 |
| `make workflow-plan WORKFLOW=http_refresh` | 预览具名模块工作流 |
| `make workflow WORKFLOW=http_refresh` | 运行具名模块工作流 |
| `make test` | 运行单元测试 |
| `make release-check` | 审计可发布文件、运行测试并验证配置 |

安装后的命令行入口包括 `jobbot`、`applybot`、`cvbot`、`jobbot-config`、`jobbot-private` 和 `jobbot-db`。

## 配置模型

`job_bot/config/` 下的版本化配置定义运行行为、来源、评分、策略、工作流、门户适配器和字段映射。`private_data/` 下被忽略的文件保存身份、证据、关键词、凭据、浏览器状态、申请材料和数据库。设置 `JOBBOT_PRIVATE_DIR` 可将整个私有目录树迁移到加密磁盘或私有同步目录。wheel 安装默认使用平台数据目录，而不是写入 `site-packages`。

请从[配置指南](docs/configuration.md)开始。仓库级 [AGENTS.md](AGENTS.md) 为 coding agent 规定了完整操作契约，包括安全规则、命令选择和扩展点。功能开发请回到[英文开发上游](https://github.com/SimonTang02/sharkapoolu_beta)；本中文版负责翻译维护并保持兼容。

## 安全与隐私

- 不要提交 `private_data/`、简历、cookie、token、截图或本地数据库。
- 法律授权、签证赞助、人口统计和声明类答案必须来自候选人的明确输入。
- 只有启用单独安全门后，自动化才可准备并保存草稿；绝不能点击最终 Submit 控件。
- 发布前运行 `make public-audit`。如旧 Git 历史可能包含个人文件，运行 `make history-audit`。

报告漏洞或疑似数据暴露前，请阅读 [SECURITY.md](SECURITY.md)。贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE)
