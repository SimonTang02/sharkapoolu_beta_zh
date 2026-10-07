#!/usr/bin/env python3
"""准备 HKSTP Talent Pool 申请，并在审阅页面停止。"""

from __future__ import annotations

import argparse
import json
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
    PageResolution,
    cdp_target_id,
    register_application_page,
)
from job_bot.application_bot import add_event, resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.bot import connect_db, load_config, load_env_file, utc_now  # noqa: E402
from private_paths import CREDENTIALS_FILE, JOBBOT_OUTPUT  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
DEFAULT_ENV = CREDENTIALS_FILE
DEFAULT_PORTAL_URLS = {
    10: "https://talentjobseeker.hkstp.org/zh-CN/job/103706/Electronic-Engineer-Intern-",
}


def _body(page) -> str:
    return " ".join(page.locator("body").inner_text(timeout=8_000).split())


def _click_text(page, text: str) -> bool:
    buttons = page.locator("button").filter(has_text=text)
    visible_buttons = [
        buttons.nth(i) for i in range(buttons.count()) if buttons.nth(i).is_visible()
    ]
    if visible_buttons:
        visible_buttons[-1].click(force=True, timeout=5_000)
        return True
    matches = page.get_by_text(text, exact=True)
    visible = [matches.nth(i) for i in range(matches.count()) if matches.nth(i).is_visible()]
    if not visible:
        return False
    visible[-1].click(force=True, timeout=5_000)
    return True


def _local_hk_phone(phone: str) -> str:
    digits = "".join(character for character in phone if character.isdigit())
    return digits[3:] if digits.startswith("852") else digits


