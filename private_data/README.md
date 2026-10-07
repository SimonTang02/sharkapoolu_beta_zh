# 候选人私有数据

除本 README 外，本目录下所有内容均由主 Git 仓库忽略。这里是候选人身份、凭据、浏览器会话、申请证据、生成简历/求职信及申请数据库的规范存放位置。

默认目录布局：

- `credentials/passport.env`
- `profiles/application_profile.json`
- `browser/state/` 和 `browser/profiles/`
- `database/`
- `cv/source/` 和 `cv/build/`
- `cv/profile/`（证据和个人定位）、`cv/variants/` 与 `cv/reports/`
- `cv/archive/`
- `outputs/job_bot/` 和 `outputs/application_bot/`

在仓库根目录创建受维护文件并验证：

```bash
python3 -m job_bot.private_config init
python3 -m job_bot.private_config check
```

`init` 会从 `examples/` 复制脱敏文件、设置权限 `600`，并保留已有文件。对应契约位于 `schemas/`。只有确实要用空白示例替换受维护私有文件时才使用 `--force`。

数据职责有意分开：

- `profiles/application_profile.json` 是填入申请表单的事实权威来源。
- `cv/profile/evidence_profile.json` 是简历声明和定位的权威来源。
- `cv/profile/application_keywords.json` 是有证据支持的技能与协作用语的权威来源。
- `credentials/passport.env` 保存密钥和会话指针。

为兼容用途，一些教育和身份事实会重复。更改后运行检查器，并检查生成文档的一致性。

运行命令前设置 `JOBBOT_PRIVATE_DIR` 可重定位整个目录树。应单独备份该目录，并通过文件系统或卷加密保护。将文件移入此处并不能从旧 Git 提交中移除个人信息；重写仓库历史是另一项会改变历史的操作。

所有字段说明见[`docs/configuration.md`](../docs/configuration.md)，结构评估见[`docs/private-data-audit.md`](../docs/private-data-audit.md)。
