# 贡献指南

使用 Python 3.10 或更新版本。初始化可编辑开发环境和脱敏本地配置：

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
```

只有涉及浏览器的更改才运行 `./scripts/bootstrap.sh --with-browser`。

提交 pull request 前运行：

```bash
make release-check
make private-check
```

全新模板中的私有配置检查可能报告空字段警告，但不得有错误。提交或 issue 中绝不能包含真实简历、候选人身份信息、凭据、cookie、浏览器状态、数据库、截图或生成的申请材料。

新增采集器放在 `job_bot/sources/`；新增招聘网站行为放在 `application_bot/` 或 `job_bot/applications/`。添加确定性的夹具测试，并确保单元测试不访问网络。申请适配器必须保留人工审阅门槛，绝不能默认执行最终提交。

仓库不变量见 `AGENTS.md`，扩展契约见 `docs/interfaces.md`。功能开发请在英文主开发仓库进行；本中文仓库维护翻译并与上游保持兼容。
