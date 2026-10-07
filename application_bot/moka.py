#!/usr/bin/env python3
"""填写已知的 Moka 申请字段，并在预览/提交前停止。"""

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
from application_bot.tab_registry import (  # noqa: E402
    page_matches_application,
    register_application_page,
    resolve_application_page,
)
from application_bot.portal_registry import resolve_company_profile  # noqa: E402
from job_bot.application_bot import add_event, resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.bot import connect_db, load_config, load_env_file, utc_now  # noqa: E402
from private_paths import CREDENTIALS_FILE, JOBBOT_OUTPUT  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
DEFAULT_ENV = CREDENTIALS_FILE


def row_for(conn, application_id: int):
    row = conn.execute(
        """
        SELECT applications.id, applications.job_id, applications.profile_path,
               applications.tailored_resume_path, jobs.url, jobs.platform,
               jobs.location, jobs.role_kind, jobs.company, jobs.title
        FROM applications JOIN jobs ON jobs.id = applications.job_id
        WHERE applications.id = ?
        """,
        (application_id,),
    ).fetchone()
    if not row or row["platform"] != "moka_cdp":
        raise SystemExit("该申请不是 Moka 批次岗位")
    return row


def update_status(conn, row, status: str, note: str, draft_url: str) -> None:
    conn.execute(
        """
        UPDATE applications SET status=?, notes=?, last_error=NULL,
            draft_url=?, updated_at=? WHERE id=?
        """,
        (status, note, draft_url, utc_now(), row["id"]),
    )
    conn.execute(
        """
        UPDATE application_campaign_jobs SET status=?, last_error=?
        WHERE application_id=?
        """,
        (status, note, row["id"]),
    )
    add_event(conn, row["id"], "moka_progress", {"status": status, "url": draft_url})
    conn.commit()


def digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def choose(page, field, value: str) -> None:
    field.click(force=True, timeout=5_000)
    page.wait_for_timeout(200)
    option = page.get_by_text(value, exact=True)
    visible = [option.nth(i) for i in range(option.count()) if option.nth(i).is_visible()]
    if not visible:
        page.keyboard.press("Escape")
        raise RuntimeError(f"未找到 Moka 选项： {value}")
    visible[-1].click(force=True, timeout=5_000)
    page.wait_for_timeout(200)


def find_input_by_value(page, expected: str):
    inputs = page.locator("input")
    for index in range(inputs.count()):
        field = inputs.nth(index)
        try:
            if field.input_value() == expected:
                return field
        except Exception:
            continue
    return None


def fill_if_empty(field, value: str) -> None:
    if value and not field.input_value().strip():
        field.fill(value)


def choose_if_needed(page, field, value: str) -> None:
    if value and field.input_value().strip() != value:
        choose(page, field, value)


def choose_phone_calling_code(page, phone_field, code: str) -> bool:
    """选择与此电话号码字段关联的区号，不要选择登录弹窗中的重复项。"""
    prefix_input = phone_field.locator("xpath=preceding::input[1]")
    try:
        prefix_input.click(force=True, timeout=5_000)
        page.wait_for_timeout(300)
        pattern = re.compile(rf"(?:^|\D){re.escape(code)}(?:\D|$)", re.I)
        candidates = page.get_by_text(pattern)
        visible = [
            candidates.nth(index)
            for index in range(candidates.count())
            if candidates.nth(index).is_visible()
        ]
        if not visible:
            page.keyboard.press("Escape")
            return False
        visible[-1].click(force=True, timeout=5_000)
        page.wait_for_timeout(300)
        return code in (prefix_input.input_value() or prefix_input.locator("xpath=..").inner_text())
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def login_prompt_visible(page) -> bool:
    prompt = page.get_by_text("首次登录会自动创建新账号", exact=False)
    try:
        return any(prompt.nth(index).is_visible() for index in range(prompt.count()))
    except Exception:
        return False


