#!/usr/bin/env python3
"""检查已打开申请门户中的可见控件。

探查器有意省略字段值，因此使用时不会将联系方式或凭据泄露到终端日志中。
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / ".python_packages"
if LOCAL_PACKAGES.is_dir() and str(LOCAL_PACKAGES) not in sys.path:
    sys.path.insert(0, str(LOCAL_PACKAGES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.application_bot import resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.bot import load_config, load_env_file  # noqa: E402
from private_paths import CREDENTIALS_FILE  # noqa: E402
from application_bot.artifacts import capture_long_page  # noqa: E402


def short(value: str | None, limit: int = 180) -> str:
    return " ".join((value or "").split())[:limit]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="已打开页面的主机名子串")
    parser.add_argument(
        "--application-id",
        type=int,
        help="选择已登记的标签名称 jobbot-application-ID",
    )
    parser.add_argument("--config", default=str(ROOT / "job_bot/config.china_hk_ic_foreign.json"))
    parser.add_argument("--env", default=str(CREDENTIALS_FILE))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--screenshot")
    parser.add_argument(
        "--full-page-screenshot",
        action="store_true",
        help="捕获整个文档；如可行，展开单页应用的滚动容器。",
    )
    parser.add_argument(
        "--preserve-scroll",
        action="store_true",
        help="将当前分区保持在视口内并截取视口图像。",
    )
    parser.add_argument("--parent-text", action="store_true")
    parser.add_argument("--ancestor-depth", type=int, default=1)
    parser.add_argument("--html", action="store_true")
    parser.add_argument("--open-combobox", help="要打开的组合框中可见文本")
    parser.add_argument("--combobox-index", type=int, default=0)
    parser.add_argument("--ancestors", action="store_true")
    parser.add_argument(
        "--list-options",
        action="store_true",
        help="列出当前可见的选项，不打开或关闭控件。",
    )
    parser.add_argument("--scroll-offset", type=int)
    parser.add_argument("--click-button", help="探查前打开非最终提交的编辑器")
    parser.add_argument("--button-index", type=int, default=0)
    parser.add_argument("--navigate-url")
    parser.add_argument("--all-pages", action="store_true")
    parser.add_argument("--page-index", type=int, default=-1)
    parser.add_argument("--body-text", type=int)
    parser.add_argument("--click-placeholder")
    parser.add_argument("--placeholder-index", type=int, default=0)
    parser.add_argument("--click-text")
    parser.add_argument("--hover-text")
    parser.add_argument("--hover-selector")
    parser.add_argument("--click-selector")
    parser.add_argument("--selector-index", type=int, default=0)
    parser.add_argument(
        "--press-keys",
        help="打开控件后要发送的逗号分隔按键",
    )
    parser.add_argument("--fill-selector")
    parser.add_argument("--fill-selector-value")
    parser.add_argument("--fill-selector-index", type=int, default=0)
    parser.add_argument(
        "--dom-click-text",
        help="对文本完全匹配且层级最深的 DOM 元素触发点击",
    )
    parser.add_argument("--text-index", type=int, default=-1)
    parser.add_argument("--click-link")
    parser.add_argument(
        "--press-link",
        help="聚焦可见且可访问的链接，并按 Enter 激活",
    )
    parser.add_argument(
        "--wait-after-ms",
        type=int,
        default=700,
        help="执行请求的点击或导航后，等待一段时间再探查页面",
    )
    parser.add_argument("--upload", help="要设置到文件输入框的本地文件")
    parser.add_argument("--upload-index", type=int, default=0)
    parser.add_argument("--fill-placeholder", help="要填写的一个文本框的占位文本")
    parser.add_argument("--fill-value", help="与 --fill-placeholder 配合使用的值")
    parser.add_argument(
        "--inspect-text",
        help="输出可见文本匹配元素的不含值的 DOM 元数据",
    )
    args = parser.parse_args()

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
            if args.host.lower() in (urlsplit(page.url).hostname or "").lower()
        ]
        if not pages and args.navigate_url:
            pages = [browser.contexts[0].new_page()]
        if not pages:
            raise SystemExit(f"没有已打开页面匹配主机名子串： {args.host}")
        if args.application_id is not None:
            label = f"jobbot-application-{args.application_id}"
            matching_pages = []
            for candidate in pages:
                try:
                    if candidate.evaluate("window.name") == label:
                        matching_pages.append(candidate)
                except Exception:
                    continue
            if not matching_pages:
                raise SystemExit(
                    f"没有已打开页面匹配申请标签名称： {label}"
                )
            pages = matching_pages
        if args.all_pages:
            for candidate in pages:
                print(candidate.url)
            return
        page = pages[args.page_index]
        if args.navigate_url:
            page.goto(args.navigate_url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(1_500)
        if args.upload:
            upload_path = Path(args.upload).expanduser().resolve()
            if not upload_path.is_file():
                raise RuntimeError(f"上传文件不存在：{upload_path}")
            file_inputs = page.locator("input[type=file]")
            if file_inputs.count() <= args.upload_index:
                raise RuntimeError(
                    f"文件输入框索引 {args.upload_index} 不可用"
                )
            file_inputs.nth(args.upload_index).set_input_files(
                str(upload_path), timeout=10_000
            )
            page.wait_for_timeout(args.wait_after_ms)
        if args.fill_placeholder:
            if args.fill_value is None:
                raise RuntimeError("--fill-placeholder 需要同时提供 --fill-value")
            fields = page.locator(
                f"input[placeholder='{args.fill_placeholder}'], "
                f"textarea[placeholder='{args.fill_placeholder}']"
            )
            if not fields.count():
                raise RuntimeError(
                    f"没有占位文本匹配的字段： {args.fill_placeholder}"
                )
            fields.first.fill(args.fill_value)
            page.wait_for_timeout(args.wait_after_ms)
        if args.click_button:
            page.get_by_role("button", name=args.click_button, exact=True).nth(
                args.button_index
            ).click(
                force=True, timeout=5_000
            )
            page.wait_for_timeout(args.wait_after_ms)
        if args.click_placeholder:
            page.locator(f"input[placeholder='{args.click_placeholder}']").nth(
                args.placeholder_index
            ).click(force=True, timeout=5_000)
            page.wait_for_timeout(args.wait_after_ms)
        if args.press_keys:
            for key in (item.strip() for item in args.press_keys.split(",")):
                if key:
                    page.keyboard.press(key)
                    page.wait_for_timeout(120)
        if args.dom_click_text:
            clicked = page.evaluate(
                """text => {
                    const matches = [...document.querySelectorAll('body *')]
                        .filter(el => (el.innerText || '').trim() === text)
                        .sort((a, b) => b.querySelectorAll('*').length - a.querySelectorAll('*').length);
                    if (!matches.length) return false;
                    const target = matches[matches.length - 1];
                    target.click();
                    return true;
                }""",
                args.dom_click_text,
            )
            if not clicked:
                raise RuntimeError(
                    f"没有 DOM 文本完全匹配： {args.dom_click_text}"
                )
            page.wait_for_timeout(args.wait_after_ms)
        if args.fill_selector:
            if args.fill_selector_value is None:
                raise RuntimeError(
                    "--fill-selector 需要同时提供 --fill-selector-value"
                )
            fill_targets = page.locator(args.fill_selector)
            visible_targets = [
                fill_targets.nth(index)
                for index in range(fill_targets.count())
                if fill_targets.nth(index).is_visible()
            ]
            if len(visible_targets) <= args.fill_selector_index:
                raise RuntimeError(
                    f"可见选择器索引 {args.fill_selector_index} 不可用："
                    f"{args.fill_selector}"
                )
            visible_targets[args.fill_selector_index].fill(
                args.fill_selector_value
            )
            page.wait_for_timeout(args.wait_after_ms)
            visible_options = [
                page.get_by_role("option").nth(index)
                for index in range(page.get_by_role("option").count())
                if page.get_by_role("option").nth(index).is_visible()
            ]
            if visible_options:
                print(f"VISIBLE_OPTIONS_AFTER_FILL {len(visible_options)}")
                for index, option in enumerate(visible_options[: args.limit]):
                    print(f"VISIBLE_OPTION_AFTER_FILL {index}: {short(option.inner_text())}")
        if args.hover_text:
            matches = page.get_by_text(args.hover_text, exact=True)
            if not matches.count():
                matches = page.get_by_text(args.hover_text, exact=False)
            visible_matches = [
                matches.nth(index)
                for index in range(matches.count())
                if matches.nth(index).is_visible()
            ]
            if not visible_matches:
                raise RuntimeError(f"没有可见文本匹配： {args.hover_text}")
            visible_matches[-1].hover(timeout=5_000)
            page.wait_for_timeout(args.wait_after_ms)
        if args.hover_selector:
            hover_target = page.locator(args.hover_selector)
            visible_targets = [
                hover_target.nth(index)
                for index in range(hover_target.count())
                if hover_target.nth(index).is_visible()
            ]
            if not visible_targets:
                raise RuntimeError(
                    f"没有可见元素匹配选择器： {args.hover_selector}"
                )
            visible_targets[0].hover(timeout=5_000)
            page.wait_for_timeout(args.wait_after_ms)
        if args.click_text:
            matches = page.get_by_text(args.click_text, exact=True)
            if not matches.count():
                matches = page.get_by_text(args.click_text, exact=False)
            visible_matches = [
                matches.nth(index)
                for index in range(matches.count())
                if matches.nth(index).is_visible()
            ]
            if not visible_matches:
                raise RuntimeError(f"没有可见文本匹配： {args.click_text}")
            index = args.text_index if args.text_index >= 0 else len(visible_matches) - 1
            if index >= len(visible_matches):
                raise RuntimeError(
                    f"可见文本索引 {index} 不可用：{args.click_text}"
                )
            visible_matches[index].click(force=True, timeout=5_000)
            page.wait_for_timeout(args.wait_after_ms)
        if args.click_selector:
            targets = page.locator(args.click_selector)
            visible_targets = [
                targets.nth(index)
                for index in range(targets.count())
                if targets.nth(index).is_visible()
            ]
            if len(visible_targets) <= args.selector_index:
                raise RuntimeError(
                    f"选择器索引 {args.selector_index} 不可用："
                    f"{args.click_selector}"
                )
            visible_targets[args.selector_index].click(force=True, timeout=5_000)
            page.wait_for_timeout(args.wait_after_ms)
            if args.scroll_offset is not None and page.get_by_role("option").count():
                page.get_by_role("option").first.evaluate(
                    """(el, offset) => {
                        let p=el.parentElement;
                        while(p && p.scrollHeight <= p.clientHeight) p=p.parentElement;
                        if (p) p.scrollTop=offset;
                    }""",
                    args.scroll_offset,
                )
                page.wait_for_timeout(800)
            visible_options = [
                page.get_by_role("option").nth(index)
                for index in range(page.get_by_role("option").count())
                if page.get_by_role("option").nth(index).is_visible()
            ]
            if visible_options:
                print(f"VISIBLE_OPTIONS {len(visible_options)}")
                for index, option in enumerate(visible_options[: args.limit]):
                    print(f"VISIBLE_OPTION {index}: {short(option.inner_text())}")
        if args.click_link:
            links = page.get_by_role("link", name=args.click_link, exact=False)
            visible_links = [links.nth(i) for i in range(links.count()) if links.nth(i).is_visible()]
            if not visible_links:
                raise RuntimeError(f"没有可见链接匹配： {args.click_link}")
            visible_links[0].click(timeout=5_000)
            page.wait_for_timeout(args.wait_after_ms)
        if args.press_link:
            links = page.get_by_role("link", name=args.press_link, exact=False)
            visible_links = [links.nth(i) for i in range(links.count()) if links.nth(i).is_visible()]
            if not visible_links:
                raise RuntimeError(f"没有可见链接匹配： {args.press_link}")
            visible_links[0].focus()
            visible_links[0].press("Enter", timeout=5_000)
            page.wait_for_timeout(args.wait_after_ms)
        if args.open_combobox:
            page.keyboard.press("Escape")
            matches = page.get_by_role("combobox", name=args.open_combobox, exact=True)
            if not matches.count():
                matches = page.get_by_role("combobox").filter(has_text=args.open_combobox)
            matches.nth(args.combobox_index).click()
            page.wait_for_timeout(500)
            options = page.get_by_role("option")
            if args.scroll_offset is not None and options.count():
                options.first.evaluate(
                    """(el, offset) => {
                        let p=el.parentElement;
                        while(p && p.scrollHeight <= p.clientHeight) p=p.parentElement;
                        p.scrollTop=offset;
                    }""",
                    args.scroll_offset,
                )
                page.wait_for_timeout(800)
                options = page.get_by_role("option")
            print(f"OPTIONS {options.count()}")
            for index in range(min(options.count(), args.limit)):
                print(f"OPTION {index}: {short(options.nth(index).inner_text())}")
                if args.html:
                    print(f"  HTML {short(options.nth(index).evaluate('el => el.outerHTML'), 700)!r}")
                if args.ancestors and index == 0:
                    ancestors = options.nth(index).evaluate(
                        """el => { const out=[]; let p=el.parentElement; while(p && out.length<8) { out.push({tag:p.tagName, cls:p.className, sh:p.scrollHeight, ch:p.clientHeight, st:p.scrollTop}); p=p.parentElement; } return out; }"""
                    )
                    print(f"  ANCESTORS {ancestors}")
            return
        if args.list_options:
            visible_options = [
                page.get_by_role("option").nth(index)
                for index in range(page.get_by_role("option").count())
                if page.get_by_role("option").nth(index).is_visible()
            ]
            print(f"VISIBLE_OPTIONS_NOW {len(visible_options)}")
            for index, option in enumerate(visible_options[: args.limit]):
                print(f"VISIBLE_OPTION_NOW {index}: {short(option.inner_text())}")
        if args.screenshot:
            screenshot_path = Path(args.screenshot)
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            if not args.preserve_scroll:
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(300)
            if args.full_page_screenshot:
                capture = capture_long_page(
                    page,
                    screenshot_path,
                    use_cdp=True,
                    full_page=True,
                )
                print(f"SCREENSHOT_MODE {capture['mode']}")
                for path in capture["screenshot_paths"]:
                    print(f"SCREENSHOT {path}")
            else:
                try:
                    page.screenshot(
                        path=str(screenshot_path),
                        full_page=False,
                        timeout=20_000,
                        animations="disabled",
                        caret="hide",
                    )
                except Exception:
                    # 某些装有大量扩展的 Windows Chrome 页面始终无法满足
                    # Playwright 的截图流程。原始 CDP 仍可截取
                    # 已渲染的视口，且不会导航或更改表单。
                    session = page.context.new_cdp_session(page)
                    captured = session.send(
                        "Page.captureScreenshot",
                        {"format": "png", "captureBeyondViewport": False},
                    )
                    screenshot_path.write_bytes(base64.b64decode(captured["data"]))
                screenshot_path.chmod(0o600)
                print(f"SCREENSHOT {args.screenshot}")
        print(f"TITLE {short(page.title())}")
        print(f"URL {page.url}")
        if args.body_text:
            print(page.locator("body").inner_text(timeout=5_000)[: args.body_text])
        if args.inspect_text:
            matches = page.evaluate(
                """text => [...document.querySelectorAll('body *')]
                    .filter(el => (el.innerText || '').trim() === text)
                    .slice(0, 12)
                    .map(el => {
                        const clickable = el.closest(
                            'a, button, [role="button"], [role="link"], [onclick]'
                        );
                        const ancestors = [];
                        for (let p = el; p && ancestors.length < 5; p = p.parentElement) {
                            ancestors.push({
                                tag: p.tagName.toLowerCase(),
                                id: p.id || '',
                                className: String(p.className || '').slice(0, 180),
                            });
                        }
                        return {
                            tag: el.tagName.toLowerCase(),
                            id: el.id || '',
                            className: String(el.className || '').slice(0, 240),
                            clickableTag: clickable ? clickable.tagName.toLowerCase() : '',
                            href: clickable ? (clickable.getAttribute('href') || '') : '',
                            outerHTML: el.outerHTML.slice(0, 900),
                            ancestors,
                        };
                    })""",
                args.inspect_text,
            )
            for index, match in enumerate(matches):
                print(f"TEXT_MATCH {index}: {match}")
        count = 0
        for frame_index, frame in enumerate(page.frames):
            controls = frame.locator(
                "input, textarea, select, button, a[href], [role=button], "
                "[role=link], [role=checkbox], [contenteditable=true]"
            )
            try:
                total = controls.count()
            except Exception:
                continue
            for index in range(total):
                if count >= args.limit:
                    return
                control = controls.nth(index)
                try:
                    # 隐藏的文件输入框通常由可见的
                    # 上传按钮触发，仍可作为 Playwright 的有效上传目标。
                    input_type = control.get_attribute("type") or ""
                    if not control.is_visible() and input_type != "file":
                        continue
                    tag = control.evaluate("el => el.tagName.toLowerCase()")
                    typ = input_type
                    name = control.get_attribute("name") or ""
                    aria = control.get_attribute("aria-label") or ""
                    placeholder = control.get_attribute("placeholder") or ""
                    text = control.inner_text(timeout=500) if tag in {"button", "div", "span", "a"} else ""
                    file_count = (
                        int(control.evaluate("el => el.files ? el.files.length : 0"))
                        if typ == "file"
                        else 0
                    )
                    print(
                        f"F{frame_index} {tag}:{typ} name={short(name, 70)!r} "
                        f"aria={short(aria, 100)!r} placeholder={short(placeholder, 100)!r} "
                        f"text={short(text, 120)!r}"
                        + (f" files={file_count}" if typ == "file" else "")
                    )
                    if args.parent_text:
                        parent_text = control.evaluate(
                            """(el, depth) => {
                                let p=el;
                                for(let i=0;i<depth && p;i++) p=p.parentElement;
                                return p ? p.innerText : '';
                            }""",
                            max(1, args.ancestor_depth),
                        )
                        print(f"  PARENT {short(parent_text, 240)!r}")
                    if args.html:
                        outer = control.evaluate("el => el.outerHTML")
                        print(f"  HTML {short(outer, 500)!r}")
                    count += 1
                except Exception:
                    continue


if __name__ == "__main__":
    main()
