# Sharkapoolu 完整操作与新对话交接手册

本仓库是简体中文发布版；功能开发请回到[英文主开发仓库](https://github.com/SimonTang02/sharkapoolu_beta)，中文版负责翻译维护并保持兼容。本版本对应的上游提交为 `bd90331c16d9f128f985928ae7752b49e8e758c9`。本地化术语与许可说明见 `docs/localization.md` 和 `docs/license-zh-CN.md`。

本文面向从 GitHub 下载本项目的人和首次进入仓库的 coding agent。它介绍当前
代码能做什么、输入放在哪里、每项命令的影响及共享数据库规则。真实候选人和
机器的配置由本地私有文件提供；公开仓库不保存私人历史状态。

## 1. 新对话开始时读取与确认

新候选人从原始PDF/TXT/LaTeX开始，先读
[`docs/candidate-onboarding.md`](docs/candidate-onboarding.md)：包含简历导入、
逐页事实溯源、填表字段映射、可消费的评分权重和cvbot定制流程。
`cvbot import-resume`生成新私有草稿及AGENT_TASK.md，不自动认定资格或覆盖材料。
纯手工资料/CSV/主库录入见[`docs/manual-database.md`](docs/manual-database.md)。
推荐Windows+WSL2+VS Code，Mac/原生Windows/Linux差异见
[`docs/platforms.md`](docs/platforms.md)；只有Codex尚不具备项目运行依赖。

非技术用户的日常入口为 `jobbot-settings`。先读
[`docs/beginner-settings.md`](docs/beginner-settings.md) 和私有
`config/easy_settings.json`；它包含模式、模块、浏览器、审查次数、地区开关。
新对话可读取入口生成的 `AGENT_TASK.md`，按其中路径恢复目标与真实审查；
任务包不等于自动启动Agent，也不等于已经提交。旧入口须显式传入生成配置
才能采用这些开关，不能把私有易用配置的存在解释为已经生效。

先完整阅读 `AGENTS.md` 和本文，再按任务阅读对应模块 README、
`docs/configuration.md`、`docs/installation.md`、`docs/shared-database.md`。
无需旧对话即可确定操作接口；真实登录、未知事实和雇主页面变化仍需现场核实。

从零开始时还须读[`docs/getting-started.md`](docs/getting-started.md)：其中逐项
区分bootstrap已完成、Agent/开发者需配置、用户本人必须确认的工作，说明现有
地区/年份/毕业日期等限制。空白模板验证通过不代表具备投递资格或材料就绪。

手动HTML标准模板入口为`applybot manual-kit`，详见
[`application_bot/README.md`](application_bot/README.md)。新生成的投递包包含
本批`AGENT_HANDOFF.md`、Manifest指纹、进度键及可复制的续接指令。新对话能
找到操作规则和原目标，但实际进度必须结合最新私有交接、用户导出及真实回执。
不要把公开模板、默认空白总表或全库submitted数量解释为本批进度。

从项目根目录开始检查：

```bash
git status --short --branch
git remote -v
python3 --version
python3 -m job_bot.private_config paths
```

记录当前机器角色：独立本地主机、共享数据库主机或 SSH 客户端。确认
`JOBBOT_PRIVATE_DIR` 是否设置；只有存在的本地私有报告才代表该机器历史。
检查已提交申请、待处理表单和实际登录情况，再选择具体任务。现有工作树有
修改时逐项保留；不要通过重新安装、覆盖 profile 或重置 Git 解决冲突。

## 2. 项目地图与完整功能入口

| 入口 | 负责内容 | 关键输入 / 输出 |
| --- | --- | --- |
| `job_bot/` / `jobbot` | 岗位采集、去重、评分、状态、摘要 | composed config → SQLite / 私有报告 |
| `job_bot/run_modules.py` | 工作流计划、筛选来源、运行记录 | workflows / selectors → 私有 manifest |
| `job_bot/daily_pipeline.py` | 日扫、评分、策略、周报、干预提示 | sources / browser / credentials |
| `job_bot/strategy_report.py` | 地区、毕业窗口、资格、公司配额与审阅队列 | strategy / applications / limits |
| `job_bot/weekly_report.py` | 从数据库重建每周汇总 | jobs / applications / scan_runs |
| `job_bot/penn_channels.py` | 已实现的 Penn 校园渠道 | 授权的机构登录 / 独立渠道报告 |
| `cv/` / `cvbot` | 证据匹配、简历变体、求职信、材料 bundle | evidence / keywords / TeX → 私有 PDF |
| `application_bot/` / `applybot` | 排队、profile 绑定、登录检查、门户路由、表单准备 | application ID / reviewed PDF / browser |
| `application_bot/batch_campaign.py` | 指定岗位或策略选出的批次、材料准备 | job IDs / campaign ID |
| `application_bot/tab_manager.py` | 标签审计、认领、去重清理 | 本机专用浏览器 / tab registry |
| `application_bot/manual_kit.py` | 标准离线手动投递HTML与新对话接手说明 | 已审阅Manifest / 逐岗Application_Data → 新私有目录 |
| `jobbot-private` | 私有模板初始化、类型及一致性检查 | examples / schemas / private_paths |
| `jobbot-db` | 本地或 SSH 数据库、备份准备、变化监控 | 私有 connection config |
| `scripts/`、`Makefile` | 安装、构建、隐私审计、发布验证 | bootstrap / release-check |

公开程序仍以硬件岗位为默认采集和评分领域。Mike Malon 的 NYU CS 简历是格式
示例；改变候选人的专业不会自动改变来源、评分、资格窗口或校园机构权限。
NYU 用户不能据此使用 Penn 登录；启用新学校渠道需要合法访问和适配实现。

## 3. 下载与安装

Linux / WSL 需要 Git、Python 3.10+ 与 venv。TeX 和浏览器按任务另装。

```bash
git clone https://github.com/SimonTang02/sharkapoolu_beta_zh.git
cd sharkapoolu_beta_zh
./scripts/bootstrap.sh
source .venv/bin/activate
jobbot-private check
make config-check
```

bootstrap 创建 `.venv`、editable install、缺失的空白私有模板并运行合成测试。按仓库实际访问权限克隆，不能假定可匿名访问。英文上游和中文发布版使用独立 clone 与 `.venv`，避免相同入口互相覆盖。
空白事实产生提示，不能解释为事实已确认。已有私有文件保留；不要使用
`jobbot-private init --force` 替换真实资料。

浏览器任务另外运行 `./scripts/bootstrap.sh --with-browser`；若提示系统库缺失，
在支持的平台安装 Playwright Chromium 系统依赖。PDF 编译需要 `latexmk` 和
pdfLaTeX；中文源选择 XeLaTeX 并安装相应字体，详见 `cv/README.md`。

环境激活后使用安装的 CLI；没有激活时可用 `.venv/bin/python -m ...`。
所有相对路径和本文命令均从项目根目录执行。

## 4. 私有目录、配置和模板填写

`private_paths.py` 是唯一私有路径接口。默认是 clone 内的 `private_data/`；
需使用外部存储时，在初始化前设定并在每次运行时保留 `JOBBOT_PRIVATE_DIR`。
不要把该变量指向另一候选人的资料目录。轮子安装路径行为见 installation 文档。

| 私有位置 | 填写或生成方式 |
| --- | --- |
| `credentials/passport.env` | 从 `job_bot/env.template` 填需要的变量；权限 600；不要打印值 |
| `profiles/application_profile.json` | 真实身份、教育、经历、文档、逐国授权及 safety |
| `cv/profile/evidence_profile.json` | 逐条已证实的简历声明，与 profile 邮箱、日期一致 |
| `cv/profile/application_keywords.json` | 有来源的技能 / 行为词汇与角色 presets |
| `config/job_bot.local.json` | 实际私有运行覆盖；只有显式 `--config` 才会被命令使用 |
| `config/database_connection.json` | 是否经 SSH 操作主库；模块自动读取，与普通 config 不同 |
| `database/`、`database/backups/` | 主库或旧本地快照、一致备份 |
| `cv/source/`、`cv/build/`、`cv/variants/` | 主简历源、PDF、职位专用材料 |
| `browser/`、`outputs/` | 本机登录状态、截图、审阅证据、日志与报告 |

公开 `_template` 文件集中在 `examples/`，每个字段或项目旁有注释说明：
`database_connection_template.json`、`database_template.sql`、
`application_profile_template.json`、`evidence_profile_template.json`、
`resume_template.tex`。JSON 用 `_comment` 保存说明，保持标准 JSON 可解析。
bootstrap 继续使用原有空白模板；不会把虚构 Mike 的身份覆盖到真实资料中。

仅在独立演示目录中复制 Mike 模板到上述 canonical locations，并把 TeX 命名为
`current.tex`。真实使用时替换姓名、联系信息、学校、日期、项目和技能；删除
虚构标识并不使虚构经历变真实。GPA、国籍、工作资格、签证及 consent 未知时
保持空白 / null。`allow_submit` 始终为 false。

## 5. 配置选择与候选人适配

公共 composition root 是 `job_bot/config/jobbot.json`；
`job_bot/config.china_hk_ic_foreign.json` 是兼容入口。includes 从声明文件所在
目录解析，后来的字典递归覆盖，列表替换；按名称修改列表用 `patches`。

runtime 管理数据库、浏览器、并发、报告和邮件；sources 管理官方来源及分页；
scoring 计算相关性；strategy 管理角色、地区、毕业窗口和配额；portals 管理
ATS 和公司路由；field_mappings 管理字段与安全边界；workflows 管理模块顺序；
penn_channels 是已实现的机构渠道。完整字段说明在 `docs/configuration.md`。

可以从 `examples/job_bot.local.json` 创建私有覆盖，但外部 private root 必须
修改 include，指向 clone 的 composition root。`database.path` 必须解析到
`private_paths.DATABASE_DIR` 中；否则 SSH 路由会把它当成本地外部测试库。
不要以为设置 `JOBBOT_PRIVATE_DIR` 会重写公共 config 中的显式数据库路径。

显式选择覆盖并预览：

```bash
jobbot-config --config private_data/config/job_bot.local.json
python3 job_bot/run_modules.py --config private_data/config/job_bot.local.json \
  --workflow http_refresh --dry-run
```

后续每项支持 `--config` 的命令都传同一文件。Make 的 daily / weekly 使用其
默认入口；需要私有覆盖时直接调用 Python，并显式传 `--config`。

调整目标岗位时同时审阅来源、scoring、strategy 和实际 JD。不要以评分或
`review_required` 代替已确认资格，不要从一人的毕业日期继承另一人的窗口。

## 6. 岗位扫描、评分、策略与报告

独立 / 主机首次创建空库：

```bash
jobbot init --config job_bot/config/jobbot.json
make workflow-plan WORKFLOW=http_refresh
make workflow WORKFLOW=http_refresh
make weekly
```

初次宜选 HTTP 来源；browser_refresh 需要专用浏览器和实际登录。来源可按
`--source`、`--company`、`--source-type`、`--source-category` 和
`--source-browser` 限定，详见 `jobbot scan --help` / `run_modules.py --help`。
不完整搜索保持 `sync_active=false`；来源错误不能解释为职位全部关闭。

日常 `make daily` 采集并更新数据库和报告；`make weekly` 重新读取现有主库，
不采集新来源。`jobbot rescore` 更新存储分数；scoring_experiment 仅计算实验
结果。strategy 的 `--sync-db` 替换审阅队列，不改变申请状态。
digest 使用现有库；run 会扫描并按 email 设置生成 / 发送摘要。
默认 `email.dry_run=true`。configure 邮件不构成自动发送授权。

扫描、评分和报告分别写私有输出。报告是快照，数据变更后重新生成；源失败、
未经核实的关闭、校园 pay 和资格条件必须保持可区分。工作流的 manifest
记录选择的模块、effective-config hash、退出码和耗时。

## 7. 浏览器登录、专用会话与门户适配

支持 `local_persistent` 和 `windows_cdp`。前者是独立 Chromium profile；后者
连接专用 Windows Chrome。将本机 CDP URL 留在私有 passport.env 的
`CHROME_CDP_URL`，不要写进公共 runtime。地址和 WSL gateway 会随重启变化。
Windows 启动与 WSL bridge 脚本见 `job_bot/scripts/windows/` 和 job_bot README。

```bash
applybot browser-health
applybot session-audit
applybot login-tabs
applybot login-preflight --campaign-id CAMPAIGN_ID
```

这些命令依据 actual portals 检查会话；CAMPAIGN_ID 需替换成已有整数。
登录页存在并不等于已登录，CDP health 通过也不代表 Playwright attach 一定
成功。用户完成登录 / MFA / CAPTCHA 后重新检查。不要导出密码管理器、cookie
或浏览器 storage。CDP 仅允许受限本机 / WSL 通道。

`portals.json` 是当前实际 adapter 清单。Workday、iCIMS、Oracle、Moka、
Eightfold 等受支持机制仍需要逐公司字段验证；不能把一家公司的枚举和 consent
直接用于另一 tenant。不存在适配器时给出计划和现场观察，不声称自动填表成功。

## 8. 简历、证据、PDF 与职位专用材料

1. 在私有 profile / evidence / keywords 中录入真实事实，运行 private-check。
2. 从注释的 resume_template.tex 学习格式，填写私有 current.tex；选择语言与
   日期，保持 Summary / Technical Skills 标记供 tailoring 使用。
3. `make current` 编译；`make visa` 需另有私有 visa.tex。
4. 逐页查看最终 PDF、可读文字、日期、页面长度、语言和附件身份，再允许上传。

```bash
applybot keywords --role "Software Engineer"
cvbot --job-url OFFICIAL_JOB_URL
cvbot --job-url OFFICIAL_JOB_URL --generate-bundle
```

URL 必须已收录，替换为实际官方 URL。也可以传 `--job-description FILE`、
`--company`、`--role`、`--profile`、`--database` 和 `--resume-source`。
bundle 含 resume / cover_letter PDF、review_packet、keywords 和 manifest。
编译成功不等于内容审批；manifest 仍待 rendered review。生成器默认定位硬件，
CS 示例的 cover-letter 需要人工检查并修订硬件定位文字。

## 9. 申请队列、批次、填表与提交记录

先核实 JD、资格、岗位开放状态、已提交记录和 `application_limits`。
确认实际整数 ID 后按顺序执行：

```bash
applybot queue --job-id JOB_ID
applybot list
applybot prepare-profile --application-id APPLICATION_ID \
  --resume REVIEWED_RESUME_PDF --cover-letter REVIEWED_COVER_LETTER_PDF
applybot dispatch --application-id APPLICATION_ID
applybot dispatch --application-id APPLICATION_ID --execute
```

dispatch 默认写计划；`--execute` 才运行支持的 adapter。它可以填写已映射事实、
上传已审阅文档并保留截图，到人工审阅点停止。保存 server draft 还需要私有
`allow_server_draft` 与相应 command 选项。国籍、工作资格、薪酬、声明和隐私
consent 来自本次候选人确认；未知答案停止，不根据 NYU 地址推断。

批量创建可用 `python3 application_bot/batch_campaign.py --job-id JOB_ID \
--job-id SECOND_JOB_ID --name reviewed-batch`；这会创建本地或共享队列记录。
`--prepare-materials` 会生成文档并需要 TeX。检查 resulting campaign ID 后再做
login-preflight；批次创建不等于表单已填或申请已提交。

最终提交由用户在门户完成。只有官方收件证据或用户明确确认才记录 submitted：

```bash
python3 application_bot/mark_submitted.py APPLICATION_ID \
  --source user_confirmed_manual_submission
```

此命令更新数据库与审计事件，不点击 Submit。已上传、保存草稿、external-start
和到达 Review 都不能记为 submitted。不要重复申请既有 submitted。

tab-manager 默认审计；`--adopt` 认领能明确匹配的旧页；`--apply` 清理允许的
空页或干净重复页。保留填写中的表单、注册申请页和每个 tenant 的登录锚点。
target ID 与绝对附件路径属于本机；跨机操作前重新做 browser/login preflight。

## 10. 单机与共享数据库：安装、更新、查询

共享架构只有一份主库：主机上的 SQLite 引擎执行 SQL；客户端通过 SSH 发请求。
系统无需数据库监听端口。SSH client config 只在客户端；主机未配置时默认 local。

主机先按第 6 节初始化实际库，再执行：

```bash
jobbot-db prepare-host
jobbot-db prepare-host --apply
jobbot-db check
```

prepare-host 创建 Backup API 一致备份、检查完整性、启用 WAL。若主机已有
SSH 配置，先查清机器角色；不要令主机连回自己。两台安装同版本共享模块。

客户端使用实际已授权 SSH 别名，路径填主机的实际项目位置：

```bash
ssh -o BatchMode=yes example-db 'cd ~/workspace/sharkapoolu_beta_zh && .venv/bin/python -m job_bot.shared_database check'
jobbot-db configure --host example-db --project-dir '~/workspace/sharkapoolu_beta_zh'
jobbot-db configure --host example-db --project-dir '~/workspace/sharkapoolu_beta_zh' --apply
jobbot-db check
```

主机 private root 在外部时再给 configure 传 `--private-dir` 的主机实际绝对
位置。`--python` 默认 `.venv/bin/python`。example-db 需在本机 SSH config 定义，
首次 host-key / 密钥 / Tailscale 认证需按已有访问流程完成。
`database_connection_template.json` 逐项注释所有设置；真实配置权限为 600。

切换前备份旧库并按 URL、外部 ID、申请关联与状态核对两端独有数据；先处理
独有更新，再切客户端模式。不要把整个旧库覆盖到主机。正确配置的客户端
继续运行正常扫描 / scoring / queue 等命令，写入直接落在主库。双方提交后
另一台的新查询可见；已开启读事务需先结束再重查。同一浏览器任务由一台负责。

查询或临时 SQL 使用项目共享入口：

```python
from private_paths import JOB_DATABASE
from job_bot.shared_database import connect

conn = connect(JOB_DATABASE)
try:
    print(conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0])
    print(conn.execute("SELECT COUNT(*) FROM applications WHERE status=?", ('submitted',)).fetchone()[0])
    # 经业务审阅的写入应参数化，在 with conn: 内执行并检查影响行数。
    # 正常退出事务块提交；异常回滚。close() 仍必须显式执行。
finally:
    conn.close()
```

业务入口 `job_bot.bot.connect_db(config)` 还会维护 schema；仅需验收读取时用
shared connect，避免额外 schema 行为。SQLite 工具或原生 sqlite3.connect 在
客户端访问的是旧本地文件；生产访问必须经共享入口。私人 database/ 中相同
filename 映射到主机；其他目录的测试库保持本地。

验证两端 jobs / applications / submitted / 最新 job ID，然后一台执行
`jobbot-db watch --interval 1`，另一台进行原本计划的真实业务更新。commit
后应报告变化。没有真实写入时记录“只读通过，生产跨端写入未实测”。
不要为了验收向申请主库加入假记录。

## 11. 一致备份、恢复与断线

活动库使用 Backup API，不直接复制 SQLite / WAL 文件。下面的备份方式同时
支持主机和客户端，目标文件保存在发起机器的私有 backup 目录：

```python
import os
import sqlite3
from datetime import datetime, timezone
from private_paths import JOB_DATABASE, DATABASE_BACKUP_DIR
from job_bot.shared_database import connect

DATABASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
name = datetime.now(timezone.utc).strftime('snapshot_%Y%m%dT%H%M%S_%fZ.sqlite3')
path = DATABASE_BACKUP_DIR / name
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
source = connect(JOB_DATABASE)
target = sqlite3.connect(path)
try:
    source.backup(target)
    assert target.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
finally:
    target.close()
    source.close()
```

恢复前停止双方业务和未提交事务，确认目标是实际主机，保留当前库一致备份。
在单独位置检查待恢复 snapshot 的 integrity、关键 ID 和提交状态，再通过主机
SQLite Backup API 恢复或迁往新主机。复查 WAL、schema、config 和双端读取后
恢复业务。离线客户端旧快照不能充当最新生产备份。

SSH 中断会直接失败，不回退本地，不自动重试写入。commit 中断意味着结果
未知；重连查询实际主库，再判断是否重试。主机 / WSL / Tailscale 必须在线；
Windows 休眠使共享访问不可用。无离线写入 / 自动合并机制。连接闲置约 15 分钟
会关闭并回滚未提交事务。协议消息上限 16 MiB，结果分页，批量写用 executemany。

## 12. 邮件、校园渠道与定时运行

SMTP 用私有 runtime overlay 设置 smtp_host、smtp_port、starttls、from、
to_env；在 passport.env 中填 SMTP_USERNAME、SMTP_PASSWORD 和 JOBBOT_EMAIL_TO。
保持 dry_run=true 先生成文件并审阅。明确需要发送时在私有 overlay 改为 false，
随后 `jobbot digest --config PRIVATE_CONFIG --env-file PRIVATE_ENV` 才执行该设置。
SMTP token 或 app password 必须只留在私有 credentials。

校园渠道命令是 `python3 job_bot/penn_channels.py --config PRIVATE_CONFIG`；
`--open-login-pages` 会打开 / 保留缺少登录的页面。启用渠道必须有该机构的实际
访问权限；报告中的活动、资源链接、工资估算与已确认在招岗位要分别审阅。

Linux cron 或 Synology Task Scheduler 可调用 clone 的 `.venv/bin/python`
和 `job_bot/bot.py run --config PRIVATE_CONFIG --env-file PRIVATE_ENV`。
调度器必须设置正确工作目录、JOBBOT_PRIVATE_DIR、SSH 密钥访问和私有日志位置，
先手工跑同样命令。避免两台定时扫描重叠；共享客户端休眠 / 断网不能离线补写。
Docker、外部简历匹配服务和新的数据库平台是扩展方案，仓库没有自动部署它们。

## 13. 更新程序、schema、排错与发布

已安装 clone 更新：先查看 status，处理自己的改动，再 `git pull --ff-only`、
重新 bootstrap、private-check 与共享 check。两台代码保持共享协议 / schema
兼容；database_template.sql 是注释参考，实际迁移使用当前 ensure_schema。

| 现象 | 检查与处理 |
| --- | --- |
| local check 成功但两端数据不同 | 查 connection mode、host 和解析后的数据库目录；local 成功不证明共享 |
| SSH 失败 | 验证 host key、已有 SSH 密钥、主机在线、project_dir、Python 与 private_dir；保留 SSH 模式 |
| template 资料仍出现在输出 | 停止真实投递，替换本地全部虚构字段并重新生成材料 |
| 简历编译失败 | 检查 TeX / 字体 / 引擎 / Summary markers；查看私有 build log |
| 浏览器可连但页面不可操作 | 检查 attach、登录、portal adapter、MFA 和 tab 归属 |
| no such table / column | 查两台代码版本和 canonical schema；勿临时删表或新建另一份生产库 |
| 配额或 submitted 冲突 | 核对正式招聘规则、现有记录和审计事件；暂停重复浏览器动作 |
| Markdown 没跟着更新 | 重跑对应 report command；数据库与文件输出分别维护 |

发布之前执行 `make release-check`、`make history-audit`、可用时
`make private-check`、`git diff --check`，审阅精确 staged 文件和历史。
真实库、PDF、passport、host config、私有报告和浏览器状态都留在 ignored root。
公开模板只含空白或明确虚构内容；不得从真实数据库导出模板。

每次工作结束把实际变更、ID、检查结果、未决事实与下一步写入本地私有报告，
只在用户要求时发送或发布。公开本文记录操作契约，不记录私人申请进度。