def fill_work_experience(page, records: list[dict]) -> int:
    if not records:
        return 0
    company_fields = page.locator("input[placeholder='公司名称']")
    # Moka 会先初始化一条工作经历，再初始化一条实习/研究经历。
    # 这两条记录都可真实地填写用户的两段本科研究经历，
    # 使用已有记录项还可避开脆弱的门户专用“添加”界面。
    work_count = min(len(records), company_fields.count())

    year_fields = page.locator("input[placeholder='年']")
    month_fields = page.locator("input[placeholder='月']")
    job_titles = page.locator("input[placeholder='职位名称']")
    locations = page.locator("input[placeholder='工作地点']")
    content_fields = page.locator("textarea[placeholder='内容']")
    for index, record in enumerate(records[:work_count]):
        fill_if_empty(company_fields.nth(index), str(record.get("company") or ""))
        fill_if_empty(job_titles.nth(index), str(record.get("job_title") or ""))
        if index < locations.count():
            fill_if_empty(locations.nth(index), str(record.get("location") or ""))
        if index < content_fields.count():
            fill_if_empty(
                content_fields.nth(index), str(record.get("description") or "")
            )
        date_values = (
            record.get("start_year"),
            record.get("start_month"),
            record.get("end_year"),
            record.get("end_month"),
        )
        choose_if_needed(page, year_fields.nth(index * 2), str(date_values[0] or ""))
        choose_if_needed(page, month_fields.nth(index * 2), str(date_values[1] or ""))
        choose_if_needed(
            page, year_fields.nth(index * 2 + 1), str(date_values[2] or "")
        )
        choose_if_needed(
            page, month_fields.nth(index * 2 + 1), str(date_values[3] or "")
        )
    return work_count


