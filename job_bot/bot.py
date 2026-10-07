#!/usr/bin/env python3
"""实习岗位监控工具。

本工具采取保守策略：采集、去重、评分并汇总职位。只有配置明确关闭
dry_run 且提供有效 SMTP 设置时，才会发送邮件。
"""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import math
import datetime as dt
import email.message
import html
from html.parser import HTMLParser
import http.client
import json
import os
import re
import smtplib
import socket
import sqlite3
import ssl
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / ".python_packages"
if LOCAL_PACKAGES.is_dir() and str(LOCAL_PACKAGES) not in sys.path:
    sys.path.insert(0, str(LOCAL_PACKAGES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.sources.third_party_boards import (  # noqa: E402
    prepare_jobsdb_hk_source,
    prepare_shixiseng_source,
    prepare_zhipin_source,
)
from job_bot.sources.campus_portals import prepare_cuhk_careers_source  # noqa: E402
from job_bot.config_loader import ConfigError, load_composed_config, validate_config  # noqa: E402
from job_bot.source_selector import (  # noqa: E402
    CDP_SOURCE_TYPES,
    select_sources,
    selector_from_cli,
    uses_browser,
)
from private_paths import DATABASE_DIR, JOBBOT_OUTPUT  # noqa: E402
from job_bot.shared_database import connect as connect_database  # noqa: E402
from job_bot.scan_browser import new_scan_page, close_scan_page  # noqa: E402

DEFAULT_CONFIG = ROOT / "job_bot" / "config.china_hk_ic_foreign.json"
DEFAULT_DB = DATABASE_DIR / "jobs.sqlite3"
DEFAULT_OUT = JOBBOT_OUTPUT
USER_AGENT = "26fall-intern-job-bot/0.1 (+local Synology-ready monitor)"
PLATFORM_COOKIE_ENV_VARS = {
    "jobsdb_hk": "PLATFORM_JOBSDB_HK_COOKIE",
    "zhipin": "PLATFORM_BOSS_ZHIPIN_COOKIE",
    "shixiseng": "PLATFORM_SHIXISENG_COOKIE",
    "cuhk_careers": "PLATFORM_CUHK_CAREERS_COOKIE",
}
LEGACY_PLATFORM_SESSION_ENV_VARS = {
    "jobsdb_hk": "JOBSDB_HK_SESSION",
    "zhipin": "BOSS_ZHIPIN_SESSION",
    "shixiseng": "SHIXISENG_SESSION",
    "cuhk_careers": "CUHK_CAREERS_SESSION",
}
PLATFORM_STORAGE_STATE_ENV_VARS = {
    "jobsdb_hk": "PLATFORM_JOBSDB_HK_STORAGE_STATE",
    "zhipin": "PLATFORM_BOSS_ZHIPIN_STORAGE_STATE",
    "shixiseng": "PLATFORM_SHIXISENG_STORAGE_STATE",
    "cuhk_careers": "PLATFORM_CUHK_CAREERS_STORAGE_STATE",
}

COOKIE_ATTRIBUTE_NAMES = {
    "domain",
    "path",
    "expires",
    "max-age",
    "samesite",
    "secure",
    "httponly",
}
LINKEDIN_ONLY_COOKIE_NAMES = {
    "bcookie",
    "bscookie",
    "li_at",
    "li_rm",
    "liap",
    "lidc",
}
GOOGLE_ONLY_COOKIE_PATTERNS = (
    re.compile(r"^(?:SID|HSID|SSID|APISID|SAPISID|NID)$", re.I),
    re.compile(r"^__Secure-(?:1P|3P)?(?:SID|APISID|SIDTS|SIDCC)$", re.I),
)


@dataclass
class JobPosting:
    source_name: str
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    external_id: str = ""
    platform: str = ""
    role_kind: str = "unknown"
    published_at: str = ""
    raw: dict[str, Any] | None = None


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): value for key, value in attrs}
        href = attr_map.get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = " ".join(" ".join(self._text).split())
            self.links.append((self._href, html.unescape(text)))
            self._href = None
            self._text = []


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = load_composed_config(path)
        # 小型 fixture 和旧版用户配置可能尚未定义 portal 或字段策略部分；
        # 仅当其中至少一项存在时才验证完整契约。
        if "portals" in config or "field_mappings" in config:
            validate_config(config)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
    config.setdefault("database", {})
    config.setdefault("scoring", {})
    config.setdefault("email", {})
    config.setdefault("sources", [])
    return config


