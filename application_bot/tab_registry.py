"""申请记录与浏览器标签页之间的持久映射。"""

from __future__ import annotations

import re
import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class PageResolution:
    page: Any
    created: bool
    method: str
    target_id: str
    tab_label: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_browser_tab_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_browser_tabs_target_id ON browser_tabs(target_id)"
    )
    conn.commit()


def canonical_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    path = urllib.parse.unquote(parts.path).rstrip("/") or "/"
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parts.query)))
    fragment = urllib.parse.unquote(parts.fragment).rstrip("/")
    return urllib.parse.urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), path, query, fragment)
    )


def job_fingerprint(url: str) -> str:
    """从详情/申请/登录网址变体中提取稳定的岗位标识。"""
    parts = urllib.parse.urlsplit(url.strip())
    host = (parts.hostname or "").casefold()
    query = dict(urllib.parse.parse_qsl(parts.query))
    for key in ("jobId", "postId", "positionId", "pid", "job_id"):
        if query.get(key):
            # Eightfold 详情页使用 /careers/job/<id>，
            # 后续申请步骤使用 /careers/apply?pid=<id>。
            # 将这两种形式视为同一个持久岗位标识。
            label = (
                "job"
                if key == "pid" and "/careers/apply" in parts.path.casefold()
                else key.casefold()
            )
            return f"{host}|{label}:{query[key].casefold()}"
    decoded = urllib.parse.unquote(f"{parts.path}#{parts.fragment}")
    patterns = (
        r"/(?:job|jobs|details)/([0-9a-f]{8}-[0-9a-f-]{27,36})(?:/|#|$)",
        r"/(?:job|jobs|details)/([A-Z]{1,5}[A-Z0-9-]*\d[A-Z0-9-]*)(?:/|#|$)",
        r"/(?:job|jobs|details)/(\d{5,})(?:/|#|$)",
        r"(?:_|/)((?:JR|R-?)\d+(?:-\d+)?)(?:/|#|$)",
        r"#/job/([0-9a-f-]{20,})(?:/|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, decoded, re.I)
        if match:
            return f"{host}|job:{match.group(1).casefold()}"
    normalized_path = re.sub(
        r"/(?:apply|autofillWithResume|applyManually|useMyLastApplication)(?:/.*)?$",
        "",
        urllib.parse.unquote(parts.path),
        flags=re.I,
    ).rstrip("/")
    fragment = urllib.parse.unquote(parts.fragment)
    return f"{host}|path:{normalized_path.casefold()}#{fragment.casefold()}"


def page_matches_application(current_url: str, expected_url: str) -> bool:
    return bool(
        current_url
        and current_url != "about:blank"
        and (
            canonical_url(current_url) == canonical_url(expected_url)
            or job_fingerprint(current_url) == job_fingerprint(expected_url)
        )
    )


def cdp_target_id(context: Any, page: Any) -> str:
    session = None
    try:
        session = context.new_cdp_session(page)
        payload = session.send("Target.getTargetInfo")
        return str(payload.get("targetInfo", {}).get("targetId", ""))
    except Exception:
        return ""
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass


def _window_name(page: Any) -> str:
    try:
        return str(page.evaluate("window.name") or "")
    except Exception:
        return ""


def _set_window_name(page: Any, label: str) -> None:
    try:
        page.evaluate("label => { window.name = label; }", label)
    except Exception:
        pass


def resolve_application_page(
    context: Any,
    conn: sqlite3.Connection,
    *,
    application_id: int,
    expected_url: str,
    browser_mode: str,
) -> PageResolution:
    ensure_browser_tab_schema(conn)
    label = f"jobbot-application-{application_id}"
    stored = conn.execute(
        "SELECT * FROM browser_tabs WHERE application_id = ?", (application_id,)
    ).fetchone()
    pages = [page for page in context.pages if not page.is_closed()]
    page_targets = [(page, cdp_target_id(context, page)) for page in pages]

    if stored and stored["target_id"]:
        for page, target_id in page_targets:
            if target_id == stored["target_id"]:
                if page_matches_application(page.url, expected_url) or _window_name(page) == label:
                    return PageResolution(page, False, "stored_target_id", target_id, label)

    for page, target_id in page_targets:
        if _window_name(page) == label:
            return PageResolution(page, False, "stored_window_name", target_id, label)

    expected_fingerprint = job_fingerprint(expected_url)
    for page, target_id in reversed(page_targets):
        if page_matches_application(page.url, expected_url):
            owner = conn.execute(
                "SELECT application_id FROM browser_tabs WHERE target_id = ? AND state = 'active'",
                (target_id,),
            ).fetchone()
            if owner and int(owner["application_id"]) != application_id:
                continue
            method = (
                "exact_canonical_url"
                if canonical_url(page.url) == canonical_url(expected_url)
                else "job_fingerprint"
            )
            _set_window_name(page, label)
            return PageResolution(page, False, method, target_id, label)

    page = context.new_page()
    target_id = cdp_target_id(context, page)
    _set_window_name(page, label)
    return PageResolution(page, True, "new_page", target_id, label)


def register_application_page(
    conn: sqlite3.Connection,
    resolution: PageResolution,
    *,
    application_id: int,
    expected_url: str,
    browser_mode: str,
    state: str = "active",
) -> None:
    ensure_browser_tab_schema(conn)
    _set_window_name(resolution.page, resolution.tab_label)
    target_id = cdp_target_id(resolution.page.context, resolution.page) or resolution.target_id
    now = _now()
    conn.execute(
        """
        UPDATE browser_tabs SET target_id = NULL, state = 'superseded', last_seen_at = ?
        WHERE target_id = ? AND application_id != ?
        """,
        (now, target_id, application_id),
    )
    conn.execute(
        """
        INSERT INTO browser_tabs(
          application_id, browser_mode, target_id, tab_label, job_fingerprint,
          canonical_url, current_url, page_title, state, resolution_method,
          created_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(application_id) DO UPDATE SET
          browser_mode=excluded.browser_mode,
          target_id=excluded.target_id,
          tab_label=excluded.tab_label,
          job_fingerprint=excluded.job_fingerprint,
          canonical_url=excluded.canonical_url,
          current_url=excluded.current_url,
          page_title=excluded.page_title,
          state=excluded.state,
          resolution_method=excluded.resolution_method,
          last_seen_at=excluded.last_seen_at
        """,
        (
            application_id,
            browser_mode,
            target_id,
            resolution.tab_label,
            job_fingerprint(expected_url),
            canonical_url(expected_url),
            resolution.page.url,
            resolution.page.title(),
            state,
            resolution.method,
            now,
            now,
        ),
    )
    conn.commit()


def mark_application_tab_closed(conn: sqlite3.Connection, application_id: int) -> None:
    ensure_browser_tab_schema(conn)
    conn.execute(
        """
        UPDATE browser_tabs SET target_id=NULL, state='closed', last_seen_at=?
        WHERE application_id=?
        """,
        (_now(), application_id),
    )
    conn.commit()
