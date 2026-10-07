# Job Bot

在 clone 中，`private_data/database/` 等相对默认路径会随 `JOBBOT_PRIVATE_DIR` 一起重定位；配置中的显式绝对 `database.path` 仍需按配置规则单独修改。

本目录包含可在 Synology 上使用的职位采集器、评分器、数据库和日报生成器。稳定的申请准备入口现为 `application_bot/cli.py`；为保持向后兼容，实现暂时仍保留在此处。

Penn 校园工作、研究、活动及其他职业平台由独立的[渠道读取器和登录队列](source_lists/upenn_channels.md)处理。运行 `make workflow WORKFLOW=penn_channels_refresh`，或运行 `python3 job_bot/penn_channels.py --open-login-pages` 保留待登录页面。日报/周报会链接到独立状态报告。

预期工作流程：

1. 从获准来源采集职位。
2. 将职位保存到本地数据库。
3. 根据简历档案为岗位评分。
4. 生成简历定制建议和求职信要点。
5. 任何申请提交前都必须由人工批准。

在明确批准每个平台账户、权限模型和申请规则之前，应保持自动提交关闭。

## Workday 半自动申请

Workday 适配器可将已存职位加入队列，在隔离 Chromium profile 中打开职位，设置本地 NVIDIA 会话 cookie，填写明确映射的联系方式，上传简历，并生成截图和字段报告。它绝不会点击最终 Submit 控件。

申请准备有两种独立浏览器通道，只能通过 JSON 配置中的 `application_browser.mode` 选择：

- `local_persistent`：原有流程，在 WSL 中启动隔离 Chromium profile。
- `windows_cdp`：通过仅供 WSL 使用的 portproxy 端点（当前端口 `9223`）连接 Windows 专用 `JobApplyChrome` profile。它只创建并关闭自己管理的标签，保持 Windows Chrome 运行，复用该 profile 已认证的会话；不会注入旧式 Cookie 请求头，也不会将 Windows 浏览器状态导出到 WSL。

配置示例：

```json
"application_browser": {
  "mode": "windows_cdp",
  "windows_cdp": {
    "url_env": "CHROME_CDP_URL",
    "url": ""
  }
}
```

切回 `local_persistent` 可使用原有流程。端点可通过 `passport.env` 中的 `CHROME_CDP_URL` 设置，且仅选择 `windows_cdp` 时读取。`--browser-mode` 和 `--cdp-url` 是临时 CLI 覆盖。如果 WSL NAT 地址变化，应更新 portproxy/防火墙规则和端点，不必更改浏览器模式。

从 Windows PowerShell 启动专用 Chrome：

```powershell
& .\job_bot\scripts\windows\start-job-chrome.ps1
Invoke-RestMethod http://127.0.0.1:9222/json/version
```

对于 NAT 模式 WSL，替换为当前地址后，在管理员 PowerShell 中配置独立的 `9223 -> 9222` 转发：

```powershell
& .\job_bot\scripts\windows\configure-wsl-cdp-portproxy.ps1 `
  -WslGatewayAddress <wsl-gateway-ip> `
  -WslAddress <wsl-ip>
```

Chrome 进程仍只监听 Windows `127.0.0.1:9222`；端口 `9223` 只绑定到 WSL 虚拟网关，防火墙规则只允许当前 WSL 地址。不要将 Chrome 绑定到 `0.0.0.0`、使用日常 Chrome profile 或添加 `--remote-allow-origins=*`。

在 WSL 检查配置连接，然后运行安全集成测试；该测试只打开并关闭一个由自动化管理的 `example.com` 标签页：

```bash
./job_bot/scripts/check-chrome-cdp.sh
./job_bot/scripts/check-chrome-cdp.sh --smoke
```

使用配置中选定的浏览器模式运行通用 Workday dry-run 预览：

```bash
python3 application_bot/cli.py workday-preview --application-id 1
```

`nvidia-preview` 仍是向后兼容的别名。每个 Workday 租户可能需要单独登录；所有租户专属问题仍须人工审阅。

