# 仓库操作契约

本文适用于整个仓库，是 coding agent 和贡献者的工作契约。更改代码、配置、测试、浏览器行为或发布文件之前必须先阅读。更多说明见[`docs/architecture.md`](docs/architecture.md)、[`docs/interfaces.md`](docs/interfaces.md)、[`docs/installation.md`](docs/installation.md)和[`SECURITY.md`](SECURITY.md)。

每次新对话还须阅读[`AGENT_HANDOFF.md`](AGENT_HANDOFF.md)，其中包含完整的安装、操作、申请及共享数据库指南。公开模板及其填写说明索引见[`examples/README.md`](examples/README.md)。操作现有安装前，先检查真实的本地私有配置和机器角色。

## 使命与边界

Sharkapoolu 是一个本地优先工具集，可用于：

1. 采集并规范化职位信息；
2. 按可配置策略为岗位评分和排序；
3. 根据本地 SQLite 数据库生成每日与每周报告；
4. 准备有证据支持的简历和求职信材料；
5. 辅助填写申请表单，直到人工审阅节点为止。

公开仓库包含可复用代码、脱敏示例、Schema 和共享策略。候选人身份、凭据、浏览器状态、申请答案、简历、数据库、截图及生成报告属于私有运行数据。最终申请提交必须由人工完成，不属于自动化边界。

## 安全不变量

以下规则是强制要求：

- 真实候选人的任何具体值都不得进入公开代码、示例、夹具、文档、日志、错误信息、Git 历史或委派提示。测试使用 `Example Company` 等合成值。
- 私有材料只能放在 `private_paths.py` 解析出的根目录下。功能模块不得另行拼接私有路径。
- 绝不打印、复制、导出或记录密码、cookie、token、浏览器存储、密码管理器值或完整私有档案字段。
- 简历和申请声明必须由证据档案支持。不得从职位描述推断候选人的熟练程度、年限、GPA、身份、性格评价或其他事实。
- 法律授权、签证赞助、移民、人口统计、同意、声明、薪酬及特定地点相关答案，都必须由候选人明确提供。缺少确认或答案冲突时，保留 `null` 或未解决状态。
- `application_browser.auto_submit`、`field_mappings.safety.allow_submit` 和 `application_profile.safety.allow_submit` 必须保持 `false`。任何适配器都不得激活最终 Submit 控件。
- 服务端草稿保存同时需要明确的本地安全标记和明确的命令选项。草稿授权不代表授权最终提交，也不代表授权其他 campaign。
- CAPTCHA、MFA、Windows Hello、账户恢复、政策审阅以及含义不明确的必答问题都必须由人工处理。不得绕过或自动化处理。
- 浏览器自动化须使用专用浏览器 profile。绝不将 CDP 调试端点暴露给 LAN、复用日常个人浏览器 profile 或导出其认证状态。
- 清理浏览器标签时，必须保留已填写表单、登记的申请标签页和已认证的租户锚点。允许关闭标签前，先保存配置的审计材料。
- 网络采集器必须限制超时、分页、并发和重试。不得规避访问控制，也不得将反机器人失败当成成功的空快照。
- 只有在完整的成功来源快照足以支持 `sync_active`，且生命周期保护器允许时，才可将缺失职位标记为不活跃。
- 持久化输出必须保存在私有根目录下。公开测试使用确定性的合成夹具，不得需要网络或真实账户。
- 除非当前任务明确授权，否则不得 push、发布、部署、提交申请、发送邮件或更改外部账户。

如果请求与不变量冲突，应遵守不变量并说明冲突。功能请求并不暗含公开私有数据或削弱提交保护的许可。

## 仓库地图