def load_env_file(path: Path) -> None:
    """读取 KEY=VALUE 格式的机密配置，不覆盖进程环境变量。"""
    if not path.is_file():
        raise SystemExit(f"未找到环境文件： {path}")
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise SystemExit(f"环境文件中存在无效条目： {path}:{line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SystemExit(f"环境文件中存在无效的环境变量名称： {path}:{line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def db_path(config: dict[str, Any]) -> Path:
    raw = config.get("database", {}).get("path") or str(DEFAULT_DB)
    path = Path(raw)
    if not path.is_absolute() and path.parts[:2] == ("private_data", "database"):
        resolved = DATABASE_DIR.joinpath(*path.parts[2:]).resolve()
        if not resolved.is_relative_to(DATABASE_DIR.resolve()):
            raise ValueError("数据库路径超出规范的私有数据库目录")
        return resolved
    return path if path.is_absolute() else ROOT / path


def connect_db(config: dict[str, Any]) -> sqlite3.Connection:
    path = db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_database(path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    schema = ROOT / "job_bot" / "schema.sql"
    if schema.exists():
        conn.executescript(schema.read_text(encoding="utf-8"))
    add_columns(conn, "jobs", {
        "source_name": "TEXT",
        "company": "TEXT",
        "external_id": "TEXT",
        "content_hash": "TEXT",
        "first_seen": "TEXT",
        "last_seen": "TEXT",
        "published_at": "TEXT",
        "inactive_since": "TEXT",
        "is_active": "INTEGER DEFAULT 1",
        "raw_json": "TEXT",
        "role_kind": "TEXT",
        "recruitment_category": "TEXT",
    })
    add_columns(conn, "applications", {
        "profile_path": "TEXT",
        "browser_state_path": "TEXT",
        "draft_url": "TEXT",
        "answers_json": "TEXT",
        "field_report_json": "TEXT",
        "last_error": "TEXT",
        "confirmation_number": "TEXT",
        "updated_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
    })
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
          id INTEGER PRIMARY KEY,
          source_name TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          status TEXT NOT NULL,
          jobs_seen INTEGER DEFAULT 0,
          jobs_new INTEGER DEFAULT 0,
          error TEXT
        )
        """
    )
    conn.commit()


def add_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def request_timeout_seconds(source: dict[str, Any]) -> int:
    return int(source.get("timeout_seconds", 20))


def source_cookie_env_vars(source: dict[str, Any]) -> list[str]:
    explicit = str(source.get("cookie_env_var", "")).strip()
    if explicit:
        candidates = [explicit]
        for source_type, canonical in PLATFORM_COOKIE_ENV_VARS.items():
            if explicit == canonical:
                candidates.append(LEGACY_PLATFORM_SESSION_ENV_VARS[source_type])
                break
        if explicit.startswith("COMPANY_") and explicit.endswith("_COOKIE"):
            candidates.append(f"{explicit[:-len('_COOKIE')]}_SESSION")
        return candidates
    source_type = str(source.get("type", ""))
    platform_variable = PLATFORM_COOKIE_ENV_VARS.get(source_type)
    legacy_platform_variable = LEGACY_PLATFORM_SESSION_ENV_VARS.get(source_type)
    if platform_variable:
        return [platform_variable, legacy_platform_variable]
    company = str(source.get("company", "")).strip()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_").upper()
    return [f"COMPANY_{slug}_COOKIE", f"COMPANY_{slug}_SESSION"] if slug else []


def source_session_env_var(source: dict[str, Any]) -> str:
    """返回当前启用的 cookie 变量，同时保留旧的公开辅助函数名称。"""
    candidates = source_cookie_env_vars(source)
    return next(
        (name for name in candidates if name and os.environ.get(name, "").strip()),
        candidates[0] if candidates else "",
    )


def source_storage_state_env_var(source: dict[str, Any]) -> str:
    explicit = str(source.get("storage_state_env_var", "")).strip()
    if explicit:
        return explicit
    platform_variable = PLATFORM_STORAGE_STATE_ENV_VARS.get(
        str(source.get("type", ""))
    )
    if platform_variable:
        return platform_variable
    company = str(source.get("company", "")).strip()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_").upper()
    return f"COMPANY_{slug}_STORAGE_STATE" if slug else ""


def source_login_env_vars(source: dict[str, Any]) -> tuple[str, str]:
    candidates = source_cookie_env_vars(source)
    canonical_cookie = candidates[0] if candidates else ""
    if canonical_cookie.endswith("_COOKIE"):
        prefix = canonical_cookie[: -len("_COOKIE")]
        return f"{prefix}_USERNAME", f"{prefix}_PASSWORD"
    return "", ""


def cookie_names(cookie_header: str) -> set[str]:
    return {
        name.strip()
        for part in cookie_header.split(";")
        for name, separator, _value in [part.partition("=")]
        if separator and name.strip()
    }


def validate_cookie_for_source(
    source: dict[str, Any], cookie_header: str, variable: str
) -> None:
    names = cookie_names(cookie_header)
    attributes = sorted(name for name in names if name.lower() in COOKIE_ATTRIBUTE_NAMES)
    if attributes:
        raise ValueError(
            f"{variable} 看起来是 Set-Cookie 响应，而不是 Cookie 请求标头 "
            f"（检测到的属性：{', '.join(attributes)})"
        )

    raw_url = str(source.get("url") or source.get("host") or "")
    hostname = (urllib.parse.urlsplit(raw_url).hostname or "").lower()
    linkedin_names = sorted(names & LINKEDIN_ONLY_COOKIE_NAMES)
    if linkedin_names and not hostname.endswith("linkedin.com"):
        raise ValueError(
            f"{variable} 包含 LinkedIn 专用 cookie，但目标主机不是 LinkedIn："
            f"{hostname or '（未知）'}: {', '.join(linkedin_names)}. "
            "OAuth 流程请使用限定域名的 Playwright storage-state 文件。"
        )

    google_names = sorted(
        name
        for name in names
        if any(pattern.fullmatch(name) for pattern in GOOGLE_ONLY_COOKIE_PATTERNS)
    )
    if google_names and not (
        hostname.endswith("google.com") or hostname.endswith("youtube.com")
    ):
        raise ValueError(
            f"{variable} 包含 Google 专用 cookie，但目标主机不是 Google："
            f"{hostname or '（未知）'}: {', '.join(google_names)}"
        )


def source_headers(source: dict[str, Any]) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    cookie_env_var = source_session_env_var(source)
    if cookie_env_var:
        cookie = os.environ.get(cookie_env_var, "").strip()
        if cookie:
            validate_cookie_for_source(source, cookie, cookie_env_var)
            headers["Cookie"] = cookie
    for key, value in source.get("headers", {}).items():
        headers[str(key)] = str(value)
    return headers


def ssl_context(source: dict[str, Any]) -> ssl.SSLContext | None:
    if source.get("ssl_verify", True) is False:
        return ssl._create_unverified_context()
    return None


def http_get(
    url: str,
    timeout_seconds: int = 20,
    headers: dict[str, str] | None = None,
    context: ssl.SSLContext | None = None,
) -> bytes:
    for attempt in range(2):
        req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds, context=context) as response:
                return response.read()
        except http.client.IncompleteRead:
            if attempt:
                raise
    raise RuntimeError("不可达的 HTTP 重试状态")


def fetch_greenhouse(source: dict[str, Any]) -> list[JobPosting]:
    token = source["board_token"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    data = json.loads(
        http_get(url, request_timeout_seconds(source), source_headers(source), ssl_context(source)).decode("utf-8")
    )
    jobs = []
    for item in data.get("jobs", []):
        location = item.get("location") or {}
        job = JobPosting(
            source_name=source["name"],
            company=source.get("company") or token,
            title=item.get("title", "").strip(),
            url=item.get("absolute_url") or item.get("url") or "",
            location=location.get("name", "") if isinstance(location, dict) else str(location),
            description=strip_html(item.get("content", "")),
            external_id=str(item.get("id", "")),
            platform="greenhouse",
            raw=item,
        )
        job.role_kind = classify_role_kind(job, source)
        jobs.append(job)
    return filter_jobs(jobs, source)


def fetch_ashby(source: dict[str, Any]) -> list[JobPosting]:
    """通过 Ashby 文档说明的职位接口获取公开招聘信息。"""
    board = str(source["board_name"]).strip()
    url = f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(board)}"
    data = json.loads(
        http_get(url, request_timeout_seconds(source), source_headers(source), ssl_context(source)).decode("utf-8")
    )
    jobs = []
    for item in data.get("jobs", []):
        job = JobPosting(
            source_name=source["name"],
            company=source.get("company") or board,
            title=str(item.get("title") or "").strip(),
            url=str(item.get("jobUrl") or item.get("applyUrl") or "").strip(),
            location=str(item.get("location") or "").strip(),
            description=strip_html(
                str(item.get("descriptionHtml") or item.get("descriptionPlain") or "")
            ),
            external_id=str(item.get("id") or item.get("jobUrl") or ""),
            platform="ashby",
            published_at=str(item.get("publishedAt") or ""),
            raw=item,
        )
        job.role_kind = classify_role_kind(job, source)
        jobs.append(job)
    return filter_jobs(jobs, source)


def fetch_lever(source: dict[str, Any]) -> list[JobPosting]:
    company = source.get("board_token") or source["company"]
    api_host = source.get("api_host", "https://api.lever.co").rstrip("/")
    url = f"{api_host}/v0/postings/{company}?mode=json"
    data = json.loads(
        http_get(url, request_timeout_seconds(source), source_headers(source), ssl_context(source)).decode("utf-8")
    )
    jobs = []
    for item in data:
        categories = item.get("categories") or {}
        location = categories.get("location", "") if isinstance(categories, dict) else ""
        job = JobPosting(
            source_name=source["name"],
            company=source.get("display_company") or source.get("company") or company,
            title=item.get("text", "").strip(),
            url=item.get("hostedUrl") or item.get("applyUrl") or "",
            location=location,
            description=strip_html(item.get("descriptionPlain") or item.get("description", "")),
            external_id=str(item.get("id", "")),
            platform="lever",
            raw=item,
        )
        job.role_kind = classify_role_kind(job, source)
        jobs.append(job)
    return filter_jobs(jobs, source)


def fetch_jobsyn(source: dict[str, Any]) -> list[JobPosting]:
    """调用公开的 Jobsyn/Symphony Talent 搜索 API。"""
    api_url = source["api_url"]
    params = {str(key): str(value) for key, value in source.get("params", {}).items()}
    page_size = max(1, int(source.get("page_size", 10)))
    max_pages = max(1, int(source.get("max_pages", 10)))
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    for page_number in range(1, max_pages + 1):
        query = {**params, "page": str(page_number), "num_items": str(page_size)}
        url = f"{api_url}?{urllib.parse.urlencode(query)}"
        response = json.loads(
            http_get(
                url,
                request_timeout_seconds(source),
                source_headers(source),
                ssl_context(source),
            ).decode("utf-8")
        )
        items = response.get("jobs", []) if isinstance(response, dict) else []
        for item in items:
            external_id = str(item.get("guid") or item.get("reqid") or "").strip()
            if not external_id or external_id in seen_ids:
                continue
            seen_ids.add(external_id)
            location = str(item.get("location_exact") or "").strip()
            location_slug = re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-")
            title_slug = str(item.get("title_slug") or "job").strip("/")
            public_host = source.get("public_host", "").rstrip("/")
            job_url = f"{public_host}/{location_slug}/{title_slug}/{external_id}/job/"
            job = JobPosting(
                source_name=source["name"],
                company=source.get("company", source["name"]),
                title=str(item.get("title_exact") or "").strip(),
                url=job_url,
                location=location,
                description=strip_html(str(item.get("description") or "")),
                external_id=external_id,
                platform="jobsyn",
                raw=item,
            )
            job.role_kind = classify_role_kind(job, source)
            jobs.append(job)
        pagination = response.get("pagination", {})
        if not pagination.get("has_more_pages") or not items:
            break
    return filter_jobs(jobs, source)


def fetch_jibe(source: dict[str, Any]) -> list[JobPosting]:
    """调用公开的 Jibe 招聘 API，不依赖页面渲染出的职位卡片。"""
    api_url = source["api_url"]
    params = {str(key): str(value) for key, value in source.get("params", {}).items()}
    max_pages = max(1, int(source.get("max_pages", 20)))
    page_param = str(source.get("page_param", "page"))
    first_page = int(source.get("first_page", 1))
    page_size = max(1, int(source.get("page_size", 10)))
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()

    for page_number in range(first_page, first_page + max_pages):
        query = {**params, page_param: str(page_number)}
        url = f"{api_url}?{urllib.parse.urlencode(query)}"
        response = json.loads(
            http_get(
                url,
                request_timeout_seconds(source),
                source_headers(source),
                ssl_context(source),
            ).decode("utf-8")
        )
        data = response.get("data", response) if isinstance(response, dict) else {}
        wrapped_items = data.get("jobs", []) if isinstance(data, dict) else []
        if not wrapped_items:
            break

        for wrapped in wrapped_items:
            item = wrapped.get("data", wrapped) if isinstance(wrapped, dict) else {}
            if not isinstance(item, dict):
                continue
            external_id = str(item.get("req_id") or item.get("slug") or "").strip()
            if external_id and external_id in seen_ids:
                continue
            if external_id:
                seen_ids.add(external_id)
            slug = str(item.get("slug") or external_id).strip()
            detail_template = source.get("job_url_template")
            if detail_template and slug:
                job_url = str(detail_template).format(slug=slug, external_id=external_id)
            else:
                job_url = str(item.get("job_url") or item.get("apply_url") or "")
            description = " ".join(
                strip_html(str(item.get(field) or ""))
                for field in ("description", "responsibilities", "qualifications")
                if item.get(field)
            )
            location = str(
                item.get("full_location")
                or item.get("short_location")
                or item.get("location_name")
                or ", ".join(
                    str(item.get(field) or "").strip()
                    for field in ("city", "country")
                    if item.get(field)
                )
            ).strip()
            job = JobPosting(
                source_name=source["name"],
                company=source.get("company", source["name"]),
                title=str(item.get("title") or "").strip(),
                url=job_url,
                location=location,
                description=description,
                external_id=external_id or slug or job_url,
                platform="jibe",
                raw=item,
            )
            job.role_kind = classify_role_kind(job, source)
            jobs.append(job)

        total = int(data.get("totalCount") or data.get("count") or 0)
        if total and page_number - first_page + 1 >= math.ceil(total / page_size):
            break
        if len(wrapped_items) < page_size:
            break
    return filter_jobs(jobs, source)


def fetch_eightfold(source: dict[str, Any]) -> list[JobPosting]:
    """从 Eightfold 招聘网站获取无需登录的公开搜索结果。"""
    host = source["host"].rstrip("/")
    domain = source["domain"]
    search_texts = source.get("search_texts") or [source.get("search_text", "")]
    locations = source.get("locations") or [source.get("location", "")]
    page_size = max(1, int(source.get("page_size", 10)))
    max_pages = max(1, int(source.get("max_pages", 20)))
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    request_delay = max(0.0, float(source.get("request_delay_seconds", 0)))
    made_request = False

    for search_text in search_texts:
        for location_query in locations:
            for page_number in range(max_pages):
                if made_request and request_delay:
                    time.sleep(request_delay)
                params = {
                    "domain": domain,
                    "query": str(search_text),
                    "location": str(location_query),
                    "start": str(page_number * page_size),
                }
                url = f"{host}/api/pcsx/search?{urllib.parse.urlencode(params)}"
                response = json.loads(
                    http_get(
                        url,
                        request_timeout_seconds(source),
                        source_headers(source),
                        ssl_context(source),
                    ).decode("utf-8")
                )
                made_request = True
                data = response.get("data", {}) if isinstance(response, dict) else {}
                items = data.get("positions", []) if isinstance(data, dict) else []
                if not items:
                    break
                for item in items:
                    external_id = str(item.get("id") or item.get("atsJobId") or "").strip()
                    if not external_id or external_id in seen_ids:
                        continue
                    seen_ids.add(external_id)
                    locations_value = item.get("locations") or item.get("standardizedLocations") or []
                    if isinstance(locations_value, str):
                        locations_value = [locations_value]
                    location = "; ".join(
                        dict.fromkeys(str(value).strip() for value in locations_value if value)
                    )
                    position_path = str(item.get("positionUrl") or "")
                    job_url = urllib.parse.urljoin(f"{host}/", position_path)
                    description = " ".join(
                        str(item.get(field) or "").strip()
                        for field in ("department", "displayJobId", "workLocationOption")
                        if item.get(field)
                    )
                    job = JobPosting(
                        source_name=source["name"],
                        company=source.get("company", source["name"]),
                        title=str(item.get("name") or item.get("title") or "").strip(),
                        url=job_url,
                        location=location,
                        description=description,
                        external_id=external_id,
                        platform="eightfold",
                        raw=item,
                    )
                    job.role_kind = classify_role_kind(job, source)
                    jobs.append(job)
                total = int(data.get("count") or 0)
                if total and (page_number + 1) * page_size >= total:
                    break
                if len(items) < page_size:
                    break
    return filter_jobs(jobs, source)


def fetch_apple_jobs(source: dict[str, Any]) -> list[JobPosting]:
    """读取 Apple 服务端渲染的搜索状态，其中包含实际工作地点。"""
    body = http_get(
        source["url"],
        request_timeout_seconds(source),
        source_headers(source),
        ssl_context(source),
    ).decode("utf-8", errors="replace")
    match = re.search(
        r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\((".*?")\);',
        body,
        re.S,
    )
    if not match:
        raise RuntimeError("Apple 搜索页面未提供公开搜索状态")
    hydration = json.loads(json.loads(match.group(1)))
    items = hydration.get("loaderData", {}).get("search", {}).get("searchResults", [])
    jobs: list[JobPosting] = []
    for item in items:
        location_parts: list[str] = []
        for location_item in item.get("locations") or []:
            name = str(location_item.get("name") or "").strip()
            country = str(location_item.get("countryName") or "").strip()
            value = ", ".join(value for value in (name, country) if value)
            if value and value not in location_parts:
                location_parts.append(value)
        external_id = str(item.get("reqId") or item.get("id") or item.get("positionId") or "")
        slug = str(item.get("transformedPostingTitle") or "")
        locale = str(item.get("localeInfo", {}).get("defaultLocaleCode") or "en-us")
        job_url = urllib.parse.urljoin(
            source["url"], f"/{locale}/details/{external_id}/{slug}"
        )
        job = JobPosting(
            source_name=source["name"],
            company=source.get("company", "Apple"),
            title=str(item.get("postingTitle") or "").strip(),
            url=job_url,
            location="; ".join(location_parts),
            description=str(item.get("jobSummary") or "").strip(),
            external_id=external_id,
            platform="apple_jobs",
            raw=item,
        )
        job.role_kind = classify_role_kind(job, source)
        jobs.append(job)
    return filter_jobs(jobs, source)


def fetch_oracle_candidate_experience(source: dict[str, Any]) -> list[JobPosting]:
    """调用 Oracle Recruiting Candidate Experience 无需登录的搜索 API。"""
    api_host = source["api_host"].rstrip("/")
    public_host = source.get("public_host", api_host).rstrip("/")
    site = source["site"]
    language = source.get("language", "en")
    search_texts = source.get("search_texts") or [source.get("search_text", "")]
    locations = source.get("locations") or [source.get("location", "")]
    page_size = max(1, int(source.get("page_size", 25)))
    max_pages = max(1, int(source.get("max_pages", 10)))
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    expand = (
        "requisitionList.workLocation,requisitionList.otherWorkLocations,"
        "requisitionList.secondaryLocations,requisitionList.requisitionFlexFields"
    )
    endpoint = f"{api_host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"

    for search_text in search_texts:
        for location_query in locations:
            for page_number in range(max_pages):
                offset = page_number * page_size
                finder = (
                    f"findReqs;siteNumber={site},limit={page_size},offset={offset},"
                    f"keyword={urllib.parse.quote(json.dumps(str(search_text)), safe='')},"
                    f"location={urllib.parse.quote(str(location_query), safe='')},"
                    "sortBy=RELEVANCY"
                )
                url = f"{endpoint}?onlyData=true&expand={expand}&finder={finder}"
                response = json.loads(
                    http_get(
                        url,
                        request_timeout_seconds(source),
                        source_headers(source),
                        ssl_context(source),
                    ).decode("utf-8")
                )
                result_sets = response.get("items", []) if isinstance(response, dict) else []
                if not result_sets:
                    break
                result_set = result_sets[0]
                items = result_set.get("requisitionList", [])
                if not items:
                    break
                for item in items:
                    external_id = str(item.get("Id") or "").strip()
                    if not external_id or external_id in seen_ids:
                        continue
                    seen_ids.add(external_id)
                    location_values = [str(item.get("PrimaryLocation") or "").strip()]
                    for field in ("otherWorkLocations", "secondaryLocations"):
                        for location_item in item.get(field) or []:
                            if isinstance(location_item, dict):
                                value = str(
                                    location_item.get("Name")
                                    or location_item.get("LocationName")
                                    or location_item.get("PrimaryLocation")
                                    or ""
                                ).strip()
                            else:
                                value = str(location_item).strip()
                            if value:
                                location_values.append(value)
                    location = "; ".join(dict.fromkeys(x for x in location_values if x))
                    description = " ".join(
                        strip_html(str(item.get(field) or ""))
                        for field in (
                            "ShortDescriptionStr",
                            "ExternalResponsibilitiesStr",
                            "ExternalQualificationsStr",
                            "JobFamily",
                            "JobType",
                        )
                        if item.get(field)
                    )
                    job = JobPosting(
                        source_name=source["name"],
                        company=source.get("company", source["name"]),
                        title=str(item.get("Title") or "").strip(),
                        url=f"{public_host}/{language}/sites/{site}/job/{external_id}/",
                        location=location,
                        description=description,
                        external_id=external_id,
                        platform="oracle_candidate_experience",
                        raw=item,
                    )
                    job.role_kind = classify_role_kind(job, source)
                    jobs.append(job)
                total = int(result_set.get("TotalJobsCount") or 0)
                if total and offset + page_size >= total:
                    break
                if len(items) < page_size:
                    break
    return filter_jobs(jobs, source)


def fetch_workday(source: dict[str, Any]) -> list[JobPosting]:
    host = source["host"].rstrip("/")
    tenant = source["tenant"].strip("/")
    site = source["site"].strip("/")
    limit = int(source.get("limit", 20))
    search_texts = source.get("search_texts") or [source.get("search_text", "")]
    jobs: list[JobPosting] = []
    seen_urls: set[str] = set()
    for search_text in search_texts:
        cxs_base = f"{host}/wday/cxs/{tenant}/{site}"
        endpoint = f"{cxs_base}/jobs"
        payload = json.dumps({"limit": limit, "offset": 0, "searchText": search_text}).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                **source_headers(source),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=request_timeout_seconds(source), context=ssl_context(source)) as response:
            data = json.loads(response.read().decode("utf-8"))
        for item in data.get("jobPostings", []):
            external_path = item.get("externalPath") or item.get("url", "")
            if external_path.startswith("http"):
                url = external_path
            else:
                url = f"{host}/en-US/{site}{external_path}"
            if url in seen_urls:
                continue
            seen_urls.add(url)
            location = item.get("locationsText") or ""
            description = " ".join(
                str(item.get(key, ""))
                for key in ("title", "locationsText", "bulletFields")
            )
            raw: dict[str, Any] = item
            if re.fullmatch(r"\s*\d+\s+Locations?\s*", location, re.I):
                try:
                    detail_url = (
                        external_path
                        if external_path.startswith("http")
                        else f"{cxs_base}{external_path}"
                    )
                    detail = json.loads(
                        http_get(
                            detail_url,
                            request_timeout_seconds(source),
                            source_headers(source),
                            ssl_context(source),
                        ).decode("utf-8")
                    )
                    info = detail.get("jobPostingInfo") or {}
                    location = expanded_workday_location(info, location)
                    detail_description = strip_html(info.get("jobDescription", ""))
                    if detail_description:
                        description = detail_description
                    raw = {"search": item, "detail": info}
                except Exception as exc:
                    raw = {
                        "search": item,
                        "location_detail_error": type(exc).__name__,
                    }
            job = JobPosting(
                source_name=source["name"],
                company=source.get("company") or tenant,
                title=(item.get("title") or "").strip(),
                url=url,
                location=location,
                description=description,
                external_id=str(item.get("bulletFields", [""])[0] if item.get("bulletFields") else external_path),
                platform="workday",
                raw=raw,
            )
            job.role_kind = classify_role_kind(job, source)
            jobs.append(job)
    return filter_jobs(jobs, source)


def expanded_workday_location(info: dict[str, Any], fallback: str = "") -> str:
    locations: list[str] = []
    primary = str(info.get("location") or "").strip()
    if primary:
        locations.append(primary)
    additional = info.get("additionalLocations") or []
    if isinstance(additional, str):
        additional = [additional]
    for value in additional:
        clean = str(value or "").strip()
        if clean and clean not in locations:
            locations.append(clean)
    return "; ".join(locations) or fallback


def post_json(source: dict[str, Any], url: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = {
        **source_headers(source),
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
    }
    if "referer" in source:
        headers["Referer"] = str(source["referer"])
    if "origin" in source:
        headers["Origin"] = str(source["origin"])
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=request_timeout_seconds(source), context=ssl_context(source)) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_hotjob(source: dict[str, Any]) -> list[JobPosting]:
    """获取 Hotjob/Wecruit 上公开的校园招聘和社会招聘职位列表。"""
    suite_key = source["suite_key"]
    endpoint = (
        f"https://wecruit.hotjob.cn/wecruit/positionInfo/listPosition/{suite_key}"
        "?iSaJAx=isAjax&request_locale=zh_CN"
    )
    recruit_types = [int(value) for value in source.get("recruit_types", [1, 2])]
    page_size = max(1, int(source.get("page_size", 15)))
    max_pages = max(1, int(source.get("max_pages", 50)))
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    for recruit_type in recruit_types:
        for page_number in range(1, max_pages + 1):
            payload = urllib.parse.urlencode(
                {
                    "isFrompb": "true",
                    "recruitType": recruit_type,
                    "pageSize": page_size,
                    "currentPage": page_number,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=payload,
                headers={
                    **source_headers(source),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json, text/plain, */*",
                },
                method="POST",
            )
            with urllib.request.urlopen(
                req,
                timeout=request_timeout_seconds(source),
                context=ssl_context(source),
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
            page_form = data.get("data", {}).get("pageForm", {})
            items = page_form.get("pageData", [])
            for item in items:
                external_id = str(item.get("postId") or item.get("externalKey") or "").strip()
                if not external_id or external_id in seen_ids:
                    continue
                seen_ids.add(external_id)
                base_url = str(source.get("public_url") or "").strip()
                job_url = url_with_query(
                    base_url,
                    postId=external_id,
                    recruitType=recruit_type,
                )
                description = " ".join(
                    str(item.get(field) or "").strip()
                    for field in (
                        "postTypeName",
                        "department",
                        "projectName",
                        "educationStr",
                        "workYears",
                    )
                    if item.get(field)
                )
                job = JobPosting(
                    source_name=source["name"],
                    company=str(item.get("company") or source.get("company") or source["name"]),
                    title=str(item.get("postName") or "").strip(),
                    url=job_url,
                    location=str(item.get("workPlaceStr") or "").strip(),
                    description=description,
                    external_id=external_id,
                    platform="hotjob",
                    raw=item,
                )
                if re.search(r"实习|實習|intern", job.title, re.I):
                    job.role_kind = "internship"
                else:
                    job.role_kind = classify_role_kind(job, source)
                    if job.role_kind == "unknown" and recruit_type in {1, 2}:
                        job.role_kind = "full_time"
                jobs.append(job)
            total_pages = int(page_form.get("totalPage") or 0)
            if not items or (total_pages and page_number >= total_pages):
                break
    return filter_jobs(jobs, source)


def fetch_zhiye(source: dict[str, Any]) -> list[JobPosting]:
    """获取公开的北森 iTalent/智业职位列表。"""
    api_url = source["api_url"]
    categories = [str(value) for value in source.get("categories", [])]
    page_size = max(1, int(source.get("page_size", 100)))
    max_pages = max(1, int(source.get("max_pages", 20)))
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    for page_index in range(max_pages):
        data = post_json(
            source,
            api_url,
            {
                "PageIndex": page_index,
                "PageSize": page_size,
                "Category": categories,
                "KeyWords": "",
                "SpecialType": 0,
                "PortalId": "",
                "DisplayFields": [
                    "Category",
                    "Kind",
                    "LocId",
                    "PostDate",
                    "WorkWeChatQrCode",
                ],
            },
        )
        items = data.get("Data", [])
        for item in items:
            external_id = str(item.get("JobAdId") or item.get("Id") or "").strip()
            if not external_id or external_id in seen_ids:
                continue
            seen_ids.add(external_id)
            public_url = url_with_query(
                str(source.get("public_url") or ""), jobId=external_id
            )
            location_values = item.get("LocNames") or []
            if isinstance(location_values, str):
                location_values = [location_values]
            job = JobPosting(
                source_name=source["name"],
                company=source.get("company", source["name"]),
                title=str(item.get("JobAdName") or "").strip(),
                url=public_url,
                location="; ".join(str(value).strip() for value in location_values if value),
                description=" ".join(
                    str(item.get(field) or "").strip()
                    for field in ("Duty", "Require", "Category", "Kind")
                    if item.get(field)
                ),
                external_id=external_id,
                platform="zhiye",
                raw=item,
            )
            job.role_kind = classify_role_kind(job, source)
            jobs.append(job)
        total = int(data.get("Count") or 0)
        if not items or (page_index + 1) * page_size >= total:
            break
    return filter_jobs(jobs, source)


def fetch_zhiye_html(source: dict[str, Any]) -> list[JobPosting]:
    """读取未提供新版 JSON API 的北森服务端渲染页面。"""
    host = str(source["host"]).rstrip("/")
    sections = [str(value).strip("/") for value in source.get("sections", ["Social"])]
    keywords = [str(value) for value in source.get("search_texts", [""])]
    max_pages = max(1, int(source.get("max_pages", 1)))
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    card_pattern = re.compile(
        r'<div\s+class=["\']item["\'][^>]*>\s*'
        r'<a\s+href=["\'](?P<href>[^"\']*jobId=(?P<id>\d+)[^"\']*)["\'][^>]*>.*?'
        r'<h3[^>]*>(?P<title>.*?)</h3>.*?'
        r'<span\s+class=["\']sx["\'][^>]*>(?P<location>.*?)</span>',
        re.I | re.S,
    )
    for section in sections:
        for keyword in keywords:
            for page_index in range(1, max_pages + 1):
                url = url_with_query(
                    f"{host}/{section}",
                    k=keyword,
                    PageIndex=page_index,
                )
                body = http_get(
                    url,
                    request_timeout_seconds(source),
                    source_headers(source),
                    ssl_context(source),
                ).decode("utf-8", errors="replace")
                matches = list(card_pattern.finditer(body))
                if not matches:
                    break
                for match in matches:
                    external_id = match.group("id")
                    if external_id in seen_ids:
                        continue
                    seen_ids.add(external_id)
                    title = strip_html(match.group("title"))
                    location = strip_html(match.group("location")).lstrip("| ")
                    job = JobPosting(
                        source_name=source["name"],
                        company=source.get("company", source["name"]),
                        title=title,
                        url=urllib.parse.urljoin(f"{host}/", match.group("href")),
                        location=location,
                        description=f"{title} {location}",
                        external_id=external_id,
                        platform="zhiye_html",
                        raw={"section": section, "keyword": keyword},
                    )
                    job.role_kind = (
                        "internship"
                        if section.lower() == "intern"
                        else classify_role_kind(job, source)
                    )
                    if job.role_kind == "unknown":
                        job.role_kind = "full_time"
                    jobs.append(job)
                if len(matches) < int(source.get("page_size", 10)):
                    break
    return filter_jobs(jobs, source)


def fetch_smartrecruiters(source: dict[str, Any]) -> list[JobPosting]:
    company_code = str(source["company_code"])
    limit = max(1, min(100, int(source.get("page_size", 100))))
    max_pages = max(1, int(source.get("max_pages", 20)))
    jobs: list[JobPosting] = []
    offset = 0
    for _ in range(max_pages):
        url = url_with_query(
            f"https://api.smartrecruiters.com/v1/companies/{company_code}/postings",
            limit=limit,
            offset=offset,
        )
        data = json.loads(
            http_get(
                url,
                request_timeout_seconds(source),
                source_headers(source),
                ssl_context(source),
            ).decode("utf-8")
        )
        items = data.get("content", [])
        for item in items:
            external_id = str(item.get("id") or "").strip()
            location = item.get("location") or {}
            description = " ".join(
                str((item.get(field) or {}).get("label") or "").strip()
                for field in ("industry", "department", "function", "typeOfEmployment")
            )
            job = JobPosting(
                source_name=source["name"],
                company=source.get("company", company_code),
                title=str(item.get("name") or "").strip(),
                url=f"https://jobs.smartrecruiters.com/{company_code}/{external_id}",
                location=str(location.get("fullLocation") or "").strip(),
                description=description,
                external_id=external_id,
                platform="smartrecruiters",
                raw=item,
            )
            job.role_kind = classify_role_kind(job, source)
            jobs.append(job)
        offset += len(items)
        if not items or offset >= int(data.get("totalFound") or 0):
            break
    return filter_jobs(jobs, source)


def fetch_cuhk_careers(source: dict[str, Any]) -> list[JobPosting]:
    api_url = source.get("api_url", "https://api.ai.cpdc.osa.cuhk.edu.hk/job/search")
    search_texts = source.get("search_texts") or [source.get("search_text", "ee")]
    limit = int(source.get("limit", 20))
    jobs: list[JobPosting] = []
    seen_urls: set[str] = set()
    for search_text in search_texts:
        payload = {
            "page": 0,
            "limit": limit,
            "keyword": search_text,
            "jobNatureList": source.get("job_nature_list", []),
            "industryList": source.get("industry_list", []),
            "statusList": source.get("status_list", []),
            "sortParam": source.get("sort_param", "SIMILARITY"),
        }
        data = post_json(source, api_url, payload)
        if data.get("status") != "success":
            raise RuntimeError(f"CUHK API 返回失败状态： {data.get('errorCode') or data.get('status')}")
        result = data.get("data") or {}
        for item in result.get("objectList", []):
            job_id = item.get("jobLinkJobId") or item.get("id") or ""
            url = item.get("applicationMethod") or f"https://cpdc.osa.cuhk.edu.hk/en/job/{job_id}"
            if url in seen_urls:
                continue
            seen_urls.add(url)
            industry = item.get("industry") or {}
            job_nature = item.get("jobNature") or {}
            description = "\n".join(
                str(value)
                for value in [
                    item.get("employmentMode", ""),
                    industry.get("name", "") if isinstance(industry, dict) else "",
                    job_nature.get("name", "") if isinstance(job_nature, dict) else "",
                    item.get("requirement", ""),
                    item.get("responsibility", ""),
                ]
                if value
            )
            job = JobPosting(
                source_name=source["name"],
                company=item.get("companyName") or source.get("company", source["name"]),
                title=(item.get("title") or "").strip(),
                url=url,
                location=item.get("location") or "",
                description=strip_html(description),
                external_id=str(job_id),
                platform="cuhk_careers",
                raw=item,
            )
            job.role_kind = classify_role_kind(job, source)
            jobs.append(job)
    return filter_jobs(jobs, source)


def fetch_rss(source: dict[str, Any]) -> list[JobPosting]:
    body = http_get(
        source["url"],
        request_timeout_seconds(source),
        source_headers(source),
        ssl_context(source),
    ).decode("utf-8", errors="replace")
    root = ET.fromstring(body)
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    jobs = []
    for item in items:
        title = find_xml_text(item, ["title"])
        location = ""
        if source.get("location_from_title"):
            location_match = re.match(r"^(.*)\s+\(([^()]*)\)\s*$", title)
            if location_match:
                title = location_match.group(1).strip()
                location = location_match.group(2).strip()
        link = find_xml_text(item, ["link"])
        if not link:
            link_el = item.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.attrib.get("href", "") if link_el is not None else ""
        desc = find_xml_text(item, ["description", "summary", "{http://www.w3.org/2005/Atom}summary"])
        job = JobPosting(
            source_name=source["name"],
            company=source.get("company", source["name"]),
            title=title,
            url=link,
            location=location,
            description=strip_html(desc),
            external_id=link or title,
            platform="rss",
            raw={"title": title, "url": link},
        )
        job.role_kind = classify_role_kind(job, source)
        jobs.append(job)
    return filter_jobs(jobs, source)


def fetch_html(source: dict[str, Any]) -> list[JobPosting]:
    base_url = source["url"]
    body = http_get(
        base_url,
        request_timeout_seconds(source),
        source_headers(source),
        ssl_context(source),
    ).decode("utf-8", errors="replace")
    parser = LinkParser()
    parser.feed(body)
    jobs = []
    seen_urls: set[str] = set()
    for href, text in parser.links:
        url = urllib.parse.urljoin(base_url, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        title = text or url
        job = JobPosting(
            source_name=source["name"],
            company=source.get("company", source["name"]),
            title=title,
            url=url,
            description=title,
            external_id=url,
            platform="html",
            raw={"title": title, "url": url},
        )
        job.role_kind = classify_role_kind(job, source)
        jobs.append(job)
    return filter_jobs(jobs, source)


def xiaomi_published_at(value: Any) -> str:
    """将 Xiaomi 的 YYYY-MM-DD 发布日期规范化为 ISO 时间戳。"""
    clean = str(value or "").strip()
    if not clean:
        return ""
    try:
        parsed = dt.datetime.strptime(clean[:10], "%Y-%m-%d")
        # 公开门户仅提供日期，没有时间或时区。明确按中国标准时间处理日期边界，
        # 不要将其伪装成精确的 UTC 发布时间。
        return parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=8))).isoformat()
    except ValueError:
        return ""


def fetch_xiaomi(source: dict[str, Any]) -> list[JobPosting]:
    """通过关键词分页调用 Xiaomi 官方公开职位搜索 API。"""
    api_url = str(
        source.get("api_url")
        or "https://hr.xiaomi.com/website/api/agent/searchJobPage"
    )
    page_size = max(1, min(100, int(source.get("page_size", 100))))
    max_pages = max(1, int(source.get("max_pages", 10)))
    search_texts = source.get("search_texts") or [""]
    jobs: list[JobPosting] = []
    seen_urls: set[str] = set()
    for search_text in search_texts:
        for page_number in range(1, max_pages + 1):
            page_url = url_with_query(
                api_url,
                keyword=str(search_text),
                cityZhNames="",
                pageSize=page_size,
                pageNum=page_number,
            )
            payload = json.loads(
                http_get(
                    page_url,
                    request_timeout_seconds(source),
                    source_headers(source),
                    ssl_context(source),
                ).decode("utf-8", errors="replace")
            )
            if int(payload.get("code", -1)) != 0:
                raise RuntimeError(
                    f"Xiaomi job API error: {payload.get('message') or payload.get('code')}"
                )
            data = payload.get("data") or {}
            items = data.get("list") or []
            for item in items:
                url = str(item.get("url") or "").strip()
                title = str(item.get("title") or "").strip()
                if not url or not title or url in seen_urls:
                    continue
                seen_urls.add(url)
                role_kind = {
                    1: "full_time",
                    2: "full_time",
                    3: "internship",
                    4: "full_time",
                }.get(item.get("type"), "unknown")
                description = strip_html(str(item.get("description") or ""))
                location = " / ".join(
                    str(value).strip()
                    for value in (item.get("cityZhNames") or [])
                    if str(value).strip()
                )
                job = JobPosting(
                    source_name=source["name"],
                    company=source.get("company", "Xiaomi"),
                    title=title,
                    url=url,
                    location=location,
                    description=description,
                    external_id=str(item.get("id") or url),
                    platform="xiaomi",
                    role_kind=role_kind,
                    published_at=xiaomi_published_at(item.get("publishTime")),
                    raw=item,
                )
                jobs.append(job)
            total = int(data.get("total") or 0)
            if not items or page_number * page_size >= total:
                break
    return filter_jobs(jobs, source)


def canonical_public_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def configured_cdp_connect_url(config: dict[str, Any]) -> str:
    browser_config = config.get("application_browser", {})
    if browser_config.get("mode") != "windows_cdp":
        raise RuntimeError(
            "此来源要求 application_browser.mode=windows_cdp"
        )
    cdp_config = browser_config.get("windows_cdp", {})
    env_name = str(cdp_config.get("url_env", "CHROME_CDP_URL"))
    endpoint = (
        os.environ.get(env_name, "").strip()
        or str(cdp_config.get("url", "")).strip()
    )
    if not endpoint:
        raise RuntimeError(f"缺少 Windows Chrome 端点： {env_name}")
    from job_bot.browser_connection import check_cdp_health

    return check_cdp_health(endpoint).connect_url


def fetch_jobsdb_cdp(source: dict[str, Any], config: dict[str, Any]) -> list[JobPosting]:
    from job_bot.applications.nvidia_workday import _playwright_api

    connect_url = configured_cdp_connect_url(config)
    max_pages = int(source.get("max_pages", 3))
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(connect_url, timeout=30_000)
        if not browser.contexts:
            raise RuntimeError("专用 Chrome 未提供持久化上下文")
        context = browser.contexts[0]
        page = new_scan_page(context)
        try:
            for page_number in range(1, max_pages + 1):
                page_url = url_with_query(
                    source["url"], page=page_number if page_number > 1 else None
                )
                response = page.goto(
                    page_url, wait_until="domcontentloaded", timeout=45_000
                )
                if response is not None and response.status >= 400:
                    raise RuntimeError(f"JobsDB 浏览器页面返回 HTTP {response.status}")
                try:
                    page.locator('article[data-testid="job-card"]').first.wait_for(
                        state="visible", timeout=20_000
                    )
                except Exception as exc:
                    body = page.locator("body").inner_text(timeout=3000).lower()
                    if "just a moment" in body or "security check" in body:
                        raise RuntimeError("JobsDB 浏览器会话触发了访问检查") from exc
                    if page_number == 1:
                        raise RuntimeError("JobsDB 未返回可见的职位卡片") from exc
                    break
                cards = page.locator('article[data-testid="job-card"]').evaluate_all(
                    """
                    cards => cards.map(card => {
                      const text = automation =>
                        (card.querySelector(`[data-automation="${automation}"]`)?.innerText || '')
                          .trim().replace(/\s+/g, ' ');
                      const link = card.querySelector('[data-automation="jobTitle"]');
                      return {
                        id: card.getAttribute('data-job-id') || '',
                        title: text('jobTitle'),
                        company: text('jobCompany'),
                        location: text('jobLocation') || text('jobCardLocation'),
                        summary: text('jobShortDescription'),
                        classification: text('jobSubClassification'),
                        card_text: (card.innerText || '').trim().replace(/\s+/g, ' '),
                        url: link?.href || ''
                      };
                    })
                    """
                )
                new_on_page = 0
                for card in cards:
                    external_id = str(card.get("id") or "").strip()
                    if not external_id or external_id in seen_ids:
                        continue
                    seen_ids.add(external_id)
                    new_on_page += 1
                    description = " ".join(
                        value
                        for value in (
                            card.get("summary", ""),
                            card.get("classification", ""),
                            card.get("card_text", ""),
                        )
                        if value
                    )
                    job = JobPosting(
                        source_name=source["name"],
                        company=card.get("company") or source.get("company", "JobsDB HK"),
                        title=str(card.get("title") or "").strip(),
                        url=canonical_public_url(str(card.get("url") or "")),
                        location=str(card.get("location") or "").strip(),
                        description=description,
                        external_id=external_id,
                        platform="jobsdb_hk_cdp",
                        raw=card,
                    )
                    job.role_kind = classify_role_kind(job, source)
                    jobs.append(job)
                if not new_on_page:
                    break
        finally:
            close_scan_page(page)
    return filter_jobs(jobs, source)


def fetch_shixiseng_cdp(source: dict[str, Any], config: dict[str, Any]) -> list[JobPosting]:
    from job_bot.applications.nvidia_workday import _playwright_api

    connect_url = configured_cdp_connect_url(config)
    max_pages = int(source.get("max_pages", 3))
    cards: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(connect_url, timeout=30_000)
        if not browser.contexts:
            raise RuntimeError("专用 Chrome 未提供持久化上下文")
        context = browser.contexts[0]
        page = new_scan_page(context)
        try:
            for page_number in range(1, max_pages + 1):
                page_url = url_with_query(
                    source["url"], page=page_number if page_number > 1 else None
                )
                response = page.goto(
                    page_url, wait_until="domcontentloaded", timeout=45_000
                )
                if response is not None and response.status >= 400:
                    raise RuntimeError(f"实习僧浏览器页面返回 HTTP {response.status}")
                try:
                    page.locator(".intern-wrap").first.wait_for(
                        state="visible", timeout=20_000
                    )
                except Exception:
                    if page_number == 1:
                        raise RuntimeError("实习僧未返回可见的实习职位卡片")
                    break
                page_cards = page.locator(".intern-wrap").evaluate_all(
                    """
                    items => items.map(card => {
                      const link = card.querySelector('a[href*="/intern/"]');
                      const city = card.querySelector('.city');
                      const company = [...card.querySelectorAll('a[title]')]
                        .find(a => !a.classList.contains('font') && a.getAttribute('href') === 'javascript:;');
                      return {
                        id: card.getAttribute('data-intern-id') || '',
                        url: link?.href || '',
                        location: (city?.innerText || '').trim(),
                        company: (company?.getAttribute('title') || company?.innerText || '').trim()
                      };
                    })
                    """
                )
                new_on_page = 0
                for card in page_cards:
                    external_id = str(card.get("id") or "").strip()
                    if not external_id or external_id in seen_ids:
                        continue
                    seen_ids.add(external_id)
                    new_on_page += 1
                    cards.append(card)
                if not new_on_page:
                    break
        finally:
            close_scan_page(page)

    jobs: list[JobPosting] = []
    for card in cards:
        url = canonical_public_url(str(card.get("url") or ""))
        body = http_get(
            url,
            request_timeout_seconds(source),
            {"User-Agent": USER_AGENT},
            ssl_context(source),
        ).decode("utf-8", errors="replace")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        meta_match = re.search(
            r'<meta\b[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']',
            body,
            re.I | re.S,
        )
        page_title = strip_html(title_match.group(1)) if title_match else ""
        title = page_title.split("实习招聘-", 1)[0].strip() if "实习招聘-" in page_title else page_title
        company = str(card.get("company") or "").strip()
        if not company and "实习招聘-" in page_title:
            company = page_title.split("实习招聘-", 1)[1].split("实习生招聘", 1)[0].strip("-")
        description = strip_html(meta_match.group(1)) if meta_match else page_title
        job = JobPosting(
            source_name=source["name"],
            company=company or source.get("company", "实习僧"),
            title=title,
            url=url,
            location=str(card.get("location") or "").strip(),
            description=description,
            external_id=str(card.get("id") or ""),
            platform="shixiseng_cdp",
            raw={"search_card": card, "page_title": page_title},
        )
        job.role_kind = classify_role_kind(job, source)
        jobs.append(job)
    return filter_jobs(jobs, source)


def fetch_moka_cdp(source: dict[str, Any], config: dict[str, Any]) -> list[JobPosting]:
    """当 Moka 的 API 载荷加密时，在真实浏览器中读取公开职位卡片。"""
    from job_bot.applications.nvidia_workday import _playwright_api

    connect_url = configured_cdp_connect_url(config)
    jobs: list[JobPosting] = []
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(connect_url, timeout=30_000)
        if not browser.contexts:
            raise RuntimeError("专用 Chrome 未提供持久化上下文")
        page = new_scan_page(browser.contexts[0])
        try:
            response = page.goto(
                source["url"], wait_until="domcontentloaded", timeout=60_000
            )
            if response is not None and response.status >= 400:
                raise RuntimeError(f"Moka 浏览器页面返回 HTTP {response.status}")
            cards = page.locator('a[href*="#/job/"]')
            cards.first.wait_for(state="visible", timeout=25_000)
            previous_count = 0
            stable_rounds = 0
            for _ in range(int(source.get("max_scrolls", 12))):
                count = cards.count()
                if count == previous_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                if stable_rounds >= 2:
                    break
                previous_count = count
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1200)
            values: list[dict[str, str]] = []
            for page_number in range(1, int(source.get("max_pages", 10)) + 1):
                values.extend(
                    cards.evaluate_all(
                        r"""
                        cards => cards.map(card => ({
                          href: card.href || '',
                          title: (
                            card.querySelector('[class^="title-"], [class*="jobTitle"], h3, h4')?.textContent
                            || (card.innerText || '').split(/\n+/).map(x => x.trim())
                                 .find(x => x && !/^(急|立即投递)$/.test(x))
                            || ''
                          ).trim(),
                          location: (card.querySelector('.no-adaptive-tooltip')?.textContent || '').trim(),
                          text: (card.innerText || '').trim().replace(/\s+/g, ' ')
                        }))
                        """
                    )
                )
                next_page = page.locator(
                    f'[class*="Pagination-item"]:not([class*="is-active"])',
                    has_text=re.compile(rf"^{page_number + 1}$"),
                )
                if not next_page.count():
                    next_page = page.locator(
                        f'button[aria-label^="第{page_number + 1}页"]'
                    )
                if not next_page.count():
                    break
                next_page.first.click()
                page.wait_for_timeout(1500)
            seen_ids: set[str] = set()
            for item in values:
                id_match = re.search(r"#/job/([^/?#]+)", str(item.get("href") or ""))
                external_id = id_match.group(1) if id_match else ""
                if not external_id or external_id in seen_ids:
                    continue
                seen_ids.add(external_id)
                job = JobPosting(
                    source_name=source["name"],
                    company=source.get("company", source["name"]),
                    title=str(item.get("title") or "").strip(),
                    url=str(item.get("href") or ""),
                    location=str(
                        item.get("location") or source.get("default_location") or ""
                    ).strip(),
                    description=str(item.get("text") or "").strip(),
                    external_id=external_id,
                    platform="moka_cdp",
                    raw=item,
                )
                job.role_kind = classify_role_kind(job, source)
                jobs.append(job)
        finally:
            close_scan_page(page)
    return filter_jobs(jobs, source)


def fetch_huawei_cdp(source: dict[str, Any], config: dict[str, Any]) -> list[JobPosting]:
    """在网关要求的网站上下文中查询 Huawei 公开职位 API。"""
    from job_bot.applications.nvidia_workday import _playwright_api

    connect_url = configured_cdp_connect_url(config)
    public_url = str(
        source.get("public_url")
        or "https://career.huawei.com/cn/social-recruitment-job-list"
    )
    api_url = str(
        source.get("api_url")
        or "https://apigw-dgg-b0.huawei.com/api/apig/channelhw/"
        "recruitmentPosition/pub/getJobPage?X-HW-ID=app_000000035886"
    )
    page_size = max(1, min(100, int(source.get("page_size", 100))))
    max_pages = max(1, int(source.get("max_pages", 20)))
    query_modes = source.get("query_modes") or [
        {"jobType": "SR", "role_kind": "full_time"},
        {
            "jobType": "CR",
            "recruitmentType": "FRESH_GRADUATE",
            "role_kind": "full_time",
        },
        {
            "jobType": "CR",
            "recruitmentType": "INTERN",
            "role_kind": "internship",
        },
    ]
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(connect_url, timeout=30_000)
        if not browser.contexts:
            raise RuntimeError("专用 Chrome 未提供持久化上下文")
        page = new_scan_page(browser.contexts[0])
        try:
            response = page.goto(public_url, wait_until="domcontentloaded", timeout=60_000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"Huawei 浏览器页面返回 HTTP {response.status}")
            page.wait_for_timeout(4_000)
            for mode in query_modes:
                for page_number in range(1, max_pages + 1):
                    payload = {
                        "curPage": page_number,
                        "pageSize": page_size,
                        **{
                            str(key): value
                            for key, value in mode.items()
                            if key != "role_kind"
                        },
                    }
                    data = page.evaluate(
                        """
                        async ({apiUrl, payload}) => {
                          const response = await fetch(apiUrl, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {
                              'Accept': 'application/json, text/plain, */*',
                              'Content-Type': 'application/json',
                              'X-Jalor-TenantAlias': 'hcm',
                              'X-Language': 'zh_CN',
                              'X-HW-ID': 'app_000000035886',
                              'X-ALB-GRAY': 'prod'
                            },
                            body: JSON.stringify(payload)
                          });
                          if (!response.ok) throw new Error(`HTTP ${response.status}`);
                          return await response.json();
                        }
                        """,
                        {"apiUrl": api_url, "payload": payload},
                    )
                    block = data.get("data") or {}
                    items = block.get("result") or []
                    for item in items:
                        external_id = str(
                            item.get("advertisementId")
                            or item.get("advertisementsIntegrationId")
                            or item.get("jobId")
                            or ""
                        ).strip()
                        if not external_id or external_id in seen_ids:
                            continue
                        seen_ids.add(external_id)
                        job = JobPosting(
                            source_name=source["name"],
                            company=source.get("company", source["name"]),
                            title=str(item.get("jobName") or "").strip(),
                            url=url_with_query(
                                "https://career.huawei.com/cn/job-details",
                                advertisementId=external_id,
                            ),
                            location=str(
                                item.get("workPlace") or item.get("jobAddress") or ""
                            ).strip(),
                            description=" ".join(
                                str(item.get(field) or "").strip()
                                for field in (
                                    "mainBusiness",
                                    "jobRequire",
                                    "jobFamilyName",
                                    "deptName",
                                )
                            ),
                            external_id=external_id,
                            platform="huawei_cdp",
                            raw=item,
                        )
                        job.role_kind = str(mode.get("role_kind") or "unknown")
                        jobs.append(job)
                    page_info = block.get("pageVO") or {}
                    total_pages = int(page_info.get("totalPages") or 0)
                    if not items or (total_pages and page_number >= total_pages):
                        break
        finally:
            close_scan_page(page)
    return filter_jobs(jobs, source)


def fetch_alibaba_cdp(source: dict[str, Any], config: dict[str, Any]) -> list[JobPosting]:
    """在专用浏览器中使用 Alibaba 的公开部门筛选器和分页器。"""
    from job_bot.applications.nvidia_workday import _playwright_api

    connect_url = configured_cdp_connect_url(config)
    public_url = str(source.get("public_url") or "https://campus-talent.alibaba.com/campus/position")
    department_label = str(source.get("department_label") or "平头哥")
    batches = source.get("batches") or [{"id": 100000760001, "role_kind": "full_time"}]
    max_pages = max(1, int(source.get("max_pages", 10)))
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(connect_url, timeout=30_000)
        if not browser.contexts:
            raise RuntimeError("专用 Chrome 未提供持久化上下文")
        for batch in batches:
            page = new_scan_page(browser.contexts[0])
            try:
                batch_url = url_with_query(public_url, batchId=batch["id"])
                response = page.goto(
                    batch_url, wait_until="domcontentloaded", timeout=60_000
                )
                if response is not None and response.status >= 400:
                    raise RuntimeError(
                        f"Alibaba 浏览器页面返回 HTTP {response.status}"
                    )
                page.wait_for_timeout(5_000)
                department = page.get_by_text(department_label, exact=True)
                if not department.count() or not department.first.is_visible():
                    continue
                with page.expect_response(
                    lambda value: "/position/search" in value.url,
                    timeout=30_000,
                ) as first_response:
                    department.first.click()
                data_pages = [first_response.value.json()]
                content = data_pages[0].get("content") or {}
                total_count = int(content.get("totalCount") or 0)
                page_size = max(1, int(content.get("pageSize") or 10))
                total_pages = min(max_pages, math.ceil(total_count / page_size))
                for _page_number in range(2, total_pages + 1):
                    page.wait_for_timeout(750)
                    next_button = page.locator(
                        '[class*="paginationWrapper"] button.next-next:not([disabled])'
                    ).last
                    if not next_button.count() or not next_button.is_visible():
                        break
                    with page.expect_response(
                        lambda value: "/position/search" in value.url,
                        timeout=30_000,
                    ) as next_response:
                        next_button.click()
                    data_pages.append(next_response.value.json())
                    page.wait_for_timeout(500)
                for data in data_pages:
                    for item in (data.get("content") or {}).get("datas", []):
                        external_id = str(item.get("id") or "").strip()
                        if not external_id or external_id in seen_ids:
                            continue
                        seen_ids.add(external_id)
                        locations = item.get("workLocations") or []
                        circle_names = item.get("circleNames") or []
                        job = JobPosting(
                            source_name=source["name"],
                            company=source.get("company", source["name"]),
                            title=str(item.get("name") or "").strip(),
                            url=url_with_query(batch_url, positionId=external_id),
                            location=" / ".join(str(value) for value in locations),
                            description=" ".join(
                                [
                                    str(item.get("description") or ""),
                                    str(item.get("requirement") or ""),
                                    " / ".join(str(value) for value in circle_names),
                                ]
                            ),
                            external_id=external_id,
                            platform="alibaba_cdp",
                            raw=item,
                        )
                        job.role_kind = str(batch.get("role_kind") or "unknown")
                        jobs.append(job)
            finally:
                close_scan_page(page)
    return filter_jobs(jobs, source)


def fetch_mediatek(source: dict[str, Any]) -> list[JobPosting]:
    locale = str(source.get("locale", "zh_CN"))
    locale_path = str(source.get("locale_path", "zh-cn"))
    page_size = int(source.get("page_size", 100))
    max_pages = int(source.get("max_pages", 20))
    jobs: list[JobPosting] = []
    page_number = 1
    total_pages = 1
    while page_number <= min(total_pages, max_pages):
        payload = {
            "0": {
                "json": {
                    "locales": locale,
                    "page": page_number,
                    "jobQueryInfo": {},
                    "filters": {
                        "categorys": [],
                        "workExperiences": [],
                        "locations": [],
                        "programs": [],
                    },
                    "sortBy": "publishedDate",
                    "order": "DESC",
                    "limit": page_size,
                }
            }
        }
        query = urllib.parse.quote(json.dumps(payload, separators=(",", ":")))
        endpoint = (
            "https://careers.mediatek.com/api/trpc/job.getJobs?batch=1&input=" + query
        )
        data = json.loads(
            http_get(
                endpoint,
                request_timeout_seconds(source),
                {"User-Agent": USER_AGENT, "Accept": "application/json"},
                ssl_context(source),
            ).decode("utf-8")
        )
        result = data[0]["result"]["data"]["json"]
        pagination = result.get("pagination") or {}
        total_pages = max(1, int(pagination.get("total_pages", 1)))
        for item in result.get("jobs", []):
            properties = item.get("properties") or {}
            location_info = properties.get("location") or {}
            category_info = properties.get("category") or {}
            experience_info = properties.get("workExperience") or {}
            external_id = str(item.get("id") or "").strip()
            description = " ".join(
                str(value)
                for value in (
                    category_info.get("label"),
                    experience_info.get("code"),
                    item.get("description"),
                )
                if value
            )
            job = JobPosting(
                source_name=source["name"],
                company=source.get("company", "MediaTek"),
                title=str(item.get("title") or "").strip(),
                url=f"https://careers.mediatek.com/{locale_path}/jobs/{external_id}",
                location=str(location_info.get("code") or item.get("location") or "").strip(),
                description=description,
                external_id=external_id,
                platform="mediatek",
                raw=item,
            )
            job.role_kind = classify_role_kind(job, source)
            jobs.append(job)
        page_number += 1
    return filter_jobs(jobs, source)


def url_with_query(url: str, **updates: Any) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    for key, value in updates.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = [str(value)]
    return urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urllib.parse.urlencode(query, doseq=True),
            parts.fragment,
        )
    )


def fetch_icims(source: dict[str, Any]) -> list[JobPosting]:
    """获取 iCIMS 公开职位卡片，包括地点和简介。"""
    base_url = url_with_query(source["url"], in_iframe=1, pr=0)
    max_pages = max(1, int(source.get("max_pages", 10)))
    jobs: list[JobPosting] = []
    seen_urls: set[str] = set()
    page_count = 1
    for page_number in range(max_pages):
        if page_number >= page_count:
            break
        page_url = url_with_query(base_url, pr=page_number)
        body = http_get(
            page_url,
            request_timeout_seconds(source),
            source_headers(source),
            ssl_context(source),
        ).decode("utf-8", errors="replace")
        page_matches = [
            int(value)
            for value in re.findall(r"Page\s+\d+\s+of\s+(\d+)", strip_html(body), re.I)
        ]
        if page_matches:
            page_count = min(max_pages, max(page_matches))
        cards = re.split(
            r'<li\b[^>]*class=["\'][^"\']*\biCIMS_JobCardItem\b[^"\']*["\'][^>]*>',
            body,
            flags=re.I,
        )[1:]
        for card in cards:
            card = card.split("</li>", 1)[0]
            link_match = re.search(
                r'<a\b[^>]*href=["\']([^"\']*/jobs/\d+/[^"\']*/job[^"\']*)["\'][^>]*>',
                card,
                re.I,
            )
            title_match = re.search(r"<h3\b[^>]*>(.*?)</h3>", card, re.I | re.S)
            if not link_match or not title_match:
                continue
            url = html.unescape(urllib.parse.urljoin(page_url, link_match.group(1)))
            if url in seen_urls:
                continue
            seen_urls.add(url)
            location_match = re.search(
                r'<div\b[^>]*class=["\'][^"\']*\bheader\b[^"\']*\bleft\b[^"\']*["\'][^>]*>(.*?)</div>',
                card,
                re.I | re.S,
            )
            description_match = re.search(
                r'<div\b[^>]*class=["\'][^"\']*\bdescription\b[^"\']*["\'][^>]*>(.*?)</div>',
                card,
                re.I | re.S,
            )
            location = strip_html(location_match.group(1)) if location_match else ""
            location = re.sub(
                r"^(?:Location\s*:\s*Location|Job Locations?)\s*", "", location, flags=re.I
            )
            external_match = re.search(r"/jobs/(\d+)/", url)
            job = JobPosting(
                source_name=source["name"],
                company=source.get("company", source["name"]),
                title=strip_html(title_match.group(1)),
                url=url,
                location=location,
                description=strip_html(description_match.group(1)) if description_match else "",
                external_id=external_match.group(1) if external_match else url,
                platform="icims",
                raw={"title": strip_html(title_match.group(1)), "url": url, "location": location},
            )
            job.role_kind = classify_role_kind(job, source)
            jobs.append(job)
    return filter_jobs(jobs, source)


def fetch_attrax(source: dict[str, Any]) -> list[JobPosting]:
    """使用官方筛选条件和分页获取 Attrax 公开职位信息。"""
    option_ids = source.get("option_ids", [])
    page_size = max(1, int(source.get("page_size", 48)))
    max_pages = max(1, int(source.get("max_pages", 10)))
    base_url = url_with_query(
        source["url"],
        options=",".join(str(value) for value in option_ids) if option_ids else None,
        size=page_size,
        page=1,
    )
    jobs: list[JobPosting] = []
    seen_urls: set[str] = set()
    page_count = 1
    for page_number in range(1, max_pages + 1):
        if page_number > page_count:
            break
        page_url = url_with_query(base_url, page=page_number)
        body = http_get(
            page_url,
            request_timeout_seconds(source),
            source_headers(source),
            ssl_context(source),
        ).decode("utf-8", errors="replace")
        total_match = re.search(r"([\d,]+)\s+result\(s\)", body, re.I)
        if total_match:
            total = int(total_match.group(1).replace(",", ""))
            page_count = min(max_pages, max(1, math.ceil(total / page_size)))
        starts = list(
            re.finditer(
                r'<div\b[^>]*class=["\'][^"\']*\battrax-vacancy-tile\b[^"\']*["\'][^>]*data-jobid=["\']([^"\']+)["\'][^>]*>',
                body,
                re.I,
            )
        )
        for index, start in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
            card = body[start.start():end]
            link_match = re.search(
                r'<a\b(?=[^>]*class=["\'][^"\']*attrax-vacancy-tile__title)(?=[^>]*href=["\']([^"\']+)["\'])[^>]*>(.*?)</a>',
                card,
                re.I | re.S,
            )
            if not link_match:
                continue
            url = html.unescape(urllib.parse.urljoin(page_url, link_match.group(1)))
            if url in seen_urls:
                continue
            seen_urls.add(url)
            location_match = re.search(
                r'attrax-vacancy-tile__location-freetext.*?<p\b[^>]*class=["\'][^"\']*attrax-vacancy-tile__item-value[^"\']*["\'][^>]*>(.*?)</p>',
                card,
                re.I | re.S,
            )
            title = strip_html(link_match.group(2))
            location = strip_html(location_match.group(1)) if location_match else ""
            job = JobPosting(
                source_name=source["name"],
                company=source.get("company", source["name"]),
                title=title,
                url=url,
                location=location,
                description=strip_html(card),
                external_id=start.group(1),
                platform="attrax",
                raw={"title": title, "url": url, "location": location},
            )
            job.role_kind = classify_role_kind(job, source)
            jobs.append(job)
    return filter_jobs(jobs, source)


def find_xml_text(item: ET.Element, names: Iterable[str]) -> str:
    for name in names:
        child = item.find(name)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(text).split())


def normalize_location(value: str) -> str:
    clean = " ".join(str(value or "").split())
    aliases = {
        "新加玻": "新加坡",
    }
    return aliases.get(clean, clean)


def filter_jobs(jobs: list[JobPosting], source: dict[str, Any]) -> list[JobPosting]:
    include_patterns = [re.compile(p, re.I) for p in source.get("include_patterns", [])]
    exclude_patterns = [re.compile(p, re.I) for p in source.get("exclude_patterns", [])]
    title_include_patterns = [re.compile(p, re.I) for p in source.get("title_include_patterns", [])]
    title_exclude_patterns = [re.compile(p, re.I) for p in source.get("title_exclude_patterns", [])]
    allowed_role_kinds = set(source.get("role_kinds", []))
    filtered = []
    for job in jobs:
        haystack = " ".join([job.title, job.location, job.description, job.url])
        if include_patterns and not all(p.search(haystack) for p in include_patterns):
            continue
        if exclude_patterns and any(p.search(haystack) for p in exclude_patterns):
            continue
        if title_include_patterns and not any(p.search(job.title) for p in title_include_patterns):
            continue
        if title_exclude_patterns and any(p.search(job.title) for p in title_exclude_patterns):
            continue
        if allowed_role_kinds and job.role_kind not in allowed_role_kinds:
            continue
        if job.title and job.url:
            filtered.append(job)
    return filtered


def classify_role_kind(job: JobPosting, source: dict[str, Any]) -> str:
    forced = source.get("force_role_kind")
    if forced:
        return forced
    title = job.title.lower()
    haystack = " ".join([job.title, job.description, job.location]).lower()
    if re.search(r"\b(intern|internship|co-?op|student)\b|实习|實習", title):
        return "internship"
    if re.search(
        r"\b(new (?:college )?grad(?:uate)?|graduate|campus|early career|engineer|designer|developer|architect|architecture|post-?doctoral|research assistant)\b"
        r"|校招|校园|校園|應屆|应届|工程师|工程師|设计师|設計師|研究助理|博士后|博士後",
        title,
    ):
        return "full_time"
    if re.search(r"\bfull[ -]?time\b|全职|全職|正职|正職", haystack):
        return "full_time"
    if re.search(r"\b(intern|internship|co-?op|student)\b|实习|實習", haystack):
        return "internship"
    if re.search(
        r"\b(engineer|designer|developer|architect|architecture)\b|工程师|工程師|设计师|設計師",
        haystack,
    ):
        return "full_time"
    return "unknown"


def fetch_source(
    source: dict[str, Any], config: dict[str, Any] | None = None
) -> list[JobPosting]:
    validate_required_env_vars(source)
    source_type = source["type"].lower()
    if source_type == "greenhouse":
        return fetch_greenhouse(source)
    if source_type == "ashby":
        return fetch_ashby(source)
    if source_type == "lever":
        return fetch_lever(source)
    if source_type == "jobsyn":
        return fetch_jobsyn(source)
    if source_type == "jibe":
        return fetch_jibe(source)
    if source_type == "eightfold":
        return fetch_eightfold(source)
    if source_type == "apple_jobs":
        return fetch_apple_jobs(source)
    if source_type == "oracle_ce":
        return fetch_oracle_candidate_experience(source)
    if source_type == "hotjob":
        return fetch_hotjob(source)
    if source_type == "zhiye":
        return fetch_zhiye(source)
    if source_type == "zhiye_html":
        return fetch_zhiye_html(source)
    if source_type == "smartrecruiters":
        return fetch_smartrecruiters(source)
    if source_type == "workday":
        return fetch_workday(source)
    if source_type == "rss":
        return fetch_rss(source)
    if source_type == "html":
        return fetch_html(source)
    if source_type == "xiaomi":
        return fetch_xiaomi(source)
    if source_type == "icims":
        return fetch_icims(source)
    if source_type == "attrax":
        return fetch_attrax(source)
    if source_type == "mediatek":
        return fetch_mediatek(source)
    if source_type == "jobsdb_hk":
        prepared = prepare_jobsdb_hk_source(source)
        if prepared.get("fetch_via_cdp"):
            if config is None:
                raise RuntimeError("JobsDB CDP 采集需要运行时配置")
            return fetch_jobsdb_cdp(prepared, config)
        try:
            return fetch_html(prepared)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise RuntimeError(
            "JobsDB 返回 Cloudflare 403。其 cf_clearance 会话绑定到原始浏览器环境；"
            "请通过受支持的浏览器/会话刷新搜索，或使用 JobsDB 保存的搜索邮件订阅。"
            "仅复制 Cookie 标头可能无法复用该会话。"
                ) from exc
            raise
    if source_type == "zhipin":
        return fetch_html(prepare_zhipin_source(source))
    if source_type == "shixiseng":
        prepared = prepare_shixiseng_source(source)
        if prepared.get("fetch_via_cdp"):
            if config is None:
                raise RuntimeError("实习僧 CDP 采集需要运行时配置")
            return fetch_shixiseng_cdp(prepared, config)
        return fetch_html(prepared)
    if source_type == "moka_cdp":
        if config is None:
            raise RuntimeError("Moka CDP 采集需要运行时配置")
        return fetch_moka_cdp(source, config)
    if source_type == "huawei_cdp":
        if config is None:
            raise RuntimeError("Huawei CDP 采集需要运行时配置")
        return fetch_huawei_cdp(source, config)
    if source_type == "alibaba_cdp":
        if config is None:
            raise RuntimeError("Alibaba CDP 采集需要运行时配置")
        return fetch_alibaba_cdp(source, config)
    if source_type == "cuhk_careers":
        return fetch_cuhk_careers(prepare_cuhk_careers_source(source))
    if source_type == "handshake":
        if config is None:
            raise RuntimeError("Handshake 采集需要运行时配置")
        from job_bot.sources.handshake import fetch_handshake
        return fetch_handshake(source, config)
    raise ValueError(f"不支持的来源类型： {source_type}")


def validate_required_env_vars(source: dict[str, Any]) -> None:
    cookie_candidates = set(source_cookie_env_vars(source))
    cookie_is_configured = any(
        name and os.environ.get(name, "").strip() for name in cookie_candidates
    )
    missing = [
        name
        for name in source.get("required_env_vars", [])
        if not os.environ.get(name)
        and not (name in cookie_candidates and cookie_is_configured)
    ]
    if missing:
        raise RuntimeError(
            f"以下来源缺少必需的环境变量：{source['name']}: {', '.join(missing)}"
        )


def score_job(job: JobPosting, config: dict[str, Any]) -> tuple[int, str]:
    scoring = config.get("scoring", {})
    algorithm = str(
        scoring.get(
            "algorithm",
            "foundation_v2" if scoring.get("foundation_groups") else "weighted_keywords_v1",
        )
    )
    foundations = scoring.get("foundation_groups", [])
    if algorithm == "foundation_v2":
        if not foundations:
            raise ConfigError("foundation_v2 需要配置 scoring.foundation_groups")
        return score_job_foundations(job, scoring)
    if algorithm != "weighted_keywords_v1":
        raise ConfigError(f"不支持的 scoring.algorithm：{algorithm}")
    groups = scoring.get("keyword_groups", [])
    haystack = " ".join([job.title, job.description]).lower()
    title_haystack = job.title.lower()
    if groups:
        score = 0
        reasons = []
        direction_groups = [group for group in groups if not group.get("bonus_only", False)]
        bonus_groups = [group for group in groups if group.get("bonus_only", False)]
        for group in direction_groups:
            matched_keyword = next(
                (keyword for keyword in group.get("keywords", []) if keyword_matches(keyword, haystack)),
                None,
            )
            if not matched_keyword:
                continue
            points = int(group.get("weight", 0))
            if keyword_matches(matched_keyword, title_haystack):
                points += int(group.get("title_bonus", 0))
            score += points
            reasons.append(f"{group.get('name', matched_keyword)} ({matched_keyword}, +{points})")
        if reasons:
            for group in bonus_groups:
                matched_keyword = next(
                    (keyword for keyword in group.get("keywords", []) if keyword_matches(keyword, haystack)),
                    None,
                )
                if not matched_keyword:
                    continue
                points = int(group.get("weight", 0))
                if keyword_matches(matched_keyword, title_haystack):
                    points += int(group.get("title_bonus", 0))
                score += points
                reasons.append(f"{group.get('name', matched_keyword)} ({matched_keyword}, +{points})")
        score = max(0, min(100, score))
        reason = "已匹配：" + "; ".join(reasons) if reasons else "未匹配到已配置的岗位方向"
        return score, reason

    keywords = scoring.get("target_keywords", [])
    hits = []
    for keyword in keywords:
        if keyword_matches(keyword, haystack):
            hits.append(keyword)
    score = min(100, 10 * len(hits))
    if re.search(r"\bintern(ship)?\b", haystack):
        score += 15
    if re.search(r"\b(cpu|rtl|systemverilog|microarchitecture|verification|eda)\b", haystack):
        score += 10
    score = min(score, 100)
    reason = "已匹配：" + ", ".join(hits[:10]) if hits else "未匹配到已配置的关键词"
    return score, reason


def matched_keywords(keywords: list[str], haystack: str) -> list[str]:
    return [keyword for keyword in keywords if keyword_matches(keyword, haystack)]


def score_job_foundations(
    job: JobPosting,
    scoring: dict[str, Any],
) -> tuple[int, str]:
    """使用一个主要基础项和较小的修正项为职位评分。

    基础方向描述职位所属的实际领域。职位标题中的匹配项获得完整基础分。仅根据职位描述
    分类时，必须匹配多个相互独立且信号较强的词项，避免泛泛提及就被误判为高度匹配。
    """

    title = job.title.casefold()
    body = " ".join((job.title, job.description)).casefold()
    foundation_matches: list[dict[str, Any]] = []
    for foundation in scoring.get("foundation_groups", []):
        keywords = [str(keyword) for keyword in foundation.get("keywords", [])]
        title_hits = matched_keywords(keywords, title)
        body_hits = matched_keywords(keywords, body)
        if title_hits:
            points = int(foundation.get("base_score", 0))
            match_scope = "title"
            hits = title_hits
        else:
            min_body_hits = max(1, int(foundation.get("min_body_hits", 2)))
            if len(body_hits) < min_body_hits:
                continue
            points = int(foundation.get("base_score", 0)) + int(
                foundation.get("body_only_adjustment", -10)
            )
            match_scope = "description"
            hits = body_hits
        foundation_matches.append(
            {
                "name": str(foundation.get("name") or hits[0]),
                "points": max(0, points),
                "scope": match_scope,
                "hits": hits,
                "specificity": max((len(hit) for hit in hits), default=0),
                "strong_title": bool(
                    title_hits
                    and any(
                        hit.casefold()
                        not in {
                            str(item).casefold()
                            for item in foundation.get("generic_title_keywords", [])
                        }
                        for hit in title_hits
                    )
                ),
            }
        )

    if not foundation_matches:
        return 0, "基础方向：无（未匹配到明确岗位方向）"

    foundation_matches.sort(
        key=lambda match: (
            0 if match["scope"] == "title" else 1,
            0 if match["strong_title"] else 1,
            -int(match["specificity"]),
            -int(match["points"]),
            -len(match["hits"]),
            match["name"],
        )
    )
    primary = foundation_matches[0]
    score = int(primary["points"])
    reasons = [
        f"基础方向：{primary['name']}（{primary['scope']}："
        f"{', '.join(primary['hits'][:3])}, {score:+d})"
    ]

    secondary_bonus = int(scoring.get("foundation_secondary_bonus", 4))
    max_secondary = max(0, int(scoring.get("max_secondary_foundations", 2)))
    for secondary in foundation_matches[1 : 1 + max_secondary]:
        if secondary_bonus <= 0:
            break
        score += secondary_bonus
        reasons.append(
            f"次要方向：{secondary['name']} "
            f"({', '.join(secondary['hits'][:2])}, {secondary_bonus:+d})"
        )

    for modifier in scoring.get("modifiers", []):
        scope = str(modifier.get("scope", "all"))
        haystack = title if scope == "title" else body
        hits = matched_keywords(
            [str(keyword) for keyword in modifier.get("keywords", [])],
            haystack,
        )
        if not hits:
            continue
        points = int(modifier.get("points", 0))
        score += points
        reasons.append(
            f"修正项：{modifier.get('name', hits[0])} "
            f"({', '.join(hits[:3])}, {points:+d})"
        )

    score = max(0, min(100, score))
    return score, "; ".join(reasons)


def rescore_jobs(config: dict[str, Any]) -> int:
    """匹配资料发生变化后重新计算契合度分数。"""
    conn = connect_db(config)
    rows = conn.execute(
        """
        SELECT id, source_name, company, title, url, location, description,
               external_id, platform, role_kind
        FROM jobs
        """
    ).fetchall()
    for row in rows:
        job = JobPosting(
            source_name=row["source_name"] or "",
            company=row["company"] or "",
            title=row["title"] or "",
            url=row["url"] or "",
            location=row["location"] or "",
            description=row["description"] or "",
            external_id=row["external_id"] or "",
            platform=row["platform"] or "",
            role_kind=row["role_kind"] or "unknown",
        )
        score, reason = score_job(job, config)
        conn.execute(
            "UPDATE jobs SET fit_score = ?, score_reason = ? WHERE id = ?",
            (score, reason, row["id"]),
        )
    conn.commit()
    conn.close()
    return len(rows)


def keyword_matches(keyword: str, haystack: str) -> bool:
    clean = keyword.strip().lower()
    if not clean:
        return False
    if re.fullmatch(r"[a-z0-9+#.]+", clean):
        return re.search(rf"(?<![a-z0-9]){re.escape(clean)}(?![a-z0-9])", haystack) is not None
    return clean in haystack


def upsert_job(conn: sqlite3.Connection, job: JobPosting, config: dict[str, Any]) -> bool:
    now = utc_now()
    job.location = normalize_location(job.location)
    score, reason = score_job(job, config)
    raw_json = json.dumps(job.raw or {}, ensure_ascii=False)
    content_hash = str(hash((job.title, job.location, job.description, job.url)))
    existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (job.url,)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE jobs
            SET title = ?, location = ?, description = ?, fit_score = ?, score_reason = ?,
                role_kind = ?, published_at = COALESCE(NULLIF(?, ''), published_at),
                last_seen = ?, is_active = 1, inactive_since = NULL,
                content_hash = ?, raw_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                job.title,
                job.location,
                job.description,
                score,
                reason,
                job.role_kind,
                job.published_at,
                now,
                content_hash,
                raw_json,
                now,
                existing["id"],
            ),
        )
        return False
    company_id = ensure_company(conn, job.company)
    conn.execute(
        """
        INSERT INTO jobs (
          company_id, company, title, location, url, platform, role_kind, description, status,
          fit_score, score_reason, source_name, external_id, content_hash,
          first_seen, last_seen, published_at, is_active, raw_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            company_id,
            job.company,
            job.title,
            job.location,
            job.url,
            job.platform,
            job.role_kind,
            job.description,
            score,
            reason,
            job.source_name,
            job.external_id,
            content_hash,
            now,
            now,
            job.published_at or None,
            raw_json,
            now,
            now,
        ),
    )
    return True


