#!/usr/bin/env python3
"""推进 TI Oracle Candidate Experience 申请，但不提交。"""

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


def _document_path(profile: dict, key: str) -> Path | None:
    value = profile.get("documents", {}).get(key)
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_file() else None


def _replace_attachment(page, *, kind: str, path: Path) -> None:
    remove_text = f"REMOVE {kind}"
    remove = page.locator("button").filter(has_text=remove_text)
    visible = [remove.nth(i) for i in range(remove.count()) if remove.nth(i).is_visible()]
    if visible:
        visible[-1].click(force=True, timeout=8_000)
        page.wait_for_timeout(1_000)
    uploads = page.locator("input[name='attachment-upload']")
    for _ in range(10):
        if uploads.count():
            break
        page.wait_for_timeout(300)
        uploads = page.locator("input[name='attachment-upload']")
    if not uploads.count():
        raise RuntimeError(f"TI {kind.lower()} 附件上传控件不可用")
    uploads.last.set_input_files(str(path), timeout=10_000)
    for _ in range(15):
        page.wait_for_timeout(300)
        if page.locator("button").filter(has_text=remove_text).count():
            return
    raise RuntimeError(f"TI 未确认新的 {kind.lower()} 附件")


def _fill_blank(page, selector: str, value: str) -> bool:
    if not value:
        return False
    field = page.locator(selector)
    if not field.count() or not field.first.is_visible():
        return False
    if (field.first.input_value() or "").strip():
        return False
    if field.first.is_editable():
        field.first.fill(value)
        return True
    return False


def _answer_radio_question(page, question: str, answer: str) -> bool:
    block = page.locator(".input-row--radiogroup").filter(has_text=question)
    if not block.count():
        return False
    answers = block.get_by_text(answer, exact=True)
    visible = [answers.nth(i) for i in range(answers.count()) if answers.nth(i).is_visible()]
    if not visible:
        return False
    selected = block.locator(
        "input[type=radio]:checked, button[role=radio][aria-checked=true]"
    )
    if selected.count():
        selected_value = (selected.first.get_attribute("value") or "").strip()
        selected_label = " ".join(selected.first.locator("xpath=..").inner_text().split())
        if answer.casefold() in {selected_value.casefold(), selected_label.casefold()}:
            return False
    visible[-1].click(force=True, timeout=5_000)
    return True


def _select_combobox_if_blank(
    page, selector: str, answer: str, *, replace_conflict: bool = False
) -> bool:
    field = page.locator(selector)
    if not field.count() or not field.first.is_visible():
        return False
    current = (field.first.input_value() or "").strip()
    if current.casefold() == answer.casefold():
        return False
    if current and not replace_conflict:
        return False
    field.first.click(force=True, timeout=5_000)
    page.wait_for_timeout(400)
    options = page.get_by_text(answer, exact=True)
    visible_options = [
        options.nth(i) for i in range(options.count()) if options.nth(i).is_visible()
    ]
    if visible_options:
        visible_options[-1].click(force=True, timeout=5_000)
    else:
        # 有些 Oracle 下拉列表只有在输入后才会显示筛选行。
        if field.first.is_editable():
            field.first.fill(answer)
        page.wait_for_timeout(350)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
    page.wait_for_timeout(250)
    updated = (field.first.input_value() or "").strip()
    if replace_conflict and updated.casefold() != answer.casefold():
        raise RuntimeError(
            f"TI 下拉列表未接受已授权的 {answer!r} 选项"
        )
    return bool(updated)