| 路径 | 职责 |
| --- | --- |
| `job_bot/` | 来源采集、规范化、SQLite 存储、评分、策略、报告、工作流运行器及兼容申请代码 |
| `job_bot/sources/` | 可复用的公开/API、HTML/feed、校园及第三方来源适配器 |
| `job_bot/applications/` | 共享或兼容 ATS 申请机制 |
| `job_bot/config/` | 规范的组合式公开配置 |
| `job_bot/source_lists/` | 公开来源理由与设置说明 |
| `application_bot/` | 会话检查、门户路由、标签管理、材料及人工审阅辅助表单适配器 |
| `cv/` | 基于证据的关键词选择、简历/求职信套件逻辑和共享 LaTeX 资源 |
| `examples/` | 复制到被忽略私有目录的脱敏模板 |
| `schemas/` | 候选人维护的私有 JSON 文件 Schema |
| `scripts/` | bootstrap、隐私/发布审计、快照导出和本地辅助脚本 |
| `docs/` | 架构、接口、安装、隐私及发布指南 |
| `private_paths.py` | 代码访问候选人自有存储的唯一规范映射 |
| `private_data/` | 被忽略的本地数据；仅允许跟踪 `private_data/README.md` |
| `Makefile` | 稳定的开发、工作流、简历、审计和发布命令 |
| `pyproject.toml` | 软件包元数据、Python 要求、可选依赖和 CLI 入口 |

迁移调用方期间可保留兼容入口。删除旧命令或包装器前，应检查其测试、README 示例、工作流定义和下游导入。

## 公开配置

`job_bot/config/jobbot.json` 是规范的公开组合配置根，按以下顺序包含文件：

| 文件 | 负责内容 |
| --- | --- |
| `runtime.json` | 数据库路径；扫描并发、重试和生命周期保护；浏览器传输；标签策略；摘要筛选；报告时区；邮件投递设置 |
| `workflows.json` | 具名模块序列、来源选择器、失败策略、工作进程覆盖及实验 manifest 行为 |
| `sources.json` | 来源端点/标识、适配器类型、公司、类别、分页、岗位类型、筛选、活跃同步策略和启用状态 |
| `scoring.json` | 宽口径职位相关性：评分算法、区间、基础类别、证据修正项和关键词组 |
| `strategy.json` | 窄口径 campaign 资格和优先级：轨道、地理位置、毕业/学位/资历门槛、阈值、模式和排序修正项 |
| `portals.json` | ATS 识别、会话审计探测、公司门户档案、适配器路由、超时、环境文件选项和草稿能力 |
| `penn_channels.json` | 公开校园渠道定义及校园就业报告规则 |
| `field_mappings.json` | 逻辑表单字段映射、文档映射、地区答案策略及禁止提交的安全策略 |

`job_bot/config.china_hk_ic_foreign.json` 是规范组合配置的兼容包装器。`job_bot/config.example.json` 和 `job_bot/config.example.toml` 展示较小的独立配置示例，不是权威生产结构。`job_bot/env.template` 只包含名称和空占位符。

公开配置可包含公开端点、招聘网站标识、正则表达式、排序策略、通用环境变量名称和安全默认值。不得包含候选人联系方式、私有机构 URL、凭据、浏览器导出、申请答案或机器专属绝对路径。

配置组合的执行顺序固定：

1. 相对于声明文件解析 `includes`。
2. 按列出顺序合并 includes。
3. 递归合并映射；后出现的标量和列表替换前值。
4. 最后应用当前包含文件自身作为覆盖。
5. 随后应用 `patches`。

修改一个具名列表项时使用 patch。每个 patch 必须恰好匹配一个项目。需要有意更改列表时使用 `$append`、`$remove` 或 `$replace`。不得复制整份 source 或 scoring 文件来调整单个值。用以下命令验证所有公开或私有覆盖：

```bash
jobbot-config --config path/to/config.json
```

评分和策略负责不同决策。评分判断职位是否广义相关，并写入 `jobs.fit_score`。策略在队列排序前应用候选人和 campaign 资格。评分关键词不能暗中绕过策略排除项。

## 私有配置与数据

首次运行私有数据命令前，可设置 `JOBBOT_PRIVATE_DIR`，将整个私有目录树重定位到加密或单独备份的存储。否则默认位置是 clone 内的 `private_data/`。所有代码都必须从 `private_paths.py` 导入路径；新增私有材料类别时，在其中加入新的规范常量。

受维护的私有输入包括：