def ensure_company(conn: sqlite3.Connection, name: str) -> int:
    clean = name or "Unknown"
    conn.execute("INSERT OR IGNORE INTO companies(name) VALUES (?)", (clean,))
    row = conn.execute("SELECT id FROM companies WHERE name = ?", (clean,)).fetchone()
    return int(row["id"])


def source_uses_cdp(source: dict[str, Any]) -> bool:
    return uses_browser(source)


def source_parallel_key(source: dict[str, Any]) -> str:
    """隔离不同雇主或租户拥有的接口，避免相互干扰。"""
    explicit = str(source.get("concurrency_group", "")).strip()
    if explicit:
        return explicit.casefold()
    company = str(source.get("company", "")).strip()
    if company:
        return company.casefold()
    for field in ("url", "api_url", "base_url", "endpoint"):
        value = str(source.get(field, "")).strip()
        if value:
            host = urllib.parse.urlsplit(value).hostname
            if host:
                return host.casefold()
    return str(source.get("name", "unnamed-source")).casefold()


def validate_sync_snapshot(
    conn: sqlite3.Connection,
    source: dict[str, Any],
    postings: list[JobPosting],
    config: dict[str, Any],
) -> None:
    """防止采集器故障或仅返回部分结果时批量停用职位。"""
    if not source.get("sync_active", False):
        return
    global_guard = config.get("scan", {}).get("lifecycle_guard", {})
    guard = dict(global_guard)
    guard.update(source.get("lifecycle_guard", {}))
    if guard.get("enabled", True) is False:
        return
    previous = int(
        conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE source_name = ? AND is_active = 1",
            (source["name"],),
        ).fetchone()[0]
    )
    if previous == 0:
        return
    current = len(postings)
    if current == 0 and guard.get("allow_empty", False):
        return
    minimum_items = max(0, int(guard.get("minimum_items", 1)))
    minimum_fraction = max(
        0.0, min(1.0, float(guard.get("minimum_fraction_of_previous", 0.0)))
    )
    required = max(minimum_items, math.ceil(previous * minimum_fraction))
    if current < required:
        raise RuntimeError(
            f"生命周期保护拒绝了来源 {source['name']} 的可疑快照："
            f"当前收到 {current} 条，之前有 {previous} 条在招职位，至少需要 "
            f"{required} 条。现有在招状态已保留。"
        )


