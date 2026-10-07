-- 公开空白 database_template，不含候选人或申请数据。
-- 根据内存中的 ensure_schema + ensure_campaign_schema 整理而来。
-- 每列均附有填写说明。SQL NULL 表示未知；日期使用 UTC。
-- 仅供参考/演示。正常使用流程：jobbot init，然后运行相应业务命令。
-- 不要将其用于覆盖生产库的恢复操作。实际 SQL 请使用 shared connect()。
PRAGMA foreign_keys = ON;
BEGIN;

-- application_campaign_jobs: 字段填写说明
-- campaign_id: application_campaigns.id 外键。
-- job_id: 真实 jobs.id 外键，先确保岗位存在。
-- application_id: 真实 applications.id 外键，不能填 CDP target。
-- rank: 批次内整数审阅顺序，不证明资格。
-- status: 批次成员状态，如selected；submitted只在申请已证实成功后同步。
-- material_bundle: 已生成的私有材料目录路径。
-- last_error: 最后错误，须脱敏，不含凭据。
CREATE TABLE IF NOT EXISTS application_campaign_jobs (
          campaign_id INTEGER NOT NULL REFERENCES application_campaigns(id),
          job_id INTEGER NOT NULL REFERENCES jobs(id),
          application_id INTEGER REFERENCES applications(id),
          rank INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'selected',
          material_bundle TEXT,
          last_error TEXT,
          PRIMARY KEY(campaign_id, job_id)
        );