本项目刻意不提供显式提交命令：适配器会阻止最终 Submit 操作，并要求 `application_browser.auto_submit=false` 以及私有申请档案中的 `safety.allow_submit=false`。CAPTCHA、MFA、登录验证和未回答筛选问题会产生需人工处理的状态；Windows CDP 模式下会保留自动化标签供人工处理。

在项目内安装可选浏览器运行依赖：

```bash
python3 -m pip install --target .python_packages \
  -r job_bot/requirements-browser.txt
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
  PYTHONPATH=.python_packages python3 -m playwright install chromium --no-shell
```

创建被忽略的档案及相关私有文件，然后只填写真实且稳定的答案：

```bash
python3 -m job_bot.private_config init
python3 -m job_bot.private_config check
```

申请档案保存在 `private_data/profiles/application_profile.json`。法律授权、签证赞助、人口统计和声明类答案绝不通过推断得出。

将 SQLite 中已有职位加入队列并预览：

```bash
python3 job_bot/application_bot.py queue --job-url 'NVIDIA_WORKDAY_URL'
python3 job_bot/application_bot.py list
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
  python3 job_bot/application_bot.py nvidia-preview \
  --application-id 1 --start-application --interactive
```

本地档案/状态路径、截图、字段清单和申请事件会保存下来供续接使用。NVIDIA Workday 在 Save/Continue 步骤后可能保留资料；除非本地档案中的 `safety.allow_server_draft=true` 且提供 `--save-draft`，否则该服务端操作会关闭。即便同时满足，最终提交仍关闭。

恢复已有 NVIDIA 草稿时，根据本地档案填写结构化 `education` 和 `skills` 条目，从 My Experience 前进至 Application Questions 检查点：

```bash
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
  python3 job_bot/application_bot.py nvidia-preview \
  --application-id 1 --start-application --save-draft \
  --advance-to-review --headless
```

Workday 层级提示可用列表表示，例如 `["University", "Example University"]`。遇到必答的法律、移民、声明或其他未答问题时工作流会停止。它绝不推断答案或点击最终 Submit。

本地申请档案、浏览器 profile 和 storage-state 文件含有个人或认证数据，并由 Git 忽略。将项目复制到 Synology 时，文件权限设为 `600`，目录权限设为 `700`。

## 当前功能

- 监控已配置的职位来源。
- 将职位存入本地 SQLite。
- 按职位 URL 去重。
- 根据硬件/数字设计关键词为职位评分。
- 生成每日摘要。
- 默认将摘要写入 `private_data/outputs/job_bot/`。
- `dry_run` 设为 `false` 时，可通过 SMTP 发送摘要。

支持的来源类型：

- `greenhouse`：公司 Greenhouse 招聘看板。
- `lever`：公司 Lever 招聘看板。
- `rss`：RSS/Atom feed。
- `html`：简单招聘页面；提取链接并按 regex 筛选。
- `icims`：公开 iCIMS 结果卡片，支持分页、标题、地点和摘要。
- `attrax`：公开 Attrax 职位卡片，支持官方选项筛选和分页。
- `workday`：Workday 公开招聘搜索端点；编号式多地点摘要会通过公开职位详情端点展开。
- `mediatek`：MediaTek 公开结构化职位端点，支持完整分页。
- `xiaomi`：Xiaomi 公开结构化搜索 API，包含官方发布日期及社招/校招/实习类型映射。
- `moka_cdp`：在 Windows 专用 Chrome 中采集 Moka 职位卡片，适用于 Cambricon、Biren 和 DJI 等门户。
- `jobsdb_hk`：JobsDB HK 结构化卡片；`fetch_via_cdp=true` 时可通过已认证的 Windows 专用 Chrome 采集。
- `zhipin`：BOSS 直聘适配器；由于预计登录/CAPTCHA 阻碍较多，共享配置中默认禁用。
- `shixiseng`：通过专用 Chrome 读取实习僧实习职位卡片；从每个公开详情页读取标准标题和描述元数据。
- `cuhk_careers`：CUHK CPDC / CU Careers 结构化搜索适配器，使用导出的浏览器会话。
- `handshake`：通过现有 PennKey Chrome 登录读取 Penn 学生职位门户，执行有边界的只读搜索。这是浏览器集成，不是仅供机构使用的 EDU API。见[Penn 设置与验证状态](source_lists/upenn_handshake.md)。

