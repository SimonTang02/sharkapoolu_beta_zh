# Penn 官方求职渠道访问

## 官方入口

- [Penn Career Services: Handshake](https://careerservices.upenn.edu/resources/handshake/)
- [PennKey 登录](https://upenn.joinhandshake.com/login)
- [学生职位](https://upenn.joinhandshake.com/job-search)
- [Penn 招聘活动](https://careerservices.upenn.edu/events/)

配置的来源为 **UPenn Handshake Hardware and IC Careers**。Penn 是访问机构，不是雇主。可用时从职位详情读取实际雇主/地点；缺失事实保持未知。活动只是参考链接，不会作为职位写入 SQLite。

## 学生浏览器集成与 EDU API 的区别

`handshake` 适配器通过现有 Windows JobApplyChrome 会话读取学生页面。它**不是官方 API 客户端**。它不使用 PennKey 密码、不导出 cookie、不调用未公开的内部端点，也不提交申请。PennKey、Duo/MFA、首次登录引导和验证挑战都必须由用户完成。

Handshake 也提供[官方 EDU API](https://support.joinhandshake.com/hc/en-us/articles/31061076506391-Getting-Started-with-EDU-API)。设置需要由 Handshake Support 签发开发者账户、获批订阅和已启用 API key。Penn 学生登录并不是该 API 的授权。我们没有假设或启用任何机构 API 访问。

## 运行

1. 在 JobApplyChrome 中打开上方 PennKey 登录链接并完成登录。
2. 从项目根目录运行 `make workflow WORKFLOW=penn_refresh`。
3. 检查来源扫描状态及重新生成的周报。

如只采集此来源而不重建全部报告：

```bash
python3 job_bot/bot.py scan --config job_bot/config.china_hk_ic_foreign.json \
  --source "UPenn Handshake Hardware and IC Careers"
```

常规每日/浏览器刷新和会话审计也包含该来源。它通过已验证的 `query` URL 参数读取硬件、ASIC 和 RTL 关键词搜索，并在异步加载后等待所请求的查询和稳定卡片。所有查询共用总预算，并使用可配置的页面/职位上限；规范化 Handshake 职位 ID，并沿用已有筛选/评分。不会根据 Penn 入口伪造机构或地点。现有策略规则仍决定职位是否适用于 US Summer 2027 或 China/HK。

必须设置 `sync_active=false`：有边界的个性化搜索结果不是完整职位库存，不能证明缺失职位已关闭。登录过期、浏览器挑战、未知标记或分页错误会产生错误，不会伪装成成功空扫描。不会导航其他用户/浏览器 Agent 的标签；适配器使用 `new_scan_page`，并遵守明确登记的 `JOBBOT_SCAN_TARGET_ID`。

## 验证

单元测试覆盖路由、URL 身份、缺失事实、认证边界、异步搜索稳定性、会话审计和采集器/配置/工作流行为。未登录请求必须报告 `Handshake login required`，不得返回成功空扫描。私有运行证据保存在 `private_data/outputs/job_bot/handshake_validation/`（`result.json` 和 `completed_refresh.log`）。这仍然是有边界的学生网站职位读取，不是机构 EDU API 访问，也不代表覆盖完整。