def fill_projects(page, projects: list[dict]) -> int:
    if not projects:
        return 0
    project_names = page.locator("input[placeholder='项目名称']")
    project_count = min(len(projects), project_names.count())
    for index, project in enumerate(projects[:project_count]):
        name_field = project_names.nth(index)
        record = name_field.locator(
            "xpath=ancestor::div[.//textarea[@placeholder='项目中职责']][1]"
        )
        if not record.count():
            raise RuntimeError(f"未找到 Moka 项目记录 {index + 1}")
        fill_if_empty(name_field, str(project.get("name") or ""))
        role = record.locator("input[placeholder='职责']")
        if role.count():
            fill_if_empty(role.first, str(project.get("role") or ""))
        description = record.locator("textarea[placeholder='内容']")
        if description.count():
            fill_if_empty(description.first, str(project.get("description") or ""))
        duty = record.locator("textarea[placeholder='项目中职责']")
        if duty.count():
            fill_if_empty(duty.first, str(project.get("role") or ""))
        date_inputs = record.locator("input")
        date_values = (
            project.get("start_year"),
            project.get("start_month"),
            project.get("end_year"),
            project.get("end_month"),
        )
        if date_inputs.count() >= 4:
            for date_index, value in enumerate(date_values):
                choose_if_needed(page, date_inputs.nth(date_index), str(value or ""))
    return project_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env", default=str(DEFAULT_ENV))
    args = parser.parse_args()

    config = load_config(Path(args.config))
    conn = connect_db(config)
    row = row_for(conn, args.application_id)
    profile = json.loads(Path(row["profile_path"]).read_text(encoding="utf-8"))
    company_profile = resolve_company_profile(
        config, company=str(row["company"] or ""), adapter_id="moka"
    )
    company_rules = company_profile.get("rules", {})
    safety = profile.get("safety", {})
    if safety.get("allow_submit"):
        raise SystemExit("安全错误：allow_submit 必须保持为 false")
    fields = profile.get("fields", {})
    required = ("first_name", "last_name", "email", "phone")
    if any(not str(fields.get(key, "")).strip() for key in required):
        raise SystemExit("隔离的申请档案缺少联系方式")
    resume = Path(row["tailored_resume_path"])
    if not resume.is_file():
        raise SystemExit("缺少定制简历")

    load_env_file(Path(args.env))
    _, cdp_url = resolve_browser_connection(config)
    apply_url = row["url"].rstrip("/")
    if not apply_url.endswith("/apply"):
        apply_url += "/apply"
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
        for _ in range(20):
            if page.get_by_role("button", name="预览并提交", exact=True).count():
                break
            page.wait_for_timeout(1_000)
        if not page.get_by_role("button", name="预览并提交", exact=True).count():
            artifact = save_fill_test_artifact(
                page,
                JOBBOT_OUTPUT / "applications" / str(row["id"]),
                adapter="moka",
                stage="application_entry",
                status="authentication_required",
                metadata={"tab_resolution": resolution.method},
            )
            update_status(
                conn,
                row,
                "authentication_required",
                "Moka 申请表单未打开；需要手动登录或修复会话。",
                page.url,
            )
            print(
                f"Moka application {row['id']}: authentication_required; "
                f"screenshot={artifact['screenshot_path']}"
            )
            return

        # 此处使用 Moka 申请中国大陆雇主岗位。请使用申请人的
        # 明确中文法定姓名，不要拼造英文姓名。
        full_name = str(fields.get("chinese_name") or "").strip()
        if not full_name or not re.search(r"[\u3400-\u9fff]", full_name):
            raise SystemExit(
                "隔离的申请档案缺少此中国大陆申请所需的有效中文姓名"
            )
        page.locator("input[placeholder='姓名']").fill(full_name)
        raw_phone = str(fields["phone"]).strip()
        phone_digits = digits(raw_phone)
        phone_blocked = False
        if raw_phone.startswith("+852"):
            if not choose_phone_calling_code(
                page, page.locator("input[placeholder='请输入手机号']"), "+852"
            ):
                if login_prompt_visible(page):
                    artifact = save_fill_test_artifact(
                        page,
                        JOBBOT_OUTPUT / "applications" / str(row["id"]),
                        adapter="moka",
                        stage="phone_calling_code",
                        status="authentication_required",
                        metadata={"tab_resolution": resolution.method},
                    )
                    update_status(
                        conn, row, "authentication_required",
                        "打开电话区号控件时 Moka 登录已过期。",
                        page.url,
                    )
                    print(
                        f"Moka application {row['id']}: authentication_required; "
                        f"screenshot={artifact['screenshot_path']}"
                    )
                    return
                # 寒武纪当前的 Moka 表单仅提供 +86。不要将
                # 香港号码填入错误的区号。继续填写其他
                # 独立字段，以便只将此问题留给人工处理。
                phone_blocked = True
                phone_digits = ""
            else:
                phone_digits = phone_digits[3:]
        phone_field = page.locator("input[placeholder='请输入手机号']")
        if phone_digits:
            phone_field.fill(phone_digits)
        page.locator("input[placeholder='邮箱']").fill(str(fields["email"]))
        skill = page.locator("textarea[placeholder='请输入技能']")
        if skill.count():
            skill.fill(", ".join(str(value) for value in profile.get("skills", [])))

        file_inputs = page.locator("input[type=file]")
        if file_inputs.count():
            file_inputs.first.set_input_files(str(resume), timeout=10_000)
            page.wait_for_timeout(5_000)

        # 简历解析器会填写教育部分的大部分内容。仅补充
        # 有用户明确答案或简历事实支持的字段。
        gender_fields = page.locator("input[placeholder='请选择']")
        if gender_fields.count() and not gender_fields.first.input_value().strip():
            choose(page, gender_fields.first, "男")
        education_records = profile.get("education", [])
        for education_profile in education_records:
            portal = education_profile.get("portal_values", {}).get("moka", {})
            school_value = str(portal.get("school") or "").strip()
            school = find_input_by_value(page, school_value) if school_value else None
            if school is None:
                continue
            education = school.locator(
                "xpath=ancestor::div[.//input[@placeholder='请输入专业名称']][1]"
            )
            education_inputs = education.locator("input")
            if education_inputs.count() >= 4:
                end_month = education_profile.get("to_month")
                if row["role_kind"] == "full_time" and education_profile.get("to_year") == 2027:
                    end_month = 6
                choose_if_needed(
                    page, education_inputs.nth(2), str(education_profile.get("to_year") or "")
                )
                choose_if_needed(page, education_inputs.nth(3), str(end_month or ""))
            major = education.locator("input[placeholder='请输入专业名称']")
            if major.count():
                fill_if_empty(major.first, str(portal.get("major") or ""))
            degree = education.locator("input[placeholder='请选择']")
            if degree.count() and not degree.first.input_value().strip():
                choose(page, degree.first, str(portal.get("degree") or ""))
        work_count = fill_work_experience(
            page, list(profile.get("work_experience") or [])
        )
        project_count = fill_projects(page, list(profile.get("projects") or []))
        note = (
            "已填写已知的中文法定姓名、联系方式、获授权的性别、2027 年 6 月全职毕业信息、教育经历、"
            f"技能、{work_count} 条工作/研究经历和 {project_count} 条项目经历，"
            "并已上传定制简历。未知的出生日期、国籍、地址、身份和人口统计信息保持空白。浏览器停留"
            "在可编辑表单页面；未点击 Preview 或 Submit。"
        )
        if phone_blocked:
            note += (
                " 公司档案已确认此寒武纪表单目前仅提供 +86；"
                "其他独立字段均已填写，+852 电话字段仍待人工处理。"
            )
        final_status = "manual_required" if phone_blocked else "browser_form_started"
        artifact = save_fill_test_artifact(
            page,
            JOBBOT_OUTPUT / "applications" / str(row["id"]),
            adapter="moka",
            stage="editable_form",
            status=final_status,
            metadata={
                "tab_resolution": resolution.method,
                "company_profile": company_profile.get("id"),
                "phone_blocked": phone_blocked,
            },
        )
        register_application_page(
            conn,
            resolution,
            application_id=row["id"],
            expected_url=apply_url,
            browser_mode="windows_cdp",
        )
        update_status(conn, row, final_status, note, page.url)
        print(
            f"Moka application {row['id']}: {final_status}; "
            f"screenshot={artifact['screenshot_path']}"
        )


if __name__ == "__main__":
    main()