-- application_campaigns: 字段填写说明
-- id: 自动整数主键；新增时省略，迁移时保留关联。
-- name: 公司正式名称或批次名称，按本表用途填写。
-- target_count: 批次计划数量，正整数，不等于提交数。
-- geographic_scope: 本批次经确认的地区标签。
-- min_score: 批次分数门槛，仍需审阅 JD。
-- status: 批次状态，如planned；不是某岗submitted或投递总数。
-- created_at: 创建 UTC 时间，由程序生成。
-- updated_at: 修改 UTC 时间，由业务程序维护。
CREATE TABLE IF NOT EXISTS application_campaigns (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          target_count INTEGER NOT NULL,
          geographic_scope TEXT NOT NULL,
          min_score INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'planned',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

-- application_events: 字段填写说明
-- id: 自动整数主键；新增时省略，迁移时保留关联。
-- application_id: 真实 applications.id 外键，不能填 CDP target。
-- event_type: 审计事件名，例如 queued/manual_submission_confirmed。
-- details_json: 事件结构化 JSON，记录来源及变化，不含凭据。
-- created_at: 创建 UTC 时间，由程序生成。
CREATE TABLE IF NOT EXISTS application_events (
  id INTEGER PRIMARY KEY,
  application_id INTEGER NOT NULL REFERENCES applications(id),
  event_type TEXT NOT NULL,
  details_json TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- application_limits: 字段填写说明
-- id: 自动整数主键；新增时省略，迁移时保留关联。
-- company: 准确公司名称，用于查询和配额匹配。
-- category: 官方配额适用类别，all 只用于确有全公司限制。
-- window_start: 官方配额窗口开始日期 YYYY-MM-DD。
-- window_end: 官方配额窗口结束日期 YYYY-MM-DD。
-- max_applications: 官方最大申请数，正整数。
-- max_preferences_per_application: 每份申请最大志愿数；未知 NULL。
-- enforcement: hard 为硬限制，advisory 为建议，按官方规则。
-- source_url: 支持本规则的官方公开 URL。
-- notes: 业务备注，真实信息只进入私有库。
-- updated_at: 修改 UTC 时间，由业务程序维护。
CREATE TABLE IF NOT EXISTS application_limits (
  id INTEGER PRIMARY KEY,
  company TEXT NOT NULL,
  category TEXT NOT NULL,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  max_applications INTEGER NOT NULL CHECK (max_applications > 0),
  max_preferences_per_application INTEGER,
  enforcement TEXT NOT NULL DEFAULT 'hard'
    CHECK (enforcement IN ('hard', 'advisory')),
  source_url TEXT,
  notes TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (company, category, window_start, window_end)
);

-- applications: 字段填写说明
-- id: 自动整数主键；新增时省略，迁移时保留关联。
-- job_id: 真实 jobs.id 外键，先确保岗位存在。
-- status: queued/draft/manual_required/submitted等申请状态；submitted仅凭真实回执或本人明确确认。
-- tailored_resume_path: 已审阅职位 PDF 路径，执行浏览器的机器须可读。
-- cover_letter_path: 已审阅求职信路径；未生成 NULL。
-- profile_path: 本次申请私有 profile 文件路径。
-- browser_state_path: 本机私有浏览器状态路径，不公开。
-- draft_url: 正式草稿 URL，不证明提交。
-- answers_json: 本次已确认答案 JSON；未知/法律答案不猜测。
-- field_report_json: 适配器生成的填表检查 JSON。
-- last_error: 最后错误，须脱敏，不含凭据。
-- confirmation_number: 正式收件确认编号，无证据 NULL。
-- submitted_at: 已确认提交 UTC 时间；未知 NULL，区别官方时间与记录时间。
-- notes: 业务备注，真实信息只进入私有库。
-- created_at: 创建 UTC 时间，由程序生成。
-- updated_at: 修改 UTC 时间，由业务程序维护。
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  status TEXT DEFAULT 'draft',
  tailored_resume_path TEXT,
  cover_letter_path TEXT,
  profile_path TEXT,
  browser_state_path TEXT,
  draft_url TEXT,
  answers_json TEXT,
  field_report_json TEXT,
  last_error TEXT,
  confirmation_number TEXT,
  submitted_at TEXT,
  notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- browser_tabs: 字段填写说明
-- application_id: 真实 applications.id 外键，不能填 CDP target。
-- browser_mode: 本机 local_persistent/windows_cdp 模式。
-- target_id: 本机当前 CDP target，生命周期结束即失效。
-- tab_label: 程序生成的持久 tab label，用于重识别。
-- job_fingerprint: 正式岗位身份 fingerprint，由程序生成。
-- canonical_url: 正式岗位 canonical URL。
-- current_url: 最近页面 URL，可能是登录/草稿/确认页。
-- page_title: 最近页面标题，不能仅凭它确认提交。
-- state: 本机 tab registry 状态，例如 active。
-- resolution_method: 本次匹配 target/label/fingerprint 的方式。
-- created_at: 创建 UTC 时间，由程序生成。
-- last_seen_at: 最近检查 tab 的 UTC 时间。
CREATE TABLE IF NOT EXISTS browser_tabs (
  application_id INTEGER PRIMARY KEY REFERENCES applications(id),
  browser_mode TEXT NOT NULL,
  target_id TEXT,
  tab_label TEXT NOT NULL,
  job_fingerprint TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  current_url TEXT,
  page_title TEXT,
  state TEXT NOT NULL DEFAULT 'active',
  resolution_method TEXT,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

-- companies: 字段填写说明
-- id: 自动整数主键；新增时省略，迁移时保留关联。
-- name: 公司正式名称或批次名称，按本表用途填写。
-- website: 官方公司公开 URL；未知 NULL。
-- notes: 业务备注，真实信息只进入私有库。
-- created_at: 创建 UTC 时间，由程序生成。
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  website TEXT,
  notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- job_strategy_reviews: 字段填写说明
-- job_id: 真实 jobs.id 外键，先确保岗位存在。
-- track: 当前 strategy 的审阅轨道名称。
-- policy_hash: 策略 JSON 的 SHA-256，由程序生成。
-- score: 策略排序分数，不证明资格。
-- tier: A/B/C 审阅优先级，不代表提交。
-- foundation: 角色基础方向，由策略识别。
-- eligibility_note: 有来源的资格条件与待确认说明。
-- review_state: review_required 等真实审阅状态，不是 submitted。
-- updated_at: 修改 UTC 时间，由业务程序维护。
CREATE TABLE IF NOT EXISTS job_strategy_reviews (
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  track TEXT NOT NULL,
  policy_hash TEXT NOT NULL,
  score INTEGER NOT NULL,
  tier TEXT NOT NULL,
  foundation TEXT NOT NULL,
  eligibility_note TEXT NOT NULL,
  review_state TEXT NOT NULL DEFAULT 'review_required',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (job_id, track)
);

-- jobs: 字段填写说明
-- id: 自动整数主键；新增时省略，迁移时保留关联。
-- company_id: 关联 companies.id；未知 NULL，不伪造 ID。
-- title: 正式岗位标题，必填。
-- location: 官方岗位所在地；不是候选人住址。
-- url: 稳定官方岗位 URL，唯一去重标识。
-- platform: 实际采集/ATS 平台标识，例如 greenhouse。
-- role_kind: internship/full_time/unknown 等已分类类型。
-- recruitment_category: 招聘项目类别，须与官方配额类别对应。
-- description: 正式 JD，保留所有资格条件。
-- status: 发现/处理状态，如new/collected；勿以本列submitted统计成功申请。
-- fit_score: 评分器生成的相关性分数，不证明资格。
-- score_reason: 评分理由，由 scoring 生成。
-- published_at: 官方发布日期；未知 NULL。
-- inactive_since: 程序首次 inactive 时间；不等于已核实关闭。
-- resume_version: 选择的版本标签或源文件名。
-- created_at: 创建 UTC 时间，由程序生成。
-- updated_at: 修改 UTC 时间，由业务程序维护。
-- source_name: 公共source配置的准确name；手动CSV来源为manual_csv。
-- company: 准确公司名称，用于查询和配额匹配。
-- external_id: 官方职位 ID，与 URL 核对。
-- content_hash: 程序生成的内容摘要，不人工填写。
-- first_seen: 第一次收录 UTC 时间，迁移保留原值。
-- last_seen: 最后成功观测 UTC 时间，失败时不刷新。
-- is_active: 1 活跃，0 非活跃，由完整快照和 lifecycle guard 管理。
-- raw_json: 原始来源 JSON，仅存私有库。
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  company_id INTEGER REFERENCES companies(id),
  title TEXT NOT NULL,
  location TEXT,
  url TEXT UNIQUE,
  platform TEXT,
  role_kind TEXT,
  recruitment_category TEXT,
  description TEXT,
  status TEXT DEFAULT 'collected',
  fit_score INTEGER,
  score_reason TEXT,
  published_at TEXT,
  inactive_since TEXT,
  resume_version TEXT DEFAULT 'current.tex',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
,
  source_name TEXT,
  company TEXT,
  external_id TEXT,
  content_hash TEXT,
  first_seen TEXT,
  last_seen TEXT,
  is_active INTEGER DEFAULT 1,
  raw_json TEXT
);

-- resume_versions: 字段填写说明
-- id: 自动整数主键；新增时省略，迁移时保留关联。
-- label: 简历版本名称，例如 current/visa。
-- tex_path: 实际私有 TeX 路径，不公开真实文件。
-- target_role: 简历版本对应角色方向。
-- notes: 业务备注，真实信息只进入私有库。
-- created_at: 创建 UTC 时间，由程序生成。
CREATE TABLE IF NOT EXISTS resume_versions (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL UNIQUE,
  tex_path TEXT NOT NULL,
  target_role TEXT,
  notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- scan_runs: 字段填写说明
-- id: 自动整数主键；新增时省略，迁移时保留关联。
-- source_name: 公共source配置的准确name；手动CSV来源为manual_csv。
-- started_at: 本轮扫描开始 UTC 时间。
-- finished_at: 本轮结束 UTC 时间，未结束 NULL。
-- status: running/ok/error扫描状态；这里禁止用submitted表示投递。
-- jobs_seen: 本轮实际读取数量，不表示全站覆盖。
-- jobs_new: 本轮新增去重岗位数，程序计算。
-- error: 脱敏来源错误，失败不是空成功快照。
CREATE TABLE IF NOT EXISTS scan_runs (
          id INTEGER PRIMARY KEY,
          source_name TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          status TEXT NOT NULL,
          jobs_seen INTEGER DEFAULT 0,
          jobs_new INTEGER DEFAULT 0,
          error TEXT
        );

CREATE INDEX IF NOT EXISTS idx_application_events_application_id ON application_events(application_id);
CREATE INDEX IF NOT EXISTS idx_application_limits_company_window
  ON application_limits(company, window_start, window_end);
CREATE INDEX IF NOT EXISTS idx_applications_job_id ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_browser_tabs_target_id ON browser_tabs(target_id);

COMMIT;
-- 不包含 INSERT 语句：业务记录数为零。
