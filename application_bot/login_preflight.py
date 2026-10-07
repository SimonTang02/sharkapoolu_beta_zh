#!/usr/bin/env python3
"""为批次中的每家公司打开一个代表性登录检查标签页。

此步骤与表单填写分开执行，以便用户在创建任何申请专用标签页之前审阅每家公司的登录状态。本模块创建的标签使用 ``jobbot-login-company-*`` 名称，可由本模块安全去重；申请标签页绝不会关闭。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / ".python_packages"
if LOCAL_PACKAGES.is_dir() and str(LOCAL_PACKAGES) not in sys.path:
    sys.path.insert(0, str(LOCAL_PACKAGES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application_bot.portal_registry import resolve_adapter  # noqa: E402
from job_bot.application_bot import resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.bot import connect_db, load_config, load_env_file  # noqa: E402
from private_paths import APPLICATION_OUTPUT, CREDENTIALS_FILE  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"


@dataclass(frozen=True)
class LoginTarget:
    company: str
    adapter: str | None
    application_id: int
    representative_url: str
    login_check_url: str
    label: str


def safe_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:70]


def _workday_user_home(url: str) -> str:
    parts = urlsplit(url)
    path_parts = [item for item in parts.path.split("/") if item]
    locale_index = next(
        (index for index, item in enumerate(path_parts) if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", item)),
        None,
    )
    if locale_index is not None and len(path_parts) > locale_index + 1:
        locale = path_parts[locale_index]
        site = path_parts[locale_index + 1]
        return urlunsplit((parts.scheme, parts.netloc, f"/{locale}/{site}/userHome", "", ""))
    return url


def _login_check_url(adapter_id: str | None, row) -> str:
    url = str(row["url"] or "")
    if adapter_id == "workday":
        return _workday_user_home(url)
    if adapter_id == "amd_icims" and row["external_id"]:
        title = str(row["title"] or "").casefold()
        host = "campus-amd.icims.com" if any(
            marker in title for marker in ("intern", "co-op", "student", "实习")
        ) else "global-external-amd.icims.com"
        return f"https://{host}/jobs/{row['external_id']}/login"
    return url


def build_login_targets(config, rows) -> list[LoginTarget]:
    """为每家公司选择一个页面，同时保留批次岗位顺序。"""
    targets: list[LoginTarget] = []
    seen: set[str] = set()
    for row in rows:
        company = str(row["company"] or "Unknown").strip()
        key = company.casefold()
        if key in seen:
            continue
        seen.add(key)
        adapter = resolve_adapter(
            config,
            company=company,
            platform=str(row["platform"] or ""),
            url=str(row["url"] or ""),
        )
        targets.append(
            LoginTarget(
                company=company,
                adapter=adapter.id if adapter else None,
                application_id=int(row["application_id"]),
                representative_url=str(row["url"] or ""),
                login_check_url=_login_check_url(adapter.id if adapter else None, row),
                label=f"jobbot-login-company-{safe_label(company)}",
            )
        )
    return targets


def campaign_rows(conn, campaign_ids: list[int]):
    placeholders = ",".join("?" for _ in campaign_ids)
    return conn.execute(
        f"""
        SELECT acj.campaign_id, acj.rank, applications.id AS application_id,
               applications.status, jobs.company, jobs.title, jobs.platform,
               jobs.url, jobs.external_id
        FROM application_campaign_jobs acj
        JOIN applications ON applications.id=acj.application_id
        JOIN jobs ON jobs.id=acj.job_id
        WHERE acj.campaign_id IN ({placeholders})
          AND applications.status NOT IN ('submitted', 'cancelled')
        ORDER BY acj.campaign_id, acj.rank
        """,
        campaign_ids,
    ).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", action="append", type=int, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=CREDENTIALS_FILE)
    parser.add_argument(
        "--plan-only", action="store_true", help="仅写入目标，不打开 Chrome 标签页。"
    )
    args = parser.parse_args()
    if args.env_file.is_file():
        load_env_file(args.env_file)
    config = load_config(args.config)
    conn = connect_db(config)
    rows = campaign_rows(conn, args.campaign_id)
    conn.close()
    targets = build_login_targets(config, rows)
    results: list[dict] = []

    if not args.plan_only:
        mode, cdp_url = resolve_browser_connection(config)
        if mode != "windows_cdp":
            raise SystemExit("登录预检需要 application_browser.mode=windows_cdp")
        sync_playwright = _playwright_api()
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=30_000)
            context = browser.contexts[0]
            pages_by_label: dict[str, list] = {}
            for page in context.pages:
                try:
                    label = str(page.evaluate("window.name") or "")
                except Exception:
                    continue
                if label.startswith("jobbot-login-company-"):
                    pages_by_label.setdefault(label, []).append(page)

            for target in targets:
                owned = pages_by_label.get(target.label, [])
                page = owned[0] if owned else context.new_page()
                for duplicate in owned[1:]:
                    try:
                        duplicate.close()
                    except Exception:
                        pass
                page.evaluate("label => { window.name = label; }", target.label)
                error = ""
                try:
                    page.goto(target.login_check_url, wait_until="domcontentloaded", timeout=45_000)
                    # Workday 有时会在租户切换后保留过时的 Create Account 标题。
                    # 显式重新加载一次即可解决此状态。
                    if target.adapter == "workday" and "create account" in page.title().casefold():
                        page.reload(wait_until="domcontentloaded", timeout=45_000)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {' '.join(str(exc).split())[-300:]}"
                results.append(
                    {
                        **asdict(target),
                        "state": "open_error" if error else ("reused" if owned else "opened"),
                        "current_url": page.url,
                        "title": page.title() if not page.is_closed() else "",
                        "error": error,
                    }
                )
    else:
        results = [{**asdict(item), "state": "planned"} for item in targets]

    APPLICATION_OUTPUT.mkdir(parents=True, exist_ok=True)
    output = APPLICATION_OUTPUT / f"login_preflight_{datetime.now():%Y%m%d_%H%M%S}.json"
    output.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "campaign_ids": args.campaign_id,
                "one_tab_per_company": True,
                "forms_filled": False,
                "final_submit_allowed": False,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(output)
    print(f"companies={len(targets)} opened={0 if args.plan_only else len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