| 私有根目录下的路径 | 用途 | 公开契约 |
| --- | --- | --- |
| `credentials/passport.env` | 可选凭据、会话请求头、storage-state 路径、CDP URL 和邮件环境变量 | 从 `job_bot/env.template` 开始；读取时不打印；文件权限为 `600` |
| `profiles/application_profile.json` | 身份字段、文档、教育、经历、项目、技能、自定义答案、明确授权、职业偏好和安全门 | 模板：`examples/application_profile.json`；Schema：`schemas/application-profile.schema.json` |
| `cv/profile/evidence_profile.json` | 已核实身份关联、毕业事实、摘要、GPA 映射及声明证据组 | 模板：`examples/evidence_profile.json`；Schema：`schemas/evidence-profile.schema.json` |
| `cv/profile/application_keywords.json` | 有证据范围的技术和协作标签、岗位预设及来源 | 模板：`examples/application_keywords.json`；Schema：`schemas/application-keywords.schema.json` |
| `config/job_bot.local.json` | 机器路径、来源启用状态、浏览器选择、报告路由和其他非机密本地覆盖 | 从 `examples/job_bot.local.json` 开始；密钥留在 `passport.env` |
| `config/easy_settings.json` | 入门模式、模块、浏览器、审阅轮数和地区控制 | 模板：`examples/easy_settings_template.json`；Schema：`schemas/easy-settings.schema.json`；选择启用的 `jobbot-settings` 入口 |
| `cv/intake/<new-directory>/` | 原始简历、可编辑转录稿及带来源链接的事实台账 | `cvbot import-resume`；`schemas/resume-fact-ledger.schema.json`；`docs/candidate-onboarding.md` |
| `database/manual/jobs.csv`、`applications.csv` | 候选人维护的手工记录 | CSV 模板和 `docs/manual-database.md`；显式事务导入前先预览 |

其他私有状态包括：

- `database/`：SQLite 职位、扫描、申请和标签状态；
- `browser/state/` 与 `browser/profiles/`：已认证浏览器材料；
- `outputs/job_bot/` 与 `outputs/application_bot/`：报告和材料；
- `cv/source/`、`cv/build/`、`cv/variants/` 与 `cv/reports/`：源文件和生成的候选人文档。

创建缺少的模板时不得替换已有文件，并应验证：

```bash
make private-init
make private-check
jobbot-private paths
```

除非任务明确要求替换本地候选人文件且已有备份，否则不要使用 `jobbot-private init --force`。目录权限应为 `700`；凭据和受维护的档案文件权限应为 `600`。验证和诊断可报告路径、键名、数量及状态，但不能报告候选人具体值。

在原生 Windows 上，POSIX 模式位无法验证私有文件 ACL；验证器会报告此限制。请使用仅所有者可访问的 NTFS ACL，并在本机检查；结构检查不代表隐私已审计或申请已就绪。

## 全新 clone 安装

新 Linux 或 WSL 机器应先安装 Git、Python 3.10 或更新版本及 Python `venv` 包。Ubuntu/Debian 上通常这样补装 venv：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

然后按照仓库的实际访问权限克隆中文发布仓库，再安装：

```bash
git clone https://github.com/SimonTang02/sharkapoolu_zh.git
cd sharkapoolu_zh
./scripts/bootstrap.sh
source .venv/bin/activate
```

