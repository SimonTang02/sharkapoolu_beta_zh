"""NVIDIA Workday 浏览器辅助表单准备。

此适配器可以填写可见且明确映射的字段；仅在调用方主动启用时保存 Workday
草稿。它绝不点击最终 Submit 按钮。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.parse
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from application_bot.artifacts import save_fill_test_artifact
from application_bot.profile_policy import (
    has_confirmed_work_permission_scope,
    is_work_permission_question,
)
from application_bot.tab_registry import (
    mark_application_tab_closed,
    page_matches_application,
    register_application_page,
    resolve_application_page,
)
from private_paths import BROWSER_PROFILE_DIR


FIELD_ALIASES = {
    "how_did_you_hear": ["How Did You Hear About Us?"],
    "first_name": ["First Name", "Given Name"],
    "last_name": ["Last Name", "Family Name", "Surname"],
    "preferred_name": ["Preferred Name"],
    "chinese_name": [
        "Chinese Name",
        "Full Name in Chinese",
        "Legal Name in Chinese",
        "Name in Chinese",
        "中文姓名",
        "中文名",
    ],
    "native_first_name": [
        "Chinese Given Name",
        "Chinese First Name",
        "First Name in Chinese",
        "Local First Name",
        "Native First Name",
        "中文名字",
    ],
    "native_last_name": [
        "Chinese Family Name",
        "Chinese Last Name",
        "Last Name in Chinese",
        "Local Last Name",
        "Native Last Name",
        "中文姓氏",
    ],
    "email": ["Email Address", "Email"],
    "phone": ["Phone Number", "Phone"],
    "address_line_1": ["Address Line 1", "Street Address", "Address"],
    "city": ["City"],
    "postal_code": ["Postal Code", "ZIP Code", "Zip"],
    "country": ["Country"],
    "linkedin_url": ["LinkedIn", "LinkedIn Profile"],
    "github_url": ["GitHub", "GitHub Profile", "Website"],
}
DROPDOWN_FIELDS = {"how_did_you_hear", "country"}
BOOLEAN_FIELDS = {
    "previous_nvidia_worker": {
        "input_name": "candidateIsPreviousWorker",
        "input_values": ("true", "false"),
        "labels": ("Yes", "No"),
    },
}

FINAL_SUBMIT_RE = re.compile(r"^\s*(submit|submit application|提交|提交申请|遞交申請)\s*$", re.I)
SAVE_RE = re.compile(r"save(\s*(and|&)\s*continue)?|保存并继续|儲存並繼續", re.I)
APPLY_RE = re.compile(
    r"^\s*(apply|apply now|continue application|立即申请|立即申請|继续申请|繼續申請)\s*$",
    re.I,
)
APPLICATION_MODE_PATTERNS = {
    "resume": re.compile(r"^\s*autofill with resume\s*$", re.I),
    "manual": re.compile(r"^\s*apply manually\s*$", re.I),
    "last": re.compile(r"^\s*use my last application\s*$", re.I),
}
CONTINUE_RE = re.compile(r"^\s*(continue|next|继续|繼續|下一步)\s*$", re.I)
MANUAL_INTERVENTION_PATTERNS = (
    ("captcha", re.compile(r"captcha|verify (?:that )?you are human|security check", re.I)),
    ("mfa", re.compile(r"two-factor|multi-factor|verification code|one-time passcode", re.I)),
    ("account_verification", re.compile(r"verify your (?:email|account)|account verification", re.I)),
)


def _restrict_permissions(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def _playwright_api():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安装 Playwright。请运行：python3 -m pip install --target .python_packages "
            "-r job_bot/requirements-browser.txt && PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers "
            "PYTHONPATH=.python_packages python3 -m playwright install chromium --no-shell"
        ) from exc
    return sync_playwright


def detect_manual_intervention(page: Any) -> str:
    try:
        body_text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""
    for reason, pattern in MANUAL_INTERVENTION_PATTERNS:
        if pattern.search(body_text):
            return reason
    return ""


def find_project_chromium(project_root: Path) -> Path | None:
    candidates = sorted(
        (project_root / ".playwright-browsers").glob("chromium-*/chrome-linux64/chrome"),
        reverse=True,
    )
    return candidates[0] if candidates else None


def local_browser_environment(
    project_root: Path,
    windows_fonts: Path = Path("/mnt/c/Windows/Fonts"),
) -> dict[str, str]:
    """在 WSL 中运行 Chromium 时，使其能够使用 Windows 中日韩字体。

    主要申请浏览器是 Windows Chrome，无需额外处理。不过，local-persistent 回退方式
    运行的是 Linux Chromium 进程；标准 WSL 安装通常没有中日韩字体，因此即使 DOM 中
    保存了正确的 Unicode 文本，截图里仍可能显示方框字。
    """

    environment = dict(os.environ)
    known_cjk_fonts = (
        windows_fonts / "msyh.ttc",
        windows_fonts / "msyhbd.ttc",
        windows_fonts / "simsun.ttc",
    )
    if not any(font.is_file() for font in known_cjk_fonts):
        return environment

    runtime_dir = BROWSER_PROFILE_DIR / "fontconfig"
    cache_dir = runtime_dir / "cache"
    config_path = runtime_dir / "fonts.conf"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    config = f"""<?xml version=\"1.0\"?>
<!DOCTYPE fontconfig SYSTEM \"urn:fontconfig:fonts.dtd\">
<fontconfig>
  <include ignore_missing=\"yes\">/etc/fonts/fonts.conf</include>
  <dir>{escape(str(windows_fonts))}</dir>
  <cachedir>{escape(str(cache_dir))}</cachedir>
  <alias>
    <family>sans-serif</family>
    <prefer><family>Microsoft YaHei</family><family>SimSun</family></prefer>
  </alias>
  <alias>
    <family>serif</family>
    <prefer><family>SimSun</family><family>Microsoft YaHei</family></prefer>
  </alias>
