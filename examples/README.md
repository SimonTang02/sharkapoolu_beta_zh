# 公开模板

所有以 `_template` 结尾的文件都是公开示例。Mike Malon、其 NYU 计算机科学学位、日期、联系方式和项目均为虚构。任何示例都不能证明真实人士的工作许可、国籍或同意状态。

| 文件 | 用途和填写说明 |
| --- | --- |
| `manual_jobs_template.csv`、`manual_applications_template.csv` | 纯手工记录所用的空白 UTF-8 CSV 表头。bootstrap 会创建缺失的私有副本；字段说明与确认规则见 `docs/manual-database.md`。 |
| `candidate_scoring_template.json` | 供 `weighted_keywords_v1` 使用的虚构软件岗位偏好权重。复制到私有目录后调整；验证 include 路径和实际评分行为。见 `docs/candidate-onboarding.md`。 |
| `easy_settings_template.json` | 面向非技术用户、带中文注释的日常控制项。bootstrap 会创建私有 `config/easy_settings.json`，且不覆盖已有文件。依次运行 `jobbot-settings check`、`plan`，再显式执行 `run --execute`；见 `docs/beginner-settings.md`。 |
| `manual_kit_manifest_template.json` | 标准离线手动投递 HTML 模板的虚构 manifest。真实资料只填在私有源套件中；审阅 PDF 并记录哈希。见 `application_bot/README.md`。 |
| `database_connection_template.json` | 每个连接字段均在 `_comment` 中说明。真实设置只写入私有 `config/database_connection.json`；优先使用 `jobbot-db configure`。 |
| `database_template.sql` | 每列均带注释的空白数据库 Schema，不含候选人或申请记录。常规初始化使用 `jobbot init`；申请命令会按需添加 campaign 表。 |
| `application_profile_template.json` | 虚构档案，并按章节提供 `_comment` 填写说明。真实使用前替换所有演示值。 |
| `evidence_profile_template.json` | 虚构声明清单及填写说明，不预设法律问题答案。 |
| `resume_template.tex` | 带注释的单页 Mike Malon / NYU CS 简历源文件；兼容生成器的 Summary 和 Technical Skills 标记。 |

现有空白的 `application_profile.json`、`evidence_profile.json` 和 `application_keywords.json` 仍是 bootstrap 默认值。`_comment` 是有效 JSON 元数据，不是 JavaScript 注释；档案读取器会容忍它。运行时 JSON 不要添加 `//` 注释或尾逗号。

如需试用虚构示例，请使用独立 clone 或临时私有根目录。将档案复制到规范位置，将 `resume_template.tex` 复制到私有 `cv/source/current.tex`，并保留由 `jobbot-private init` 初始化的关键词库。使用真实资料前请阅读[交接指南](../AGENT_HANDOFF.md)。bootstrap 不会把虚构人物复制覆盖到现有私有档案上。
