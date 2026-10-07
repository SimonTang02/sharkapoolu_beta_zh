# 配置责任与就绪状态

完成初始设置后，日常功能开关请使用带注释的 [`easy_settings.json` 指南](beginner-settings.md)。其 `jobbot-settings` 入口会组合当前基础配置，不会更改现有高级配置或浏览器进度；个人事实仍需单独填写。

本指南说明全新 clone 会初始化什么、哪些内容需要候选人输入，以及 Agent 或开发者可以协助哪些工作。Agent 并非必需；具备技术经验的用户可以自行完成相同配置。候选人确认和门户验证步骤不能通过推断答案替代。

## bootstrap 会完成什么

先阅读 [AGENTS.md](../AGENTS.md) 和 [AGENT_HANDOFF.md](../AGENT_HANDOFF.md)，再遵循[安装指南](installation.md)。bootstrap 会创建虚拟环境、安装代码、创建缺少的空白 profile/evidence/keyword/credential 文件、验证结构并运行合成测试。它会保留已有文件。必填事实为空时会发出警告；没有错误并不代表已可申请。

bootstrap 不会生成完整简历、选择合适来源、调整职业偏好、初始化生产职位历史、授予机构访问权限、登录雇主门户、配置 SSH 共享、SMTP 或调度器。可选浏览器安装不会选择或验证浏览器 profile。目前附带的 runtime 选择 Windows CDP；仅用 Linux 的用户需在私有覆盖中主动选择 `local_persistent`。

## 各项由谁配置

| 项目 | 现有输入或工具 | Agent/开发者可协助 | 候选人或操作者必须提供 |
| --- | --- | --- | --- |
| 私有存储与机器角色 | `JOBBOT_PRIVATE_DIR`、`private_paths.py`、`jobbot-private paths` | 选择路径、权限、备份及本地/SSH 角色 | 存储位置及获授权的机器 |
| 身份、教育和日期 | `profiles/application_profile.json` | 整理已提供事实；检查重复项和格式 | 法定身份、联系方式、学位/GPA 与确认的毕业日期 |
| 技能与证据 | `cv/profile/evidence_profile.json`、`application_keywords.json` | 将已核实经历映射到证据组、来源 ID 和关键词 | 真实经历、来源文件及明确限制 |
| 工作许可、签证赞助和同意 | 档案中的授权与精确私有答案 | 保留未解决答案；检查门户措辞和适用地点 | 地区事实及每项所需政策决定 |
| 职业偏好与来源 | 私有 `job_bot.local.json` 覆盖中的 `sources`、`scoring`、`strategy` | 将偏好映射到实际读取的设置；停用不适合的来源；预览小规模运行 | 岗位、地区、招聘周期、可入职时间和排除条件 |
| 新地区、年份或职业类别 | 现有三个策略轨道及硬件分类器 | 现有轨道无法表达偏好时修改代码和测试 | 期望范围及资格规则审查 |
| 机构渠道 | 来源定义及会话预检 | 配置受支持来源或实现新适配器 | 合法账户/访问权限、登录与 MFA |
| 浏览器准备 | browser extra、私有 `application_browser.mode`、`CHROME_CDP_URL` | 设置专用 profile、受限 CDP 传输和路由 | 登录、CAPTCHA、MFA、账户恢复及政策选择 |
| LaTeX 材料 | 私有 `cv/source/current.tex`、共享 `cv/latex/`、`cvbot` | 将已核实内容转换为预期布局、配置 TeX、修复渲染 | 批准的声明、语言、来源材料和最终 PDF 审阅 |
| 手动 HTML 投递包 | `applybot manual-kit`、已审阅 manifest 和逐岗 `Application_Data.json` | 汇总事实答案、官方 URL、PDF 审阅哈希及附件；生成新的投递包 | 已审阅目标、批准的 PDF 和实际支持文件 |
| 门户专用准备 | 队列、批次、login-preflight 和受支持的 dispatcher | 学习雇主的首份真实表单；配置精确选项或实现缺失行为 | 未知必填答案、数量限制和逐公司同意 |
| 共享数据库 | `jobbot-db configure`、`prepare-host`、`check` | 核实角色、去重历史、准备备份和 SSH 路由 | 获授权主机、已有 SSH 访问和在线可用性 |
| 报告与 SMTP | 工作流预览、周报、私有 `email` 设置 | 配置时区/收件人并检查 dry-run 输出 | 私有凭据及明确发送授权 |
| 定期运行 | 现有 CLI 和操作系统调度器 | 按要求创建并验证调度器/环境设置 | 运行时间和投递授权 |
| 续接与提交记录 | 私有交接、manifest、进度导出、`mark_submitted.py` | 匹配排名/URL，查询权威数据库，记录已确认结果 | 当前岗位、实际回执或明确成功确认 |

