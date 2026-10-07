#!/usr/bin/env python3
"""填写 Infineon Eightfold 表单，并在提交申请前停止。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


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
from private_paths import CREDENTIALS_FILE  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
DEFAULT_ENV = CREDENTIALS_FILE


def choose(page, field, value: str) -> None:
    field.click(force=True, timeout=5_000)
    page.wait_for_timeout(800)
    option = page.get_by_role("option", name=value, exact=True)
    if not option.count():
        option = page.get_by_text(value, exact=True)
    if not option.count():
        option = page.get_by_text(value, exact=False)
    visible = [option.nth(i) for i in range(option.count()) if option.nth(i).is_visible()]
    if not visible:
        page.keyboard.press("Escape")
        raise RuntimeError(f"未找到 Infineon 选项：{value}")
    visible[-1].click(force=True, timeout=5_000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-id", type=int, default=92)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env", default=str(DEFAULT_ENV))
    args = parser.parse_args()
    config = load_config(Path(args.config))
    conn = connect_db(config)
    row = conn.execute(
        """
        SELECT applications.id, applications.profile_path,
               applications.tailored_resume_path, applications.cover_letter_path,
               jobs.url, jobs.company
        FROM applications JOIN jobs ON jobs.id=applications.job_id
        WHERE applications.id=?
        """,
        (args.application_id,),
    ).fetchone()
    if not row or row["company"] != "Infineon Technologies":
        raise SystemExit("该申请不是 Infineon 批次岗位")
    profile = json.loads(Path(row["profile_path"]).read_text(encoding="utf-8"))
    if profile.get("safety", {}).get("allow_submit"):
        raise SystemExit("安全错误：allow_submit 必须保持为 false")
    fields = profile.get("fields", {})

    load_env_file(Path(args.env))
    _, cdp_url = resolve_browser_connection(config)
    parts = urlsplit(row["url"])
    pid = parts.path.rstrip("/").split("/")[-1]
    apply_url = f"{parts.scheme}://{parts.netloc}/careers/apply?pid={pid}"
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=30_000)
        context = browser.contexts[0]
        resolution = resolve_application_page(
            context,
            conn,
            application_id=row["id"],
            expected_url=apply_url,
            browser_mode="windows_cdp",
        )
        page = resolution.page
        if resolution.created or not page_matches_application(page.url, apply_url):
            page.goto(apply_url, wait_until="domcontentloaded", timeout=45_000)
        register_application_page(
            conn,
            resolution,
            application_id=row["id"],
            expected_url=apply_url,
            browser_mode="windows_cdp",
        )
        for _ in range(40):
            if page.get_by_role("button", name="Submit application", exact=True).count():
                break
            page.wait_for_timeout(500)
        if not page.get_by_role("button", name="Submit application", exact=True).count():
            raise RuntimeError("Infineon 申请表单未能完成加载")
        page.keyboard.press("Escape")

        location = page.locator(
            "input[aria-labelledby^='Position_Location_s__position_location']"
        )
        if location.count() and not location.input_value().strip():
            choose(page, location, "Shanghai上海 (China)")
        page.locator("#Contact_Information_firstname").fill(str(fields["first_name"]))
        page.locator("#Contact_Information_lastname").fill(str(fields["last_name"]))
        page.locator("#Contact_Information_email").fill(str(fields["email"]))

        raw_phone = str(fields["phone"])
        phone_digits = re.sub(r"\D", "", raw_phone)
        code = page.get_by_role("combobox", name="Country code")
        if raw_phone.startswith("+852"):
            choose(page, code, "+852")
            phone_digits = phone_digits[3:]
        page.locator("input[placeholder='Phone Number']").fill(phone_digits)

        gender = page.locator("input[aria-labelledby*='Bewerber_Anrede']")
        if gender.count() and not gender.input_value().strip():
            choose(page, gender, "Male")
        language = page.locator("input[aria-labelledby*='Bewerbung_Korrespondenz']")
        if language.count() and not language.input_value().strip():
            choose(page, language, "English")

        file_inputs = page.locator("input[type=file]")
        if file_inputs.count():
            file_inputs.nth(0).set_input_files(row["tailored_resume_path"], timeout=10_000)
            page.wait_for_timeout(1_000)
        if file_inputs.count() > 1 and row["cover_letter_path"]:
            file_inputs.nth(1).set_input_files(row["cover_letter_path"], timeout=10_000)

        status = "browser_form_started"
        note = (
            "已填写 Infineon 地点、定制简历/求职信、联系方式、获授权的性别、电话和申请语言。"
            "居住国家和首选入职日期仍未回答。未点击提交申请。"
        )
        artifact = save_fill_test_artifact(
            page,
            ROOT / "job_bot" / "out" / "applications" / str(row["id"]),
            adapter="infineon_eightfold",
            stage="editable_form",
            status=status,
            metadata={"tab_resolution": resolution.method},
        )
        register_application_page(
            conn,
            resolution,
            application_id=row["id"],
            expected_url=apply_url,
            browser_mode="windows_cdp",
        )
        conn.execute(
            "UPDATE applications SET status=?, notes=?, last_error=NULL, draft_url=?, updated_at=? WHERE id=?",
            (status, note, page.url, utc_now(), row["id"]),
        )
        conn.execute(
            "UPDATE application_campaign_jobs SET status=?, last_error=? WHERE application_id=?",
            (status, note, row["id"]),
        )
        add_event(conn, row["id"], "infineon_progress", {"status": status, "url": page.url})
        conn.commit()
        print(
            f"Infineon application {row['id']}: {status}; "
            f"screenshot={artifact['screenshot_path']}"
        )


if __name__ == "__main__":
    main()
