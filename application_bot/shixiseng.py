#!/usr/bin/env python3
"""准备实习僧申请标签页，不触发一键投递。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / ".python_packages"
if LOCAL_PACKAGES.is_dir() and str(LOCAL_PACKAGES) not in sys.path:
    sys.path.insert(0, str(LOCAL_PACKAGES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application_bot.artifacts import save_fill_test_artifact  # noqa: E402
from application_bot.tab_registry import (  # noqa: E402
    page_matches_application,
    register_application_page,
    resolve_application_page,
)
from job_bot.application_bot import add_event, resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.bot import connect_db, load_config, load_env_file, utc_now  # noqa: E402
from private_paths import CREDENTIALS_FILE, JOBBOT_OUTPUT  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
DEFAULT_ENV = CREDENTIALS_FILE


def _body(page) -> str:
    return " ".join(page.locator("body").inner_text(timeout=8_000).split())


def _update(conn, application_id: int, status: str, note: str, url: str) -> None:
    conn.execute(
        """UPDATE applications SET status=?, notes=?, last_error=NULL,
           draft_url=?, updated_at=? WHERE id=?""",
        (status, note, url, utc_now(), application_id),
    )
    conn.execute(
        """UPDATE application_campaign_jobs SET status=?, last_error=?
           WHERE application_id=?""",
        (status, note, application_id),
    )
    add_event(
        conn,
        application_id,
        "shixiseng_progress",
        {"status": status, "url": url, "delivery_clicked": False},
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env", default=str(DEFAULT_ENV))
    args = parser.parse_args()

    config = load_config(Path(args.config))
    conn = connect_db(config)
    row = conn.execute(
        """
        SELECT applications.id, applications.profile_path,
               applications.tailored_resume_path, jobs.url, jobs.platform
        FROM applications JOIN jobs ON jobs.id=applications.job_id
        WHERE applications.id=?
        """,
        (args.application_id,),
    ).fetchone()
    if not row or row["platform"] != "shixiseng_cdp":
        raise SystemExit("该申请不是实习僧批次岗位")
    profile = json.loads(Path(row["profile_path"]).read_text(encoding="utf-8"))
    if profile.get("safety", {}).get("allow_submit"):
        raise SystemExit("安全错误：allow_submit 必须保持为 false")
    resume_path = Path(row["tailored_resume_path"])
    if not resume_path.is_file():
        raise SystemExit(f"定制简历不可用： {resume_path}")

    load_env_file(Path(args.env))
    _, cdp_url = resolve_browser_connection(config)
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=30_000)
        context = browser.contexts[0]
        resolution = resolve_application_page(
            context,
            conn,
            application_id=row["id"],
            expected_url=row["url"],
            browser_mode="windows_cdp",
        )
        page = resolution.page
        if resolution.created or not page_matches_application(page.url, row["url"]):
            page.goto(row["url"], wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2_000)
        register_application_page(
            conn,
            resolution,
            application_id=row["id"],
            expected_url=row["url"],
            browser_mode="windows_cdp",
        )

        body = _body(page)
        password_login = page.locator("input[name='username'], input[name='password']")
        visible_login_input = any(
            password_login.nth(index).is_visible()
            for index in range(password_login.count())
        )
        # 实习僧完成身份验证后仍会将登录弹窗保留在 DOM 中。
        # 因此，不能根据隐藏的用户名/密码输入框判断登录状态。
        signed_out = "登录/注册" in body or visible_login_input
        if signed_out:
            status = "authentication_required"
            note = (
                "Chrome 重启后，实习僧会话处于登出状态。请在已登记的标签页中手动登录，"
                "然后重新运行此适配器。"
            )
            stage = "login"
        elif "投个简历" in body:
            # 实习僧可能会将此操作实现为立即投递。除非已批准此确切岗位的提交操作，
            # 否则不要点击。
            status = "delivery_confirmation_required"
            note = (
                "已登录的实习僧职位已备好定制中文简历，但未点击可能会立即投递的“投个简历”控件。"
            )
            stage = "application_entry"
        else:
            status = "portal_state_review_required"
            note = "无法识别实习僧页面状态；未使用投递控件。"
            stage = "unrecognized"

        artifact = save_fill_test_artifact(
            page,
            JOBBOT_OUTPUT / "applications" / str(row["id"]),
            adapter="shixiseng",
            stage=stage,
            status=status,
            metadata={
                "tab_resolution": resolution.method,
                "resume_present": True,
                "delivery_clicked": False,
            },
        )
        _update(conn, row["id"], status, note, page.url)
        print(
            f"Shixiseng application {row['id']}: {status}; "
            f"screenshot={artifact['screenshot_path']}"
        )


if __name__ == "__main__":
    main()