def transient_source_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionResetError)):
        return True
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "http 408",
            "http 425",
            "http 429",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
            "timed out",
            "timeout",
            "connection reset",
            "remote end closed connection",
            "unexpected_eof_while_reading",
        )
    )


def fetch_source_with_retry(
    source: dict[str, Any], config: dict[str, Any]
) -> list[JobPosting]:
    scan_config = config.get("scan", {})
    attempts = max(1, int(scan_config.get("retry_attempts", 1) or 1))
    backoff = max(0.0, float(scan_config.get("retry_backoff_seconds", 1.0) or 0.0))
    for attempt in range(1, attempts + 1):
        try:
            return fetch_source(source, config)
        except Exception as exc:
            if attempt >= attempts or not transient_source_error(exc):
                raise
            delay = backoff * (2 ** (attempt - 1))
            print(
                f"{source['name']}：临时错误；将在 {delay:g} 秒后重试 "
                f"{attempt + 1}/{attempts}（{exc}）",
                file=sys.stderr,
                flush=True,
            )
            if delay:
                time.sleep(delay)
    raise AssertionError("不可达的重试循环")


def scan(
    config: dict[str, Any],
    selected_sources: list[str] | None = None,
    max_workers: int | None = None,
) -> dict[str, int]:
    from job_bot.operator_settings import require_module, region_allowed
    require_module(config, "scan")
    conn = connect_db(config)
    total_seen = 0
    total_new = 0
    selected_requested = selected_sources is not None
    selected = {name.casefold() for name in (selected_sources or [])}
    sources: list[dict[str, Any]] = []
    for source in config.get("sources", []):
        if selected_requested and str(source.get("name", "")).casefold() not in selected:
            continue
        if source.get("enabled", True) is False:
            print(f"{source['name']}：跳过已禁用的来源", flush=True)
            continue
        sources.append(source)

    configured_workers = int(config.get("scan", {}).get("max_workers", 1) or 1)
    worker_count = max(1, int(max_workers or configured_workers))

    def begin(source: dict[str, Any]) -> int:
        print(f"{source['name']}：正在扫描……", flush=True)
        started = utc_now()
        run_id = conn.execute(
            "INSERT INTO scan_runs(source_name, started_at, status) VALUES (?, ?, 'running')",
            (source["name"], started),
        ).lastrowid
        conn.commit()
        return int(run_id)

    def finish(source: dict[str, Any], run_id: int, postings: list[JobPosting]) -> None:
        nonlocal total_seen, total_new
        if config.get("operator_controls"):
            source = {**source, "sync_active": False}
            postings = [job for job in postings if region_allowed(job.location, config)]
        validate_sync_snapshot(conn, source, postings, config)
        if source.get("sync_active", False):
            conn.execute(
                """
                UPDATE jobs
                SET is_active = 0, inactive_since = COALESCE(inactive_since, ?)
                WHERE source_name = ? AND is_active = 1
                """,
                (utc_now(), source["name"]),
            )
        new_count = 0
        for job in postings:
            if upsert_job(conn, job, config):
                new_count += 1
        conn.execute(
            """
            UPDATE scan_runs
            SET finished_at = ?, status = 'ok', jobs_seen = ?, jobs_new = ?
            WHERE id = ?
            """,
            (utc_now(), len(postings), new_count, run_id),
        )
        conn.commit()
        total_seen += len(postings)
        total_new += new_count
        print(f"{source['name']}：已发现={len(postings)} 新增={new_count}", flush=True)

    def fail(source: dict[str, Any], run_id: int, exc: BaseException) -> None:
        conn.execute(
            "UPDATE scan_runs SET finished_at = ?, status = 'error', error = ? WHERE id = ?",
            (utc_now(), str(exc), run_id),
        )
        conn.commit()
        print(f"{source['name']}：错误 {exc}", file=sys.stderr, flush=True)

    if worker_count == 1 or len(sources) < 2:
        for source in sources:
            run_id = begin(source)
            try:
                finish(source, run_id, fetch_source_with_retry(source, config))
            except Exception as exc:
                fail(source, run_id, exc)
        conn.close()
        return {"seen": total_seen, "new": total_new}

    http_sources = [source for source in sources if not source_uses_cdp(source)]
    cdp_sources = [source for source in sources if source_uses_cdp(source)]
    futures: dict[Future[list[JobPosting]], tuple[dict[str, Any], int]] = {}
    group_locks = {
        source_parallel_key(source): threading.Lock() for source in http_sources
    }

    def fetch_http_guarded(source: dict[str, Any]) -> list[JobPosting]:
        with group_locks[source_parallel_key(source)]:
            return fetch_source_with_retry(source, config)

    print(
        f"并行扫描：http={len(http_sources)} 工作线程={worker_count}；"
        f"cdp_serial={len(cdp_sources)}；same_company_serial=true",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="job-source") as executor:
        for source in http_sources:
            run_id = begin(source)
            futures[executor.submit(fetch_http_guarded, source)] = (source, run_id)

        # CDP 来源共用一个交互式浏览器配置，因此仍需串行运行。
        for source in cdp_sources:
            run_id = begin(source)
            try:
                finish(source, run_id, fetch_source_with_retry(source, config))
            except Exception as exc:
                fail(source, run_id, exc)

        for future in as_completed(futures):
            source, run_id = futures[future]
            try:
                finish(source, run_id, future.result())
            except Exception as exc:
                fail(source, run_id, exc)

    conn.close()
    return {"seen": total_seen, "new": total_new}