每条已存职位均有 `role_kind`：

- `internship`
- `full_time`
- `unknown`

除非来源强制指定岗位类型，否则根据标题/描述/地点推断该标签。

## 快速开始

从项目根目录运行：

```bash
python3 job_bot/bot.py init --config job_bot/config.example.json
python3 job_bot/bot.py digest --config job_bot/config.example.json --print
```

如需扫描真实来源，将 `config.example.json` 复制为 `config.local.json`，替换示例来源项后运行：

```bash
python3 job_bot/bot.py run --config job_bot/config.local.json
```

如需明确重试一个或多个来源，使用确切配置名称重复传入 `--source`。扫描器会在每个来源开始前和结束后打印一行状态：

```bash
python3 job_bot/bot.py scan --config job_bot/config.local.json \
  --source "NVIDIA Greater China Hardware" \
  --source "Cambricon Campus Careers"
```

如果 `config.local.json` 包含私有邮箱或来源详情，则不得提交。

中国大陆/香港 IC 设计目标清单可从以下配置开始：

```bash
python3 job_bot/bot.py init --config job_bot/config.china_hk_ic_foreign.json
python3 job_bot/bot.py run --config job_bot/config.china_hk_ic_foreign.json --env-file private_data/credentials/passport.env
```

来源选择理由见 `job_bot/source_lists/china_hk_ic_foreign_companies.md`。

调整来源时可用的配置字段：

- `role_kinds`：只保留该来源中的 internship/full-time/unknown 职位。
- `include_patterns`：所有 regex 组都必须在标题/地点/描述/URL 中匹配。可用一组限定地区，另一组限定 IC 岗位关键词。
- `exclude_patterns`：排除噪声岗位。
- `title_include_patterns`：职位标题至少需匹配一个高精度 regex。
- `title_exclude_patterns`：标题匹配任意列出的 regex 时拒绝该职位。
- 摘要级 `include_title_patterns`：低分职位须具备相关标题信号；`title_match_bypass_score` 会保留标题强匹配且描述包含足够已核实证据的通用岗位。
- `enabled: false`：来源保留在清单中，但扫描时跳过。
- `timeout_seconds`：覆盖每个请求的默认超时时间。

凭据/会话支持：

- 不得提交账户密码、cookie 或 token。
- 本地密钥放入被忽略的 `private_data/credentials/passport.env` 文件或 Synology Task Scheduler 环境变量。
- 每家公司/平台均可使用可选的 `*_USERNAME`、`*_PASSWORD`、`*_COOKIE` 和 `*_STORAGE_STATE` 变量。只添加有值的变量；缺失变量按空值处理。用户名/密码字段为未来交互式登录适配器预留，当前采集器不会读取。
- `*_COOKIE` 必须是且仅是对应域名的 Cookie **请求**头。机器人会拒绝常见 `Set-Cookie` 属性和明显的 Google/LinkedIn 跨域误用。
- `*_STORAGE_STATE` 是 Playwright storage-state JSON 文件路径。对于 OAuth、SSO、多域、localStorage、CAPTCHA 或 Apply with LinkedIn 等 2FA 流程，浏览器辅助适配器优先使用它；纯 HTTP 采集器只报告其是否配置，不会加载该文件。
- 招聘网站要求登录时，优先使用短期浏览器状态/cookie 导出，不要保存原始密码。
- 从精简版 `job_bot/env.template` 开始，它只为最常用的公司/平台保留空字段；只填写实际需要的本地值。
- 可运行 `python3 job_bot/migrate_passport.py --env-file private_data/credentials/passport.env` 迁移或精简现有文件，且不会打印密钥。该工具保留已有值和精选空字段，删除其他空占位符，并转换旧 `*_SESSION` 名称；旧名称仍可作为后备读取。
- 显式使用 `--env-file private_data/credentials/passport.env` 传入；已有进程环境变量优先。
- CUHK 来源调用门户结构化 `/job/search` API，绝不需要 CUHK 账户密码。
- 导出的 CUHK 会话过期时，只需刷新 `PLATFORM_CUHK_CAREERS_COOKIE`；失败扫描会列在摘要中。
- 公司来源自动使用 `COMPANY_<NORMALIZED_COMPANY>_COOKIE` 及对应用户名/密码/storage-state 变量。例如 `Arm` 对应 `COMPANY_ARM_COOKIE`，`Huawei / HiSilicon` 对应 `COMPANY_HUAWEI_HISILICON_COOKIE`。
- 当前平台适配器使用 `PLATFORM_JOBSDB_HK_COOKIE`、`PLATFORM_BOSS_ZHIPIN_COOKIE`、`PLATFORM_SHIXISENG_COOKIE` 和 `PLATFORM_CUHK_CAREERS_COOKIE`。
- 同一命名规则支持 LinkedIn、Handshake、Indeed、Glassdoor、Simplify、Wellfound、ZipRecruiter、RippleMatch、Liepin、51job、Zhaopin、Maimai、CTgoodjobs 和 cpjobs。存在凭据变量并不表示已实现对应采集器。
- 公开抓取可用时，将可选凭据留空。只有 cookie 值非空时才会附加。
- 失败的抓取会记录在 `scan_runs`；`digest.include_scan_errors=true` 时会出现在摘要中。