def _fill_application_form(page, profile: dict) -> list[str]:
    fields = profile.get("fields", {})
    changed: list[str] = []
    for selector, key in (
        ("input[name='firstName']", "first_name"),
        ("input[name='knownAs']", "preferred_name"),
        ("input[name='lastName']", "last_name"),
    ):
        if _fill_blank(page, selector, str(fields.get(key, "")).strip()):
            changed.append(key)

    phone = str(fields.get("phone", "")).strip()
    local_phone = phone.replace(" ", "")
    if local_phone.startswith("+852"):
        local_phone = local_phone[4:]
        if _select_combobox_if_blank(
            page, "input[name='phoneNumber']", "Hong Kong (+852)"
        ):
            changed.append("phone_country_code")
    if _fill_blank(page, "input[type='tel'][aria-label='Phone Number']", local_phone):
        changed.append("phone")

    if _fill_blank(page, "input[name='siteLink-1']", str(fields.get("linkedin_url", ""))):
        changed.append("linkedin_url")

    # 从档案中读取，而不是硬编码。
    work_auth = profile.get("custom_answers", {}).get(
        "Are you legally authorized to work in the country where this position is located?"
    )
    if work_auth is not None:
        work_auth_str = "Yes" if work_auth else "No"
        if _answer_radio_question(
            page,
            "Do you have the necessary legal work authorization to work in the country of the job(s) to which you are applying?",
            work_auth_str
        ):
            changed.append("work_auth")

    gender = profile.get("voluntary_disclosures", {}).get("gender")
    if gender:
        if _select_combobox_if_blank(
            page,
            "input[name='CN-STANDARD-ORA_GENDER-STANDARD']",
            gender,
            replace_conflict=True,
        ):
            changed.append("gender")

    full_name = " ".join(
        part
        for part in (
            str(fields.get("first_name", "")).strip(),
            str(fields.get("last_name", "")).strip(),
        )
        if part
    )
    if _fill_blank(page, "input[name='fullName']", full_name):
        changed.append("e_signature")
    return changed