def recent_jobs(
    conn: sqlite3.Connection, hours: int, since: str | None = None
) -> list[sqlite3.Row]:
    published_cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    discovery_cutoff = published_cutoff
    if since:
        requested = parse_time(since)
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=dt.timezone.utc)
        discovery_cutoff = max(discovery_cutoff, requested.astimezone(dt.timezone.utc))
    return list(conn.execute(
        """
        SELECT company, title, location, url, fit_score, score_reason, first_seen,
               published_at, source_name
             , role_kind
        FROM jobs
        WHERE first_seen >= ? AND is_active = 1
          AND (published_at IS NULL OR published_at = '' OR published_at >= ?)
        ORDER BY fit_score DESC, COALESCE(published_at, first_seen) DESC
        """,
        (
            discovery_cutoff.replace(microsecond=0).isoformat(),
            published_cutoff.replace(microsecond=0).isoformat(),
        ),
    ))


def active_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        """
        SELECT company, title, location, url, fit_score, score_reason, first_seen,
               published_at, source_name,
               role_kind
        FROM jobs
        WHERE is_active = 1
        ORDER BY fit_score DESC, last_seen DESC
        """
    ))


def recent_scan_errors(conn: sqlite3.Connection, hours: int) -> list[sqlite3.Row]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    return list(conn.execute(
        """
        SELECT scan_runs.source_name, scan_runs.started_at, scan_runs.error
        FROM scan_runs
        JOIN (
          SELECT source_name, MAX(id) AS latest_id
          FROM scan_runs
          GROUP BY source_name
        ) AS latest ON latest.latest_id = scan_runs.id
        WHERE scan_runs.status = 'error' AND scan_runs.started_at >= ?
        ORDER BY scan_runs.started_at DESC
        """,
        (cutoff.replace(microsecond=0).isoformat(),),
    ))