## 重要实现限制

`career_preferences` 记录意向，但任意新增偏好键不会自动被每条命令读取。策略报告目前构建三个具名队列：中国/香港校园招聘、美国新毕业生、美国暑期实习。它使用偏硬件的模式和招聘年份逻辑。仅新增 JSON 轨道或地区标签不会新增采集器或报告分支。宣称覆盖能力前，应追踪实际消费该配置的代码。

授权验证器目前对中国大陆、香港和美国设有一等范围。其他地区需要保留经明确确认的私有答案，并进行门户专用审查；地址字段中添加国家并不能证明工作许可。

原始 PDF/TXT/TeX 导入、证据到答案映射、实际评分字段和安全定制方式见[候选人资料导入指南](candidate-onboarding.md)。`cvbot import-resume` 会保留原件并创建审阅草稿和 Agent 任务；仅含图片的 PDF 需要视觉阅读/OCR。一般简历和批次生成现在使用私有 evidence 中的毕业日期，不再固定覆盖招聘年份。日期为空时保留已审阅的源值。旧导出常量仅为兼容保留，这些入口不会再自动应用它。

标准 HTML 生成器读取已组装的 manifest 和每个岗位的 `Application_Data.json`，目前尚不支持将 `batch_campaign.py` 输出一键转换为投递包。它会检查声明的 PDF 审阅状态和哈希，但无法代替视觉审阅或验证事实准确性。成绩单官方/非官方标记和雇主要求仍需复核。公开示例值不能证明候选人资格。

数据库共享会将 SQL 路由到一台在线主机。它不会同步私有文件、浏览器会话或未保存表单；不支持离线写入或自动合并系统。迁移和更新方法见[共享数据库指南](shared-database.md)。

## 有边界的首次运行

1. 选择一个稳定的私有根目录；创建并验证空白文件。启用申请功能前，记录事实和待确认问题。
2. 从 `examples/job_bot.local.json` 创建私有 runtime 覆盖。其 include 路径假定私有目录位于仓库内；外部目录时需调整 include 和 `database.path`。验证组合配置。
3. 选择合适来源和受支持的策略范围。先预览小规模、仅 HTTP 工作流；检查实际结果和错误后再扩大范围。
4. 初始化预期使用的数据库并运行已审阅工作流。共享模式下，先确认主机保存唯一权威历史。
5. 准备已审阅的私有简历和证据档案。准备批次之前先了解一个雇主当前表单。保持最终提交保护为 false。
6. 选择手动 HTML 投递或受支持的 Agent 辅助表单准备。检查实际门户并由本人提交，之后记录已确认结果。

接受 `--config` 的命令必须传入同一个私有覆盖。Makefile 的 daily/weekly 默认入口不会自动载入任意私有设置。审阅来源选择后，例如：

```bash
jobbot-config --config private_data/config/job_bot.local.json
python3 job_bot/run_modules.py --config private_data/config/job_bot.local.json \
  --workflow http_refresh --dry-run
```

## 开始新的 Agent 对话

全新 clone 提供工具和配置契约，但不能从公开模板恢复私有申请历史或浏览器本地进度。使用已有安装时，应在仓库契约之后读取当前规范私有文件和最新的私有续接记录。

每个新渲染的手动投递包都包含自己的 `AGENT_HANDOFF.md`，其中记录包目录、原始目标数量、manifest 指纹、进度键前缀和回执规则。面板会链接该文件，并提供可复制的续接提示。Agent 可据此在没有旧聊天记录时定位资料，但在确认进度前必须获取当前岗位、导出或回执；不得重放旧的清理或材料生成步骤。

已有投递包不会为加入此功能而修改。保留其入口文件地址和 localStorage 键，并使用现有私有交接说明。只有官方回执证据或明确成功确认才允许新增 `submitted` 记录；进度导出不会自动同步数据库。
