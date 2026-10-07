# 申请机器人

## 标准离线手动申请套件

`manual-kit` 使用 `templates/manual_kit/` 中的共享模板，将已审阅材料渲染到一个新的私有投递目录。它保留原始职位编号和 manifest 顺序，支持任意批次大小、岗位筛选、逐字段复制按钮、文档链接，以及带回执备注和 JSON 导出的浏览器本地进度。该流程不访问数据库或公司门户。

每个新套件还包含 campaign 专属的 `AGENT_HANDOFF.md`、manifest SHA-256、进度键前缀和可复制的新对话提示。该文件标明材料和续接规则，但不证明当前进度。Agent 仍须取得最新私有记录以及候选人当前的岗位、导出或回执。bootstrap 完成哪些初始化、哪些事项需要候选人/Agent 协助，见[配置责任](../docs/getting-started.md)。

```bash
python3 application_bot/cli.py manual-kit \
  --manifest private_data/outputs/application_bot/example_source/Manifest.json \
  --output private_data/outputs/application_bot/example_delivery \
  --progress-key example-campaign-
```

源套件包含 `Manifest.json`（参见[`manual-kit-manifest.schema.json`](../schemas/manual-kit-manifest.schema.json)和[虚构 manifest](../examples/manual_kit_manifest_template.json)）。每个职位必须有原始正整数 `rank`、唯一单层 `folder`、HTTPS 职位/申请 URL，以及带 SHA-256 和 `visual_review: passed` 的 `pdfs` 项。生成器会校验哈希并复制现有 PDF 字节；它不会生成新的简历声明或批准审阅。

每个岗位目录包含具有相同 `rank` 的 `Application_Data.json`。可选章节包括 `common_fields`、`regional_authorization`、`role_specific_answers`（标签到答案的对象）、`education`、`work_experience`、`projects`（对象列表）、`portal_notes` 和 `transcript_verified_education_facts`。未确认/空值仍保持未确认状态。可选岗位 TXT/JSON 参考和 manifest 支持文件会原样复制，不修改其内容或官方/非官方标签。

输出必须位于规范私有根目录下，且目标路径尚不存在。生成器拒绝覆盖已交付套件。每个 campaign 选择一个不同且稳定的 `--progress-key`，并保持用户入口文件路径不变。已有浏览器进度会原样加载，不重置键；manifest 或数据库状态不会预填进度。导出的进度保持 `rank`、`status`、`receipt` 数组格式。初始空白状态不能证明没有申请。只有回执或候选人明确确认，才允许通过现有 `mark_submitted.py` 工作流登记数据库提交状态。

这是浏览器辅助申请准备的稳定入口。兼容迁移期间，实现仍保留在 `job_bot/application_bot.py`，因为现有队列、测试和命令依赖该路径。

默认情况下，机器人可以打开申请页面、填写已批准的档案数据、上传已审阅简历、保存材料并停在 Review。最终提交需要针对相关申请的明确会话授权，并完成审阅。授权需记录在被忽略的本地 campaign manifest 中；某个 campaign 的授权不适用于其他申请。绝不推断法律/移民问题答案或绕过 CAPTCHA/MFA。

准备技能标签或个人优势答案时，查询 `private_data/cv/profile/application_keywords.md`（JSON 路径：`private_paths.APPLICATION_KEYWORDS`）。为具体字段选择相关且有证据支持的条目。词库示例不能证明技能等级、年限或已经确认的性格自评。档案准备、批次材料生成、档案同步和 Workday 预览现在都调用共享选择器。它们会保留手动技能列表，只从受支持技术条目填入空白技能列表（或刷新未更改过的机器生成列表）。协作示例会保留在 `profile.application_keywords` 供审阅，不作为性格答案。其他适配器按各自现有字段支持情况使用准备好的档案。关键词选择不会触发浏览器动作。

Workday 在填写时会根据职位地点核对合法工作资格和签证赞助答案，包括直接调用 `workday-preview` 的情况。候选人档案的地址国家不能替代职位地点。地点范围缺失、混杂或未经确认时，该问题保持待处理；仅限中国大陆的许可不涵盖香港。此保护不会认证浏览器中已有答案，也不会解决身份/教育事实冲突。

从项目根目录运行示例：

```bash
python3 application_bot/cli.py list
python3 application_bot/cli.py workday-preview --application-id 1 \
  --start-application --interactive
```

统一 CLI 还提供由配置驱动的操作：

```bash
python3 application_bot/cli.py session-audit
python3 application_bot/cli.py login-tabs
python3 application_bot/cli.py login-preflight --campaign-id 3 --campaign-id 4
python3 application_bot/cli.py dispatch --application-id 1
```

`session-audit` 根据 `portals.json` 的 ATS 会话范围为每个范围生成一个探测，在并发检查无关租户时不记录 cookie 或字段值，并关闭每个临时页面。`login-tabs` 读取该审计结果，只打开确实需要登录/MFA 的页面。`dispatch` 将申请映射到适配器；默认只写计划，除非提供 `--execute`，否则不运行任何操作。