def render_digest(
    config: dict[str, Any],
    hours: int,
    *,
    all_active: bool = False,
    edition: int | None = None,
    since: str | None = None,
) -> tuple[str, str]:
    from job_bot.operator_settings import require_module, region_allowed
    require_module(config, "report")
    conn = connect_db(config)
    jobs = active_jobs(conn) if all_active else recent_jobs(conn, hours, since)
    jobs = [job for job in jobs if region_allowed(job["location"], config)]
    digest_config = config.get("digest", {})
    max_items = int(digest_config.get("max_items", 0) or 0)
    min_score = int(digest_config.get("min_score", 0) or 0)
    if min_score:
        jobs = [job for job in jobs if int(job["fit_score"] or 0) >= min_score]
    excluded_titles = [re.compile(pattern, re.I) for pattern in digest_config.get("exclude_title_patterns", [])]
    if excluded_titles:
        jobs = [job for job in jobs if not any(pattern.search(job["title"] or "") for pattern in excluded_titles)]
    included_titles = [
        re.compile(pattern, re.I)
        for pattern in digest_config.get("include_title_patterns", [])
    ]
    if included_titles:
        bypass_score = int(digest_config.get("title_match_bypass_score", 40) or 40)
        jobs = [
            job
            for job in jobs
            if int(job["fit_score"] or 0) >= bypass_score
            or any(pattern.search(job["title"] or "") for pattern in included_titles)
        ]
    if digest_config.get("deduplicate_similar", True):
        unique_jobs: list[sqlite3.Row] = []
        seen_job_keys: set[tuple[str, str, str]] = set()
        for job in jobs:
            key = tuple(
                re.sub(r"\W+", " ", str(value or "").lower()).strip()
                for value in (job["company"], job["title"], job["location"])
            )
            if key in seen_job_keys:
                continue
            seen_job_keys.add(key)
            unique_jobs.append(job)
        jobs = unique_jobs
    hidden_count = 0
    if max_items and len(jobs) > max_items:
        hidden_count = len(jobs) - max_items
        jobs = jobs[:max_items]
    scan_errors = recent_scan_errors(conn, hours) if digest_config.get("include_scan_errors", True) else []
    today = dt.datetime.now().strftime("%Y-%m-%d")
    edition_label = f" #{edition}" if edition is not None else ""
    if all_active:
        subject = f"每日职位摘要{edition_label}：{len(jobs)} 个在招职位（{today}）"
        window_label = "全部当前在招职位（基准清单）"
    else:
        subject = f"每日职位摘要{edition_label}：{len(jobs)} 个新职位（{today}）"
        window_label = f"过去 {hours} 小时"
        if since:
            window_label += f"；首次发现时间晚于基准 {since}"
    lines = [
        subject,
        "",
        f"时间范围：{window_label}",
        f"数据库：{db_path(config)}",
        "",
    ]
    if not jobs:
        lines.append("没有找到新职位。")
    if hidden_count:
        lines.extend([
            f"当前显示前 {len(jobs)} 个职位；数据库中仍有 {hidden_count} 个排名较低的职位。",
            "",
        ])
    sections = (
        ("第一部分：实习", "internship"),
        ("第二部分：全职岗位", "full_time"),
        ("第三部分：岗位类型待确认", "unknown"),
    )
    for section_title, role_kind in sections:
        section_jobs = [job for job in jobs if (job["role_kind"] or "unknown") == role_kind]
        if role_kind == "unknown" and not section_jobs:
            continue
        lines.extend([f"{section_title} ({len(section_jobs)})", ""])
        if not section_jobs:
            lines.extend(["此类别中没有匹配的职位。", ""])
            continue
        for index, job in enumerate(section_jobs, start=1):
            fit_score = int(job["fit_score"] or 0)
            if fit_score >= 40:
                match_tier = "core"
            elif fit_score >= 16:
                match_tier = "supporting"
            else:
                match_tier = "adjacent"
            lines.extend([
                f"{index}. {job['title']} — {job['company']}",
                f"   地点：{job['location'] or 'N/A'}",
                f"   匹配层级：{match_tier}",
                f"   契合度评分：{fit_score}/100",
                f"   原因：{job['score_reason'] or 'N/A'}",
                f"   来源：{job['source_name']}",
                *(
                    [f"   发布于：{job['published_at']}"]
                    if job["published_at"]
                    else []
                ),
                f"   URL：{job['url']}",
                "",
            ])
    if scan_errors:
        lines.extend([
            "来源警告：",
            "",
        ])
        for error in scan_errors:
            lines.extend([
                f"- {error['source_name']} at {error['started_at']}: {error['error']}",
            ])
        lines.append("")
    return subject, "\n".join(lines)


