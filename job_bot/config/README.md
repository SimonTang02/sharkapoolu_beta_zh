# 组合式配置

`jobbot.json` 是规范的组合配置根。旧版 `../config.china_hk_ic_foreign.json` 会包含它，以保持既有命令兼容。

## 各文件职责

| 文件 | 负责内容 | 不负责内容 |
|---|---|---|
| `runtime.json` | 数据库、重试/并发、生命周期保护、浏览器、报告和邮件运行参数 | 岗位偏好 |
| `workflows.json` | 可独立运行模块的具名序列 | 凭据或网站内选择器 |
| `sources.json` | 端点、分页、来源筛选、来源类别和活跃同步策略 | 全局排序 |
| `scoring.json` | 广义相关性：Foundation 分数和简历证据修正项 | 毕业/地点资格 |
| `strategy.json` | 当前两条轨道的窄口径申请资格和优先级 | HTTP/浏览器机制 |
| `portals.json` | ATS 识别、登录探测和适配器路由 | 候选人事实 |
| `field_mappings.json` | 逻辑表单映射和禁止提交的安全策略 | 凭据 |

评分有意分为两层。`scoring` 判断“职位是否广义符合候选人方向”，并写入 `jobs.fit_score`。`strategy` 判断“按当前 campaign，此岗位是否符合资格并值得申请”，会应用地理位置、毕业学期、学位、资历和轨道专属排序。检索关键词不能暗中绕过策略排除项。

合并规则确定且固定：

1. 按列出的顺序加载 includes，路径相对于包含它的文件。
2. 递归合并对象。
3. 后出现的列表和标量替换前值。
4. 最后应用当前包含文件自身。
5. 文件合并后再应用 `patches`。
6. 开始工作前会拒绝 include 循环、含糊 patch、重复 ID、无效分数/工作流、不安全的生命周期参数和启用最终提交策略的配置。

## 小范围、可审阅的实验

只调整一个值时，不要复制全部 `sources.json` 或 `scoring.json`。覆盖配置可 patch 恰好一个具名列表项：

```json
{
  "includes": ["../jobbot.json"],
  "experiment": {
    "name": "rtl_cdc_v1",
    "hypothesis": "CDC should improve direct digital-design ranking"
  },
  "patches": [
    {
      "path": "scoring.foundation_groups",
      "match": {"name": "digital RTL design"},
      "set": {
        "base_score": 76,
        "keywords": {
          "$append": ["CDC", "clock-domain crossing"],
          "$remove": ["an obsolete keyword"]
        }
      }
    }
  ]
}
```

`$append` 会去重，`$remove` 会移除精确值，`$replace` 用于显式替换列表。patch 必须恰好匹配一个对象；拼写错误会报错，不会静默跳过。可运行示例见 `experiments/keyword_tuning.example.json`。

验证并查看生效配置：

```bash
make config-check
make config-check CONFIG=job_bot/config/experiments/keyword_tuning.example.json
```

不更改 SQLite，评估关键词/权重变化：

```bash
make workflow \
  WORKFLOW=score_experiment \
  CONFIG=job_bot/config/experiments/keyword_tuning.example.json
```

实验报告会记录 effective-config SHA-256、分数分布、最大变化值及高分职位。审阅报告后才使用显式 `rescore` 模块。

## 可独立运行的模块

`workflows.json` 定义具名工作流：

- `daily`：现有完整每日流程。
- `digest_24h`：只根据 SQLite 构建/发送滚动 24 小时摘要。
- `report_only`：不访问网络或写入数据库评分，只重建所有报告。
- `weekly_only`：只访问数据库重建周报。
- `http_refresh`：HTTP 来源、重新评分、评分报告、策略报告和周报。
- `browser_refresh`：CDP 来源后生成相同报告。
- `score_experiment`：使用覆盖配置进行只读分数模拟。
- `login_audit`：只读登录/会话探测。

运行前可先预览精确 argv 命令，或直接执行：

```bash
make workflow-plan WORKFLOW=http_refresh
make workflow WORKFLOW=http_refresh
```

运行器会写入 `module_run_*.json`，记录配置哈希、选择器、命令、耗时和返回码，不包含凭据值。

低层扫描器也可通过命令行选择来源，无需编辑 JSON：

```bash
python3 job_bot/bot.py scan --source-browser http --max-workers 3
python3 job_bot/bot.py scan --company NVIDIA
python3 job_bot/bot.py scan \
  --source-category official_company_us_2027_internship
python3 job_bot/bot.py scan --source-type workday --exclude-source "NXP Greater China IC Design"
```

不同字段的选择条件会相交；同一字段内重复的值表示任选其一。已禁用来源不会被选择。

## 值得调整的参数

- `scan.max_workers`：网络并发数。J1900 从 3 开始；当前工作站从 8 开始。
- `retry_attempts` / `retry_backoff_seconds`：恢复瞬时网络失败，不得用于绕过认证或反机器人控制。
- 每来源的 `max_pages`、`page_size`、`request_delay_seconds`：采集负载和覆盖范围。
- Foundation 的 `base_score`、`body_only_adjustment`、`min_body_hits`：奖励前岗位的基础类别分。
- Modifier 的 `points`、`scope`、`keywords`：简历证据、偏好和明确扣分项。
- `scoring.bands`：广义相关性报告/队列分段。
- `strategy.minimum_score`、`tier_thresholds`、`tracks` 和 `patterns`：最终 campaign 资格和优先级。

`sync_active` 不是普通调参开关。它表示来源成功响应构成完整快照，缺失职位可能被标记为不活跃。运行时生命周期保护器会在更改活跃状态前拒绝空快照或异常小的快照。只有确认该来源分页完整后，才可按来源覆盖阈值。

密钥和候选人事实绝不能放在这里。它们应保存在被忽略的 `private_data/` 目录树中，并通过 `private_paths.py` 引用。
