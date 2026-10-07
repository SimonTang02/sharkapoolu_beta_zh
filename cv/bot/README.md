# CV 与求职信机器人

本模块仅使用 `evidence_profile.json` 中已审阅的证据，将职位描述转换为匹配报告和求职信草稿。处理过程刻意保持确定性：不调用 LLM、不编造指标，也不提交任何申请。

示例：

```bash
python3 -m cv.bot.bot \
  --job-description /path/to/job_description.txt \
  --company NVIDIA \
  --role "ASIC Design Intern"
```

Markdown 审阅包写入 `private_data/cv/reports/`。将其视为草稿：复制任何内容到申请前，检查相关性、真实性、语气、公司名和岗位名称。

已由职位监控器采集的岗位无需手动复制 JD：

```bash
python3 -m cv.bot.bot --job-url 'STORED_JOB_URL'
```

机器人从本地 SQLite 数据库读取公司、标题、地点和描述。`--company` 与 `--role` 可覆盖不准确的来源标签，但不会更改数据库。

可生成完整的已审阅套件，包括职位专用 LaTeX 简历、简历 PDF、求职信源文件/PDF、审阅包及 manifest：

```bash
python3 -m cv.bot.bot \
  --job-url 'STORED_JOB_URL' \
  --generate-bundle
```

套件分别保存在 `private_data/cv/variants/` 下；绝不会覆盖规范源文件 `private_data/cv/source/current.tex`。生成 PDF 在 manifest 和渲染页面审阅前均为草稿。

## 外部项目选择

`cv/docs/external_tools.md` 记录评估过、可选集成的项目。Resume Matcher 是适合单独容器化的本地服务候选。其输出应保留为建议版，不能自动覆盖 `current.tex`。

## 共享申请关键词库

常规报告和套件生成会通过 `cv.application_keywords.select_keywords` 读取 `private_paths.APPLICATION_KEYWORDS`。选择预设时，岗位标题优先于 JD 文本；不受支持的岗位关键词不能变成候选人技能。使用 `--keyword-library /path/to/library.json` 可覆盖私有默认值。缺少可选词库时保留原有行为；格式错误的词库会报错。纯渲染辅助函数也接受显式 `keyword_selection`。

套件的 Technical Skills 栏最多使用十个有证据支持的技术标签；保留项目源文本；在审阅报告和 `application_keywords.json` 中记录证据与协作示例，并由 manifest 引用。仅编辑关键词库不会重写已有套件。

两个机器人都可以直接调用选择器：

```python
from cv.application_keywords import select_keywords, apply_keyword_selection

selection = select_keywords("GPU Architecture Engineer", job_description)
prepared_profile = apply_keyword_selection(application_profile, selection)
```

申请辅助函数会保留手工技能、事实答案和安全标记。它只会填充空技能列表，或更新此前生成且内容未变的列表。协作用语仅作为审阅背景保留，绝不升级为性格自评。

私有证据档案提供 `expected_graduation_date` 时，渲染器会对所有岗位类型（包括实习）使用该日期，并将解析日期记录到套件 manifest。未确认毕业日期的档案会保留源文件日期，除非显式传入覆盖值。只更新 `graduation_school`；其他学位和日期保持不变。
