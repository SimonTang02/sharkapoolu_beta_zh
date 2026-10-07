# Sharkapoolu 中文发布版

这是 Sharkapoolu 的简体中文发布仓库。Sharkapoolu 是由 Agent 驱动的求职工具集，用于职位发现、定制简历和申请跟踪。主开发与功能开发在[英文上游仓库](https://github.com/SimonTang02/sharkapoolu_beta)进行；本仓库用于维护与上游兼容的中文翻译和发布内容。此版本适配的上游提交为 `bd90331c16d9f128f985928ae7752b49e8e758c9`。翻译对应关系见[本地化说明](docs/localization.md)，简体中文翻译许可说明见[中文许可说明](docs/license-zh-CN.md)。

Sharkapoolu 是一个本地优先的求职工具集，可发现和排序职位、定制简历，并辅助准备申请材料。项目最初围绕硬件和数字设计岗位构建，但来源与评分层可配置。候选人资料保存在公开 Git 历史之外；最终申请提交始终由用户本人完成。

新用户和 coding agent 请先阅读[完整操作与交接手册](AGENT_HANDOFF.md)及[配置责任与就绪指南](docs/getting-started.md)，再查看[带注释的公开模板](examples/README.md)，其中包括虚构的 Mike Malon / NYU 计算机科学简历示例和共享数据库设置。

## 主要功能

- **简历导入与手工记录。** 将原始 PDF、文本或 LaTeX 简历导入私有审阅包，其中包含可编辑草稿，以及关于回答、证据关键词和评分的 Agent 说明。UTF-8 CSV 支持手工维护职位和已确认的申请历史，并可事务性导入。请从[候选人资料导入指南](docs/candidate-onboarding.md)及[Windows + WSL2 + VS Code 平台指南](docs/platforms.md)开始。
- **单文件入门设置。** 带中文注释的设置文件可选择模块、地区、HTTP 或隔离浏览器采集、CDP 准备、审查轮次、时间预算和重试。`jobbot-settings` 会在显式执行前检查并预览更改。详见[入门用户指南](docs/beginner-settings.md)。
- **两种申请工作流。** 手动投递提供离线 HTML 面板、逐职位可复制答案、已审阅 PDF 和支持材料链接、回执备注及浏览器本地进度导出。Agent 辅助准备提供队列、显式批次、ATS 路由，以及直到人工审阅点为止的受支持表单填写。两种流程都由候选人完成最终提交；排队、上传或到达 Review 都不代表已经提交。
- **基于偏好的职位发现与审查。** 从 HTTP feed、招聘 API 和经授权的浏览器会话采集并规范化职位。可配置来源选择、岗位关键词、评分权重，以及已实现的地区、学位和招聘周期筛选。资格和工作许可仍须有证据并由候选人确认。新增地区、年份或岗位类别可能需要调整当前以硬件为重点的策略代码。
- **跨机器共享申请历史。** SQLite 保存职位和申请事件。可选的、经过身份验证的 SSH 允许客户端操作主机上的私有数据库，无须开放数据库监听端口。机器须保持在线；浏览器会话、PDF 和进度导出不会自动同步。详见[共享规则](docs/shared-database.md)。
- **以证据为依据的材料。** `cvbot` 从私有资料中选择有依据的经历和关键词，生成定制建议；配置 TeX 工具链后，还可渲染 LaTeX 简历与求职信 PDF 套件。生成的 PDF 仍需审阅；生成器不会创造新的资格条件。
- **私有存储与个人定制。** 身份、证据、关键词、凭据、会话、回执和生成材料均保存在候选人自行管理的私有目录树中。公开模板使用空白或虚构资料；配置和隐私审计只报告结构，不打印具体值。
- **专用浏览器会话与标签管理。** 支持使用独立 Chromium profile，或受限的 Windows Chrome CDP。会话审计、登录预检和经过审计的标签清理会保留正在填写的申请表及身份验证锚点。MFA、CAPTCHA 和政策选择仍由人工处理；持久化 profile 不保证登录持续有效。
- **报告与可选通知。** 可生成每日、每周和策略审查报告。SMTP 投递需要私有配置和明确授权，默认采用 dry-run。定期运行需另行配置调度器。
- **新对话交接。** 每个新生成的手动申请包都包含 campaign 交接说明、manifest 指纹和可复制的续接提示。新 Agent 可在没有旧聊天记录时找到文件和操作规则；实际进度仍以当前私有记录、用户导出和回执为准。

## 快速开始

请按仓库实际访问权限选择 HTTPS 或 SSH clone；仓库访问配置不同，不能保证匿名用户可以克隆。英文开发仓库与本中文版请使用独立 clone 和虚拟环境，避免相同 CLI 入口相互覆盖。

需要 Python 3.10 或更新版本。

```bash
git clone https://github.com/SimonTang02/sharkapoolu_beta_zh.git
cd sharkapoolu_beta_zh
./scripts/bootstrap.sh
source .venv/bin/activate
```

bootstrap 脚本会创建 `.venv`、安装软件包、从安全示例生成被忽略的私有配置文件、验证配置并运行测试。英文开发仓库与本中文发布仓库请使用彼此独立的 clone 和虚拟环境，避免覆盖相同的命令行入口。空白模板检查通过并不代表已具备申请条件。请填写 `private_data/` 下生成的文件，再运行验证：

```bash
jobbot-private check
jobbot-config --config job_bot/config.china_hk_ic_foreign.json
```

如需浏览器辅助采集和表单准备：

```bash
./scripts/bootstrap.sh --with-browser
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