def _missing_required(page) -> list[str]:
    missing: list[str] = []
    controls = page.locator("input[aria-required='true'], textarea[aria-required='true']")
    for index in range(controls.count()):
        field = controls.nth(index)
        if not field.is_visible():
            continue
        try:
            value = (field.input_value() or "").strip()
        except Exception:
            continue
        if not value:
            missing.append(
                field.get_attribute("name")
                or field.get_attribute("aria-label")
                or field.get_attribute("id")
                or "unnamed_required_field"
            )
    groups = page.locator(".input-row--radiogroup")
    for index in range(groups.count()):
        group = groups.nth(index)
        required = group.locator(".input-row__label--required")
        selected = group.locator(
            "input[type=radio]:checked, button[role=radio][aria-checked=true]"
        )
        if required.count() and not selected.count():
            label = " ".join(required.first.inner_text().split())
            missing.append(label[:120])
    return sorted(set(missing))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env", default=str(DEFAULT_ENV))
    parser.add_argument(
        "--replace-documents",
        action="store_true",
        help="替换现有简历和求职信，然后在提交前停止。",
    )
    parser.add_argument(
        "--fill-form",
        action="store_true",
        help="填写已授权且已知的字段，并在 Submit 前停止。",
    )
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
    if not row or row["platform"] != "oracle_candidate_experience":
        raise SystemExit("该申请不是 TI Oracle 批次岗位")
    profile = json.loads(Path(row["profile_path"]).read_text(encoding="utf-8"))
    if profile.get("safety", {}).get("allow_submit"):
        raise SystemExit("安全错误：allow_submit 必须保持为 false")
    email = str(profile.get("fields", {}).get("email", "")).strip()
    if not email:
        raise SystemExit("隔离的档案中没有电子邮箱")

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
        register_application_page(
            conn,
            resolution,
            application_id=row["id"],
            expected_url=row["url"],
            browser_mode="windows_cdp",
        )
        application_form_open = (
            "/apply/section/" in page.url
            and page.locator("button").filter(has_text="SUBMIT").count()
            and "Supporting Documents and URLs" in page.locator("body").inner_text(timeout=8_000)
        )
        if application_form_open and args.replace_documents:
            resume_path = Path(row["tailored_resume_path"])
            cover_path = _document_path(profile, "cover_letter_path")
            if not resume_path.is_file():
                raise SystemExit(f"定制简历不可用： {resume_path}")
            if cover_path is None:
                raise SystemExit("定制求职信不可用")
            _replace_attachment(page, kind="RESUME", path=resume_path)
            _replace_attachment(page, kind="COVER LETTER", path=cover_path)
            changed = _fill_application_form(page, profile) if args.fill_form else []
            missing = _missing_required(page)
            if missing:
                status = "manual_required"
                note = (
                    "TI 定制材料和已授权的已知字段已准备就绪；以下必填字段仍待人工审阅：" + ", ".join(missing)
                )
            else:
                status = "review_ready"
                note = (
                    "TI 定制材料和已授权的已知字段已准备就绪；申请可供审阅，未点击 SUBMIT。"
                )
        elif application_form_open and args.fill_form:
            changed = _fill_application_form(page, profile)
            missing = _missing_required(page)
            status = "manual_required" if missing else "review_ready"
            note = (
                "TI 已填写获授权的已知字段；"
                + (
                    "以下必填字段仍待处理：" + ", ".join(missing)
                    if missing
                    else "申请可供审阅；未点击 SUBMIT。"
                )
            )
        elif application_form_open:
            changed = []
            missing = _missing_required(page)
            status = "browser_form_started"
            note = "TI 申请表已打开；未使用文档或提交控件。"
        else:
            email_field = page.locator("input[name='primary-email']")
            if not email_field.count():
                apply = page.locator("button.apply-now-button--apply-now")
                if not apply.count():
                    apply = page.get_by_role("button", name="Apply Now", exact=True)
                for _ in range(40):
                    if apply.count():
                        break
                    page.wait_for_timeout(500)
                    apply = page.locator("button").filter(has_text="Apply Now")
                if apply.count():
                    apply.last.click(force=True, timeout=5_000)
                    try:
                        page.locator("input[name='primary-email']").wait_for(
                            state="visible", timeout=25_000
                        )
                    except Exception:
                        pass
                    email_field = page.locator("input[name='primary-email']")
            if not email_field.count():
                status = "manual_required"
                note = "TI 职位页面已加载，但未检测到申请邮箱输入框。"
            else:
                email_field.fill(email)
                body = " ".join(
                    page.locator("body").inner_text(timeout=5_000).lower().split()
                )
                next_button = page.get_by_role("button", name="Next", exact=True)
                policy_present = any(
                    term in body
                    for term in ("privacy notice", "terms and conditions", "consent")
                )
                consent_inputs = page.locator('input[type="checkbox"]')
                consent_checked = any(
                    consent_inputs.nth(index).is_checked()
                    for index in range(consent_inputs.count())
                )
                if policy_present and not consent_checked:
                    status = "policy_consent_required"
                    note = (
                        "已填写 TI 邮箱；必需的条款同意仍需明确授权并由人工确认。"
                    )
                else:
                    next_button.click(timeout=5_000)
                    page.wait_for_timeout(4_000)
                    body = " ".join(
                        page.locator("body").inner_text(timeout=5_000).lower().split()
                    )
                    files = page.locator("input[type=file]")
                    if files.count() and Path(row["tailored_resume_path"]).is_file():
                        files.first.set_input_files(
                            row["tailored_resume_path"], timeout=10_000
                        )
                        page.wait_for_timeout(2_000)
                    if any(
                        term in body
                        for term in ("verification code", "enter your password", "sign in")
                    ):
                        status = "authentication_required"
                        note = (
                            "TI 已接受获授权的邮箱，现在需要账户验证/登录。"
                        )
                    else:
                        status = "browser_form_started"
                        note = (
                            "TI 已接受邮箱并打开申请流程；未点击提交控件。"
                        )

        artifact = save_fill_test_artifact(
            page,
            JOBBOT_OUTPUT / "applications" / str(row["id"]),
            adapter="ti_oracle",
            stage="application_entry",
            status=status,
            metadata={
                "tab_resolution": resolution.method,
                "documents_replaced": bool(args.replace_documents and application_form_open),
                "authorized_fields_changed": changed if application_form_open else [],
                "missing_required_fields": missing if application_form_open else [],
                "submit_clicked": False,
            },
            # 通过 Windows 到 WSL 的 CDP 代理使用原始 Page.captureScreenshot 时，Oracle CX
            # 偶尔会卡住。Playwright 限时全页截图
            # 足以覆盖此表单，并能避免阻塞批次运行器。
            use_cdp=False,
            full_page=False,
        )
        register_application_page(
            conn,
            resolution,
            application_id=row["id"],
            expected_url=row["url"],
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
        add_event(conn, row["id"], "ti_oracle_progress", {"status": status, "url": page.url})
        conn.commit()
        print(
            f"TI application {row['id']}: {status}; "
            f"screenshot={artifact['screenshot_path']}"
        )


if __name__ == "__main__":
    main()
