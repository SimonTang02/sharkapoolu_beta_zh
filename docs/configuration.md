# 配置参考

Sharkapoolu 将可共享的行为配置与候选人自有事实分开。公开配置纳入 Git 版本管理。身份、经历声明、凭据、浏览器状态、数据库和生成文档保存在被忽略的私有目录树中。

## 路径与优先级

`private_paths.py` 定义所有私有位置。在源码 checkout 中，默认根目录为 `private_data/`。wheel 安装使用 `$XDG_DATA_HOME/sharkapoolu`（通常为 `~/.local/share/sharkapoolu`），避免写入 `site-packages`。`JOBBOT_PRIVATE_DIR` 可覆盖这两种默认值。每台机器应使用一个稳定的绝对路径。

共享配置入口为 `job_bot/config/jobbot.json`。它按顺序加载 `includes`，后加载的映射会覆盖先前映射。本地文件可包含共享入口并覆盖少量键。相对 include 路径以声明该项的文件所在目录为基准。

分别验证两层配置：

```bash
jobbot-config --config job_bot/config/jobbot.json
jobbot-private check
jobbot-private paths
```

## 公开配置

### `runtime.json`

- `database.path`：SQLite 文件位置，应位于私有根目录下。
- `scan.max_workers`：来源请求的最大并行数。
- `scan.retry_attempts` 和 `retry_backoff_seconds`：有上限的重试策略。
- `scan.lifecycle_guard`：阻止可疑的小批或空刷新覆盖先前健康的来源结果。
- `scan.auto_start_windows_chrome` 和 `browser_start_wait_seconds`：可选的专用浏览器启动行为。
- `application_browser.mode`：`local_persistent` 或 `windows_cdp`。
- `application_browser.auto_submit`：必须保持 `false`。
- `application_browser.windows_cdp`：受限专用 Chrome 连接的端点或环境变量名称。
- `tab_management`：限制活动标签页，保护已填写表单和会话锚点，并控制重复页清理和截图。
- `digest`：报告条数、最低分数、标题筛选、去重和扫描错误可见性。
- `reporting`：时区和周报生成策略。
- `email`：SMTP 传输及环境变量名称。除非明确配置投递，否则应保持 `dry_run` 开启。

### `sources.json`

每个 `sources[]` 项描述一个采集器。常见字段包括 `name`、`type`、公司/租户标识、`source_category`、`sync_active`、`role_kinds`、搜索文本、包含/排除模式和官方参考链接。允许适配器专用的端点及分页字段。若某来源定义日后可能还会使用，应停用而不是删除。

### `scoring.json`

- `bands`：职位采集报告中展示的分数区间。
- `foundation_groups`：主要岗位族及其证据关键词。
- `modifiers`：按标题/正文范围设置的有界加分或扣分。
- `keyword_groups`：兼容评分权重和标题加分。

分数用于安排审查顺序，不代表候选人具备相应资格。

### `strategy.json`

定义地区和学位轨道、目标方向顺序、分类优先级、A/B 阈值、已存分数加成、岗位模式分类器、基础方向覆盖规则和按轨道划分的资格窗口。候选人的招聘周期变化时，应更新日期和毕业时间假设。

### `workflows.json`

`workflows` 将工作流名称映射到有序模块、可选来源选择器、并行设置和错误策略。`experiment_tracking` 控制可复现 manifest 和 effective-config 哈希。遇到不熟悉的工作流，务必先预览：

```bash
make workflow-plan WORKFLOW=<name>
```

### `portals.json`

- `session_audit`：登录探测的并发数和超时。
- `company_profiles`：公司匹配和账户规则。
- `adapters`：平台/主机路由、脚本、超时、会话探测和草稿能力。

声明适配器并不等于授权提交申请。

### `field_mappings.json`

将中性的档案路径映射到身份信息和文档等表单概念。地区策略记录哪些值必须显式确认。安全设置要求授权后才能填入相应值，禁止推断法律答案，捕获审阅材料，并保持提交功能关闭。

### `penn_channels.json`

定义大学职业渠道和校园就业线索。工时方案用于在报告中估算月薪。要求登录的渠道仍通过浏览器辅助访问，不能将机构凭据写入配置。

## 私有申请档案

根据 `examples/application_profile.json` 创建。该文件是写入申请表单的事实的权威来源。

### 顶层元数据

- `$schema`：指向仓库 JSON Schema 的编辑器提示。
- `schema_version`：当前为 `1`；只有执行仓库迁移时才修改。

### `fields`

保存稳定的联系与身份信息：推荐来源、是否曾在雇主处工作、法定/偏好/本族姓名、邮箱、电话、邮寄地址、国家、出生日期和公开个人资料 URL。门户询问法定身份时，使用与正式证件一致的格式。未知值留空或设为 `null`，不要猜测。

### `documents`

`resume_path` 和 `cover_letter_path` 选择已审阅的上传文件。路径可相对于项目根目录。打开申请适配器前，确认文件符合目标岗位和语言要求。