审计会话覆盖情况，不暴露任何密钥值：

```bash
python3 job_bot/bot.py auth-status \
  --config job_bot/config.china_hk_ic_foreign.json \
  --env-file private_data/credentials/passport.env
```

摘要中可用的字段：

- `include_scan_errors`：在每日邮件中包含失败来源。
- `max_items`：限制邮件长度，但职位仍全部保存在 SQLite 中。
- `min_score`：不在邮件中显示低分职位。
- `deduplicate_similar`：在邮件中合并公司/标题/地点相似的重复招聘条目。
- `exclude_title_patterns`：从邮件隐藏资深或其他不合适职位标题，但不从 SQLite 删除。

常规摘要包含新发现的活跃职位，并分为实习与全职两部分。来源提供官方发布日期时，24 小时筛选使用该日期；否则使用首次发现时间。使用 `--since` 指定上一轮基线/报告时间，排除已覆盖职位：

```bash
python3 job_bot/bot.py digest \
  --config job_bot/config.china_hk_ic_foreign.json \
  --env-file private_data/credentials/passport.env \
  --hours 24 --since '2026-08-28T00:26:23+08:00' --edition 2
```

如需为筛选和去重后的当前全部活跃职位生成编号基线：

```bash
python3 job_bot/bot.py digest \
  --config job_bot/config.china_hk_ic_foreign.json \
  --env-file private_data/credentials/passport.env \
  --all-active --edition 1
```

中国大陆/香港档案采用两级 Foundation 评分。Foundation 首先识别岗位本质类别；随后根据简历证据、早期职业信号、资历以及非设计/软件修正项调整分数。数字 RTL 设计和 CPU/计算机架构是优先方向；验证和 EDA 是强相关邻近方向；PD/DFT 和模拟岗位也可检索，但基础分较低。更改策略后，用以下命令刷新已存分数：

```bash
python3 job_bot/bot.py rescore --config job_bot/config.china_hk_ic_foreign.json
```

渲染当前 Foundation 定义、完整关键词列表、修正项、决策区间和当前高匹配职位：

```bash
python3 job_bot/scoring_report.py
```

### 可配置申请策略

申请短名单现已与普通日报摘要分开：

当前共享策略有三个审阅轨道：中国大陆/香港校园全职、美国应届全职和美国暑期实习。应在私有覆盖中为实际候选人配置招聘周期、学位、毕业窗口和目标方向。分数或审阅队列都不能证明具备资格。官方岗位要求和雇主申请数量限制仍需审查。

美国采集来源除现有全球 Intel/Lattice/Samsung/Skyworks/Cirrus/MaxLinear 外，还包括 NVIDIA、AMD、Qualcomm、Apple、ADI、Broadcom、Marvell、Cadence、TI、Etched 和 SpaceX 专用来源。使用可重复的 `--source` 参数单独扫描新来源，再渲染当前短名单：

