CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  website TEXT,
  notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

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
);

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

CREATE TABLE IF NOT EXISTS application_events (
  id INTEGER PRIMARY KEY,
  application_id INTEGER NOT NULL REFERENCES applications(id),
  event_type TEXT NOT NULL,
  details_json TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_applications_job_id ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_application_events_application_id ON application_events(application_id);

-- 按当前筛选策略重建审阅队列。此操作不会断言申请资格，也不会更改申请提交状态。
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

-- 上限适用于一个雇主、招聘类别和提交时段。
-- 仅当雇主明确规定公司级上限时，才使用类别 'all'。
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

CREATE INDEX IF NOT EXISTS idx_application_limits_company_window
  ON application_limits(company, window_start, window_end);

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

CREATE INDEX IF NOT EXISTS idx_browser_tabs_target_id ON browser_tabs(target_id);

CREATE TABLE IF NOT EXISTS resume_versions (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL UNIQUE,
  tex_path TEXT NOT NULL,
  target_role TEXT,
  notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO resume_versions(label, tex_path, target_role, notes)
VALUES
  ('current', 'current.tex', '26fall hardware/digital design internship', 'Default internship resume'),
  ('visa', 'visa.tex', 'visa/administrative review', 'Academic background version');
