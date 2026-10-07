#!/usr/bin/env python3
"""填写已登录的实习僧中文档案，但不投递简历。"""

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
from job_bot.application_bot import add_event, resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.bot import connect_db, load_config, load_env_file  # noqa: E402
from private_paths import (  # noqa: E402
    APPLICATION_PROFILE,
    CREDENTIALS_FILE,
    JOBBOT_OUTPUT,
)


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"


def _visible(locator):
    return [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]


def _open_basic_editor(page) -> None:
    birth = page.locator("input[placeholder='选择出生年月']")
    if birth.count() and birth.first.is_visible():
        return
    card = page.locator(".basePannel")
    if not card.count():
        raise RuntimeError("未找到实习僧基本信息卡片")
    card.first.hover(timeout=5_000)
    edit = _visible(card.first.get_by_text("编辑", exact=True))
    if not edit:
        raise RuntimeError("未找到实习僧基本信息编辑控件")
    edit[-1].click(force=True, timeout=5_000)
    page.wait_for_timeout(600)


def _fill_known_fields(page, profile: dict) -> tuple[list[str], list[str]]:
    fields = profile.get("fields", {})
    changed: list[str] = []
    missing: list[str] = []
    # 当前 Vue
    # 版本将编辑器渲染为 `.basePannel` 的同级元素，因此按可见控件定位，而不依赖脆弱的 DOM 父级关系。
    text_inputs = _visible(page.locator("input.el-input__inner"))
    if not text_inputs:
        raise RuntimeError("未找到实习僧基本信息文本框")

    chinese_name = str(fields.get("chinese_name", "")).strip()
    if chinese_name and text_inputs[0].input_value().strip() != chinese_name:
        text_inputs[0].fill(chinese_name)
        changed.append("chinese_name")

    male = page.get_by_text("男", exact=True)
    male_options = _visible(male)
    selected_male = page.locator("input[type=radio][value='1']").first
    if male_options and not selected_male.is_checked():
        male_options[-1].click(force=True, timeout=5_000)
        changed.append("gender")

    email = str(fields.get("email", "")).strip()
    editable_text = [field for field in text_inputs if field.is_editable()]
    if email and len(editable_text) >= 2:
        email_field = editable_text[-1]
        if email_field.input_value().strip() != email:
            email_field.fill(email)
            changed.append("email")

    birth_field = page.locator("input[placeholder='选择出生年月']")
    if not birth_field.count() or not birth_field.first.input_value().strip():
        missing.append("birth_month")
    city_field = page.locator("input[placeholder='请选择']")
    if not city_field.count() or not city_field.first.input_value().strip():
        missing.append("current_city")
    if not email:
        missing.append("email")
    return changed, missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env", default=str(CREDENTIALS_FILE))
    parser.add_argument(
        "--profile",
        help="可选的档案覆盖项；默认使用隔离的申请档案。",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    conn = connect_db(config)
    row = conn.execute(
        """
        SELECT applications.id, applications.profile_path, jobs.platform
        FROM applications JOIN jobs ON jobs.id=applications.job_id
        WHERE applications.id=?
        """,
        (args.application_id,),
    ).fetchone()
    if not row or row["platform"] != "shixiseng_cdp":
        raise SystemExit("该申请不是实习僧批次岗位")
    profile_path = Path(args.profile) if args.profile else Path(row["profile_path"])
    if not profile_path.is_file():
        # 保留私有规范档案，作为
        # 手动创建申请记录时的兼容性后备项。
        profile_path = APPLICATION_PROFILE
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    load_env_file(Path(args.env))
    _, cdp_url = resolve_browser_connection(config)
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=30_000)
        label = f"jobbot-application-{args.application_id}"
        pages = [
            page
            for context in browser.contexts
            for page in context.pages
            if page.evaluate("window.name") == label
        ]
        if not pages:
            raise SystemExit(f"没有已打开且已登记的标签页：{label}")
        page = pages[-1]
        if "resume.shixiseng.com/resume/" not in page.url:
            raise SystemExit("已登记的实习僧标签页当前不在档案编辑器中")

        _open_basic_editor(page)
        changed, missing = _fill_known_fields(page, profile)
        # 在所有必填事实字段
        # 均填写之前，特意不保存。这样可避免将猜测的出生月份或当前城市
        # 写入候选人档案。
        status = "manual_required" if missing else "review_ready"
        artifact = save_fill_test_artifact(
            page,
            JOBBOT_OUTPUT / "applications" / str(args.application_id),
            adapter="shixiseng_profile",
            stage="chinese_basic_information",
            status=status,
            metadata={
                "language": "zh-CN",
                "changed_fields": changed,
                "missing_required_fields": missing,
                "profile_saved": False,
                "resume_uploaded": False,
                "delivery_clicked": False,
            },
            use_cdp=False,
            full_page=False,
        )
        add_event(
            conn,
            args.application_id,
            "shixiseng_profile_progress",
            {
                "status": status,
                "language": "zh-CN",
                "changed_fields": changed,
                "missing_required_fields": missing,
                "profile_saved": False,
                "delivery_clicked": False,
            },
        )
        conn.commit()
        print(
            f"Shixiseng profile {args.application_id}: {status}; "
            f"缺少字段={','.join(missing) or '无'}；"
            f"screenshot={artifact['screenshot_path']}"
        )


if __name__ == "__main__":
    main()
