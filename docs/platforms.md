# 平台选择：推荐Windows + WSL2 + VS Code

本项目需要自己的Git/Python/虚拟环境和私有资料，安装Codex本身不会把这些
依赖安装好，也不会提供本人简历、主库和门户登录。新电脑仅装Codex时，可
让Agent按照本指南协助安装与整理；尚不能直接运行整个投递系统。
OpenAI当前提供原生Windows及WSL使用路径；本项目推荐WSL是依据自身Linux
工具链与现有验证范围，不是说Codex只能在WSL工作。
[OpenAI Windows文档](https://learn.chatgpt.com/docs/windows/windows-sandbox)。

## 实际兼容性与验证边界

| 环境 | 当前使用方式 | 需要注意 |
| --- | --- | --- |
| Windows + WSL2 + VS Code（推荐） | WSL运行Python/Git/SQLite/TeX，Windows专用Chrome负责门户与本人操作，VS Code用WSL扩展打开Linux项目 | 现有主要工作流以此组合和Linux为基础；浏览器需专用profile、受限CDP及路径/网络映射 |
| 原生Linux桌面 | Bash bootstrap、全部Python命令；本机专用Chrome的loopback CDP，或临时内置Chromium采集 | 不需要WSL或Windows portproxy；Playwright需系统库，PDF渲染需TeX/字体 |
| 无桌面Linux服务器 | HTTP扫描、评分、SQLite/SSH主库、报告、简历文本整理；内置headless用于适合的公开源 | 服务器自己没有可供本人点提交的桌面；实际门户登录与提交在本人可见的浏览器机器上。SSH只共享DB，PDF与浏览器不会自动同步 |
| macOS | Bash bootstrap与Python核心命令；本机专用Chrome CDP；自行安装TeX和字体 | 核心代码具备移植路径；尚未在真实Mac验证完整门户链路，不能承诺即装即投。Windows专用网络脚本不能照用 |
| 原生Windows | 新增PowerShell bootstrap与Python CLI；手动CSV/资料、评分、HTML投递包等核心功能有运行路径 | Bash、Make、Linux路径不能照抄；SSH共享RPC当前不支持Windows管道，使用WSL。真实浏览器/TeX链路仍须验证 |

这里区分本项目实际验证和底层库支持。Playwright官方支持Windows、macOS与
受支持的Linux版本，但这不证明每个ATS适配器在三端都通过。
新核心平台smoke工作流提供进一步验证入口，结果应看GitHub实际运行状态。
[Playwright系统要求](https://playwright.dev/python/docs/intro)。

## 一台新Windows电脑的推荐安装顺序

1. 在Windows安装VS Code和其WSL扩展、Chrome，以及所用Codex入口并登录。VS Code装在Windows，代码运行位置由WSL扩展连接确定。
2. 以管理员身份在PowerShell运行下面命令，按系统提示重启，首次打开Ubuntu时创建Linux用户。系统或企业策略不允许时由本人/管理员处理，Agent不能越过限制。

```powershell
wsl --install -d Ubuntu-24.04
```

Microsoft要求受支持的Windows版本，具体启用/更新步骤见
[WSL官方安装说明](https://learn.microsoft.com/en-us/windows/wsl/install)。

3. 在Ubuntu终端安装Git/Python/venv，再下载公开代码。HTTPS clone无需先配置GitHub SSH密钥：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/SimonTang02/sharkapoolu_beta_zh.git
cd sharkapoolu_beta_zh
./scripts/bootstrap.sh --with-resume
source .venv/bin/activate
code .
```

VS Code窗口应显示WSL环境；终端中的Python来自Linux `.venv/bin/python`。
Agent也需操作同一项目和环境，避免Windows解释器与WSL解释器混用。
[VS Code WSL说明](https://code.visualstudio.com/docs/remote/wsl)。

4. 暂时保持手动辅助和自动填写关闭。导入原始PDF，按
[candidate-onboarding.md](candidate-onboarding.md)整理资料。无TeX时先完成
文字/答案/关键词，不把无PDF渲染环境说成已经完成材料。
5. 需要浏览器采集或辅助填表时，再运行 `./scripts/bootstrap.sh --with-browser --with-resume`。
Ubuntu/WSL按提示安装Playwright系统库。安装TeX与中文字体另行配置。
6. 由Agent建立专用Chrome profile和CDP，本人登录并处理验证，再做单岗试用。
WSL跨宿主网络与原生Linux不同：旧高级入口允许受限网关转发；易用入口仅
接受localhost/127.0.0.1/::1，须先配置本机受限映射或隧道，不把网关地址
直接塞进易用入口。具体主机网络拓扑应现场验证，调试端口不开放给LAN。

Windows下载的简历可在WSL通过 `/mnt/c/...`读取；程序复制原件到私有intake。
上传到Windows Chrome时，本人可用WSL共享文件入口找到PDF，或在授权后复制
到Windows私有目录。浏览器自动上传仍需验证运行脚本与浏览器看到的路径。
不把WSL路径当成Windows磁盘路径，不自动复制全部私人数据。

## 原生Windows替代安装

先安装Git和Python3.10+，重新打开PowerShell；在项目根目录运行：

```powershell
.\scripts\bootstrap.ps1 -WithResume
.\.venv\Scripts\python.exe -m job_bot.easy_cli check
.\.venv\Scripts\python.exe -m cv.resume_import --input <resume.pdf> --execute
```

`-WithBrowser`安装可选Playwright和Chromium；`-SkipTests`只用于已验证版本的
短时修复。脚本只运行核心合同测试；Linux/SSH完整测试在WSL中执行。
脚本不安装操作系统依赖、不开CDP、不访问投递门户。
可直接使用上面的虚拟环境python，不必全局修改PowerShell执行策略。
若系统阻止脚本执行，由本人按设备策略解决，或让Agent逐项运行脚本中的
普通Python命令。不要为了安装工具关闭所有权限保护。

Mac/Linux均可用Bash bootstrap，前提为已安装Git、Python3.10+和venv；Mac的
Python安装方式与Ubuntu apt不同，不照抄apt命令。Windows Chrome启动脚本、
portproxy、防火墙规则不能照搬到Mac/Linux。三端本机CDP都使用专用profile。
现有配置名 `windows_cdp` 表示CDP传输，可连接Mac/Linux本机Chrome，
并不表示运行系统必须为Windows。原生Windows凭证隐私须核对NTFS ACL，
结构检查会提示Unix权限位无法验证ACL，不能把检查通过当作隐私已审阅。

## 依赖按功能安装

| 功能 | 最小依赖 |
| --- | --- |
| 已交付离线HTML、本人填写上传 | 普通Chrome和真实材料；仅查看包不需要Python或Codex |
| 新建/查询主库、CSV录入、HTTP扫描与报告 | Git、Python3.10+、venv、项目包；SQLite由Python提供 |
| 原始PDF文字导入 | 上述环境及resume extra；扫描图片还需要Agent视觉读取/另行OCR |
| Agent辨识图片和整理答案 | 已登录的Agent工具、合法本地文件访问及已确认事实；脚本不自行购买模型服务 |
| 浏览器采集/自动准备 | browser extra、Chromium或专用Chrome、相应系统库/CDP配置、本人有效会话 |
| LaTeX生成最终PDF | latexmk及TeX发行版/字体；中文/Unicode选择XeLaTeX；安装TeX不会替代视觉审阅 |
| 在线SSH主客机共享 | 两端可用SSH、已确认host key与主机角色、主机在线；不保证浏览器或材料同步 |

旧Linux专用工具的路径已在跨平台说明中区分；本轮把cvbot的TeX搜索路径分隔符
改为平台分隔符并增加显式引擎选择。当前机器能验证的是Linux核心和独立
浏览器流程；真实Mac/原生Windows端完整投递仍须在该机器验证。

当前SSH共享RPC用selectors处理子进程stdin/stdout管道，Windows仅支持套接字，
因此原生Windows共享客户端不是已支持路径；本机SQLite不受此限制。主库host
仍按Linux/WSL的远端shell契约配置。依据[Python官方说明](https://docs.python.org/3/library/selectors.html)和job_bot/shared_database.py审查。