def send_or_write_digest(
    config: dict[str, Any],
    subject: str,
    body: str,
    edition: int | None = None,
) -> Path | None:
    email_config = config.get("email", {})
    dry_run = email_config.get("dry_run", True)
    if dry_run:
        DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = f"daily_report_{edition:03d}" if edition is not None else "digest"
        out = DEFAULT_OUT / f"{prefix}_{timestamp}.txt"
        out.write_text(body, encoding="utf-8")
        print(f"试运行摘要已写入 {out}")
        return out

    host = email_config["smtp_host"]
    port = int(email_config.get("smtp_port", 587))
    username = os.environ.get(email_config.get("username_env", "SMTP_USERNAME"), "")
    password = os.environ.get(email_config.get("password_env", "SMTP_PASSWORD"), "")
    sender = email_config["from"]
    recipients = email_config.get("to", [])
    if isinstance(recipients, str):
        recipients = [recipients]
    recipient_env = str(email_config.get("to_env", "JOBBOT_EMAIL_TO"))
    env_recipients = [
        value.strip()
        for value in os.environ.get(recipient_env, "").split(",")
        if value.strip()
    ]
    recipients = [*recipients, *env_recipients]
    if not recipients:
        raise RuntimeError(
            f"未配置摘要收件人；请在私有环境文件中设置 {recipient_env}"
        )
    if not username or not password:
        raise SystemExit("dry_run=false 时必须配置 SMTP 用户名和密码环境变量")

    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if email_config.get("starttls", True):
            smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)
    print(f"摘要已发送至 {', '.join(recipients)}")
    return None


