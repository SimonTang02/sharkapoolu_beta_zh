"""读取宾大职业渠道，并为每个渠道保留一个用户登录标签页。

带类型的机会参考信息与已核实的企业职位分开保存。本模块不会编辑档案、注册、
提交申请或发送消息。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.bot import DEFAULT_CONFIG, load_config, configured_cdp_connect_url
from job_bot.applications.nvidia_workday import _playwright_api
from job_bot.scan_browser import target_id
from private_paths import JOBBOT_OUTPUT


def safe_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        return ""
    if "pennkey" in p.hostname or p.hostname.endswith(".duosecurity.com"):
        return p.scheme + "://" + p.hostname + "/"
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def matching_host(url: str, suffixes: list[str]) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == s or host.endswith("." + s) for s in suffixes)


def classify(channel: dict, state: dict) -> str:
    url = state["url"]
    host = urlsplit(url).hostname or ""
    if state.get("access_error"):
        return "access_error"
    if host.endswith(".duosecurity.com") or state.get("challenge"):
        return "verification_required"
    if "pennkey" in host or state.get("password") or state.get("login"):
        return "login_required"
    if state.get("onboarding"):
        return "registration_or_onboarding_required"
    if not matching_host(url, channel["hosts"]):
        return "entry_or_redirect_pending"
    if channel.get("public"):
        return "public_resources_available"
    if channel["kind"] == "network":
        return "network_navigation_only"
    return "page_available_mapping_pending"


SNAPSHOT = r"""() => {
  const visible = e => e.checkVisibility();
  const text = document.body.innerText;
  const controls = [...document.querySelectorAll('button,a')].filter(visible);
  const root = document.querySelector('main') || document.querySelector('#main') || document.body;
  return {url:location.href,
    password:[...document.querySelectorAll('input[type=password]')].some(visible),
    login:(/\/login|\/signin/.test(location.pathname) && /log.?in|sign.?in|sign.?up/i.test(text)) ||
      (controls.some(e=>/^(PennKey Login|Log.?in|Sign.?in|Member Login)$/i.test(e.innerText.trim())) &&
      (!!document.querySelector('input[type=email]') || location.hostname==='mypenn.upenn.edu')),
    challenge:/verify you are human|verification code|approve.*Duo/i.test(text),
    onboarding:/complete your registration|accept.*terms to continue/i.test(text),
    access_error:/This site can.t be reached|Access Denied|403 Forbidden|Too Many Requests/i.test(document.title+' '+[...document.querySelectorAll('h1,[role=alert]')].filter(visible).map(e=>e.innerText).join(' ')),
    links:[...root.querySelectorAll('a[href]')].filter(visible).map(e=>({title:e.innerText.trim(),url:e.href})),
    job_links:[...document.querySelectorAll('a[data-automation-id="jobTitle"]')].filter(visible).map(e=>({title:e.innerText.trim(),url:e.href})),
    research_login:/Please login to see all available opportunities/i.test(text),
    student_recruiting:/Find Jobs for Student Employment|Student Recruiting|Student Employment Opportunities/i.test(text)
  };
}"""


def extract_references(channel: dict, state: dict, today: dt.date) -> list[dict]:
    result = {}
    if channel["id"] == "workday" and not state.get("student_recruiting"):
        return []
    for link in state.get("job_links" if channel["id"] == "workday" else "links", []):
        url = safe_url(link["url"])
        title = " ".join(link["title"].split())
        if not url or not title or len(title) < 5:
            continue
        kind = channel["kind"]
        if channel.get("link_pattern"):
            if not re.search(channel["link_pattern"], url):
                continue
        elif channel["id"] == "engineering_hub":
            if not re.search(r"career|job|internship|employer|resume|interview|Handshake", title, re.I):
                continue
        elif channel["id"] != "workday":
            continue  # 不猜测解析受限平台或校友名单。
        if kind == "event":
            match = re.search(r"/events/(\d{4})/(\d{2})/(\d{2})/", url)
            if not match or dt.date(*map(int, match.groups())) < today:
                continue
        result[url] = {"title": title, "url": url, "kind": kind,
                       "channel": channel["id"], "verification": "visible_listing_only"}
    return list(result.values())[:100]


def write_private(path: Path, value: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.chmod(0o600)
    temp.replace(path)


def run(config: dict, *, open_pages: bool = False, only: list[str] | None = None) -> dict:
    out = JOBBOT_OUTPUT / "penn_channels"
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = out / "tabs.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    report = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "channels": [], "opportunities": []}
    channels = config.get("penn_channels", {}).get("channels", [])
    if only and set(only) - {c["id"] for c in channels}:
        raise ValueError("未知的宾大渠道 ID")
    with _playwright_api()() as p:
        browser = p.chromium.connect_over_cdp(configured_cdp_connect_url(config), timeout=30000)
        try:
            context = browser.contexts[0]
            pages = {}
            for page in context.pages:
                try:
                    pages[target_id(page)] = page
                except Exception:
                    pass
            for channel in channels:
                key = channel["id"]
                if only and key not in only:
                    continue
                if channel.get("enabled", True) is False:
                    report["channels"].append({
                        "id": key, "name": channel["name"], "kind": channel["kind"],
                        "entry_url": channel["entry_url"], "status": "paused_by_user",
                        "items": 0, "note": channel.get("note", ""),
                        "checked_at": report["checked_at"],
                    })
                    continue
                page = pages.get(manifest.get(key, {}).get("target_id"))
                login_target = manifest.get(key, {}).get("target_id")
                # 在单独的临时标签页中刷新已实现的读取器。
                # 保留的登录/用户页面维持用户离开时的状态。
                if not open_pages and key in {"engineering", "engineering_hub", "curf"}:
                    page = None
                created = page is None
                keep = open_pages or not created
                row = {"id": key, "name": channel["name"], "kind": channel["kind"],
                       "entry_url": channel["entry_url"], "note": channel.get("note", ""),
                       "checked_at": report["checked_at"]}
                try:
                    if created:
                        page = context.new_page()
                        if keep:
                            manifest[key] = {"target_id": target_id(page), "entry_url": channel["entry_url"]}
                            write_private(manifest_path, manifest)
                        try:
                            page.goto(channel["entry_url"], wait_until="domcontentloaded", timeout=20000)
                            if channel.get("entry_link"):
                                link = page.get_by_role("link", name=channel["entry_link"], exact=True).first
                                page.goto(link.get_attribute("href"), wait_until="domcontentloaded", timeout=20000)
                        except Exception:
                            # SSO 导航可能中断原始页面加载。
                            # 分类之前先检查实际到达的页面。
                            pass
                        page.wait_for_timeout(1500)
                    for attempt in range(3):
                        try:
                            state = page.evaluate(SNAPSHOT)
                            break
                        except Exception:
                            if attempt == 2:
                                raise
                            page.wait_for_timeout(750)
                    row["status"] = classify(channel, state)
                    if key == "curf" and state.get("research_login"):
                        row["status"] = "login_required"
                    row["current_url"] = safe_url(state["url"])
                    row["target_id"] = target_id(page) if keep else login_target
                    references = []
                    if row["status"] in {"public_resources_available", "page_available_mapping_pending"}:
                        references = extract_references(channel, state, dt.date.today())
                    if references:
                        row["status"] = "references_collected"
                    row["items"] = len(references)
                    report["opportunities"].extend(references)
                except Exception as exc:
                    row.update(status="access_error", error_type=type(exc).__name__, items=0)
                finally:
                    if created and not keep and page is not None:
                        page.close()
                report["channels"].append(row)
        finally:
            browser.close()
    if only and (out / "latest.json").exists():
        previous = json.loads((out / "latest.json").read_text())
        report["channels"] = [r for r in previous.get("channels", []) if r["id"] not in only] + report["channels"]
        report["opportunities"] = [r for r in previous.get("opportunities", []) if r["channel"] not in only] + report["opportunities"]
        report["partially_refreshed_channels"] = only
    write_private(out / "latest.json", report)
    lines = ["# 宾大渠道：校内工作、科研与职业资源", "", report["checked_at"], "",
             "以下内容为渠道状态和页面可见信息，并非已核实的职位录用或申请提交记录。", "",
             "| 渠道 | 状态 | 条目数 |", "|---|---|---|"]
    for row in report["channels"]:
        lines.append(f"| [{row['name']}]({row['entry_url']}) | {row['status']} | {row['items']} |")
    kind_labels = {
        "campus_job": "校内岗位", "research_lead": "科研机会", "event": "活动",
        "resource": "资源", "external_job": "校外职位", "network": "人脉",
    }
    for kind in ("campus_job", "research_lead", "event", "resource", "external_job", "network"):
        lines.extend(["", "## " + kind_labels[kind], ""])
        items = [x for x in report["opportunities"] if x["kind"] == kind]
        lines.extend(f"- [{x['title']}]({x['url']})" for x in items)
        if not items:
            lines.append("此类别没有采集到已核实的职位；请查看上方渠道状态。")
    lines.extend(["", "校内岗位必须明确核实 Work-Study 资格。CURF 主要面向本科生；有薪工作资格和硕士生是否符合条件都需要确认。"])
    path = out / "latest.md"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--open-login-pages", action="store_true")
    parser.add_argument("--channel", action="append")
    args = parser.parse_args()
    report = run(load_config(args.config), open_pages=args.open_login_pages, only=args.channel)
    for row in report["channels"]:
        print(f"{row['name']}：{row['status']}（{row['items']} 条参考信息）")
    print(JOBBOT_OUTPUT / "penn_channels/latest.md")


if __name__ == "__main__":
    main()
