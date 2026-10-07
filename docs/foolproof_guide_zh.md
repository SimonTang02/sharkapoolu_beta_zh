# 第一次使用：从安装到准备30个岗位的材料

这份指南写给没有编程经验的人。你负责提供真实资料、确认答案、登录招聘网站和最终提交；Agent负责安装检查、整理资料、配置筛选、生成材料和解释表单。先做完一个岗位，再扩展到30个。

工具能帮你做四件事：按偏好找岗位；根据真实经历准备简历和求职信；提供逐岗答案和支持的填表辅助；记录你已经确认成功的投递。网站支持范围有限，不保证每个网站都能自动填写。

推荐组合是 **Windows + WSL2 + VS Code + Codex**。Windows负责你看到的界面和浏览器；WSL中的Ubuntu运行项目；VS Code把文件、终端和Agent放在一起。只安装Codex还不够。

## 1. 开始前准备什么

- Windows 11，或支持WSL的Windows 10（2004／内部版本19041及以上），能安装软件、联网和重启。公司管理的电脑可能需要管理员协助。[微软要求](https://learn.microsoft.com/en-us/windows/wsl/install)。
- 能登录Codex的账号；使用订阅额度时选择ChatGPT账号登录，本文不要求购买API额度。
- 你现有的简历：**PDF可以**，UTF-8文本、单个LaTeX文件也可以。已有LaTeX项目请一起准备主文件、样式、图片和字体等依赖，最好附上原PDF。
- 目标岗位方向、地区、毕业时间、实习／全职偏好。工作许可、是否需要雇主支持、毕业后能否实习等不确定项先记为“待确认”。Agent不能从学校、住址或语言推断。

本文按“本机保存一份资料和数据库”开始，不要求SSH或服务器。英文开发版与中文发布版选一个安装，各用自己的文件夹和虚拟环境。

## 2. 安装Windows上的软件

先分清两个窗口。标为`powershell`的命令在Windows PowerShell运行；标为`bash`的命令在Ubuntu／WSL终端运行。只复制代码框内的命令，提示符不用复制。

| 窗口 | 可能看到的提示符 | 用来做什么 |
| --- | --- | --- |
| Windows PowerShell | `PS C:\Users\你的用户名>` | 安装WSL、Windows软件 |
| Ubuntu／VS Code的WSL终端 | `你的用户名@电脑名:~$` | 下载项目、运行Python和编译材料 |
| 激活项目环境后的WSL终端 | `(.venv) 你的用户名@电脑名:~/projects/sharkapoolu_beta_zh$` | 运行项目命令 |

### 2.1 安装WSL2和Ubuntu

Windows开始菜单搜索PowerShell，右键“以管理员身份运行”，输入：

```powershell
wsl --install -d Ubuntu-24.04
```

按提示重启。打开Ubuntu，设置Linux用户名和密码；输入密码时不显示字符是正常的。已有Ubuntu就先检查，不要卸载或重新初始化它。

在PowerShell检查：

```powershell
wsl --list --verbose
```

Ubuntu-24.04这一行的VERSION应为`2`。若为`1`，转换这一个发行版：

```powershell
wsl --set-version Ubuntu-24.04 2
```

若你已有的发行版叫`Ubuntu`，把命令里的名字换成列表中的实际名称。安装卡住或提示虚拟化错误，先把报错交给Agent；不要反复删除发行版。[官方安装与故障入口](https://learn.microsoft.com/en-us/windows/wsl/install)。

### 2.2 安装VS Code和浏览器

**VS Code安装在Windows。** 可以从[官网下载安装程序](https://code.visualstudio.com/docs/setup/windows)。如果PowerShell中`winget --version`能正常显示版本，也可以用以下命令：

```powershell
winget install --id Microsoft.VisualStudioCode --exact --source winget
```

已有Chrome或Edge就可以手动查看PDF和提交申请。想安装Chrome，可以从[Chrome官网](https://www.google.com/chrome/)下载，或在PowerShell运行：

```powershell
winget install --id Google.Chrome --exact --source winget
```

`winget`不存在时用官网安装程序即可；不必为它卡住。[WinGet说明](https://learn.microsoft.com/en-us/windows/package-manager/winget/install)。安装完重新打开PowerShell，避免旧窗口找不到新命令。

### 2.3 安装WSL扩展和Codex，登录账号

打开VS Code左侧“扩展”，安装微软的 **WSL** 和OpenAI的 **Codex**。可核对扩展ID分别为`ms-vscode-remote.remote-wsl`和`openai.chatgpt`。也可以在安装VS Code后的PowerShell运行：

```powershell
code --install-extension ms-vscode-remote.remote-wsl
code --install-extension openai.chatgpt
```

在VS Code设置（`Ctrl+,`）搜索`chatgpt.runCodexInWindowsSubsystemForLinux`，开启在WSL中运行Codex的选项，按提示重新加载窗口。设置找不到时先更新官方扩展，或让Agent检查当前客户端。这个选项属于VS Code设置，不写进项目JSON。[Codex WSL说明](https://learn.chatgpt.com/docs/windows/wsl)、[IDE设置](https://learn.chatgpt.com/docs/developer-settings?surface=ide)。

打开Codex侧栏，选择用ChatGPT账号登录，完成浏览器授权。扩展自带所需的Codex命令程序，走这条路径不要求你另装Node.js或npm。[官方扩展安装与登录](https://learn.chatgpt.com/docs/codex/ide)。

## 3. 在Ubuntu中安装项目

从开始菜单打开Ubuntu-24.04，或在普通PowerShell输入：

```powershell
wsl -d Ubuntu-24.04
```

从这里开始，以下命令都在Ubuntu／WSL运行。密码是刚才设置的Linux密码。

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip make ca-certificates bubblewrap
python3 --version
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/SimonTang02/sharkapoolu_beta_zh.git
cd sharkapoolu_beta_zh
./scripts/bootstrap.sh --with-resume
source .venv/bin/activate
code .
```

Python须为3.10或更新。HTTPS下载公开仓库不需要配置SSH密钥；若GitHub要求仓库访问授权，先确认访问权限。已有同名目录时停止下载，让Agent检查是否已经安装，别删除重来。

VS Code左下角应显示`WSL: Ubuntu-24.04`。选择“Terminal → New Terminal”，在终端核对：

```bash
echo "$WSL_DISTRO_NAME"
pwd
source .venv/bin/activate
python --version
jobbot-private check
```

`pwd`应指向Ubuntu里的项目，如`/home/你的用户名/projects/sharkapoolu_beta_zh`；WSL名称不应为空。[VS Code WSL使用说明](https://code.visualstudio.com/docs/remote/wsl)。

初始化会安装Python包、PDF文字提取组件，创建缺少的空白私有模板并运行检查／测试。**检查通过只表示结构正确。** 个人事实、岗位来源、评分、TeX编译器和网站登录还没配置好。测试失败时保留完整报错给Agent，不要跳过检查继续投递。

现在发给Codex的第一条消息可以直接复制：

```text
请先读AGENTS.md、AGENT_HANDOFF.md、docs/getting-started.md、
docs/candidate-onboarding.md、docs/beginner-settings.md、docs/platforms.md，
并按需要读取examples/README.md及相关schema。
检查我是否在WSL2、项目虚拟环境、正确的私有目录和本机数据库角色中。
检查已有私有文件，保留它们；不要重置数据、覆盖材料或改源码。
先说明环境已完成什么、还缺什么。默认手动辅助、关闭自动填写；
先不扫描岗位或访问申请门户。缺少事实或偏好时集中问我，继续不依赖答案的工作。
请把安装状态和下一步写进一个新的私有交接文件，并告诉我确切路径。
```

## 4. 把你的简历交进来

在已激活环境的项目终端打开一个私有收件文件夹：

```bash
mkdir -p private_data/inbox
explorer.exe "$(wslpath -w "$PWD/private_data/inbox")"
```

Windows资源管理器会打开该文件夹。把自己的简历拖进去。下面假设文件叫`resume.pdf`；若不同，替换为真实文件名，保留引号。

```bash
cvbot import-resume --input "private_data/inbox/resume.pdf"
cvbot import-resume --input "private_data/inbox/resume.pdf" --execute
```

第一条只检查输入；第二条创建新的私有导入包，并保持原件不变。输出中会告诉你新目录，内有`AGENT_TASK.md`、原件、文字、`resume_draft.tex`和事实台账。把**那个具体的AGENT_TASK.md路径**发给Agent。

导入器接收PDF、UTF-8 TXT、单个`.tex`，不直接接收ZIP或整个LaTeX项目。多文件LaTeX请把整个文件夹／ZIP及原PDF放进收件目录，让Agent先检查、解包和保留依赖，不要只导入主文件后丢掉其他文件。

PDF须是可读取的非加密副本，当前上限20MB、25页。扫描图片PDF没有可靠文字层时，内置提取器不会OCR；需要Agent逐页看图或另行OCR，多栏文字也要核对顺序。PDF转LaTeX产物是可编辑转录草稿，不保证复原布局，不能直接标成投递就绪。

复制这段给Agent，把第一行的路径改成实际输出：

```text
请读取刚生成的私有导入目录中的AGENT_TASK.md，以及原始简历。
按docs/candidate-onboarding.md逐页核对，整理事实台账，区分原文明写、
本人确认、待确认。为每条填表信息和关键词记录来源、页码、证据范围。
基于真实经历整理个人档案、技能证据和关键词；不要使用演示人物资料。
工作许可、雇主支持、精确毕业日期和毕业后实习资格等缺失答案问我，不推断。
向我确认岗位方向、地区、招聘年份和排序偏好，提出评分权重并验证正反例；
关键词库和评分配置分别设置，不把评分当录取概率或资格。
保留原件，在新私有目录重建可编辑LaTeX。检查模板与cvbot标记兼容性，
编译后视觉比较。现有定制主要处理摘要、技能及匹配的毕业日期，
项目和工作经历的改写需逐项审阅。不要覆盖现有current.tex或已交付材料。
列出待我核实的事实、缺少的依赖，以及首岗材料准备所需的步骤。
```

你不用自己填写所有JSON。Agent可以整理草稿，你逐项核实；也可以完全手工维护资料，见第7节。

例如，简历写“课程项目使用Python和SQLite”，可以得到项目范围的Python／SQL标签，不能写成多年商业后端经验。如果你想找后端实习，Agent可以提议把这些作为主要检索词、其他有证据的技能作为补充，并用直接相关、相邻及资历不符的岗位测试实际评分。权重表达你的找岗偏好，必须写入真正生效的配置，不能只生成一张没被程序读取的表。

## 5. 生成PDF需要另外安装TeX

仓库提供LaTeX模板和编译调用，**没有随代码附带TeX编译器**。`latexmk`负责调用实际的TeX引擎；它和字体都要安装。只建档、读PDF或整理答案时，可以暂时跳过本节。

需要生成PDF时，在Ubuntu／WSL运行：

```bash
sudo apt install -y latexmk texlive-latex-extra texlive-fonts-recommended texlive-xetex texlive-lang-chinese fonts-noto-cjk
latexmk --version
xelatex --version
```

下载可能较大。已有LaTeX项目仍可能需要自己的样式包和字体，由Agent按报错补齐。编译前让Agent检查原始LaTeX内容和外部依赖。

让Agent先把**已核实的新源文件**编译到新私有目录，用中文／Unicode内容时选择XeLaTeX。`cvbot --generate-bundle --latex-engine xelatex`可选择引擎；`make current`使用pdfLaTeX，不能把它当作中文通用编译命令。生成材料的完整参数见[简历初始化指南](candidate-onboarding.md)。

对Agent准备好的文件，你可以复制这段检查要求：

```text
请选择当前已核实的LaTeX主文件，告诉我源路径、引擎和新输出目录，
编译PDF并逐页检查：文字是否缺失、裁切或乱码，日期/GPA/经历是否一致，
有无演示人物或无依据的说法。给出PDF路径和检查结果，等待我确认材料。
编译成功不等于审阅通过；不要只看日志就登记附件通过。
```

**不需要VS Code的PDF预览插件才能编译或查看。** 可用上面的资源管理器方法打开Agent给出的输出文件夹，双击PDF选择Chrome／Edge。VS Code内的PDF预览扩展只是可选便利。

上传时在Windows文件选择器用资源管理器显示的WSL共享路径；不要把Linux的`/home/...`直接填进Windows文件选择器。需要复制到Windows时，只复制所需材料到你自己的私有文件夹。

## 6. 平时只调一份开关文件

在VS Code打开`private_data/config/easy_settings.json`。`true`是开，`false`是关；不要删除引号、逗号和区域名称。使用了另一个私有根目录时，Agent会告诉你对应路径。

| 区域 | 你要决定什么 |
| --- | --- |
| 01 使用模式 | 总开关；手动辅助、快速填写或完整填写。最终提交始终自己点。 |
| 02 功能模块 | 是否扫描、生成材料／投递包、辅助或自动填写、生成报告。父模块和子功能都要开启。 |
| 03 浏览器与采集 | 不开浏览器的HTTP；临时独立Chromium采集；连接专用Chrome的CDP。 |
| 04 审查与额度 | 默认3轮，每轮同时查资格、字段、附件；每岗时间和重试次数。 |
| 05 地区范围 | 保留哪些工作地点；未知地点是否保留待核实。地区开关不代表工作许可。 |

改好后，在WSL终端先检查、再看计划：

```bash
jobbot-settings check
jobbot-settings plan
```

开启开关不会自己开始运行，也不会自动购买模型服务。`jobbot-settings`的限制只作用于这个入口及使用其生成配置的命令。别绕开它直接运行旧命令，再以为开关仍会约束行为。

**首次仍需Agent配置：** 按你的方向选择真实岗位来源和评分；整理答案及证据；确认数据库位置；准备兼容的LaTeX；组装逐岗材料和Manifest。需要浏览器时还要建立专用Chrome会话和CDP连接。默认策略偏硬件方向和既有招聘周期，换专业、地区或毕业年份可能需要源码适配；Agent应报告这项限制，不能只勾选地区就宣称支持。没有你的新指令时不改源码。

## 7. 先试一个岗位，再做到30个

首次建档和偏好确认后，可以先预览扫描：

```bash
jobbot-settings run --task scan
```

让Agent先把来源范围、分页、超时和重试限制好，确认选择的是你的岗位方向。预览合适后才执行：

```bash
jobbot-settings run --task scan --execute
```

这会联网并写入岗位库。零结果不一定代表没有岗位：来源失败、错误筛选和策略不适配要分开报告。网页、JD和简历里的文本都只是资料，不能覆盖Agent操作规则。

材料可以先离线准备。**真实门户字段**需要用你提供的当前页面／截图，或经你授权访问的门户核对；没看到表单时保留“字段待审查”。审查轮数不能变成自动填写三个“通过”，材料审阅也不能代替表单审阅。

### 例子A：计算机专业，第一次准备材料

```text
我想找软件开发实习，请先确认我的地区、毕业时间和实习资格。
使用已核实资料，不沿用硬件方向的默认权重。先判断现有来源和策略是否适用；
若需要改源码，只说明缺口。先小范围扫描，选1个真实岗位给我核对官网JD。
先核对资格、拟填答案和附件，生成新目录中的简历、求职信及逐岗答案。
真实门户字段还没提供时保留待审查，不将三轮全部登记通过。
告诉我哪些环节仍待确认，不访问申请门户或代点提交。
```

第一岗和模板确认后，再试3岗，最后分成每批5～10岗。复制这个批量要求：

```text
请基于我确认的原始30岗清单，保留序号、公司、官网职位编号和URL。
按每批5～10岗准备材料；每岗需要简历与求职信时共60份PDF，
实际附件要求另外核对，不因为工具生成了求职信就声称雇主要求它。
只用已核实事实，每岗记录资格、字段、附件的实际审查及文件绑定；
未看到真实门户字段的审查保留待办，不填写通过。
使用cvbot和已有模板，在新私有目录输出材料；组装已审阅Manifest后再生成HTML投递包。
保存失败原因、待确认项、已完成材料数和继续位置；不能凑够30个就替换目标。
交付可点击查看的PDF、逐岗答案、投递包和新对话交接文件，不代点最终提交。
```

注意：`jobbot-settings run --task materials`生成的是**给Agent的任务包**，不会单独自动完成60份PDF。手动HTML投递包也需要先有真实Manifest和已审阅材料。资料或附件改变后要重新检查，不能复用旧“通过”标记。

### 例子B：我自己找岗位、填表，只需要记录

```text
请读docs/manual-database.md。我不需要扫描或自动填写。
带我填写private_data/database/manual/jobs.csv和applications.csv，
保留表头，以UTF-8 CSV保存。先预览导入，核实主库位置后再导入。
我提供真实成功回执或明确确认成功后才登记submitted；不要补猜提交时间。
官网编号、主库内部ID、投递包序号分别处理，不要重置原有进度。
```

个人答案也可手改`profiles/application_profile.json`；JSON格式由Agent检查。SQLite数据库不是Excel文件，不直接用Excel覆盖。你没有远端仓库写入权限也可以在本机保存资料；这些操作不需要GitHub提交／推送。

## 8. 想让Agent看表单或自动填写时，再装浏览器组件

只做HTTP扫描、简历和手动记录不必安装这些。需要浏览器采集、截图或受支持的填写时，在项目的WSL终端运行：

```bash
./scripts/bootstrap.sh --with-browser --with-resume
source .venv/bin/activate
sudo .venv/bin/python -m playwright install-deps chromium
```

这安装Playwright、专用Chromium和Linux系统库，**不会自动配置你Windows上的Chrome登录／CDP**。CDP是Agent连接一个专用Chrome窗口的方式；WSL与Windows间还需要现场检查连接和文件路径。让Agent按[平台指南](platforms.md)和[安装指南](installation.md)配置，使用独立浏览器资料，不连接日常Chrome，不把调试端口开放给局域网。

选择手动辅助时，你可以自己打开网页，把具体问题或截图交给Agent；使用任务包截图功能时，需要先配置专用CDP。你自己登录、处理验证码／MFA、检查附件并提交。

- **快速填写：** 默认每岗45秒、0次重试，遇验证码、登录边界或超时就结束本岗适配器，留下原因并继续下一岗。
- **完整填写：** 默认每岗240秒、1次重试，遇需要本人处理的步骤或失败就停止批次。它增加准备时间，最终提交仍由你完成。

这些是支持门户的准备模式，不是所有网站通用的全自动投递。到Review页、上传成功或脚本返回成功，都不等于投递成功。

## 9. 模型和额度怎么安排

日常安装检查、文字整理和小批量材料准备，优先尝试模型选择器中的 **GPT-6 Luna**；名字或可用性以你的客户端为准。官方将它定位为较省资源、适合集中任务和大量整理工作的模型。[官方模型说明](https://learn.chatgpt.com/docs/models)。复杂排错、证据冲突或模板改造可换更强模型复核；法律和许可事实仍由本人确认。

“部署＋有限范围扫描＋个人建档＋30岗材料，有机会在Plus的一个5小时额度窗口内完成”可以作为**项目作者的粗略规划目标**，目前不是经验证的耗时或额度基准。它假定你已准备好原简历、明确目标、网络正常，并分批生成；网站登录、验证码、人工审阅和最终投递另算。

**5小时是用量统计窗口，不是完成时限或保证，也不意味着每5小时获得固定量的工作。** 模型、上下文、工具调用、重试和任务复杂度影响额度，还可能有其他限制；以Codex显示的余额／恢复时间为准。Plus订阅和API计费不同。[官方额度说明](https://learn.chatgpt.com/docs/pricing)。

先做1岗估计自己的消耗，再扩批；遇阻保存交接，不让Agent反复尝试验证码或同一失败源。不要把30份材料全部生成后才第一次打开PDF检查模板。

## 10. 第二天或新对话怎么继续

在Ubuntu／WSL打开项目并激活环境：

```bash
cd ~/projects/sharkapoolu_beta_zh
source .venv/bin/activate
code .
```

新对话不会自动知道旧聊天内容。把Agent此前给你的**具体私有交接路径**一起发过去：

```text
请先读AGENTS.md、AGENT_HANDOFF.md和docs/foolproof_guide_zh.md，
再读我提供的私有AGENT_TASK.md或campaign交接文件，以及它引用的Manifest和审查记录。
先只读核对当前配置、数据库角色、材料和未完成项，不重新初始化或覆盖文件。
本次要继续的岗位／任务是：……；私有交接文件路径是：……。
实际投递进度以当前数据库、我提供的浏览器进度导出和真实回执为准；
材料完成数、总库submitted数都不能当成本批进度。
若HTML进度只在我的浏览器中，请先让我导出manual30_progress.json再核对，不重置。
不知道的地方向我确认，不自动最终提交。
```

**备份和分享分开做。** 自己的安全备份需要保留`private_data/`里的资料与数据库；给别人求助的分享副本才排除这些，以及其他目录里的真实简历、截图、凭证和回执。不要为了求助删除工作目录的私有资料。`.gitignore`不等于加密；只给你信任并选择使用的Agent读取必要资料。

| 常见现象 | 下一步 |
| --- | --- |
| 找不到`jobbot-settings`／`cvbot` | 确认WSL、项目目录和`source .venv/bin/activate`；仍失败发安装报错，不全局乱装包。 |
| 导入后文字为空／顺序乱 | 让Agent看原PDF页面，记录视觉转录需求；不要把空文字当没经历。 |
| PDF编译失败／缺字体 | 把TeX日志路径给Agent，核对引擎、字体、模板依赖；未出PDF就不能说完成。 |
| 没有合适岗位 | 核对来源运行状态、关键词、地区及年份策略，不只加大扫描范围。 |
| Chrome连接失败／验证码 | 交给Agent检查连接配置；验证码你自己处理，按模式中断，保存已填表单。 |
| 不知道哪岗投过 | 用浏览器导出、回执或本人逐岗确认核对，不凭生成材料推断。 |

其他平台：Linux桌面可跳过WSL，使用本机Python、TeX和专用浏览器；无桌面服务器主要负责扫描、数据库和材料，本人提交在可见浏览器中完成。macOS和原生Windows有核心功能安装路径，但完整浏览器链路仍需在该机器验证。不要在这些系统照抄Ubuntu的`apt`或Windows专用网络命令；详见[平台兼容性](platforms.md)。