</fontconfig>
"""
    if not config_path.is_file() or config_path.read_text(encoding="utf-8") != config:
        config_path.write_text(config, encoding="utf-8")
    _restrict_permissions(config_path, 0o600)
    _restrict_permissions(runtime_dir, 0o700)
    _restrict_permissions(cache_dir, 0o700)
    environment["FONTCONFIG_FILE"] = str(config_path)
    return environment


def parse_cookie_header(cookie_header: str, url: str) -> list[dict[str, str]]:
    cookies = []
    for pair in cookie_header.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        if name:
            cookies.append({"name": name, "value": value.strip(), "url": url})
    return cookies


def _fill_locator(locator: Any, value: str) -> bool:
    try:
        if not locator.is_visible() or locator.is_disabled():
            return False
        tag_name = locator.evaluate("element => element.tagName.toLowerCase()")
        input_type = (locator.get_attribute("type") or "").lower()
        if input_type in {"file", "checkbox", "radio", "hidden", "submit", "button"}:
            return False
        if tag_name == "select":
            locator.select_option(label=value)
        else:
            locator.fill(value)
        return True
    except Exception:
        return False


def _select_dropdown(
    page: Any,
    locator: Any,
    value: Any,
    trace: list[dict[str, Any]] | None = None,
) -> bool:
    path = value if isinstance(value, list) else [value]
    path = [str(item).strip() for item in path if str(item).strip()]
    if not path:
        return False
    final_value = path[-1]
    exact_final_value = re.compile(rf"^\s*{re.escape(final_value)}\s*$", re.I)

    def click_virtualized_option(pattern: re.Pattern[str]) -> bool:
        """在 Workday 虚拟列表中查找并立即点击选项。

        Workday 滚动列表时会重复使用相同的 DOM 节点。匹配后再返回 Locator 并不安全，
        因为调用方点击时，该节点可能已经代表另一条目（例如从 Penn 变成 Arizona 或
        Washington）。因此，文本核验和点击会在同一次虚拟列表迭代中完成。
        """
        # Workday 只渲染大型提示列表的一小段。NVIDIA 的院校列表和国际电话区号列表
        # 可能需要滚动数十个虚拟窗口，目标项才会出现在 DOM 中。
        for loop_index in range(60):
            options = page.locator('[data-automation-id="promptOption"]')
            visible_labels: list[str] = []
            popup_candidates = []
            for option_index in range(options.count()):
                candidate = options.nth(option_index)
                try:
                    # 已选中的多选标签也会暴露 promptOption 和 role=option。
                    # 它不属于当前打开的弹窗，且其祖先节点会滚动整个页面，因此在此排除。
                    if candidate.evaluate(
                        "el => !!el.closest('[data-automation-id=\"selectedItem\"]')"
                    ):
                        continue
                    popup_candidates.append(candidate)
                    label = candidate.inner_text(timeout=400).strip()
                    if candidate.is_visible(timeout=300):
                        visible_labels.append(label)
                    if pattern.search(label):
                        candidate.scroll_into_view_if_needed(timeout=2_000)
                        candidate.click(force=True, timeout=2_000)
                        if trace is not None:
                            trace.append({
                                "event": "option_clicked",
                                "label": label,
                                "loop": loop_index,
                            })
                        return True
                except Exception:
                    continue
            if not popup_candidates:
                if trace is not None:
                    trace.append({"event": "no_visible_options", "loop": loop_index})
                return False
            moved = popup_candidates[0].evaluate(
                """el => {
                    let p=el.parentElement;
                    while(p && p.scrollHeight <= p.clientHeight) p=p.parentElement;
                    if (!p) return false;
                    const before=p.scrollTop;
                    p.scrollTop=Math.min(
                        p.scrollTop + Math.max(180, p.clientHeight * .8),
                        p.scrollHeight
                    );
                    return p.scrollTop > before;
                }"""
            )
            if not moved:
                if trace is not None:
                    trace.append({
                        "event": "scroll_end_without_match",
                        "loop": loop_index,
                        "visible_labels": visible_labels[-8:],
                    })
                return False
            page.wait_for_timeout(100)
        return False

    def selection_is_visible() -> bool:
        try:
            current_value = locator.input_value().strip()
            if current_value.lower() == final_value.lower():
                return True
        except Exception:
            pass
        try:
            container = locator.locator(
                "xpath=ancestor::*[@data-automation-id='multiSelectContainer'][1]"
            )
            selected = container.locator(
                '[data-automation-id="selectedItem"]'
            ).filter(has_text=exact_final_value)
            return bool(selected.count() and selected.first.is_visible())
        except Exception:
            return False

    try:
        if not locator.is_visible() or locator.is_disabled():
            if trace is not None:
                trace.append({"event": "locator_unavailable"})
            return False
        if selection_is_visible():
            if trace is not None:
                trace.append({"event": "already_selected"})
            return True
        try:
            container = locator.locator(
                "xpath=ancestor::*[@data-automation-id='multiSelectContainer'][1]"
            )
            selected_items = container.locator(
                '[data-automation-id="selectedItem"]:visible'
            )
            for index in range(selected_items.count()):
                selected_item = selected_items.nth(index)
                if selected_item.inner_text().strip().lower() != final_value.lower():
                    delete_charm = selected_item.locator(
                        '[data-automation-id="DELETE_charm"]'
                    )
                    if delete_charm.count():
                        delete_charm.click(force=True)
                    else:
                        selected_item.press("Delete")
                    if trace is not None:
                        trace.append({"event": "old_selection_removed"})
                    page.wait_for_timeout(300)
        except Exception:
            pass
        # 之前的层级提示可能仍处于打开状态。此时点击另一个搜索框可能会关闭提示，
        # 而不是打开目标字段，因此先统一弹窗状态。
        for _ in range(3):
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
        locator.click(timeout=5_000)
        page.wait_for_timeout(800)
        if trace is not None:
            trace.append({"event": "dropdown_opened"})
        for index, segment in enumerate(path):
            exact_segment = re.compile(rf"^\s*{re.escape(segment)}\s*$", re.I)
            code_match = re.search(r"\(?(\+\d{1,4})\)?", segment)
            match_pattern = (
                re.compile(rf"{re.escape(code_match.group(1))}(?:\D|$)", re.I)
                if code_match
                else exact_segment
            )
            # 可搜索输入框还会让 Workday 加载原本被裁切的首段选项。
            # 它不能可靠筛选列表，因此仍须通过精确匹配和虚拟滚动确定点击项。
            try:
                locator.fill(segment, timeout=2_000)
                page.wait_for_timeout(400)
            except Exception:
                pass
            if not click_virtualized_option(match_pattern):
                if trace is not None:
                    trace.append({"event": "segment_not_found", "segment": segment})
                page.keyboard.press("Escape")
                return False
            page.wait_for_timeout(1000 if index < len(path) - 1 else 500)
        selected = selection_is_visible()
        if trace is not None:
            trace.append({"event": "selection_verified", "selected": selected})
        if not selected:
            page.keyboard.press("Escape")
        return selected
    except Exception:
        if trace is not None:
            trace.append({"event": "selection_exception"})
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def fill_safe_fields(
    page: Any,
    profile: dict[str, Any],
    company_rules: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    results = []
    fields = profile.get("fields", {})
    company_field_values = (company_rules or {}).get("field_values", {})
    raw_phone = str(fields.get("phone") or "").strip()
    phone_digits = re.sub(r"\D", "", raw_phone)
    phone_country_label = ""
    local_phone = raw_phone
    if phone_digits.startswith("852") and len(phone_digits) > 3:
        phone_country_label = "Hong Kong (+852)"
        local_phone = phone_digits[3:]
    elif phone_digits.startswith("86") and len(phone_digits) > 2:
        phone_country_label = "China (+86)"
        local_phone = phone_digits[2:]
    if (
        (company_rules or {}).get("phone_number_format") == "grouped_4"
        and len(local_phone) == 8
        and local_phone.isdigit()
    ):
        local_phone = f"{local_phone[:4]} {local_phone[4:]}"
    if phone_country_label:
        phone_code_trace: list[dict[str, Any]] = []
        code_field = page.get_by_label(
            re.compile(r"Country Phone Code", re.I)
        ).first
        if not code_field.count():
            code_field = page.locator("#phoneNumber--countryPhoneCode").first
        code_filled = bool(
            code_field.count()
            and _select_dropdown(
                page, code_field, phone_country_label, trace=phone_code_trace
            )
        )
        results.append(
            {
                "field": "phone_country_code",
                "status": "filled" if code_filled else "not_found",
                "matched_label": "Country Phone Code" if code_filled else "",
                "diagnostic": phone_code_trace,
            }
        )
    for field_name, aliases in FIELD_ALIASES.items():
        raw_value = company_field_values.get(field_name, fields.get(field_name))
        if raw_value is None or str(raw_value).strip() == "":
            continue
        filled = False
        matched_alias = ""
        for alias in aliases:
            locator = page.get_by_label(re.compile(re.escape(alias), re.I)).first
            if field_name == "how_did_you_hear" and not locator.count():
                locator = page.locator("#source--source").first
            if field_name in DROPDOWN_FIELDS:
                dropdown_trace: list[dict[str, Any]] = []
                filled_now = locator.count() and _select_dropdown(
                    page, locator, raw_value, trace=dropdown_trace
                )
            else:
                value = local_phone if field_name == "phone" else str(raw_value)
                filled_now = locator.count() and _fill_locator(locator, value)
            if filled_now:
                filled = True
                matched_alias = alias
                break
        results.append({
            "field": field_name,
            "status": "filled" if filled else "not_found",
            "matched_label": matched_alias,
            **(
                {"diagnostic": dropdown_trace}
                if field_name in DROPDOWN_FIELDS
                else {}
            ),
        })
    for field_name, field_config in BOOLEAN_FIELDS.items():
        raw_value = fields.get(field_name)
        if raw_value is None:
            continue
        labels = field_config["labels"]
        label = labels[0] if bool(raw_value) else labels[1]
        radio_index = 0 if bool(raw_value) else 1
        locator = page.locator(
            f'input[name="{field_config["input_name"]}"]'
            f'[value="{field_config["input_values"][radio_index]}"]'
        )
        filled = False
        diagnostic: dict[str, Any] = {"count": locator.count()}
        try:
            diagnostic["visible"] = locator.is_visible() if locator.count() else False
            if locator.count() and locator.is_visible():
                input_id = locator.get_attribute("id")
                click_targets = [locator]
                if input_id:
                    click_targets.append(page.locator(f'label[for="{input_id}"]'))
                click_targets.extend([
                    page.get_by_text(
                        re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)
                    ).last,
                    locator.locator("xpath=.."),
                ])
                for target in click_targets:
                    target.click(force=True)
                    page.wait_for_timeout(400)
                    filled = locator.is_checked()
                    if filled:
                        break
                diagnostic["checked_after_click"] = filled
        except Exception as exc:
            diagnostic["error"] = type(exc).__name__
            filled = False
        results.append({
            "field": field_name,
            "status": "filled" if filled else "not_found",
            "matched_label": label if filled else "",
            "diagnostic": diagnostic,
        })
    return results


def _select_single_choice(page: Any, button: Any, value: str) -> bool:
    try:
        button.click()
        option = page.get_by_role(
            "option", name=re.compile(rf"^\s*{re.escape(value)}\s*$", re.I)
        ).last
        option.wait_for(state="visible", timeout=5000)
        option.click()
        page.wait_for_timeout(300)
        return value.lower() in button.inner_text().strip().lower()
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def _fill_month_year(container: Any, month: Any, year: Any) -> dict[str, Any]:
    try:
        month_text = str(int(month))
        year_text = str(int(year))
    except (TypeError, ValueError):
        return {"filled": False, "rendered_value": "", "input_count": 0}

    inputs = container.locator("input:visible")
    input_count = inputs.count()
    try:
        if input_count == 1:
            field = inputs.first
            field.fill(f"{month_text}/{year_text}")
            field.press("Tab")
            rendered = field.input_value().strip()
            return {
                "filled": month_text in rendered and year_text in rendered,
                "rendered_value": rendered,
                "input_count": input_count,
            }

        month_field = container.locator(
            '[data-automation-id="dateSectionMonth-input"]'
        ).first
        year_field = container.locator(
            '[data-automation-id="dateSectionYear-input"]'
        ).first

        def replace_spinbutton(
            field: Any, text: str, *, explicit_delete: bool
        ) -> bool:
            # 展开的 Workday 表单可能将可编辑的日期微调框报告为位于视口之外。
            # 聚焦操作可避开指针坐标，同时仍使用控件正常的键盘处理逻辑。
            field.focus()
            field.press("Control+A")
            # 月份片段可能保留最后一位数字（例如 12 变成 2），因此只删除一次选中值。
            # 在年份片段重复删除会让焦点回到月份，并将年份首位误写进去，
            # 所以年份仅依靠选中后替换。
            if explicit_delete:
                field.press("Backspace")
            field.type(text, delay=35)
            field.press("Tab")
            rendered_value = (
                field.input_value().strip()
                or (field.get_attribute("aria-valuenow") or "").strip()
            )
            return rendered_value == text

        # Workday 可能把输入的年份首位写入相邻的月份片段。先填写年份，
        # 再最后写入月份，确保目标月份不会被该行为覆盖。
        year_ok = replace_spinbutton(
            year_field, year_text, explicit_delete=False
        )
        month_ok = replace_spinbutton(
            month_field, month_text, explicit_delete=True
        )
        month_rendered = (
            month_field.input_value().strip()
            or (month_field.get_attribute("aria-valuenow") or "").strip()
        )
        year_rendered = (
            year_field.input_value().strip()
            or (year_field.get_attribute("aria-valuenow") or "").strip()
        )
        rendered = "/".join((month_rendered, year_rendered))
        return {
            "filled": bool(month_ok and year_ok),
            "rendered_value": rendered,
            "input_count": input_count,
        }
    except Exception as exc:
        return {
            "filled": False,
            "rendered_value": "",
            "input_count": input_count,
            "error": f"{type(exc).__name__}: {' '.join(str(exc).split())[-300:]}",
        }


def fill_experience_fields(
    page: Any,
    profile: dict[str, Any],
    project_root: Path,
    company_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    results: dict[str, Any] = {
        "work_experience": [],
        "education": [],
        "languages": [],
        "skills": [],
        "documents": [],
    }
    work_experience = profile.get("work_experience", [])
    work_group = page.get_by_role("group").filter(
        has_text=re.compile(r"^\s*Work Experience", re.I)
    ).first

    def click_work_add(label: str) -> bool:
        candidates = work_group.get_by_role(
            "button", name=label, exact=True
        )
        # 不同租户的标记结构不同：NVIDIA 提供可访问性分组，而 MPS 当前将 Work Experience
        # 操作显示为页面上的第一个 Add 按钮。优先使用作用域内的控件，再按文档顺序选择。
        fallbacks = [candidates]
        if label == "Add":
            fallbacks.append(page.get_by_role("button", name="Add", exact=True))
        else:
            fallbacks.append(page.get_by_role("button", name="Add Another", exact=True))
        for collection in fallbacks:
            for button_index in range(collection.count()):
                button = collection.nth(button_index)
                try:
                    if button.is_visible():
                        button.click(timeout=10_000)
                        page.wait_for_timeout(500)
                        return True
                except Exception:
                    continue
        return False

    for index, entry in enumerate(work_experience):
        job_titles = page.locator('input[name="jobTitle"]:visible')
        if job_titles.count() <= index:
            add_label = "Add" if index == 0 else "Add Another"
            if not click_work_add(add_label):
                results["work_experience"].append({
                    "job_title": entry.get("job_title", ""),
                    "company": entry.get("company", ""),
                    "add_control_found": False,
                })
                break
        try:
            page.locator('input[name="jobTitle"]:visible').nth(index).wait_for(
                state="visible", timeout=8_000
            )
        except Exception:
            results["work_experience"].append({
                "job_title": entry.get("job_title", ""),
                "company": entry.get("company", ""),
                "add_control_found": True,
                "fields_rendered": False,
            })
            break

        job_ok = _fill_locator(
            page.locator('input[name="jobTitle"]:visible').nth(index),
            str(entry.get("job_title", "")),
        )
        company_ok = _fill_locator(
            page.locator('input[name="companyName"]:visible').nth(index),
            str(entry.get("company", "")),
        )
        location_ok = _fill_locator(
            page.locator('input[name="location"]:visible').nth(index),
            str(entry.get("location", "")),
        )
        start = page.locator(
            '[data-automation-id="formField-startDate"]'
        ).nth(index)
        end = page.locator(
            '[data-automation-id="formField-endDate"]'
        ).nth(index)
        start_result = _fill_month_year(
            start, entry.get("start_month"), entry.get("start_year")
        )
        end_result = _fill_month_year(
            end, entry.get("end_month"), entry.get("end_year")
        )
        description_ok = _fill_locator(
            page.locator(
                '[data-automation-id="formField-roleDescription"] textarea:visible'
            ).nth(index),
            str(entry.get("description", "")),
        )
        results["work_experience"].append({
            "job_title": entry.get("job_title", ""),
            "company": entry.get("company", ""),
            "job_title_filled": job_ok,
            "company_filled": company_ok,
            "location_filled": location_ok,
            "start_month_filled": start_result["filled"],
            "start_year_filled": start_result["filled"],
            "end_month_filled": end_result["filled"],
            "end_year_filled": end_result["filled"],
            "start_date_diagnostic": start_result,
            "end_date_diagnostic": end_result,
            "description_filled": description_ok,
        })

    education = profile.get("education", [])
    for index, entry in enumerate(education):
        existing_school_count = page.locator('input[name="schoolName"]:visible').count()
        if index and existing_school_count <= index:
            page.get_by_role("button", name="Add Another", exact=True).first.click()
            page.wait_for_timeout(500)
        school_inputs = page.locator('input[name="schoolName"]:visible')
        school_inputs.nth(index).wait_for(state="visible", timeout=5000)
        school_ok = _fill_locator(school_inputs.nth(index), str(entry.get("school", "")))

        degree_buttons = page.locator(
            '[data-automation-id="formField-degree"] button:visible'
        )
        raw_degree = str(entry.get("degree", ""))
        degree_aliases = {
            "masters": ("Master Degree", "Master's Degree", "Masters Degree", "Masters"),
            "bachelors": ("Bachelor Degree", "Bachelor's Degree", "Bachelors Degree", "Bachelors"),
        }
        degree_options = degree_aliases.get(raw_degree.casefold(), (raw_degree,))
        degree_button = degree_buttons.nth(index)
        try:
            selected_degree = degree_button.inner_text().strip().casefold()
        except Exception:
            selected_degree = ""
        degree_ok = any(
            option.casefold() in selected_degree for option in degree_options
        ) or any(
            _select_single_choice(page, degree_button, option)
            for option in degree_options
        )

        field_inputs = page.get_by_label(re.compile(r"Field of Study", re.I))
        field_ok = _select_dropdown(
            page, field_inputs.nth(index), entry.get("field_of_study", "")
        )

        gpa_ok = True
        gpa = str(entry.get("gpa", "")).strip()
        if gpa:
            gpa_ok = _fill_locator(
                page.locator('input[name="gradeAverage"]:visible').nth(index), gpa
            )

        from_container = page.locator(
            '[data-automation-id="formField-firstYearAttended"]'
        ).nth(index)
        to_container = page.locator(
            '[data-automation-id="formField-lastYearAttended"]'
        ).nth(index)
        from_ok = _fill_locator(from_container.locator("input").first, str(entry.get("from_year", "")))
        to_ok = _fill_locator(to_container.locator("input").first, str(entry.get("to_year", "")))
        results["education"].append({
            "school": entry.get("school", ""),
            "school_filled": school_ok,
            "degree_filled": degree_ok,
            "field_of_study_filled": field_ok,
            "gpa_filled": gpa_ok,
            "from_year_filled": from_ok,
            "to_year_filled": to_ok,
        })

    language_aliases = {
        "mandarin chinese": ("Chinese", "Mandarin Chinese", "Mandarin"),
        "mandarin": ("Chinese", "Mandarin Chinese", "Mandarin"),
        "cantonese": ("Cantonese",),
        "english": ("English",),
    }
    proficiency_aliases = {
        "native": "5 - Fluent",
        "fluent": "5 - Fluent",
        "professional working proficiency": "4 - Advanced",
    }
    proficiency_aliases.update({
        str(key).casefold(): str(value)
        for key, value in (company_rules or {}).get("language_scale", {}).items()
    })
    unsupported_languages = {
        str(item).casefold()
        for item in (company_rules or {}).get("unsupported_language_options", [])
    }

    def visible_option_labels(button: Any) -> list[str]:
        try:
            page.keyboard.press("Escape")
            button.click(timeout=5_000)
            page.wait_for_timeout(400)
            options = page.get_by_role("option")
            labels = [
                options.nth(option_index).inner_text().strip()
                for option_index in range(options.count())
                if options.nth(option_index).is_visible()
            ]
            page.keyboard.press("Escape")
            return labels
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return []

    configured_languages = profile.get("languages", [])
    language_buttons = page.locator('button[name="language"]:visible')
    available_languages = (
        visible_option_labels(language_buttons.first)
        if language_buttons.count()
        else []
    )
    for entry in configured_languages:
        raw_language = str(entry.get("language", "")).strip()
        if raw_language.casefold() in unsupported_languages:
            results["languages"].append({
                "language": raw_language,
                "status": "company_profile_marks_portal_option_unavailable",
            })
            continue
        aliases = language_aliases.get(raw_language.casefold(), (raw_language,))
        portal_language = next(
            (
                alias
                for alias in aliases
                if any(alias.casefold() == option.casefold() for option in available_languages)
            ),
            "",
        )
        if not portal_language:
            results["languages"].append({
                "language": raw_language,
                "status": "portal_option_unavailable",
            })
            continue

        language_buttons = page.locator('button[name="language"]:visible')
        target_index = None
        for button_index in range(language_buttons.count()):
            try:
                if portal_language.casefold() in language_buttons.nth(button_index).inner_text().strip().casefold():
                    target_index = button_index
                    break
            except Exception:
                continue
        if target_index is None:
            before_count = language_buttons.count()
            add_buttons = page.get_by_role("button", name="Add Another", exact=True)
            clicked = False
            for button_index in reversed(range(add_buttons.count())):
                try:
                    if add_buttons.nth(button_index).is_visible():
                        add_buttons.nth(button_index).click(timeout=5_000)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                try:
                    page.locator('button[name="language"]:visible').nth(before_count).wait_for(
                        state="visible", timeout=8_000
                    )
                    target_index = before_count
                except Exception:
                    target_index = None
        if target_index is None:
            results["languages"].append({
                "language": raw_language,
                "status": "add_control_failed",
            })
            continue

        language_button = page.locator('button[name="language"]:visible').nth(target_index)
        try:
            selected_language = language_button.inner_text().strip()
        except Exception:
            selected_language = ""
        language_filled = (
            portal_language.casefold() in selected_language.casefold()
            or _select_single_choice(page, language_button, portal_language)
        )
        native_requested = bool(entry.get("native"))
        native_checkbox = page.locator('input[name="native"]:visible').nth(target_index)
        native_filled = False
        try:
            if native_checkbox.count():
                native_checkbox.set_checked(native_requested, force=True)
                native_filled = native_checkbox.is_checked() == native_requested
        except Exception:
            native_filled = False

        proficiency = proficiency_aliases.get(
            str(entry.get("proficiency", "")).casefold(),
            str(entry.get("proficiency", "")),
        )
        proficiency_results = {}
        # 有些租户即使将语言标记为母语，仍要求填写全部五项熟练度字段。
        # 两种情况下都填写明确的等级。
        for name in ("Comprehension", "Overall", "Reading", "Speaking", "Writing"):
            buttons = page.locator(f'button[aria-label^="{name}"]:visible')
            button = buttons.nth(target_index)
            try:
                selected = button.inner_text().strip()
            except Exception:
                selected = ""
            filled = bool(
                proficiency.casefold() in selected.casefold()
                or _select_single_choice(page, button, proficiency)
            )
            proficiency_results[name] = "filled" if filled else "not_filled"
        results["languages"].append({
            "language": raw_language,
            "portal_language": portal_language,
            "language_filled": language_filled,
            "native_requested": native_requested,
            "native_filled": native_filled,
            "proficiency": proficiency,
            "proficiency_fields": proficiency_results,
        })

    skill_input = page.get_by_label(re.compile(r"Type to Add Skills", re.I)).first
    consecutive_skill_failures = 0
    for skill in profile.get("skills", [])[:8]:
        filled = bool(skill_input.count() and _select_dropdown(page, skill_input, str(skill)))
        results["skills"].append({"skill": skill, "filled": filled})
        consecutive_skill_failures = 0 if filled else consecutive_skill_failures + 1
        if consecutive_skill_failures >= 2:
            # 技能为可选项。如果租户连续拒绝资料中的技能词，就停止尝试，
            # 不要在同一控件上反复重试数分钟。
            break

    results["documents"] = upload_documents(
        page, profile, project_root, company_rules
    )
    return results


def fill_application_questions(
    page: Any,
    profile: dict[str, Any],
    company_rules: dict[str, Any] | None = None,
    *,
    job_location: str = "",
) -> dict[str, Any]:
    results: dict[str, Any] = {"answered": [], "pending": []}
    configured_answers = profile.get("custom_answers", {})
    form_fields = page.locator('[data-automation-id^="formField"]')
    for question, raw_answer in configured_answers.items():
        matching_field = None
        for index in range(form_fields.count()):
            field = form_fields.nth(index)
            try:
                if question.lower() in field.inner_text().lower():
                    matching_field = field
                    break
            except Exception:
                continue
        if matching_field is None:
            # Workday 可能将问题分布在多个页面。当前页面没有某个已配置答案时，
            # 不应在此处阻断流程。
            continue
        if is_work_permission_question(question) and not has_confirmed_work_permission_scope(
            profile, job_location
        ):
            results["pending"].append({
                "question": question,
                "reason": "work_permission_location_unconfirmed",
            })
            continue
        if raw_answer is None or str(raw_answer).strip() == "":
            results["pending"].append({"question": question, "reason": "no_answer"})
            continue
        answer = str(raw_answer).strip()
        button = matching_field.locator("button:visible").first
        filled = bool(button.count() and _select_single_choice(page, button, answer))
        target = results["answered"] if filled else results["pending"]
        target.append({
            "question": question,
            "answer": answer if filled else "",
            "reason": "" if filled else "option_not_found",
        })

    question_rules = (company_rules or {}).get("application_questions", {})
    checkbox_groups = profile.get("workday_checkbox_groups", {})
    checkbox_inputs = page.locator('input[type="checkbox"]:visible')

    def check_named_option(option_name: str) -> bool:
        for index in range(checkbox_inputs.count()):
            checkbox = checkbox_inputs.nth(index)
            try:
                label = checkbox.locator("xpath=ancestor::label[1]")
                label_text = (
                    label.inner_text().strip()
                    if label.count()
                    else checkbox.locator("xpath=../..").inner_text().strip()
                )
                if label_text.casefold() != option_name.casefold():
                    continue
                checkbox.set_checked(True, force=True)
                return checkbox.is_checked()
            except Exception:
                continue
        return False

    # 仅在至少包含该组一个选项的页面应用此组设置；这样第 1 页和第 2 页可分别恢复。
    for group_name, option_names in checkbox_groups.items():
        option_names = [str(item) for item in option_names]
        page_has_group = any(
            page.get_by_text(option_name, exact=True).count()
            for option_name in option_names
        )
        if not page_has_group:
            continue
        for option_name in option_names:
            filled = check_named_option(option_name)
            target = results["answered"] if filled else results["pending"]
            target.append({
                "question": str(group_name),
                "answer": option_name if filled else "",
                "reason": "" if filled else "checkbox_option_not_found",
            })

    for question in question_rules.get("manual_review_questions", []):
        question = str(question)
        matching_field = None
        for index in range(form_fields.count()):
            field = form_fields.nth(index)
            try:
                if question.casefold() in field.inner_text().casefold():
                    matching_field = field
                    break
            except Exception:
                continue
        if matching_field is None:
            continue
        button = matching_field.locator("button:visible").first
        selected = button.inner_text().strip() if button.count() else ""
        if selected and selected.casefold() != "select one":
            results["answered"].append({
                "question": question,
                "answer": selected,
                "reason": "existing_selection_preserved",
            })
        else:
            results["pending"].append({
                "question": question,
                "reason": "manual_fact_required",
            })
    return results


def fill_voluntary_disclosures(
    page: Any,
    profile: dict[str, Any],
    company_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configured = profile.get("voluntary_disclosures", {})
    disclosure_rules = (company_rules or {}).get("voluntary_disclosures", {})
    gender = configured.get("gender")
    gender_filled = False
    effective_gender = ""

    gender_prompt = page.get_by_text(re.compile(r"^\s*Gender\s*\*?\s*$", re.I))
    try:
        gender_present = any(
            gender_prompt.nth(index).is_visible()
            for index in range(gender_prompt.count())
        )
    except Exception:
        gender_present = False
    gender_policy = str(disclosure_rules.get("gender", "if_present"))
    gender_required = gender_policy == "required" or (
        gender_policy == "if_present" and gender_present
    )

    # Workday 中已有的选项可能是在创建本地资料后由人工设置的。
    # 保留浏览器/服务器中的这一明确值；仅当控件仍显示 "Select One" 时
    # 才应用配置中的备用值。
    gender_choice_re = re.compile(
        r"^\s*(Male|Female|Decline to State)\s*$", re.I
    )
    selected_gender = page.locator("button:visible").filter(
        has_text=gender_choice_re
    ).first
    try:
        if selected_gender.count() and selected_gender.is_visible():
            effective_gender = (
                selected_gender.inner_text()
                or selected_gender.get_attribute("aria-label")
                or ""
            ).strip()
            gender_filled = bool(effective_gender)
    except Exception:
        gender_filled = False

    if gender and not gender_filled:
        button = page.locator("button:visible").filter(
            has_text=re.compile(r"^\s*Select One\s*$", re.I)
        ).first
        gender_filled = bool(
            button.count() and _select_single_choice(page, button, str(gender))
        )
        if not gender_filled:
            try:
                page.get_by_text("Select One", exact=True).first.click()
                option = page.get_by_role(
                    "option",
                    name=re.compile(rf"^\s*{re.escape(str(gender))}\s*$", re.I),
                ).last
                option.wait_for(state="visible", timeout=5000)
                option.click()
                page.wait_for_timeout(300)
                selected_text = page.get_by_text(str(gender), exact=True)
                gender_filled = bool(
                    selected_text.count() and selected_text.first.is_visible()
                )
                if gender_filled:
                    effective_gender = str(gender)
            except Exception:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                gender_filled = False

    terms_requested = configured.get("accept_terms") is True
    terms_checked = False
    if terms_requested:
        checkbox = page.locator('input[name="acceptTermsAndAgreements"]')
        try:
            # Workday 将原生输入框视觉隐藏在样式化复选框后面。Playwright 仍可通过
            # force=True 安全设置真实输入框，并用 is_checked 验证结果状态。
            if checkbox.count():
                try:
                    checkbox.check(force=True)
                except Exception:
                    pass
                page.wait_for_timeout(300)
                terms_checked = checkbox.is_checked()
                if not terms_checked:
                    try:
                        terms_checked = bool(
                            checkbox.evaluate(
                                "el => { if (!el.checked) el.click(); return el.checked; }"
                            )
                        )
                        page.wait_for_timeout(300)
                    except Exception:
                        terms_checked = False
                if not terms_checked:
                    input_id = checkbox.get_attribute("id")
                    label = page.locator(f'label[for="{input_id}"]') if input_id else checkbox.locator("xpath=..")
                    try:
                        label.click(force=True, timeout=5_000)
                        page.wait_for_timeout(300)
                        terms_checked = checkbox.is_checked()
                    except Exception:
                        terms_checked = False
        except Exception:
            terms_checked = False
    return {
        "gender": effective_gender or str(gender or ""),
        "configured_gender": str(gender or ""),
        "gender_present": gender_present,
        "gender_policy": gender_policy,
        "existing_selection_preserved": bool(
            effective_gender and effective_gender.lower() != str(gender or "").lower()
        ),
        "gender_filled": gender_filled,
        "terms_requested": terms_requested,
        "terms_checked": terms_checked,
        "complete": bool(
            (gender_filled or not gender_required)
            and (terms_checked if terms_requested else True)
        ),
    }


def current_application_stage(page: Any) -> str:
    active_step = page.locator('[data-automation-id="progressBarActiveStep"]')
    try:
        if active_step.count() and active_step.is_visible():
            active_text = active_step.inner_text()
            for stage in (
                "Autofill with Resume",
                "My Information",
                "My Experience",
                "Application Questions",
                "Voluntary Disclosures",
                "Review",
            ):
                if stage.lower() in active_text.lower():
                    return stage
    except Exception:
        pass
    for stage in (
        "Review",
        "Voluntary Disclosures",
        "Application Questions",
        "My Experience",
        "My Information",
        "Autofill with Resume",
    ):
        heading = page.get_by_role("heading", name=stage, exact=True).last
        try:
            if heading.count() and heading.is_visible():
                return stage
        except Exception:
            continue
    return "unknown"


def upload_documents(
    page: Any,
    profile: dict[str, Any],
    project_root: Path,
    company_rules: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    results = []
    documents = profile.get("documents", {})
    for document_name in ("resume_path", "cover_letter_path"):
        if (
            document_name == "cover_letter_path"
            and (company_rules or {}).get("cover_letter_upload") is False
        ):
            results.append({
                "document": document_name,
                "status": "not_supported_by_company_profile",
            })
            continue
        diagnostic: dict[str, Any] | None = None
        raw_path = str(documents.get(document_name, "")).strip()
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = project_root / path
        status = "missing_file"
        if path.is_file():
            existing_name = page.get_by_text(path.name, exact=True)
            if existing_name.count() and existing_name.first.is_visible():
                results.append({
                    "document": document_name,
                    "status": "already_present",
                    "path": str(path),
                })
                continue
            inputs = page.locator('input[type="file"]')
            target_index = 0 if document_name == "resume_path" else 1
            if inputs.count() > target_index:
                target = inputs.nth(target_index)
                diagnostic = target.evaluate(
                    """
                    element => {
                      const ancestors = [];
                      let node = element;
                      for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                        const automationId = node.getAttribute?.('data-automation-id');
                        if (automationId) ancestors.push(automationId);
                      }
                      const region = element.closest('[role="group"], section, [data-automation-id^="formField"]');
                      return {
                        name: element.getAttribute('name') || '',
                        accept: element.getAttribute('accept') || '',
                        automation_ancestors: ancestors,
                        region_text: (region?.innerText || '').trim().slice(0, 500),
                      };
                    }
                    """
                )
                target.set_input_files(str(path))
                try:
                    page.get_by_text(
                        re.compile(re.escape(path.name), re.I)
                    ).first.wait_for(state="visible", timeout=30_000)
                    page.wait_for_timeout(5000)
                    status = "uploaded"
                except Exception:
                    status = "upload_unverified"
            else:
                status = "input_not_found"
                diagnostic = {"input_count": inputs.count()}
        result: dict[str, Any] = {
            "document": document_name,
            "status": status,
            "path": str(path),
        }
        if diagnostic is not None:
            result["diagnostic"] = diagnostic
        results.append(result)
    return results


def discover_fields(page: Any) -> list[dict[str, Any]]:
    return page.locator("input, textarea, select").evaluate_all(
        """
        elements => elements.map(element => {
          const id = element.id || '';
          const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
          const wrapped = element.closest('label');
          return {
            tag: element.tagName.toLowerCase(),
            type: element.getAttribute('type') || '',
            name: element.getAttribute('name') || '',
            label: (explicit?.innerText || wrapped?.innerText || element.getAttribute('aria-label') || '').trim(),
            placeholder: element.getAttribute('placeholder') || '',
            required: Boolean(element.required || element.getAttribute('aria-required') === 'true'),
            visible: Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length),
            has_value: ['radio', 'checkbox'].includes((element.getAttribute('type') || '').toLowerCase())
              ? Boolean(element.checked)
              : Boolean(element.value)
          };
        })
        """
    )


def _click_named_control(page: Any, pattern: re.Pattern[str]) -> bool:
    for role in ("button", "link"):
        candidates = page.get_by_role(role, name=pattern)
        for index in range(candidates.count()):
            locator = candidates.nth(index)
            if not locator.is_visible():
                continue
            label = (locator.inner_text() or locator.get_attribute("aria-label") or "").strip()
            if FINAL_SUBMIT_RE.search(label):
                raise RuntimeError("策略禁止操作最终 Submit 控件")
            try:
                locator.click(timeout=15_000)
            except Exception:
                # 浏览器扩展可能调整有效视口大小，而 Workday 控件仍在 DOM 中可见。
                # 该标签已经通过最终提交保护检查，因此对这个已定位的控件执行 DOM 点击
                # 是安全的备用方案。
                locator.evaluate("element => element.click()")
            page.wait_for_timeout(1500)
            return True
    return False


def workday_sign_in_required(page: Any) -> bool:
    def any_visible(locator: Any) -> bool:
        return any(locator.nth(index).is_visible() for index in range(locator.count()))

    heading = page.get_by_role("heading", name="Sign In", exact=True)
    create_heading = page.get_by_role(
        "heading", name=re.compile(r"Create Account", re.I)
    )
    email_button = page.get_by_role(
        "button", name=re.compile(r"Sign in with email", re.I)
    )
    # 这些控件位于 Workday 申请页面内，与 Simplify 扩展侧栏中的
    # "Log In to Autofill" 链接不同。
    if any_visible(heading) or any_visible(create_heading) or any_visible(email_button):
        return True

    # 即使租户已完成认证切换，Workday 申请页面仍可能保留过期的文档标题
    # "Create Account"。申请步骤指示器和上传控件比标题更能说明当前状态，
    # 也能避免将扩展界面（例如 Simplify 自带的登录提示）纳入判断。
    application_controls = page.locator(
        '[data-automation-id="progressBarActiveStep"], '
        '[data-automation-id="progressBar"], input[type="file"]'
    )
    try:
        if "/apply/" in page.url.casefold() and any_visible(application_controls):
            return False
    except Exception:
        pass
    try:
        if re.search(r"\b(?:Sign In|Create Account)\b", page.title(), re.I):
            return True
    except Exception:
        pass
    sign_in_link = page.get_by_role("link", name="Sign In", exact=True)
    create_step = page.get_by_text(re.compile(r"Create Account\s*/\s*Sign In", re.I))
    return bool(
        any_visible(sign_in_link) and any_visible(create_step)
    )


def recover_workday_transient_error(page: Any) -> dict[str, Any]:
    """刷新一个可恢复的 Workday SPA 错误，不重启草稿。"""
    result = {"detected": False, "reloaded": False, "recovered": False, "error": ""}
    try:
        body = page.locator("body").inner_text(timeout=3_000)
        if not (
            re.search(r"Something went wrong", body, re.I)
            and re.search(r"refresh the page", body, re.I)
        ):
            return result
        result["detected"] = True
        page.reload(wait_until="domcontentloaded", timeout=60_000)
        result["reloaded"] = True
        try:
            page.locator(
                'input:visible, textarea:visible, select:visible, button:visible'
            ).first.wait_for(state="visible", timeout=30_000)
        except Exception:
            page.wait_for_timeout(5_000)
        refreshed_body = page.locator("body").inner_text(timeout=3_000)
        result["recovered"] = not bool(
            re.search(r"Something went wrong", refreshed_body, re.I)
        ) and not workday_sign_in_required(page)
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result


def _open_workday_email_sign_in(page: Any) -> None:
    """显示电子邮件/密码表单，但不操作凭据。"""
    email_entry = page.get_by_role(
        "button", name=re.compile(r"Sign in with email", re.I)
    ).first
    sign_in_link = page.get_by_role("link", name="Sign In", exact=True).first
    if email_entry.count() and email_entry.is_visible():
        email_entry.click()
        page.wait_for_timeout(2000)
    elif sign_in_link.count() and sign_in_link.is_visible():
        sign_in_link.click()
        page.wait_for_timeout(2000)


def _click_workday_sign_in_submit(page: Any) -> str:
    submit_candidates = page.locator('[data-automation-id="signInSubmitButton"]')
    for index in range(submit_candidates.count()):
        submit = submit_candidates.nth(index)
        if not submit.is_visible():
            continue
        try:
            submit.click(timeout=10_000)
            method = "button_click"
        except Exception:
            try:
                submit.click(force=True, timeout=5_000)
                method = "forced_button_click"
            except Exception:
                box = submit.bounding_box()
                if box:
                    page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                    method = "coordinate_click"
                else:
                    submit.evaluate("element => element.click()")
                    method = "dom_click"
        return method
    raise RuntimeError("未找到可见的 Workday Sign In 提交按钮")


def _visible_workday_login_feedback(page: Any) -> bool:
    candidates = page.locator(
        '[role="alert"], [data-automation-id="errorMessage"], '
        '[data-automation-id="formError"]'
    )
    try:
        return any(
            candidates.nth(index).is_visible() for index in range(candidates.count())
        )
    except Exception:
        return False


def _submit_workday_login(page: Any, password_input: Any) -> dict[str, Any]:
    """点击 Sign In 并确认页面有响应；必要时使用一次 Enter 作为备用操作。"""
    before_url = page.url
    before_title = page.title()
    methods = [_click_workday_sign_in_submit(page)]
    page.wait_for_timeout(5000)
    feedback = _visible_workday_login_feedback(page)
    changed = page.url != before_url or page.title() != before_title
    authentication_required = workday_sign_in_required(page)
    if authentication_required and not feedback and not changed:
        password_input.press("Enter")
        methods.append("password_enter_fallback")
        page.wait_for_timeout(5000)
        feedback = _visible_workday_login_feedback(page)
        changed = page.url != before_url or page.title() != before_title
        authentication_required = workday_sign_in_required(page)
    if authentication_required and not feedback and not changed:
        submit = page.locator('[data-automation-id="signInSubmitButton"]').first
        submit.evaluate("button => button.form.requestSubmit(button)")
        methods.append("form_request_submit_fallback")
        page.wait_for_timeout(5000)
        feedback = _visible_workday_login_feedback(page)
        changed = page.url != before_url or page.title() != before_title
        authentication_required = workday_sign_in_required(page)
    return {
        "submit_control_activated": True,
        "submit_methods": methods,
        "page_reacted": bool(changed or feedback or not authentication_required),
        "page_feedback_detected": feedback,
        "authentication_required_after_submit": authentication_required,
    }


def recover_workday_authenticated_session(page: Any) -> dict[str, Any]:
    """同一租户已有认证会话时，重新加载过期的 Sign In 标签页。"""
    result = {"attempted": False, "succeeded": False, "error": ""}
    if not workday_sign_in_required(page):
        return result
    expected_host = urllib.parse.urlsplit(page.url).hostname
    authenticated_peer = None
    for candidate in page.context.pages:
        if candidate is page:
            continue
        candidate_parts = urllib.parse.urlsplit(candidate.url)
        if candidate_parts.hostname != expected_host:
            continue
        if "/userHome" in candidate_parts.path and not workday_sign_in_required(candidate):
            authenticated_peer = candidate
            break
    if authenticated_peer is None:
        return result
    result["attempted"] = True
    try:
        page.reload(wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(5000)
        result["succeeded"] = not workday_sign_in_required(page)
        if not result["succeeded"]:
            result["error"] = "authenticated_tenant_reload_did_not_clear_sign_in"
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result


def attempt_workday_saved_password_login(page: Any) -> dict[str, Any]:
    """提交已由 Chrome Password Manager 填入的凭据。

    适配器只检查字段是否已有内容。它绝不读取、返回、记录、导出或持久化
    用户名或密码的具体内容。
    """
    result = {
        "attempted": False,
        "succeeded": False,
        "credentials_available": False,
        "error": "",
    }
    if not workday_sign_in_required(page):
        return result
    try:
        _open_workday_email_sign_in(page)
        email_input = page.locator('input[type="email"]').first
        if not email_input.count():
            email_input = page.get_by_label(re.compile(r"Email|Username", re.I)).first
        password_input = page.locator('input[type="password"]').first
        email_input.wait_for(state="visible", timeout=15_000)
        password_input.wait_for(state="visible", timeout=15_000)
        page.wait_for_timeout(1500)
        # `:-webkit-autofill` 可帮助区分 Chrome Password Manager 填入的内容与
        # 过期、手动或配置的字段内容，无需读取其中任何值。
        autofill_probe = (
            "element => { try { return element.matches(':-webkit-autofill'); } "
            "catch (_) { return false; } }"
        )
        email_present = bool(email_input.evaluate(autofill_probe))
        password_present = bool(password_input.evaluate(autofill_probe))
        result["credentials_available"] = email_present and password_present
        if not result["credentials_available"]:
            return result
        result["attempted"] = True
        result.update(_submit_workday_login(page, password_input))
        result["succeeded"] = not workday_sign_in_required(page)
        if not result["succeeded"] and not result.get("page_reacted"):
            result["error"] = "login_submit_not_effective"
        elif not result["succeeded"] and result.get("page_feedback_detected"):
            result["error"] = "login_rejected_or_verification_required"
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result


def attempt_workday_login(page: Any, username: str, password: str) -> dict[str, Any]:
    result = {"attempted": False, "succeeded": False, "error": ""}
    if not username or not password or not workday_sign_in_required(page):
        return result
    result["attempted"] = True
    try:
        _open_workday_email_sign_in(page)
        email_input = page.locator('input[type="email"]').first
        if not email_input.count():
            email_input = page.get_by_label(re.compile(r"Email|Username", re.I)).first
        password_input = page.locator('input[type="password"]').first
        email_input.wait_for(state="visible", timeout=15_000)
        password_input.wait_for(state="visible", timeout=15_000)
        email_input.fill(username)
        password_input.fill(password)
        result.update(_submit_workday_login(page, password_input))
        result["succeeded"] = not workday_sign_in_required(page)
        if not result["succeeded"] and not result.get("page_reacted"):
            result["error"] = "login_submit_not_effective"
        elif not result["succeeded"] and result.get("page_feedback_detected"):
            result["error"] = "login_rejected_or_verification_required"
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result


def run_preview(
    *,
    job_url: str,
    job_location: str = "",
    cookie_header: str,
    profile: dict[str, Any],
    project_root: Path,
    browser_profile_dir: Path,
    browser_state_path: Path,
    output_dir: Path,
    headless: bool,
    start_application: bool,
    apply_mode: str,
    advance_one_step: bool,
    advance_to_review: bool,
    save_draft: bool,
    interactive: bool,
    cdp_url: str = "",
    cookie_origin: str = "https://nvidia.wd5.myworkdayjobs.com",
    login_username: str = "",
    login_password: str = "",
    application_id: int | None = None,
    registry_conn: Any | None = None,
    company_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if profile.get("safety", {}).get("allow_submit") is not False:
        raise RuntimeError("资料中的 safety.allow_submit 必须为 false")
    if save_draft and not start_application:
        raise RuntimeError("--save-draft 需要同时指定 --start-application")
    if advance_one_step and not start_application:
        raise RuntimeError("--advance-one-step 需要同时指定 --start-application")
    if advance_to_review and not save_draft:
        raise RuntimeError("--advance-to-review 需要同时指定 --save-draft")
    if save_draft and not profile.get("safety", {}).get("allow_server_draft", False):
        raise RuntimeError("使用 --save-draft 前，请先设置 safety.allow_server_draft=true")

    output_dir.mkdir(parents=True, exist_ok=True)
    browser_profile_dir.mkdir(parents=True, exist_ok=True)
    browser_state_path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(output_dir, 0o700)
    _restrict_permissions(browser_profile_dir, 0o700)
    _restrict_permissions(browser_state_path.parent, 0o700)
    checkpoint_path = output_dir / "run_checkpoints.jsonl"

    def checkpoint(stage: str, **details: Any) -> None:
        record = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "stage": stage,
            **details,
        }
        with checkpoint_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        _restrict_permissions(checkpoint_path, 0o600)

    checkpoint("run_started", application_id=application_id)
    sync_playwright = _playwright_api()
    cdp_url = cdp_url.strip()
    # 每次适配器调用都已在独立子进程中运行。Playwright 正常的上下文管理器关闭流程
    # 在断开长期运行的外部 Chrome CDP 会话时可能无限等待。CDP 模式下让进程退出时
    # 仅关闭传输 socket；绝不向用户的专用 Chrome 发送 Browser.close。
    playwright_scope = (
        nullcontext(sync_playwright().start()) if cdp_url else sync_playwright()
    )
    with playwright_scope as playwright:
        created_page = None
        keep_created_page_open = False
        page_resolution = None
        if cdp_url:
            remote_browser = playwright.chromium.connect_over_cdp(
                cdp_url,
                timeout=30_000,
            )
            if not remote_browser.contexts:
                raise RuntimeError("已连接的 Chrome 未提供可用的浏览器上下文")
            context = remote_browser.contexts[0]
            browser_mode = "windows_cdp"
        else:
            executable = find_project_chromium(project_root)
            launch_options: dict[str, Any] = {
                "user_data_dir": str(browser_profile_dir),
                "headless": headless,
                "viewport": {"width": 1440, "height": 1000},
                "env": local_browser_environment(project_root),
            }
            if executable:
                launch_options["executable_path"] = str(executable)
            context = playwright.chromium.launch_persistent_context(
                **launch_options,
            )
            browser_mode = "local_persistent"
        if application_id is not None and registry_conn is not None:
            page_resolution = resolve_application_page(
                context,
                registry_conn,
                application_id=application_id,
                expected_url=job_url,
                browser_mode=browser_mode,
            )
            page = page_resolution.page
            if page_resolution.created:
                created_page = page
        else:
            page = context.new_page() if cdp_url else (
                context.pages[0] if context.pages else context.new_page()
            )
            created_page = page if cdp_url else None
        page.set_default_timeout(int((company_rules or {}).get("action_timeout_ms", 8_000)))
        try:
            if cookie_header and not cdp_url:
                context.add_cookies(parse_cookie_header(cookie_header, cookie_origin))
            reuse_current_page = bool(
                page_resolution is not None
                and not page_resolution.created
                and page_matches_application(page.url, job_url)
            )
            response = None
            if not reuse_current_page:
                response = page.goto(
                    job_url, wait_until="domcontentloaded", timeout=60_000
                )
            try:
                page.wait_for_function(
                    "document.body && document.body.innerText.trim().length > 200",
                    timeout=30_000,
                )
            except Exception:
                page.wait_for_timeout(5000)
            if page_resolution is not None:
                register_application_page(
                    registry_conn,
                    page_resolution,
                    application_id=application_id,
                    expected_url=job_url,
                    browser_mode=browser_mode,
                )
            checkpoint("page_ready", stage_name=current_application_stage(page))
            application_started = False
            apply_mode_selected = False
            authentication_required = False
            transient_recovery = recover_workday_transient_error(page)
            login_result: dict[str, Any] = {
                "attempted": False,
                "succeeded": False,
                "error": "",
                "method": "none",
            }
            if start_application:
                entry_stage = current_application_stage(page)
                if entry_stage != "unknown":
                    application_started = True
                    apply_mode_selected = (
                        apply_mode != "resume" or entry_stage == "Autofill with Resume"
                    )
                    authentication_required = workday_sign_in_required(page)
                else:
                    authentication_required = False
            if start_application and current_application_stage(page) == "unknown":
                try:
                    page.locator(
                        '[data-automation-id="adventureButton"]'
                    ).first.wait_for(state="visible", timeout=15_000)
                except Exception:
                    pass
                application_started = _click_named_control(page, APPLY_RE)
                page.wait_for_timeout(2000)
                mode_pattern = APPLICATION_MODE_PATTERNS.get(apply_mode)
                if mode_pattern:
                    apply_mode_selected = _click_named_control(page, mode_pattern)
                if apply_mode == "resume" and apply_mode_selected:
                    try:
                        page.locator('input[type="file"]').first.wait_for(state="attached", timeout=15_000)
                    except Exception:
                        pass
                page.wait_for_timeout(3000)
                try:
                    page.get_by_role(
                        "button", name=re.compile(r"Sign in with email", re.I)
                    ).first.wait_for(state="visible", timeout=10_000)
                except Exception:
                    pass
                authentication_required = workday_sign_in_required(page)
                if authentication_required:
                    session_recovery = recover_workday_authenticated_session(page)
                    if session_recovery["attempted"]:
                        login_result = {
                            **session_recovery,
                            "method": "existing_tenant_session_reload",
                        }
                    authentication_required = workday_sign_in_required(page)
                if authentication_required:
                    saved_login = attempt_workday_saved_password_login(page)
                    login_result = {**saved_login, "method": "chrome_saved_password"}
                    authentication_required = workday_sign_in_required(page)
                if authentication_required:
                    configured_login = attempt_workday_login(
                        page, login_username, login_password
                    )
                    if configured_login["attempted"]:
                        login_result = {
                            **configured_login,
                            "method": "configured_credentials",
                        }
                    authentication_required = workday_sign_in_required(page)
            manual_intervention = (
                "sign_in_required" if authentication_required else detect_manual_intervention(page)
            )
            if manual_intervention and interactive:
                input(
                    f"需要手动操作浏览器（{manual_intervention}）。请在专用 Chrome 标签页中完成操作，"
                    "然后按 Enter 继续。"
                )
                authentication_required = workday_sign_in_required(page)
                manual_intervention = (
                    "sign_in_required"
                    if authentication_required
                    else detect_manual_intervention(page)
                )
            manual_blocked = bool(manual_intervention)
            checkpoint(
                "authentication_checked",
                authentication_required=authentication_required,
                manual_intervention=manual_intervention,
            )
            documents = (
                []
                if authentication_required or manual_blocked
                else upload_documents(page, profile, project_root, company_rules)
            )
            checkpoint(
                "documents_checked",
                statuses=[item.get("status") for item in documents],
            )
            advanced_one_step = False
            resume_step_advanced = False
            resume_ready = any(
                item.get("document") == "resume_path"
                and item.get("status") in {"uploaded", "already_present"}
                for item in documents
            )
            if (
                save_draft
                and not manual_blocked
                and current_application_stage(page) == "Autofill with Resume"
                and resume_ready
            ):
                resume_step_advanced = _click_named_control(page, CONTINUE_RE)
                if resume_step_advanced:
                    page.wait_for_timeout(3000)
                    post_resume_recovery = recover_workday_transient_error(page)
                    if post_resume_recovery["detected"]:
                        transient_recovery = post_resume_recovery
                    try:
                        page.locator("#source--source").wait_for(
                            state="visible", timeout=12_000
                        )
                    except Exception:
                        pass
            if advance_one_step and not manual_blocked:
                advanced_one_step = _click_named_control(page, CONTINUE_RE)
                if advanced_one_step:
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except Exception:
                        page.wait_for_timeout(5000)
                    try:
                        page.get_by_label(re.compile(r"How Did You Hear About Us", re.I)).first.wait_for(
                            state="visible",
                            timeout=30_000,
                        )
                    except Exception:
                        page.get_by_label(re.compile(r"Given Name|First Name", re.I)).first.wait_for(
                            state="visible",
                            timeout=15_000,
                        )
            fields = (
                []
                if authentication_required or manual_blocked
                else fill_safe_fields(page, profile, company_rules)
            )
            checkpoint(
                "safe_fields_checked",
                filled=sum(item.get("status") == "filled" for item in fields),
                missing=sum(item.get("status") == "not_found" for item in fields),
            )
            server_draft_saved = False
            validation_blocked = False
            experience_fields: dict[str, Any] = {}
            experience_saved = False
            application_questions: dict[str, Any] = {}
            application_questions_saved = False
            voluntary_disclosures: dict[str, Any] = {}
            voluntary_disclosures_saved = False
            if save_draft and not authentication_required and not manual_blocked:
                stage_before_save = current_application_stage(page)
                if stage_before_save == "My Information":
                    server_draft_saved = _click_named_control(page, SAVE_RE)
                    page.wait_for_timeout(3000)
                    error_summary = page.get_by_text(re.compile(r"Errors Found", re.I)).first
                    validation_blocked = bool(error_summary.count() and error_summary.is_visible())
                    if validation_blocked:
                        server_draft_saved = False
                elif stage_before_save in {
                    "My Experience",
                    "Application Questions",
                    "Voluntary Disclosures",
                    "Review",
                }:
                    # 恢复的草稿已越过 My Information。填写当前部分前不要推进到下一部分。
                    server_draft_saved = True
            if advance_to_review and server_draft_saved:
                try:
                    page.get_by_role(
                        "heading", name="My Experience", exact=True
                    ).wait_for(state="visible", timeout=30_000)
                except Exception:
                    pass
                if current_application_stage(page) == "My Experience":
                    try:
                        experience_fields = fill_experience_fields(
                            page, profile, project_root, company_rules
                        )
                        experience_saved = _click_named_control(page, SAVE_RE)
                        page.wait_for_timeout(3000)
                        error_summary = page.get_by_text(re.compile(r"Errors Found", re.I)).first
                        validation_blocked = bool(
                            error_summary.count() and error_summary.is_visible()
                        )
                        if validation_blocked:
                            experience_saved = False
                        elif current_application_stage(page) == "Application Questions":
                            try:
                                page.locator(
                                    "input:visible, textarea:visible, select:visible"
                                ).first.wait_for(state="visible", timeout=30_000)
                            except Exception:
                                page.wait_for_timeout(5000)
                    except Exception as exc:
                        experience_fields = {
                            "error": f"{type(exc).__name__}: {' '.join(str(exc).split())[-500:]}"
                        }
                        validation_blocked = True
            checkpoint(
                "primary_sections_processed",
                stage_name=current_application_stage(page),
                server_draft_saved=server_draft_saved,
                experience_saved=experience_saved,
                validation_blocked=validation_blocked,
            )
            if advance_to_review and current_application_stage(page) == "Application Questions":
                application_questions = fill_application_questions(
                    page, profile, company_rules, job_location=job_location
                )
                if not application_questions.get("pending"):
                    application_questions_saved = _click_named_control(page, SAVE_RE)
                    page.wait_for_timeout(3000)
                    error_summary = page.get_by_text(re.compile(r"Errors Found", re.I)).first
                    validation_blocked = bool(
                        error_summary.count() and error_summary.is_visible()
                    )
                    if validation_blocked:
                        application_questions_saved = False
                    elif current_application_stage(page) == "Voluntary Disclosures":
                        try:
                            page.locator(
                                "input:visible, textarea:visible, select:visible"
                            ).first.wait_for(state="visible", timeout=30_000)
                        except Exception:
                            page.wait_for_timeout(5000)
            if advance_to_review and current_application_stage(page) == "Voluntary Disclosures":
                voluntary_disclosures = fill_voluntary_disclosures(
                    page, profile, company_rules
                )
                if voluntary_disclosures.get("complete"):
                    voluntary_disclosures_saved = _click_named_control(page, SAVE_RE)
                    page.wait_for_timeout(3000)
                    error_summary = page.get_by_text(re.compile(r"Errors Found", re.I)).first
                    validation_blocked = bool(
                        error_summary.count() and error_summary.is_visible()
                    )
                    if validation_blocked:
                        voluntary_disclosures_saved = False
                    else:
                        try:
                            page.get_by_role(
                                "heading", name="Review", exact=True
                            ).wait_for(state="visible", timeout=30_000)
                        except Exception:
                            page.wait_for_timeout(5000)

            if application_questions.get("pending"):
                manual_intervention = "screening_questions"
            current_stage = current_application_stage(page)
            keep_created_page_open = bool(
                cdp_url
                and (
                    manual_intervention
                    or server_draft_saved
                    or current_stage == "Review"
                )
            )

            if page_resolution is not None:
                register_application_page(
                    registry_conn,
                    page_resolution,
                    application_id=application_id,
                    expected_url=job_url,
                    browser_mode=browser_mode,
                    state=("review" if current_stage == "Review" else "active"),
                )

            screenshot_path = output_dir / "preview.png"
            checkpoint("screenshot_started", stage_name=current_stage)
            stage = current_stage or "application_entry"
            artifact_status = (
                "authentication_required"
                if authentication_required
                else manual_intervention
                or ("draft_saved" if server_draft_saved else "fill_test_completed")
            )
            progress_artifact = save_fill_test_artifact(
                page,
                output_dir,
                adapter="workday",
                stage=stage,
                status=artifact_status,
                metadata={
                    "server_draft_saved": server_draft_saved,
                    "validation_blocked": validation_blocked,
                    "login": login_result,
                },
                # 通过转发的 Windows CDP socket 调用 Page.captureScreenshot 没有协议级超时，
                # 可能卡住整个批次。Playwright 的截图流程有时限，仍会展开 SPA 内部滚动容器，
                # 生成便于审阅的长图。
                use_cdp=False,
                full_page=bool((company_rules or {}).get("full_page_screenshot", True)),
                full_page_timeout_ms=int(
                    (company_rules or {}).get("screenshot_timeout_ms", 15_000)
                ),
            )
            shutil.copyfile(progress_artifact["screenshot_path"], screenshot_path)
            _restrict_permissions(screenshot_path, 0o600)
            checkpoint(
                "screenshot_completed",
                capture_mode=progress_artifact.get("capture", {}).get("mode", ""),
            )
            report = {
                "job_url": job_url,
                "final_url": page.url,
                "browser_mode": browser_mode,
                "http_status": response.status if response else None,
                "page_title": page.title(),
                "body_text_length": len(page.locator("body").inner_text()),
                "application_started": application_started,
                "authentication_required": authentication_required,
                "login": login_result,
                "manual_intervention": manual_intervention,
                "apply_mode": apply_mode,
                "apply_mode_selected": apply_mode_selected,
                "advanced_one_step": advanced_one_step,
                "resume_step_advanced": resume_step_advanced,
                "transient_recovery": transient_recovery,
                "server_draft_created": advanced_one_step,
                "server_draft_saved": server_draft_saved,
                "experience_saved": experience_saved,
                "application_questions_saved": application_questions_saved,
                "voluntary_disclosures_saved": voluntary_disclosures_saved,
                "validation_blocked": validation_blocked,
                "current_stage": current_application_stage(page),
                "experience_fields": experience_fields,
                "application_questions": application_questions,
                "voluntary_disclosures": voluntary_disclosures,
                "safe_fields": fields,
                "documents": documents,
                "visible_form_fields": discover_fields(page),
                "screenshot_path": str(screenshot_path),
                "progress_screenshot_path": progress_artifact["screenshot_path"],
                "browser_state_saved": not bool(cdp_url),
                "automation_tab_left_open": keep_created_page_open,
                "tab_registry": (
                    {
                        "target_id": page_resolution.target_id,
                        "tab_label": page_resolution.tab_label,
                        "resolution_method": page_resolution.method,
                    }
                    if page_resolution is not None
                    else {}
                ),
                "submit_clicked": False,
                "company_rules": company_rules or {},
            }
            report_path = output_dir / "field_report.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            _restrict_permissions(report_path, 0o600)
            checkpoint("run_completed", stage_name=current_stage)
            if not cdp_url:
                context.storage_state(path=str(browser_state_path))
                _restrict_permissions(browser_state_path, 0o600)
            if interactive:
                input("请检查浏览器中的内容，然后按 Enter 结束（不要提交）。")
                if not cdp_url:
                    context.storage_state(path=str(browser_state_path))
                    _restrict_permissions(browser_state_path, 0o600)
            return report
        finally:
            if created_page is not None:
                if not keep_created_page_open:
                    try:
                        created_page.close()
                    except Exception:
                        pass
                    if application_id is not None and registry_conn is not None:
                        mark_application_tab_closed(registry_conn, application_id)
            elif not cdp_url:
                context.close()
                if application_id is not None and registry_conn is not None:
                    mark_application_tab_closed(registry_conn, application_id)
