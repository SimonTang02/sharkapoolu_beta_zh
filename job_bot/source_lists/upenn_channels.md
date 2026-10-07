# Handshake 以外的 Penn 渠道

截至 2026 年 9 月 17 日（纽约时间；UTC 为 9 月 18 日）尝试并检查。配置：`job_bot/config/penn_channels.json`。读取器及访问报告：`job_bot/penn_channels.py`。这些渠道与现有 Handshake 职位来源和企业职位评分分开。

| 渠道 | 已核实状态 | 采集范围 |
|---|---|---|
| Workday@Penn Student Employment | 需要重新进行 PennKey 认证；保留页面 | 学生岗位读取器仍待登录和字段映射核实；未摄入职位 |
| Engineering recruiting events | 收集到 3 条即将举行活动的参考 | 可见的带日期活动链接；不含已结束活动 |
| Engineering Career Development Hub | PennKey 访问成功；收集到 5 条资源链接 | 职业资源，不是职位 |
| Interstride | 保留邮件登录页面 | 未验证已认证职位采集器 |
| CareerShift | 保留登录/注册页面 | 未验证已认证职位采集器 |
| GoinGlobal | 保留当前官方登录页面 | Penn 旧版 `default.aspx` 链接未加载；机构访问尚未验证 |
| MyPenn | 现有 PennKey 会话进入网站 | 仅收集人脉入口/状态；不抓取校友资料或联系任何人 |
| CURF Research Directory | PennKey 访问成功；收集到 10 个可见研究链接 | 仅当前目录页；主要是本科机会，不代表已验证有薪/硕士岗位 |

已禁用渠道会跳过，也不会重新打开其登录页面。现有标签页会保留。按账户区分的登录结果应写入被忽略的输出报告，而不是共享来源指南。

## 命令

```bash
# 读取/重新检查所有渠道并刷新已实现的资源读取器
make workflow WORKFLOW=penn_channels_refresh

# 打开缺失的入口/登录页面；如果已有保留目标则复用
python3 job_bot/penn_channels.py --open-login-pages
```

Chrome 可用时，每日处理会刷新此报告。W38 及之后的周报会链接到报告。参考信息与企业职位计数、评分及毕业生/实习策略分开。具体而言，Work-Study 和 Non-Work-Study 资格必须逐项确认；研究目录中的名录不能证明有薪、当前空缺或硕士资格。

## 浏览器与交接

被忽略的 `private_data/outputs/job_bot/penn_channels/tabs.json` 保存保留的 CDP target ID。用户登录期间请保留这些页面。重复运行 `--open-login-pages` 不会导航或复制已有目标。常规运行中的已实现读取器会在临时标签页中刷新；已有用户页面只读取、不更改。用户关闭 target 后，打开页面命令可重新创建其入口。PennKey/MFA/注册/同意步骤仍由人工处理。

结果写入 `private_data/outputs/job_bot/penn_channels/latest.md` 和 `latest.json`。报告记录每个渠道的状态和类型化参考，不保存凭据、cookie、档案正文或 SSO 查询字符串。待处理招聘网站须在登录后检查，才能启用完整职位采集。不要将已配置的入口称为已实现 API 或完整职位采集器。

各渠道的官方参考链接与配置保存在一起：[Penn 学生职位](https://srfs.upenn.edu/student-employment/job-search)、[工程职业发展](https://academics.engineering.upenn.edu/student-career-development/)、[Interstride Penn](https://www.interstride.com/upenn/)、[CareerShift Penn](https://upenn.careershift.com/)、[Penn GoinGlobal](https://careerservices.upenn.edu/resources/goinglobal/)、[当前 GoinGlobal 登录](https://online.goinglobal.com/user/login)、[MyPenn](https://mypenn.upenn.edu/)、[CURF 目录](https://curf.upenn.edu/undergraduate-research/research-directory)。

## 每周校园收入章节

`make weekly` 会在周报靠前位置重建独立的校园就业章节。`campus_employment_report.py` 会读取最新渠道快照中的 `campus_job` 参考，以及配置中 `penn_channels.campus_employment.leads` 下已审阅的线索。它不采用企业 IC 相关性分数。公开页面是人工核实的快照；仅重建数据库的周报命令不会刷新它们，应保留核查日期。

可选数值字段：`hourly_usd: [min, max]`、`weekly_hours: [min, max]`。应包含 `pay_basis`、`hours_basis`、`status`、`checked_at`、`eligibility` 和 `next_step`，以便区分当前录用、历史薪资引用和未知项。月工时按一年 52 周、每月 12 周换算；税前工资按未四舍五入的周工时计算。缺失值继续保持未知。研究线索不会自动变成有薪工作。单独的预算表是假设情景。PSA 公开页面仍标注费率为 2025 年 8 月；请在 Workday 核实当前职位空缺和条款。

官方参考页面：[Penn Recreation](https://recreation.upenn.edu/sports/2021/8/26/structured-sports-employment-opportunities.aspx)、[Penn Student Agencies](https://psa.universitylife.upenn.edu/join/)、[ISSS 校内就业](https://global.upenn.edu/isss/oncampus/)、[ISSS SSN 流程](https://global.upenn.edu/isss/ssn/)。
