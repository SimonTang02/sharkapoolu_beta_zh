#!/usr/bin/env python3
"""完成已授权的 MediaTek 候选人档案信息，但不申请岗位。

此适配器仅在已完成身份验证的档案页面运行，不会打开岗位申请页面，也不包含最终提交控件的选择器。
"""

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
from job_bot.application_bot import resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.bot import load_config, load_env_file  # noqa: E402
from private_paths import APPLICATION_PROFILE, CREDENTIALS_FILE, JOBBOT_OUTPUT  # noqa: E402


PROFILE_URL_PART = "careers.mediatek.com/zh-cn/dashboard/my-resume"
MONTH_NAMES = (
    "一月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "十一月", "十二月",
)


def visible(locator):
    return locator.filter(visible=True) if hasattr(locator, "filter") else locator


def select_option(page, combobox, option_name: str) -> None:
    combobox.click()
    option = page.get_by_role("option", name=option_name, exact=True)
    if not option.count():
        option = page.get_by_role("option").filter(has_text=option_name)
    if not option.count():
        months = [
            "一月", "二月", "三月", "四月", "五月", "六月",
            "七月", "八月", "九月", "十月", "十一月", "十二月",
        ]
        if option_name.isdigit() or option_name in months:
            # 年份列表在 52px 行高的滚动视口中虚拟化显示。
            # 滚动所属视口，直到所需选项加载出来。
            mounted = page.get_by_role("option")
            if not mounted.count():
                raise RuntimeError("MediaTek 年份列表打开后未显示选项")
            mounted.first.evaluate(
                """(el, offset) => {
                    let p = el.parentElement;
                    while (p && p.scrollHeight <= p.clientHeight) p = p.parentElement;
                    if (!p) throw new Error('未找到年份列表的滚动视口');
                    p.scrollTo(0, offset);
                }""",
                (
                    max(0, int(option_name) - 1946)
                    if option_name.isdigit()
                    else months.index(option_name)
                )
                * 52,
            )
            page.wait_for_timeout(1_000)
            mounted = page.get_by_role("option")
            option_index = (
                int(option_name) - 1946
                if option_name.isdigit()
                else months.index(option_name)
            )
            if mounted.count() <= option_index:
                mounted_text = [
                    " ".join(mounted.nth(i).inner_text().split())
                    for i in range(min(mounted.count(), 8))
                ]
                page.keyboard.press("Escape")
                raise RuntimeError(
                    f"MediaTek 虚拟年份选项未加载：{option_name}；"
                    f"count={mounted.count()} first={mounted_text}"
                )
            mounted.nth(option_index).click()
        else:
            mounted = page.get_by_role("option")
            if not mounted.count():
                raise RuntimeError("MediaTek 选择列表打开后未显示选项")
            mounted.first.evaluate(
                """el => {
                    let p=el.parentElement;
                    while(p && p.scrollHeight <= p.clientHeight) p=p.parentElement;
                    if (!p) throw new Error('未找到选择列表的滚动视口');
                    p.scrollTo(0, Math.min(10000, p.scrollHeight - p.clientHeight));
                }"""
            )
            page.wait_for_timeout(1_000)
            option = page.get_by_role("option", name=option_name, exact=True)
            if not option.count():
                option = page.get_by_role("option").filter(has_text=option_name)
            if not option.count():
                page.keyboard.press("Escape")
                raise RuntimeError(f"展开后未找到 MediaTek 选项：{option_name}")
            option.last.click()
    elif option.count():
        option.last.click()
    page.wait_for_timeout(250)
    selected = " ".join(combobox.inner_text().split())
    if selected != option_name:
        raise RuntimeError(
            f"MediaTek 选项未变更为 {option_name}；当前值={selected}"
        )


def current_education_editor(page):
    save = page.get_by_role("button", name="保存", exact=True)
    visible_saves = [save.nth(i) for i in range(save.count()) if save.nth(i).is_visible()]
    if not visible_saves:
        raise RuntimeError("未找到可见的 MediaTek 教育信息保存控件")
    editor = visible_saves[-1].locator(
        "xpath=ancestor::div[count(.//button[@role='combobox']) >= 8][1]"
    )
    if editor.count() != 1:
        raise RuntimeError("无法定位 MediaTek 教育信息编辑器")
    return editor, visible_saves[-1]