访问条件取决于仓库设置，不能假定可匿名 clone。功能开发请在[英文主开发仓库](https://github.com/SimonTang02/sharkapoolu_beta)进行；本仓库负责翻译兼容维护。

任务需要 Playwright 时运行 `./scripts/bootstrap.sh --with-browser`。Linux 上若缺少系统库，按脚本打印的管理员命令安装。无桌面服务器执行核心采集、评分、报告和测试不需要 TeX 或浏览器软件包。仅在本机渲染 PDF 时安装 TeX 发行版。

私有数据要放在 clone 之外时，应在 bootstrap 前设置 `JOBBOT_PRIVATE_DIR`。只从加密且另有授权的备份恢复该目录；公开代码 clone 不含候选人数据。恢复后，运行任何采集、简历或申请命令前先执行 `jobbot-private check`。跨机器复制私有数据是另一项敏感操作，clone 或更新公开仓库的许可不包含此操作。

更新仓库时使用 `git pull --ff-only`，再运行同一 bootstrap 脚本。绝不能通过删除或覆盖私有根目录解决更新冲突。

## 命令与副作用

在仓库根目录运行命令。bootstrap 后优先使用已安装入口；开发期间也可直接调用 `python3` 入口。

### 安装与验证

```bash
./scripts/bootstrap.sh                 # 核心 editable 安装、模板、验证与测试
./scripts/bootstrap.sh --with-browser  # 另安装 Playwright 和 Chromium
source .venv/bin/activate
make private-check
make config-check
make test
```

`bootstrap.sh` 可重复运行，并保留现有私有文件。只有在当前版本的测试已通过且只需快速修复时，才使用 `--skip-tests`。

### 职位发现与报告

| 命令 | 效果 |
| --- | --- |
| `jobbot init --config <config>` | 初始化私有 SQLite Schema |
| `jobbot scan --config <config>` | 获取选定来源并更新数据库 |
| `jobbot rescore --config <config>` | 重新计算已存职位的广义相关分数 |
| `jobbot auth-status --config <config> --env-file <private-env>` | 报告已配置会话覆盖情况，不显示具体值 |
| `jobbot digest --config <config> --print` | 根据已存职位生成摘要 |
| `jobbot run --config <config>` | 扫描后生成/发送已配置摘要 |
| `make daily` | 运行完整每日流程 |
| `make weekly` | 根据 SQLite 重建周报 |

扫描时应通过精确来源名称、公司、来源类别、来源类型或浏览器传输限制范围，而不是全局停用无关来源。HTTP 扫描和基于 CDP 的扫描前提不同；其中一种失败时不得静默切换传输方式。

具名工作流提供可复现的模块接口：

```bash
make workflow-plan WORKFLOW=http_refresh
make workflow WORKFLOW=http_refresh
python3 job_bot/run_modules.py --module weekly --config <config>
```

新建或大幅修改工作流前始终先预览。运行 manifest 是私有输出，包含 effective-config 哈希、选定模块、命令、耗时和返回码，绝不能包含凭据值。

### 简历与申请准备

```bash
cvbot --job-description <synthetic-or-private-file> \
  --company <company> --role <role>
applybot list
applybot keywords --role <role>
applybot session-audit
applybot login-preflight --campaign-id <id>
applybot dispatch --application-id <id>       # 仅生成计划
applybot dispatch --application-id <id> --execute
```

`dispatch` 默认只生成计划。浏览器执行时可打开或复用专用标签页、填写获准字段、上传明确选定的已审阅文档并写入私有材料。遇到未解决问题时必须停止，且必须在最终提交前停止。打开大量申请标签之前先做登录/会话预检。

用 `prepare-profile` 将已审阅文档绑定到一份申请。允许上传前，先检查渲染 PDF 和其 manifest；仅仅编译成功不等于已审阅。简历入口放在私有根目录下，并使用 `cv/latex/` 中的共享类/样式：

```bash
make check-tools
make current
make visa
```

应优先执行只验证、生成计划、评分或报告的命令，再运行访问网络、打开浏览器、保存服务端草稿、发送邮件或更改申请状态的命令。

## 代码接口与扩展点

### 配置与路径

- 使用 `job_bot.config_loader.load_composed_config` 或既有的 `job_bot.bot.load_config` 包装器加载组合 JSON，随后调用 `validate_config`。不要实现第二套合并算法。
- 只能通过 `private_paths.py` 定位候选人自有文件。
- 保持可选集成真正可选。导入软件包和运行核心测试不得要求网络、浏览器、私有数据或专有 SDK。
- 能远程写入、打开大量标签或更改申请状态的新 CLI 操作，必须先有计划/dry-run 形式；必需操作失败时返回非零状态。

### 来源适配器

- 在 `job_bot/config/sources.json` 声明每个来源；将其 `type` 路由到 `job_bot` 或 `job_bot/sources/` 中的采集器。
- 返回稳定 URL 和标题。若可获取，也包括公司、地点、描述、发布日期、来源名称和 `role_kind`。
- 容忍缺少可选字段，限制分页，应用配置筛选；来源失败时如实记录，不得伪造职位。
- 根据实际需要，为解析、分页、规范化、筛选、缺少认证和错误行为添加确定性夹具测试。
- 环境变量名称可预留未来认证支持；它出现在配置中并不表示适配器已经存在。

预留来源接口包括新增 `sources[].type` 值、公司或平台凭据命名空间、公开 API/HTML/feed/CDP 传输、来源类别、工作流选择器和通知接收端。

### 申请适配器

- 在 `job_bot/config/portals.json` 注册通用 ATS 路由和公司专用行为。ATS 机制可共享；字段选项、同意、必填部分和限制属于公司档案。
- 使用共享门户注册表、dispatcher、标签注册表/管理器及材料辅助工具。恢复申请时，应基于持久化申请身份和指纹定位；CDP target ID 只是临时值。
- 探测登录状态时不记录字段或 cookie 值。只管理自动化创建的标签，并关闭临时探测标签。
- 公司的首份申请档案必须作为学习模板的试运行并交由人工审查。不得将同一 ATS 上其他租户的问题泛化到该雇主。
- 添加模拟且确定性的测试，覆盖路由、未解决问题、安全门、标签保留和材料。测试必须证明不会调用最终提交控件。

预留适配器钩子包括会话探测、草稿支持、环境文件选项、门户专用超时、通用主机后缀路由、其他隔离浏览器传输和新 ATS 实现。

### 候选人档案与模型辅助功能

档案消费者应接受新增的可选字段，拒绝不兼容类型，并在关联证据或关键词时使用稳定 ID。更改受维护档案契约时，应同时更新示例、Schema、语义验证器、文档和测试。

可选模型服务可以提出排序或措辞更改建议，但本地证据档案和验证器仍是权威。生成文字不能创建新的候选人事实。若提示或输出含职位描述或候选人材料，应私下存储。

## 开发流程

1. 编辑前检查 `git status` 及相关文档/配置/测试。保留无关用户修改，避免顺手重排大段内容。
2. 确认更改是否影响公开策略、私有契约、网络采集、浏览器/申请状态或发布安全。
3. 优先使用现有的最小接口。仅当现有扩展点无法表达行为时才新增抽象。
4. 更改配置时，验证组合结果并检查生效的来源/工作流摘要。工作流先运行计划形式。
5. 迭代时运行有针对性的确定性测试，随后执行下面要求的发布检查。
6. 提交前检查可发布文件清单。绝不暂存私有或生成材料。

使用兼容 Python 3.10 的语法；除非依赖确有明确价值，否则使用标准库。运行时依赖及可选依赖组写入 `pyproject.toml`；如有需要，也更新 bootstrap 和 CI 行为。优先采用 `pathlib.Path`、显式超时、稳定标识、结构化 JSON、原子化私有输出写入和不泄漏值的可操作错误。

## 测试与发布标准

测试使用 `unittest` 发现机制：

```bash
python3 -m unittest discover -s . -p 'test_*.py'
make test
```

每项影响解析、选择、评分、配置组合/验证、档案验证、路由、安全门、生命周期变化或输出契约的行为更改都要有针对性测试。避免只重复实现细节的测试。单元测试须使用合成本地夹具并模拟网络/浏览器边界。

创建 pull request、公开提交、标签或快照前运行：

```bash
make release-check
make private-check        # 本机有私有目录时
git diff --check
git status --short
git ls-files --cached --others --exclude-standard
```

`make release-check` 会运行可发布目录隐私审计、全部单元测试和共享配置验证。CI 必须能在没有密钥和候选人私有文件的全新 clone 中运行。如果仓库包含较早提交，或可能曾跟踪敏感材料，也运行：

```bash
make history-audit
```

删除当前树中的文件并不能从 Git 历史移除它。任何凭据暴露后应先轮换，再按[`docs/GITHUB_RELEASE.md`](docs/GITHUB_RELEASE.md)中审阅过的干净快照或历史重写流程处理。不能因为当前树审计通过就发布含有风险历史的仓库。

公开审计必须继续拒绝私有目录内容、类似凭据的文件名、数据库、渲染 PDF、压缩包、浏览器状态、私钥、已知 token 形状、本机绝对路径、符号链接、异常大文件和候选人标记。不得为通过发布审计而削弱审计规则；应移动或脱敏违规数据，并在适当时添加回归测试。