def cmd_init(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    path = db_path(config)
    connect_db(config).close()
    print(f"数据库已初始化：{path}")


def cmd_scan(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    selector = selector_from_cli(args)
    selected = select_sources(config, selector)
    if not selected:
        raise SystemExit("来源选择器未匹配到已启用的来源")
    summary = scan(
        config,
        selected_sources=[str(source["name"]) for source in selected],
        max_workers=args.max_workers,
    )
    print(f"扫描完成：已发现={summary['seen']} 新增={summary['new']}")


def cmd_digest(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    subject, body = render_digest(
        config,
        args.hours,
        all_active=args.all_active,
        edition=args.edition,
        since=args.since,
    )
    if args.print:
        print(body)
    else:
        send_or_write_digest(config, subject, body, args.edition)


def cmd_rescore(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    count = rescore_jobs(config)
    print(f"已重新计算 {count} 个已保存职位的评分")


def cmd_auth_status(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    print("凭据状态（绝不显示具体值）：")
    for source in config.get("sources", []):
        cookie_variable = source_session_env_var(source)
        cookie_configured = bool(
            cookie_variable and os.environ.get(cookie_variable, "").strip()
        )
        storage_variable = source_storage_state_env_var(source)
        storage_value = os.environ.get(storage_variable, "").strip()
        storage_state = "empty"
        if storage_value:
            storage_path = Path(storage_value)
            if not storage_path.is_absolute():
                storage_path = ROOT / storage_path
            storage_state = "configured" if storage_path.is_file() else "missing-file"
        username_variable, password_variable = source_login_env_vars(source)
        username_set = bool(
            username_variable and os.environ.get(username_variable, "").strip()
        )
        password_set = bool(
            password_variable and os.environ.get(password_variable, "").strip()
        )
        login_state = (
            "configured"
            if username_set and password_set
            else "partial"
            if username_set or password_set
            else "empty"
        )
        source_state = "disabled" if source.get("enabled", True) is False else "enabled"
        cookie_state = "configured" if cookie_configured else "empty"
        print(
            f"- {source['name']}: login={login_state}; "
            f"cookie={cookie_variable or 'N/A'}[{cookie_state}]; "
            f"browser={storage_variable or 'N/A'}[{storage_state}]; {source_state}"
        )


def cmd_run(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    scan(config)
    subject, body = render_digest(
        config,
        args.hours,
        all_active=args.all_active,
        edition=args.edition,
        since=args.since,
    )
    send_or_write_digest(config, subject, body, args.edition)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="监控招聘信息并生成每日邮件摘要。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Examples:
              python3 job_bot/bot.py init --config job_bot/config.china_hk_ic_foreign.json
              python3 job_bot/bot.py scan --config job_bot/config.china_hk_ic_foreign.json
              python3 job_bot/bot.py rescore --config job_bot/config.china_hk_ic_foreign.json
              python3 job_bot/bot.py auth-status --config job_bot/config.china_hk_ic_foreign.json
              python3 job_bot/bot.py digest --config job_bot/config.china_hk_ic_foreign.json --print
              python3 job_bot/bot.py run --config job_bot/config.china_hk_ic_foreign.json
            """
        ),
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON 配置文件路径")
    parser.add_argument("--env-file", help="可选的本地 KEY=VALUE 机密配置文件")
    sub = parser.add_subparsers(required=True)

    init_p = sub.add_parser("init", help="初始化 SQLite 数据库")
    init_p.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON 配置文件路径")
    init_p.add_argument("--env-file", help="可选的本地 KEY=VALUE 机密配置文件")
    init_p.set_defaults(func=cmd_init)

    scan_p = sub.add_parser("scan", help="采集来源并保存新职位")
    scan_p.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON 配置文件路径")
    scan_p.add_argument("--env-file", help="可选的本地 KEY=VALUE 机密配置文件")
    scan_p.add_argument(
        "--source",
        action="append",
        help="仅扫描此来源名称；可重复指定多个来源",
    )
    scan_p.add_argument(
        "--exclude-source",
        action="append",
        help="排除此来源名称；可重复指定多个来源",
    )
    scan_p.add_argument(
        "--source-category",
        action="append",
        help="仅包含此 source_category；可重复指定多个类别",
    )
    scan_p.add_argument(
        "--source-type",
        action="append",
        help="仅包含此适配器类型；可重复指定多个类型",
    )
    scan_p.add_argument(
        "--company",
        action="append",
        help="仅包含此公司；可重复指定多个公司",
    )
    scan_p.add_argument(
        "--source-browser",
        choices=("any", "http", "cdp"),
        default="any",
        help="按是否需要 CDP 浏览器限制来源",
    )
    scan_p.add_argument(
        "--max-workers",
        type=int,
        help="覆盖 HTTP 来源的并行工作线程数；CDP/浏览器来源仍按串行运行",
    )
    scan_p.set_defaults(func=cmd_scan)

    rescore_p = sub.add_parser("rescore", help="重新计算已保存职位的评分")
    rescore_p.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON 配置文件路径")
    rescore_p.add_argument("--env-file", help="可选的本地 KEY=VALUE 机密配置文件")
    rescore_p.set_defaults(func=cmd_rescore)

    auth_p = sub.add_parser("auth-status", help="显示会话变量覆盖情况，不泄露变量值")
    auth_p.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON 配置文件路径")
    auth_p.add_argument("--env-file", help="可选的本地 KEY=VALUE 机密配置文件")
    auth_p.set_defaults(func=cmd_auth_status)

    digest_p = sub.add_parser("digest", help="根据近期职位创建摘要")
    digest_p.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON 配置文件路径")
    digest_p.add_argument("--env-file", help="可选的本地 KEY=VALUE 机密配置文件")
    digest_p.add_argument("--hours", type=int, default=24)
    digest_p.add_argument(
        "--since",
        help="可选 ISO 时间戳；隐藏已被较新基准时间涵盖的职位",
    )
    digest_p.add_argument(
        "--all-active",
        action="store_true",
        help="包含全部在招职位，而非仅包含新发现的职位",
    )
    digest_p.add_argument("--edition", type=int, help="可选的报告版本编号")
    digest_p.add_argument("--print", action="store_true", help="直接打印，不写入文件或发送")
    digest_p.set_defaults(func=cmd_digest)

    run_p = sub.add_parser("run", help="扫描后写入或发送摘要")
    run_p.add_argument("--config", default=str(DEFAULT_CONFIG), help="JSON 配置文件路径")
    run_p.add_argument("--env-file", help="可选的本地 KEY=VALUE 机密配置文件")
    run_p.add_argument("--hours", type=int, default=24)
    run_p.add_argument(
        "--since",
        help="可选 ISO 时间戳；隐藏已被较新基准时间涵盖的职位",
    )
    run_p.add_argument(
        "--all-active",
        action="store_true",
        help="扫描后生成完整的在招职位报告",
    )
    run_p.add_argument("--edition", type=int, help="可选的报告版本编号")
    run_p.set_defaults(func=cmd_run)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.env_file:
        load_env_file(Path(args.env_file))
    args.func(args)


if __name__ == "__main__":
    main()