def choose_binary(editor, label: str, choice: str) -> None:
    prompt = editor.get_by_text(label, exact=False).first
    group = prompt.locator("xpath=following-sibling::*[1]")
    target = group.get_by_role("button", name=re.compile(rf"^{re.escape(choice)}"))
    if not target.count():
        # 提示文本和单选按钮通常位于同一父元素中。
        group = prompt.locator("xpath=parent::*")
        target = group.get_by_role("button", name=re.compile(rf"^{re.escape(choice)}"))
    if not target.count():
        index = 0 if "毕业" in label else 1
        target = editor.get_by_role(
            "button", name=re.compile(rf"^{re.escape(choice)}")
        )
        if target.count() <= index:
            raise RuntimeError(f"无法找到 {label}：{choice}")
        target = target.nth(index)
    if "text-orange-200" in (target.last.get_attribute("class") or ""):
        return
    target.last.click()


def fill_education(page, values: dict[str, str]) -> None:
    editor, save = current_education_editor(page)
    print("MediaTek 教育信息：设置毕业标记", flush=True)
    choose_binary(editor, "是否已毕业", values["graduated"])
    choose_binary(editor, "最高学历", values["highest"])
    boxes = editor.get_by_role("combobox")
    if boxes.count() < 8:
        raise RuntimeError(f"预期有 8 个 MediaTek 教育信息组合框，实际找到 {boxes.count()} 个")
    for index, key in (
        (0, "degree"),
        (1, "school"),
        (2, "start_month"),
        (3, "start_year"),
        (4, "end_month"),
        (5, "end_year"),
        (6, "category"),
        (7, "major"),
    ):
        expected = values[key]
        current = " ".join(boxes.nth(index).inner_text().split())
        if current != expected:
            print(f"MediaTek 教育信息：设置 {key}", flush=True)
            select_option(page, boxes.nth(index), expected)
    print("MediaTek 教育信息：正在保存", flush=True)
    save.click()
    page.wait_for_timeout(1_000)


def add_education(page) -> None:
    buttons = page.get_by_role("button", name="添加更多", exact=True)
    visible_buttons = [buttons.nth(i) for i in range(buttons.count()) if buttons.nth(i).is_visible()]
    if len(visible_buttons) < 2:
        raise RuntimeError("无法找到 Education ‘Add more’ 按钮")
    visible_buttons[1].click()
    page.wait_for_timeout(600)


def set_work_authorization(page) -> None:
    authorized = page.get_by_role(
        "button", name="我具有应征职缺当地的合法工作身分", exact=True
    )
    if not authorized.count():
        raise RuntimeError("未找到 MediaTek 工作授权答案")
    authorized.last.click()
    page.wait_for_timeout(400)


def set_gender(page) -> None:
    gender = page.get_by_role("combobox", name="性别", exact=True)
    if not gender.count():
        gender = page.locator("button[role=combobox]").filter(has_text="性别")
    name_input = page.locator("input[placeholder='名字...']")
    if not gender.count() and name_input.count():
        personal = name_input.first.locator(
            "xpath=ancestor::div[.//button[normalize-space()='保存']][1]"
        )
        gender = personal.locator("button[role=combobox]").first
    if not gender.count():
        edit = page.get_by_role("button", name="编辑", exact=True)
        if not edit.count():
            raise RuntimeError("未找到 MediaTek 个人信息编辑器")
        edit.first.click()
        page.wait_for_timeout(500)
        gender = page.get_by_role("combobox", name="性别", exact=True)
        if not gender.count():
            gender = page.locator("button[role=combobox]").filter(has_text="性别")
    if not gender.count():
        raise RuntimeError("未找到 MediaTek 性别组合框")
    select_option(page, gender.first, "男性")
    name_input = page.locator("input[placeholder='名字...']")
    editor = name_input.first.locator(
        "xpath=ancestor::div[.//button[normalize-space()='保存']][1]"
    )
    save = editor.get_by_role("button", name="保存", exact=True)
    if not save.count():
        raise RuntimeError("未找到 MediaTek 个人信息保存控件")
    save.first.click(timeout=10_000)
    page.wait_for_timeout(800)


