#!/usr/bin/env python3
"""填写 Micron Eightfold 申请，并在提交申请前停止。

Micron 使用 Eightfold，但其岗位申请页面 (careers.micron.com/careers/apply) 与通用 Eightfold 启动流程不同：简历和联系方式页面之后，会出现一系列岗位专属的 Yes/No 组合框问题，以及一项条款同意复选框。此适配器只填写私有档案中已确认的事实，为人工审阅者列出其余必答问题，且绝不点击最终提交按钮。

官方成功回执是 Eightfold 提交后的档案审阅页面
("感谢您的申请" / "Thank you for your application")。检测过程为只读；不会执行提交操作。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
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

# 此适配器仅负责填写，不得提交。绝不能点击这些按钮；其中的文本
# 仅用于识别表单状态和断言安全约束。
SUBMIT_BUTTON_NAMES = ("提交申请", "Submit application", "Submit")

# Eightfold 提交后档案审阅页上的已确认成功标记。
SUCCESS_MARKERS = ("感谢您的申请", "thank you for your application")

# 在 Micron ASIC 数字设计表单中观察到的岗位专属问题。
# 除非能从已确认的档案事实中得出答案，否则 `answer` 为 None；
# 此类问题将保持未答状态，并报告给人工审阅者。
MICRON_QUESTION_LABELS = (
    "Are you at least 18 years old?",
    "Are you in one of the above five groups?",
    "When would you be available if an offer was accepted?",
    "Will you now or in the future require sponsorship",
    "Have you applied on any previous occasions for employment in any capacity with Micron?",
    "can you submit verification of your legal right to work",
    "Have you ever been terminated or asked to resign",
    "Do you have any friends/relatives presently employed by Micron?",
    "Are you a citizen of, or do you hold dual citizenship with any of these countries?",
    "Do you have any plans to join the board of directors",
    "Are you currently serving on the board of directors",
    "In the last 5 years, have you been an employee of a U.S. federal, state, or local government?",
)


def _local_phone(phone: str) -> str:
    """使用 +852 国家/地区代码时，返回本地号码部分。"""
    compact = "".join(character for character in phone if character.isdigit())
    return compact[3:] if compact.startswith("852") else compact


def _is_adult(dob: str | None, today: date | None = None) -> str | None:
    if not dob:
        return None
    try:
        parsed = date.fromisoformat(dob)
    except (TypeError, ValueError):
        return None
    reference = today or date.today()
    return "是" if (reference - parsed).days > 18 * 366 else "否"


def resolve_micron_answers(profile: dict, today: date | None = None) -> list[dict]:
    """仅根据已确认的档案事实为 Micron 问题匹配答案。

    每个已知问题返回一条记录。如果答案属于无法从档案推导的候选人事实，``answer`` 将为 None，必须由人工审阅者确认，不得猜测。
    """
    fields = profile.get("fields", {})
    results: list[dict] = []
    for label in MICRON_QUESTION_LABELS:
        answer: str | None = None
        if "Are you at least 18 years old" in label:
            answer = _is_adult(fields.get("date_of_birth"), today)
        results.append({"question": label, "answer": answer})
    return results


def classify_body(body: str) -> str:
    """根据渲染后的正文文本判断 Micron 页面状态。"""
    text = " ".join(str(body).casefold().split())
    if any(marker in text for marker in SUCCESS_MARKERS):
        return "submitted"
    if any(marker in text for marker in ("提交申请", "submit application", "apply for")):
        return "browser_form_started"
    if any(marker in text for marker in ("verification code", "check your email", "验证码", "密码")):
        return "authentication_required"
    if any(marker in text for marker in ("create account", "创建帐户", "创建账户")):
        return "account_creation_required"
    if any(marker in text for marker in ("privacy", "consent", "terms")):
        return "policy_consent_required"
    return "manual_required"


def _body_text(page) -> str:
    return " ".join(page.locator("body").inner_text(timeout=8_000).lower().split())


def _fill_if_present(page, selector: str, value: str) -> bool:
    field = page.locator(selector)
    if not field.count() or not value:
        return False
    field.first.fill(value)
    return True


def _confirmed_email(profile: dict) -> str:
    field = str(profile.get("fields", {}).get("email", "")).strip()
    if field:
        return field
    facts = profile.get("personal_facts_confirmation", {})
    for key in ("cross_employer_contact_and_interview_20260925",):
        block = facts.get(key, {})
        email = str(block.get("primary_university_email", "")).strip()
        if email:
            return email
    return ""


def _select_question(page, label_fragment: str, value: str) -> bool:
    """根据问题标签选择 Micron 岗位专属组合框选项。"""
    wrappers = page.locator("[data-test-id^='_______QUESTION_SETUP_']")
    for index in range(wrappers.count()):
        wrapper = wrappers.nth(index)
        inp = wrapper.locator("input[role='combobox']")
        if not inp.count():
            continue
        if inp.first.input_value():
            continue
        labelledby = inp.first.get_attribute("aria-labelledby") or ""
        label_text = ""
        for part in labelledby.split():
            label = page.locator(f"#{part}")
            if label.count():
                label_text = label.first.inner_text()
                break
        if label_fragment.casefold() not in label_text.casefold():
            continue
        inp.first.click()
        page.wait_for_timeout(450)
        option = page.get_by_role("option", name=value, exact=True)
        if not option.count():
            option = page.get_by_text(value, exact=True)
        if not option.count():
            continue
        option.first.click()
        page.wait_for_timeout(250)
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env", default=str(DEFAULT_ENV))
    args = parser.parse_args()
    config = load_config(Path(args.config))
    conn = connect_db(config)
    row = conn.execute(
        """
        SELECT applications.id, applications.profile_path,
               applications.tailored_resume_path, jobs.url, jobs.company, jobs.platform
        FROM applications JOIN jobs ON jobs.id=applications.job_id
        WHERE applications.id=?
        """,
        (args.application_id,),
    ).fetchone()
    if not row or row["platform"] != "eightfold":
        raise SystemExit("该申请不是 Eightfold 批次岗位")
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
        page.wait_for_timeout(1_500)
        status = classify_body(_body_text(page))

        # 只读操作：如果已存在官方成功回执，则报告该状态，
        # 不再操作表单。
        if status != "submitted":
            files = page.locator("input[type=file]")
            if files.count() and Path(row["tailored_resume_path"]).is_file():
                files.first.set_input_files(row["tailored_resume_path"], timeout=10_000)
                page.wait_for_timeout(3_000)

            email = _confirmed_email(profile)
            _fill_if_present(page, "input[name='email']", email)
            _fill_if_present(page, "[data-test-id='_____email']", email)
            _fill_if_present(page, "[data-test-id='_____firstname']", str(fields.get("first_name", "")))
            _fill_if_present(page, "[data-test-id='_____lastname']", str(fields.get("last_name", "")))
            phone = str(fields.get("phone", "")).strip()
            if phone:
                _fill_if_present(page, "[data-test-id='_____phone']", _local_phone(phone))
            _fill_if_present(page, "[data-test-id='_____city']", str(fields.get("city", "")))

            answers = resolve_micron_answers(profile)
            filled_questions: list[str] = []
            for item in answers:
                if item["answer"] and _select_question(page, item["question"], item["answer"]):
                    filled_questions.append(item["question"])
            unanswered = [item["question"] for item in answers if item["answer"] is None]
            page.wait_for_timeout(500)
            status = classify_body(_body_text(page))
        else:
            filled_questions = []
            unanswered = []

        note = (
            "Micron Eightfold 申请已准备就绪，未提交。仅在存在已确认事实时才填写简历、联系方式和可推导的问题；"
            "其余岗位专属问题和条款复选框留给人工审阅者处理。"
        )
        if status == "submitted":
            note = "已检测到 Micron 官方成功回执（致谢/档案审阅页面）。"
        artifact = save_fill_test_artifact(
            page,
            ROOT / "job_bot" / "out" / "applications" / str(row["id"]),
            adapter="micron",
            stage="editable_form" if status != "submitted" else "confirmation",
            status=status,
            metadata={
                "tab_resolution": resolution.method,
                "filled_questions": filled_questions,
                "unanswered_questions": unanswered,
            },
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
        add_event(conn, row["id"], "micron_progress", {
            "status": status,
            "url": page.url,
            "filled_questions": filled_questions,
            "unanswered_questions": unanswered,
        })
        conn.commit()
        print(
            f"Micron 申请 {row['id']}：{status}；"
            f"已填写={len(filled_questions)} 未答={len(unanswered)}；"
            f"截图={artifact['screenshot_path']}"
        )


if __name__ == "__main__":
    main()