### `education`

每条记录保存学校、学位、专业、GPA、开始/结束月份和年份。`portal_values` 保存特定门户要求的已核实拼写或层级选项，不更改中性事实。已知时同时保留毕业月份和年份。

### `languages`、`work_experience`、`projects` 和 `skills`

这些字段是可复用的结构化表单条目。语言记录包含语言、是否母语和自评熟练度。工作和项目记录可包含门户支持的字段，但每项声明必须与证据档案或已审阅简历一致。`skills` 是简短标签列表。

### `workday_checkbox_groups` 和 `custom_answers`

将门户精确提示映射到候选人确认的答案。不确定答案保持 `null`。门户文字变化会使精确提示匹配失效，因此应检查最终表单中的每一项。

### `answer_rules`

保存适用于兼容提示的、范围严格限定的可复用答案规则。每条规则必须注明适用范围和事实来源。法律、移民、人口统计、薪酬或声明类问题不得使用宽泛规则。

### `voluntary_disclosures`

包含可选人口统计选择和条款接受状态。空值表示未选择。设置 `accept_terms` 前，必须审阅本次申请对应的条款。

### `explicit_authorization`

- `user_confirmed`：候选人是否明确确认了记录的授权事实。
- `location_scopes`：该确认涵盖的国家或司法辖区。
- `work_authorized` 和 `sponsorship_required`：已确认答案，或 `null`。
- `company_consents`：针对具体公司的已确认同意。

某个国家的授权不得挪用于其他国家。

### `career_preferences`

保存岗位/城市调整偏好和 `resume_language_policy`。后者按雇主组选择简历默认语言，允许明确的职位语言要求覆盖默认值，记录期望页数，要求审阅渲染 PDF，并注明所选中文字体。

### `safety`

- `allow_sensitive_answers`：允许填写已确认的敏感答案；不允许推断答案。
- `allow_server_draft`：允许适配器在明确操作下保存服务端草稿。
- `allow_submit`：必须保持 `false`。

### `personal_facts_confirmation`

此可选兼容对象记录尚无稳定一等字段的候选人事实来源和确认日期，例如顾问/实验室澄清。已有常规结构化字段时应优先使用。每条记录都应注明候选人何时、如何确认。

## 私有证据档案

`evidence_profile.json` 是简历工具使用的权威声明清单。

- `identity`：展示名/标题姓名及跨文件邮箱。
- `graduation_school` 和 `expected_graduation_date`：规范化的当前学位完成信息。
- `tailored_summaries`：按目标岗位族记录的已审阅摘要。
- `candidate_summary` 和 `closing_strength`：可复用的个人定位陈述。
- `evidence_groups`：具名证据组、匹配关键词和事实陈述。
- `education_gpa`：按学校整理并已审阅的 GPA 展示格式。
- `graduation_confirmation`：当前毕业信息的学校、月份/年份、确认日期和来源。

检查器会将身份邮箱与申请档案比对。其他重复教育信息仍需人工审阅。

## 私有关键词库

`application_keywords.json` 保存简历和申请工具可使用的、有证据支持的词汇。

- `sources`：带稳定 ID 的来源路径/说明。
- `usage_rules`：应用于每次选择的限制。
- `technical_keywords`：技术技能记录。
- `collaboration_personality_keywords`：团队协作和工作风格记录。
- `role_presets`：适用于目标岗位族的精选关键词 ID。

每个关键词都需要唯一的 `id`、中英文标签、事实证据、英文示例、有效的 `source_ids` 和 `claim_status`。可选字段记录熟练程度、匹配变体和简历短标签。预设只能引用对应分组中存在的 ID；检查器会验证。

查看选择结果，不要修改表单：

```bash
applybot keywords --help
cvbot --help
```

## 凭据和会话指针

`private_data/credentials/passport.env` 使用 `NAME=VALUE` 格式，文件权限为 `600`。支持的命名族包括：

- `COMPANY_<SLUG>_{USERNAME,PASSWORD,COOKIE,STORAGE_STATE}`
- `PLATFORM_<SLUG>_{USERNAME,PASSWORD,COOKIE,STORAGE_STATE}`
- `CHROME_CDP_URL`
- `SMTP_USERNAME`、`SMTP_PASSWORD` 和 `JOBBOT_EMAIL_TO`

只填写已启用集成实际使用的变量。SSO、MFA 或依赖 localStorage 的会话优先使用浏览器 storage state。cookie 值只适用于一个确切目标域的请求头，不能在其他地方复用。

## 本地运行覆盖

`examples/job_bot.local.json` 演示用于数据库路径、浏览器端点和邮件路由的私有覆盖。行为更改放在此处；密钥放在环境文件中。私有根目录在 clone 外部时，将共享配置 include 调整为绝对路径。

## 编辑检查清单

更改公开配置文件后：

```bash
make config-check
make test
```

更改私有事实或关键词后：

```bash
make private-check
make public-audit
```

使用前审阅生成的简历 PDF 和申请字段报告。
