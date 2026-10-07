# 外部简历与求职信工具

本评估于 2026-08-28 根据各项目官方 GitHub 仓库完成。

## 建议

可将 Resume Matcher 作为可选且隔离的分析服务，用于 JD 关键词覆盖、建议和求职信草稿。保留 `current.tex` 作为规范简历；晋升某个变体前，必须检查差异并审阅 PDF。

不要将 AIHawk 用作生产申请引擎。该项目虽然很受欢迎，但仓库已归档，第三方服务插件也已从其仓库移除。其广泛自动申请模式与本项目按网站逐项审阅及禁止提交的安全边界冲突。

本地 `cv/bot` 模块刻意保持精简：只选择已审阅证据，生成审阅包，不访问网络，也没有提交路径。

## 已评估项目

- Resume Matcher：<https://github.com/srbhr/Resume-Matcher>
  - 活跃项目，支持本地 Ollama、多种托管 LLM 服务商、主简历/JD 定制、求职信生成和 PDF 导出。
  - 适合未来在 Synology 上用 Docker 单独试验。
- AIHawk：<https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk>
  - 约 30k stars，但已于 2026 年 5 月归档，公开仓库缺少第三方申请插件。
  - 可作为架构参考，不作为自动提交依赖。
- CoverLetterGPT：<https://github.com/vincanger/coverlettergpt>
  - 专门的求职信和职位管理应用，但引入 Wasp、PostgreSQL、身份验证、支付和外部 API 等复杂性，而当前本地工作流并不需要。
- tailor-resume：<https://github.com/narendranathe/tailor-resume>
  - 有意思的确定性 LaTeX/证据方案和相关性门槛，但目前项目规模较小；可借鉴设计，不作为核心依赖。

## 集成边界

未来如需集成外部工具，必须：

1. 在单独环境/容器中以固定版本运行。
2. 接收简历副本，绝不接收唯一规范源文件。
3. 在 `private_data/cv/variants/` 下生成可审计差异或新的变体。
4. 绝不加入未经核实的技能、指标、雇主或资质。
5. 绝不发送申请；未经人工审阅绝不覆盖 `current.tex`。