def _choose_page(context, portal_url: str):
    job_token = "/103706/"
    pages = [page for page in context.pages if not page.is_closed()]
    for page in reversed(pages):
        if (
            (urlsplit(page.url).hostname or "").casefold()
            == "talentjobseeker.hkstp.org"
            and job_token in page.url
        ):
            return page, "hkstp_job_page"
    for page in reversed(pages):
        if (urlsplit(page.url).hostname or "").casefold() == "talentjobseeker.hkstp.org":
            return page, "hkstp_authenticated_page"
    return context.new_page(), "new_page"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--portal-url")
    parser.add_argument("--availability", default="一周")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env", default=str(DEFAULT_ENV))
    args = parser.parse_args()

    portal_url = args.portal_url or DEFAULT_PORTAL_URLS.get(args.application_id)
    if not portal_url:
        raise SystemExit("此申请需要提供 HKSTP 门户网址")

    config = load_config(Path(args.config))
    conn = connect_db(config)
    row = conn.execute(
        """
        SELECT applications.id, applications.profile_path,
               applications.tailored_resume_path, jobs.company, jobs.title
        FROM applications JOIN jobs ON jobs.id=applications.job_id
        WHERE applications.id=?
        """,
        (args.application_id,),
    ).fetchone()
    if not row:
        raise SystemExit("申请记录不存在")
    profile = json.loads(Path(row["profile_path"]).read_text(encoding="utf-8"))
    if profile.get("safety", {}).get("allow_submit"):
        raise SystemExit("安全错误：allow_submit 必须保持为 false")
    resume_path = Path(row["tailored_resume_path"])
    if not resume_path.is_file():
        raise SystemExit(f"定制简历不可用： {resume_path}")
    if resume_path.stat().st_size > 2 * 1024 * 1024:
        raise SystemExit("HKSTP 仅接受不超过 2 MB 的简历")

    load_env_file(Path(args.env))
    _, cdp_url = resolve_browser_connection(config)
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=30_000)
        context = browser.contexts[0]
        page, method = _choose_page(context, portal_url)
        target_id = cdp_target_id(context, page)
        resolution = PageResolution(
            page=page,
            created=method == "new_page",
            method=method,
            target_id=target_id,
            tab_label=f"jobbot-application-{row['id']}",
        )
        register_application_page(
            conn,
            resolution,
            application_id=row["id"],
            expected_url=portal_url,
            browser_mode="windows_cdp",
        )

        if "/103706/" not in page.url:
            page.goto(portal_url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2_000)
        if "onepass.hkstp.org" in page.url:
            status = "authentication_required"
            note = "HKSTP 已跳转到 OnePass；请手动登录后重新运行。"
        else:
            body = _body(page)
            if (
                "action=preview" in page.url
                and any(term in body for term in ("提交", "Submit"))
            ):
                status = "review_ready"
                note = (
                    "HKSTP 审阅页面已打开，其中包含定制简历和已批准答案；"
                    "未点击最终提交。"
                )
            elif "/job/" in page.url and any(term in body for term in ("现在申请", "Apply Now")):
                if not (_click_text(page, "现在申请") or _click_text(page, "Apply Now")):
                    raise RuntimeError("无法点击 HKSTP 申请入口控件")
                page.wait_for_timeout(2_500)
                body = _body(page)
            if "action=preview" not in page.url:
                # 简历管理器会写入候选人档案，但不会提交岗位申请。
                selected_resume = page.locator("input.MuiSelect-nativeInput")
                has_resume = bool(
                    selected_resume.count()
                    and str(selected_resume.first.input_value() or "").strip()
                )
                if not has_resume and "点击此处上传简历" in body:
                    if not _click_text(page, "点击此处上传简历"):
                        raise RuntimeError("HKSTP 简历管理器未打开")
                    page.wait_for_timeout(1_000)
                    body = _body(page)
                if "action=manageCv" in page.url or page.locator("input[type=file]").count():
                    files = page.locator("input[type=file]")
                    if files.count() and not files.first.evaluate("el => el.files && el.files.length"):
                        files.first.set_input_files(str(resume_path), timeout=10_000)
                        page.wait_for_timeout(800)
                    resume_name = page.locator("input[placeholder='为您的履历命名']")
                    if resume_name.count():
                        resume_name.fill(resume_path.stem[:100] or "application_resume")
                    if not _click_text(page, "好的"):
                        raise RuntimeError("HKSTP 简历保存控件不可用")
                    page.wait_for_timeout(2_500)
                    body = _body(page)

                fields = profile.get("fields", {})
                if page.locator("input[name='name']").count():
                    page.locator("input[name='name']").fill(
                        f"{fields.get('first_name', '')} {fields.get('last_name', '')}".strip()
                    )
                if page.locator("input[name='country_code']").count():
                    page.locator("input[name='country_code']").fill("852")
                if page.locator("input[name='phone']").count():
                    page.locator("input[name='phone']").fill(
                        _local_hk_phone(str(fields.get("phone", "")))
                    )

                sponsorship_required = bool(
                    profile.get("explicit_authorization", {}).get(
                        "sponsorship_required", False
                    )
                )
                permit = page.locator("input[name='working_permit']")
                if permit.count():
                    if sponsorship_required and not permit.is_checked():
                        permit.check()
                    elif not sponsorship_required and permit.is_checked():
                        permit.uncheck()

                availability = page.locator("#availability_id")
                if availability.count() and not availability.input_value().strip():
                    if availability.get_attribute("aria-expanded") != "true":
                        availability.click(force=True)
                    option = page.get_by_role("option", name=args.availability, exact=True)
                    if not option.count():
                        option = page.get_by_text(args.availability, exact=True)
                    option.last.click(force=True, timeout=5_000)
                    page.wait_for_timeout(500)

                if not _click_text(page, "预览"):
                    if not _click_text(page, "Preview"):
                        raise RuntimeError("HKSTP 预览控件不可用")
                page.wait_for_timeout(2_500)
                body = _body(page)
                status = "review_ready"
                note = (
                    "HKSTP 表单字段和定制简历已准备就绪；审阅页面已打开，未点击最终提交。"
                )
                if any(term in body for term in ("必填", "required field")) and "/apply/" in page.url:
                    status = "form_validation_required"
                    note = "HKSTP 预览发现仍有必填字段未通过校验。"

        artifact = save_fill_test_artifact(
            page,
            JOBBOT_OUTPUT / "applications" / str(row["id"]),
            adapter="hkstp",
            stage="review",
            status=status,
            metadata={"tab_resolution": method, "portal_url": portal_url},
        )
        register_application_page(
            conn,
            resolution,
            application_id=row["id"],
            expected_url=portal_url,
            browser_mode="windows_cdp",
        )
        conn.execute(
            "UPDATE applications SET status=?, notes=?, draft_url=?, updated_at=? WHERE id=?",
            (status, note, page.url, utc_now(), row["id"]),
        )
        conn.execute(
            "UPDATE application_campaign_jobs SET status=?, last_error=? WHERE application_id=?",
            (status, note, row["id"]),
        )
        add_event(
            conn,
            row["id"],
            "hkstp_progress",
            {"status": status, "url": page.url, "submit_clicked": False},
        )
        conn.commit()
        print(
            f"HKSTP application {row['id']}: {status}; "
            f"screenshot={artifact['screenshot_path']}"
        )


if __name__ == "__main__":
    main()
