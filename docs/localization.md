# 中文版维护与英文上游

本仓库是 Sharkapoolu 的简体中文发布版，面向中文用户提供说明、模板注释、命令行帮助和操作提示。

| 仓库 | 用途 |
| --- | --- |
| [sharkapoolu_beta](https://github.com/SimonTang02/sharkapoolu_beta) | 主开发仓库：功能开发、缺陷修复和接口演进在这里进行 |
| [sharkapoolu_beta_zh](https://github.com/SimonTang02/sharkapoolu_beta_zh) | 中文发布仓库：跟进上游版本、维护翻译并验证兼容性 |

此次中文版本基于英文上游提交 `bd90331c16d9f128f985928ae7752b49e8e758c9`。
机器可读的版本对应关系见 [upstream.json](../localization/upstream.json)。
保留上游历史便于核对来源，新的中文提交只推送到中文仓库。

## 翻译范围

安装、配置、交接、安全规则、开发说明、模板说明、命令行帮助和界面提示使用简体中文。
下列内容保持原有形式，以便程序继续运行、事实可核对：

- 文件路径、命令、参数、配置键、数据库列名、协议状态和程序接口。
- 岗位检索词、匹配规则、来源标识，以及需要匹配的官网英文问题和字段标签。
- 公司、产品和技术名称，如 Sharkapoolu、Python、SQLite、SystemVerilog、WSL、CDP。
- 用于验证英文门户和英文岗位的合成测试输入，以及由候选人和岗位要求决定语言的简历、求职信正文。
- [LICENSE](../LICENSE) 中的正式许可证文本及第三方资源版权信息；中文参考译文见 [许可证中文说明](license-zh-CN.md)。

使用中文版不改变个人资料的语言，不把英文经历自动改成中文事实，也不改变投递安全规则。
最终提交仍由本人完成，成功登记仍须来自真实回执或明确成功确认。

## 跟进上游

中英文各使用自己的目录和虚拟环境。两版命令行入口同名，不要在同一个虚拟环境内同时安装。
维护者可在中文工作副本添加英文上游：

```bash
git remote add upstream https://github.com/SimonTang02/sharkapoolu_beta.git
git fetch upstream
git log --oneline main..upstream/main
```

若已经存在 `upstream`，先核对地址，直接执行 `git fetch upstream`。
在新分支审查上游变化、处理翻译冲突并更新版本记录；不要用重置覆盖中文修改或私有资料。
业务修复先提交英文上游，再带入中文版本。每次发布都执行隐私审计、完整测试和配置检查：

```bash
make release-check
make history-audit
git diff --check
```

这些检查不会替代个人事实确认、PDF视觉审阅或真实门户验证。个人配置、原始简历、PDF、凭证、数据库和浏览器状态始终保留在私有目录中。
