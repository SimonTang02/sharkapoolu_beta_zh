#!/usr/bin/env python3
"""准备 AMD iCIMS 申请直至同意/CAPTCHA 环节，绝不提交。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / ".python_packages"
if LOCAL_PACKAGES.is_dir() and str(LOCAL_PACKAGES) not in sys.path:
    sys.path.insert(0, str(LOCAL_PACKAGES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application_bot.artifacts import save_fill_test_artifact  # noqa: E402
from application_bot.campaign_progress import render  # noqa: E402
from application_bot.tab_registry import (  # noqa: E402
    page_matches_application,
    register_application_page,
    resolve_application_page,
)
from job_bot.application_bot import add_event, resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.bot import connect_db, load_config, load_env_file, utc_now  # noqa: E402
from private_paths import APPLICATION_PROFILE, CREDENTIALS_FILE, JOBBOT_OUTPUT  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
DEFAULT_ENV = CREDENTIALS_FILE
DEFAULT_PROFILE = APPLICATION_PROFILE


def application_row(conn, application_id: int):
    row = conn.execute(
        """
        SELECT applications.id, applications.profile_path, jobs.id AS job_id,
               jobs.url, jobs.company, jobs.location, jobs.title, jobs.role_kind
        FROM applications JOIN jobs ON jobs.id = applications.job_id
        WHERE applications.id = ?
        """,
        (application_id,),
    ).fetchone()
    if not row or row["company"].lower() != "amd":
        raise SystemExit("该申请不属于 AMD 批次")
    match = re.search(r"/jobs/(\d+)", row["url"])
    if not match:
        raise SystemExit("无法提取 AMD iCIMS 岗位 ID")
    return row, match.group(1)


def update_status(conn, application_id: int, job_id: int, status: str, note: str) -> None:
    now = utc_now()
    conn.execute(
        "UPDATE applications SET status=?, last_error=?, updated_at=? WHERE id=?",
        (status, note, now, application_id),
    )
    conn.execute(
        """
        UPDATE application_campaign_jobs SET status=?, last_error=?
        WHERE application_id=?
        """,
        (status, note, application_id),
    )
    add_event(conn, application_id, "amd_icims_progress", {"status": status, "note": note})
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env", default=str(DEFAULT_ENV))
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--open-only", action="store_true")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    conn = connect_db(config)
    row, external_id = application_row(conn, args.application_id)
    profile_path = Path(row["profile_path"] or args.profile)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    consent = profile.get("explicit_authorization", {}).get("company_consents", {})
    if not consent.get("amd_applicant_privacy_statement"):
        raise SystemExit("尚未明确授权 AMD 隐私同意")
    if profile.get("safety", {}).get("allow_submit"):
        raise SystemExit("安全错误：allow_submit 必须保持为 false")

    load_env_file(Path(args.env))
    _, cdp_url = resolve_browser_connection(config)
    title = str(row["title"] or "")
    is_student_role = row["role_kind"] == "internship" or bool(
        re.search(r"\b(?:intern(?:ship)?|co-?op)\b", title, re.I)
    )
    icims_host = "campus-amd.icims.com" if is_student_role else "global-external-amd.icims.com"
    target_url = f"https://{icims_host}/jobs/{external_id}/login"
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=30_000)
        context = browser.contexts[0]
        resolution = resolve_application_page(
            context,
            conn,
            application_id=args.application_id,
            expected_url=target_url,
            browser_mode="windows_cdp",
        )
        page = resolution.page
        if resolution.created or not page_matches_application(page.url, target_url):
            page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
        register_application_page(
            conn,
            resolution,
            application_id=args.application_id,
            expected_url=target_url,
            browser_mode="windows_cdp",
        )
        page.wait_for_timeout(5_000)
        if args.open_only:
            print("已打开 AMD iCIMS 申请入口")
            return

        email = str(profile.get("fields", {}).get("email", "")).strip()
        if not email:
            raise SystemExit("隔离的申请档案中没有电子邮箱")
        touched = False
        captcha = any("hcaptcha" in frame.url.lower() for frame in page.frames)
        login_frames = []
        for _ in range(20):
            login_frames = []
            for frame in page.frames:
                try:
                    if frame.locator("input[name='css_loginName']").count():
                        login_frames.append(frame)
                except Exception:
                    # iCIMS 会在导航过程中替换启动 iframe。
                    continue
            if login_frames:
                break
            page.wait_for_timeout(1_000)
        if login_frames:
            frame = login_frames[-1]
            field = frame.locator("input[name='css_loginName']")
            field.fill(email, timeout=5_000)
            touched = True
            checkbox = frame.locator("input[name='accept_gdpr']")
            if checkbox.count() and not checkbox.is_checked():
                consent_changed = False
                label = frame.locator("label[for='accept_gdpr']")
                if label.count():
                    try:
                        label.click(force=True, timeout=5_000)
                        page.wait_for_timeout(300)
                        consent_changed = checkbox.is_checked()
                    except Exception:
                        consent_changed = False
                if not consent_changed:
                    try:
                        checkbox.check(force=True, timeout=5_000)
                        consent_changed = checkbox.is_checked()
                    except Exception:
                        consent_changed = False
                if not consent_changed:
                    try:
                        consent_changed = bool(
                            checkbox.evaluate(
                                """element => {
                                  element.checked = true;
                                  element.dispatchEvent(new Event('input', {bubbles: true}));
                                  element.dispatchEvent(new Event('change', {bubbles: true}));
                                  return element.checked;
                                }"""
                            )
                        )
                    except Exception:
                        consent_changed = False
                touched = touched or consent_changed
            try:
                normalized = " ".join(
                    frame.locator("body").inner_text(timeout=2_000).lower().split()
                )
                captcha = captcha or "protected by hcaptcha" in normalized
            except Exception:
                pass

        note = (
            "已打开 AMD iCIMS 申请入口；已填写获授权的邮箱/隐私字段。"
            "完成 hCaptcha 后，才能继续编辑申请表。"
            if captcha
            else "已打开 AMD iCIMS 申请入口并填写获授权字段；需要身份验证/人工审阅。"
        )
        status = "captcha_required" if captcha else "authentication_required"
        artifact = save_fill_test_artifact(
            page,
            ROOT / "job_bot" / "out" / "applications" / str(args.application_id),
            adapter="amd_icims",
            stage="login_consent_boundary",
            status=status,
            metadata={
                "changed_fields": touched,
                "tab_resolution": resolution.method,
            },
        )
        register_application_page(
            conn,
            resolution,
            application_id=args.application_id,
            expected_url=target_url,
            browser_mode="windows_cdp",
        )
        update_status(conn, args.application_id, row["job_id"], status, note)
        print(
            f"AMD 申请 {args.application_id}：{status}；已更改字段数={touched}；"
            f"截图={artifact['screenshot_path']}"
        )


if __name__ == "__main__":
    main()