def fill_work_experience(page, item: dict) -> None:
    title_inputs = page.locator("input[name^='workExperience.'][name$='.jobTitle']")
    visible_titles = [
        title_inputs.nth(i)
        for i in range(title_inputs.count())
        if title_inputs.nth(i).is_visible()
    ]
    if not visible_titles:
        adds = page.get_by_role("button", name="添加更多", exact=True)
        visible_adds = [adds.nth(i) for i in range(adds.count()) if adds.nth(i).is_visible()]
        if not visible_adds:
            raise RuntimeError("未找到 MediaTek Work Experience ‘Add more’ 按钮")
        visible_adds[0].click()
        page.wait_for_timeout(500)
        title_inputs = page.locator("input[name^='workExperience.'][name$='.jobTitle']")
        visible_titles = [
            title_inputs.nth(i)
            for i in range(title_inputs.count())
            if title_inputs.nth(i).is_visible()
        ]
    title = visible_titles[-1]
    editor = title.locator(
        "xpath=ancestor::div[.//button[normalize-space()='保存']][1]"
    )
    company = editor.locator("input[name$='.company']")
    duties = editor.locator("textarea[name$='.duties']")
    title.fill(str(item["job_title"]))
    company.fill(str(item["company"]))

    no = editor.get_by_role("button", name=re.compile(r"^否"))
    if not no.count():
        raise RuntimeError("未找到 MediaTek 当前就业状态答案")
    if "text-orange-200" not in (no.last.get_attribute("class") or ""):
        no.last.click()

    boxes = editor.get_by_role("combobox")
    months = [
        "", "一月", "二月", "三月", "四月", "五月", "六月",
        "七月", "八月", "九月", "十月", "十一月", "十二月",
    ]
    expected = (
        "实习",
        months[int(item["start_month"])],
        str(item["start_year"]),
        months[int(item["end_month"])],
        str(item["end_year"]),
    )
    for index, value in enumerate(expected):
        current = " ".join(boxes.nth(index).inner_text().split())
        if current != value:
            print(f"MediaTek 工作经历：设置字段 {index}", flush=True)
            select_option(page, boxes.nth(index), value)
    duties.fill(str(item.get("description", ""))[:4000])
    save = editor.get_by_role("button", name="保存", exact=True)
    if not save.count():
        raise RuntimeError("未找到 MediaTek Work Experience 保存控件")
    print("MediaTek 工作经历：正在保存", flush=True)
    save.last.click(timeout=10_000)
    page.wait_for_timeout(1_000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "job_bot/config.china_hk_ic_foreign.json"))
    parser.add_argument("--env", default=str(CREDENTIALS_FILE))
    parser.add_argument("--profile", default=str(APPLICATION_PROFILE))
    parser.add_argument(
        "--step",
        choices=("education", "authorization", "gender", "work"),
        required=True,
    )
    parser.add_argument("--education-index", type=int)
    parser.add_argument("--work-index", type=int, choices=(0, 1))
    args = parser.parse_args()

    # 在此加载档案是授权检查；不会记录任何字段值。
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    auth = profile.get("explicit_authorization", {})
    safety = profile.get("safety", {})
    disclosures = profile.get("voluntary_disclosures", {})
    if (
        not auth.get("user_confirmed")
        or not safety.get("allow_sensitive_answers")
        or disclosures.get("gender") != "Male"
    ):
        raise SystemExit("本地档案中缺少用户明确授权")
    load_env_file(Path(args.env))
    config = load_config(Path(args.config))
    _, cdp_url = resolve_browser_connection(config)

    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=30_000)
        pages = [
            page
            for context in browser.contexts
            for page in context.pages
            if PROFILE_URL_PART in page.url
        ]
        if not pages:
            raise SystemExit("请先打开已登录的 MediaTek 档案页面")
        page = pages[-1]
        if args.step != "gender":
            page.keyboard.press("Escape")
        if args.step == "education":
            education_records = profile.get("education", [])
            if args.education_index is None or args.education_index >= len(education_records):
                raise RuntimeError("需要有效的 --education-index")
            item = education_records[args.education_index]
            portal = item.get("portal_values", {}).get("mediatek", {})
            if not portal:
                raise RuntimeError("私有档案缺少 MediaTek 教育信息")
            saves = page.get_by_role("button", name="保存", exact=True)
            if not any(saves.nth(i).is_visible() for i in range(saves.count())):
                add_education(page)
            fill_education(
                page,
                {
                    "graduated": str(portal["graduated"]),
                    "highest": str(portal["highest"]),
                    "degree": str(portal["degree"]),
                    "school": str(item["school"]),
                    "start_month": MONTH_NAMES[int(item["from_month"]) - 1],
                    "start_year": str(item["from_year"]),
                    "end_month": MONTH_NAMES[int(item["to_month"]) - 1],
                    "end_year": str(item["to_year"]),
                    "category": str(portal["category"]),
                    "major": str(portal["major"]),
                },
            )
        elif args.step == "authorization":
            set_work_authorization(page)
        elif args.step == "gender":
            set_gender(page)
        else:
            work = profile.get("work_experience", [])
            if args.work_index is None or args.work_index >= len(work):
                raise RuntimeError("需要有效的 --work-index")
            fill_work_experience(page, work[args.work_index])
        artifact = save_fill_test_artifact(
            page,
            JOBBOT_OUTPUT / "application_profiles" / "mediatek",
            adapter="mediatek_profile",
            stage=args.step,
            status="step_completed",
            metadata={
                "work_index": args.work_index,
                "education_index": args.education_index,
            },
        )
        print(
            f"MediaTek profile step completed: {args.step}; "
            f"screenshot={artifact['screenshot_path']}"
        )


if __name__ == "__main__":
    main()