分发 campaign 前运行 `login-preflight`。即使多个雇主共用同一个 ATS，它也只会为每家公司打开或复用一个由机器人管理的检查标签页。先在那里完成登录/MFA 审阅，再创建申请专用标签页。再次运行预检时，只会去重自身创建的公司标签页，绝不关闭可能保留表单状态的申请标签。

门户适配器实现共享机制（Workday、iCIMS、Moka、Oracle），但 `job_bot/config/portals.json` 还定义公司档案。公司档案负责该雇主的字段选项、必填部分、同意规则和已知限制。每家公司的首次申请是学习模板的试运行；该公司的后续岗位会复用此档案，而不会从使用相同 ATS 的其他雇主那里重新学习行为。

`nvidia-preview` 作为向后兼容别名保留。标准 Workday 租户可使用 `workday-preview`，但每个租户可能需要各自的账户/会话并审阅自定义问题。

Windows CDP 模式下，Workday 登录首先检查 Chrome Password Manager 是否已自动填好用户名和密码两个字段。如果已填入，机器人可点击 Sign In，但不会读取或导出任一凭据。浏览器自带密码提示、Windows Hello、MFA、验证码及 CAPTCHA 仍需人工处理。如果没有填入已保存凭据，仍可使用按公司配置的 `passport.env` 凭据回退方式。

每次完成填写测试，都会在对应申请的 `fill_tests/` 目录追加带时间戳的截图和不含字段值的 JSONL 记录。Workday 仍保留旧版 `preview.png`，但历史截图不再被覆盖。MediaTek 仅档案步骤写入 `private_data/outputs/job_bot/application_profiles/mediatek/fill_tests/`。这些材料可能包含个人信息，因此会以私有权限保存。

申请标签页在 SQLite 中登记申请 ID、CDP target ID、持久化 `window.name` 标签、规范职位 URL 和门户专用职位指纹。适配器按此顺序解析；所有检查都失败时才新建标签页。恢复 Workday 申请时，如匹配到申请页会予以保留，不会导航回职位列表。target ID 只在标签打开期间有效；申请 ID 和职位指纹才是持久身份。

审计专用 Chrome，也可认领职位指纹明确匹配单个申请的旧标签：

```bash
python3 application_bot/tab_manager.py
python3 application_bot/tab_manager.py --adopt
```

安全清理须显式执行。关闭页面前会逐页截图；默认只关闭空白页和 URL 完全相同且干净的重复页。已填写表单、已登记申请和每租户一个已认证会话锚点都会保留。要关闭其他干净的旧标签，需额外提供标记：

```bash
python3 application_bot/tab_manager.py --apply
python3 application_bot/tab_manager.py --apply --include-clean-legacy
```

可审计专用 Chrome 中已经打开的十个不同门户，不点击、不填写、不接受政策，也不提交：

```bash
python3 application_bot/platform_audit.py --limit 10
```

`private_data/outputs/application_bot/` 下的 Markdown 和 JSON 报告会区分可编辑表单与身份验证、CAPTCHA/政策同意、档案不完整及仅最终提交的流程。这样可避免将 MediaTek 这类一键投递门户误认为支持草稿的系统。

打开浏览器前，先将职位专用 PDF 绑定到私有的逐申请档案。仅当 `current.tex` 中明确存在联系字段且对应目标字段为空时才会填入；地址和法律/移民答案不会更改：

```bash
python3 application_bot/cli.py prepare-profile \
  --application-id 1 \
  --resume /absolute/path/to/resume.pdf \
  --cover-letter /absolute/path/to/cover_letter.pdf
```

无需打开浏览器即可检查某岗位当前按证据筛选的关键词：

```bash
python3 application_bot/cli.py keywords --role "GPU Architecture Engineer"
```

命令还接受 `--job-description FILE`、`--library FILE` 和 `--preset ID`。选择记录词库 SHA-256 和支持来源，供后续审阅。

上传新生成/修订的简历前，检查其 manifest 和最终渲染页面。遵守配置的页数限制；优先修复孤立标题、额外稀疏页和文本提取错误。仅编译成功不能标记 PDF 已审阅。逐项确认网站技能已保存为标签，而非只出现在搜索文本或高亮下拉选项里。对于 Workday 技能，`aria-selected` 可能只表示键盘焦点；应使用实际复选框，并确认最终 Review 中显示所选标签。

填写 Workday 日期分段时，先目视定位月份/年份显示，使用真实 CDP 鼠标点击和键盘输入，保存前将焦点移出日期组。仅 DOM input 值看似正确时，保存日期仍可能缺失。如果模拟输入导致 No Items，应对远程技能搜索使用真实文本输入。公司专用观察结果和 campaign 授权应记录在被忽略的本地报告中，不要写进共享文档。