```bash
python3 job_bot/bot.py scan \
  --config job_bot/config.china_hk_ic_foreign.json \
  --env-file private_data/credentials/passport.env \
  --source "NVIDIA United States 2027 Hardware Internships"
python3 job_bot/strategy_report.py
```

### 一键每日流程

推荐的每日入口会检查专用 Windows Chrome，并尽可能从 WSL 自动启动；并行扫描普通 HTTP 来源，串行扫描共享 CDP 来源，为已存职位重新评分，并生成配置的策略增量报告及简明人工干预报告：

```bash
python3 job_bot/daily_pipeline.py \
  --config job_bot/config.china_hk_ic_foreign.json \
  --env-file private_data/credentials/passport.env
```

基线存储在 `private_data/outputs/job_bot/daily_pipeline_state.json`，因此后续运行只报告上一轮后首次发现的职位。使用 `--since` 覆盖基线。如果 Chrome 或 WSL portproxy 不可用，流程会继续处理公开 HTTP 来源，并在干预报告中列出跳过的浏览器来源，而不会阻塞整轮运行。修复 portproxy/防火墙仍是需要管理员明确执行的操作。

遇到瞬时 408/429/5xx、超时、连接重置和 SSL-EOF 失败时，会进行有界指数退避重试。即使不同雇主并行运行，对同一雇主的请求仍会串行化，避免并行地区搜索互相触发限流。认证失败及解析器/页面布局变化不会盲目重试，而是列入干预报告。

需要人工审阅的情况仅包括登录过期、MFA/CAPTCHA、确有歧义的资格答案、门户布局变化和最终提交。

所有轨道使用相同的候选人确认学位日期。逐一核实实习岗位的在读及返校要求，不得为匹配岗位而更改毕业日期。策略报告不会提交申请。新用户应阅读完整[交接指南](../AGENT_HANDOFF.md)。

## 本地测试

仓库提供小型 RSS 夹具，可在不访问外网的情况下测试完整流程：

```bash
python3 job_bot/bot.py scan --config job_bot/test_fixtures/config.fixture.json
python3 job_bot/bot.py digest --config job_bot/test_fixtures/config.fixture.json --print
```

## 邮件

测试期间将 `email.dry_run` 保持为 `true`。机器人会写入摘要文本文件，而不是发送邮件。

如需启用 SMTP 邮件：

```json
"email": {
  "dry_run": false,
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "starttls": true,
  "username_env": "SMTP_USERNAME",
  "password_env": "SMTP_PASSWORD",
  "from": "your_email@example.com",
  "to": [],
  "to_env": "JOBBOT_EMAIL_TO"
}
```

然后在 Synology 任务中设置环境变量：

```bash
export SMTP_USERNAME="your_email@example.com"
export SMTP_PASSWORD="your_app_password"
python3 /volume1/path/to/sharkapoolu_zh/job_bot/bot.py run --config /volume1/path/to/sharkapoolu_zh/job_bot/config.local.json
```

使用 app password 或 SMTP token，不要使用邮箱主密码。

## 平台说明

从风险较低的来源开始：

- 提供公开职位的公司招聘页面。
- 手动导出或保存 LinkedIn/Handshake/Simplify/Greenhouse/Lever 链接。
- 转发到结构化收件箱的邮件提醒。

可考虑的数据库/平台集成：

- 使用本地 SQLite 进行私有测试。
- 连接插件后使用 Airtable 作为可视化跟踪器。
- 连接插件后使用 Google Drive 保存简历材料。

## Synology 部署计划

将项目复制到 Synology 服务器后，可使用 Task Scheduler：

- 用户自定义脚本。
- 每日运行，例如上午 8:30。
- 命令：`python3 /volume1/path/to/sharkapoolu_zh/job_bot/bot.py run --config /volume1/path/to/sharkapoolu_zh/job_bot/config.local.json`
- 前几天保持 `dry_run=true`，并检查 `private_data/outputs/job_bot/`。

其他部署选项：

- 简单的每日运行可用 Synology Task Scheduler。
- 用 Docker 容器固定 Python/运行时环境。
- 将 SQLite 备份到 Synology Drive。
