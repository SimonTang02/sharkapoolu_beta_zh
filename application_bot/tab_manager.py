#!/usr/bin/env python3
"""审计、认领并安全清理专用求职 Chrome 中的标签页。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
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
    canonical_url,
    cdp_target_id,
    ensure_browser_tab_schema,
    job_fingerprint,
    mark_application_tab_closed,
    register_application_page,
)
from job_bot.application_bot import resolve_browser_connection  # noqa: E402
from job_bot.applications.nvidia_workday import _playwright_api  # noqa: E402
from job_bot.browser_connection import check_cdp_health  # noqa: E402
from private_paths import APPLICATION_OUTPUT, CREDENTIALS_FILE, JOBBOT_OUTPUT  # noqa: E402
from job_bot.bot import connect_db, load_config, load_env_file  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
DEFAULT_ENV = CREDENTIALS_FILE
DEFAULT_OUT = APPLICATION_OUTPUT


@dataclass
class TabAssessment:
    target_id: str
    domain: str
    title: str
    url: str
    application_id: int | None
    application_status: str
    registered: bool
    form_activity: int
    session_anchor: bool
    exact_url_duplicates: int
    decision: str
    reason: str


def form_activity_score(page) -> int:
    """统计已填写的控件数量，但不返回任何字段值。"""
    try:
        return int(
            page.locator("body").evaluate(
                """body => [...body.querySelectorAll('input, textarea, select')]
                .filter(e => {
                  if (['hidden', 'submit', 'button'].includes(e.type)) return false;
                  if (['checkbox', 'radio'].includes(e.type)) return e.checked;
                  if (e.type === 'file') return e.files && e.files.length > 0;
                  return Boolean(e.value && e.value.trim());
                }).length"""
            )
        )
    except Exception:
        # 检查失败时按可能包含填写内容处理。
        return 1


def is_session_anchor(url: str, title: str) -> bool:
    parts = urlsplit(url)
    title_l = title.casefold()
    return bool(
        "/userhome" in parts.path.casefold()
        or "candidate home" in title_l
        or "candidate homepage" in title_l
    )


def application_candidates(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    rows = conn.execute(
        """
        SELECT applications.id AS application_id, applications.status,
               jobs.url, jobs.company, jobs.title
        FROM applications JOIN jobs ON jobs.id = applications.job_id
        WHERE applications.status NOT IN ('submitted', 'cancelled')
        """
    ).fetchall()
    index: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        index[job_fingerprint(str(row["url"]))].append(item)
    return index


def adopt_legacy_tabs(context, conn: sqlite3.Connection) -> list[dict]:
    """为每个未登记的申请匹配最合适的旧标签页。"""
    ensure_browser_tab_schema(conn)
    registered_targets = {
        str(row["target_id"])
        for row in conn.execute(
            "SELECT target_id FROM browser_tabs WHERE target_id IS NOT NULL"
        )
    }
    candidates = application_candidates(conn)
    grouped: dict[int, list[tuple[int, object, str, dict]]] = defaultdict(list)
    for page in context.pages:
        target_id = cdp_target_id(context, page)
        if not target_id or target_id in registered_targets:
            continue
        matches = candidates.get(job_fingerprint(page.url), [])
        if len(matches) != 1:
            continue
        item = matches[0]
        score = form_activity_score(page) * 10
        score += 3 if "/apply" in page.url.casefold() else 0
        score += 1 if "sign in" not in page.title().casefold() else 0
        grouped[int(item["application_id"])].append((score, page, target_id, item))

    adopted = []
    for application_id, options in grouped.items():
        _, page, target_id, item = sorted(options, key=lambda value: value[0])[-1]
        resolution = PageResolution(
            page=page,
            created=False,
            method="legacy_adoption",
            target_id=target_id,
            tab_label=f"jobbot-application-{application_id}",
        )
        register_application_page(
            conn,
            resolution,
            application_id=application_id,
            expected_url=str(item["url"]),
            browser_mode="windows_cdp",
        )
        adopted.append(
            {
                "application_id": application_id,
                "target_id": target_id,
                "company": item["company"],
                "title": item["title"],
                "url": page.url,
            }
        )
    return adopted


def assess_tabs(context, conn: sqlite3.Connection) -> list[tuple[object, TabAssessment]]:
    ensure_browser_tab_schema(conn)
    registrations = {
        str(row["target_id"]): dict(row)
        for row in conn.execute(
            """
            SELECT browser_tabs.*, applications.status AS application_status
            FROM browser_tabs JOIN applications
              ON applications.id = browser_tabs.application_id
            WHERE browser_tabs.target_id IS NOT NULL
            """
        )
    }
    pages = [page for page in context.pages if not page.is_closed()]
    duplicate_counts = Counter(canonical_url(page.url) for page in pages)
    page_metadata = []
    protected_urls: set[str] = set()
    for index, page in enumerate(pages):
        if page.is_closed():
            continue
        target_id = cdp_target_id(context, page)
        registration = registrations.get(target_id)
        activity = form_activity_score(page)
        try:
            title = page.title()
        except Exception:
            # 用户或门户可能会在 CDP
            # 页面列表快照和元数据采集之间关闭页面。此时已无
            # 浏览器状态需要保留或清理。
            continue
        anchor = is_session_anchor(page.url, title)
        normalized_url = canonical_url(page.url)
        if registration or activity or anchor:
            protected_urls.add(normalized_url)
        page_metadata.append(
            (
                index,
                page,
                target_id,
                registration,
                activity,
                title,
                anchor,
                normalized_url,
            )
        )

    # 如果某个完全相同的网址对应的所有标签页都可清理，则保留其中一个。
    # 已受保护的已登记/表单/会话页面已满足此要求。
    clean_duplicate_keepers: set[int] = set()
    for item in page_metadata:
        index, _, _, _, _, _, _, normalized_url = item
        if duplicate_counts[normalized_url] > 1 and normalized_url not in protected_urls:
            clean_duplicate_keepers.add(index)
            protected_urls.add(normalized_url)

    results = []
    for item in page_metadata:
        (
            index,
            page,
            target_id,
            registration,
            activity,
            title,
            anchor,
            normalized_url,
        ) = item
        duplicate_count = duplicate_counts[normalized_url]
        if registration and str(registration["application_status"] or "") == "submitted":
            decision = "close_submitted_explicit"
            reason = (
                "申请已记录为 submitted；保留截图证据后，仅通过 "
                "--close-submitted 关闭。"
            )
        elif registration:
            decision = "keep_registered"
            reason = "与申请关联的标签页；保留其 target ID 和当前页面状态。"
        elif anchor:
            decision = "keep_session_anchor"
            reason = "已认证的租户主页是共享登录会话的锚点。"
        elif activity:
            decision = "keep_possible_form_state"
            reason = "至少有一个表单控件已填写；关闭页面可能会丢失状态。"
        elif page.url in {"about:blank", "chrome://newtab/"}:
            decision = "close_safe"
            reason = "空白且未登记的标签页。"
        elif index in clean_duplicate_keepers:
            decision = "keep_duplicate_anchor"
            reason = "这是此确切网址最后一个干净副本；保留一个可导航页面。"
        elif duplicate_count > 1:
            decision = "close_safe"
            reason = "干净且未登记的网址重复页面。"
        else:
            decision = "close_clean_legacy_optional"
            reason = "干净的旧标签页；仅使用 --include-clean-legacy 时关闭。"
        assessment = TabAssessment(
            target_id=target_id,
            domain=urlsplit(page.url).hostname or "local",
            title=title,
            url=page.url,
            application_id=(
                int(registration["application_id"]) if registration else None
            ),
            application_status=(
                str(registration["application_status"] or "")
                if registration
                else ""
            ),
            registered=bool(registration),
            form_activity=activity,
            session_anchor=anchor,
            exact_url_duplicates=duplicate_count,
            decision=decision,
            reason=reason,
        )
        results.append((page, assessment))
    return results


def render_report(
    assessments: list[TabAssessment],
    *,
    adopted: list[dict],
    applied: bool,
    closed: list[dict],
    max_tabs: int,
) -> str:
    counts = Counter(item.decision for item in assessments)
    lines = [
        "# 专用 Chrome 标签页管理报告",
        "",
        f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"模式：{'已执行清理' if applied else '仅审计'}",
        f"观察到的已打开标签页：{len(assessments)}（配置上限：{max_tabs}）",
        f"已认领到申请注册表的旧标签页：{len(adopted)}",
        f"已关闭标签页：{len(closed)}",
        "",
        "## 决策计数",
        "",
    ]
    for key, value in sorted(counts.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## 标签页",
            "",
            "| 决策 | 申请 | 状态 | 表单活动 | 域名 | 标题 | 原因 |",
            "|---|---:|---|---:|---|---|---|",
        ]
    )
    for item in assessments:
        title = item.title.replace("|", "/")[:90]
        reason = item.reason.replace("|", "/")
        lines.append(
            f"| {item.decision} | {item.application_id or ''} | "
            f"{item.application_status} | {item.form_activity} | {item.domain} | "
            f"{title} | {reason} |"
        )
    if len(assessments) > max_tabs:
        lines.extend(
            [
                "",
                f"警告：浏览器标签页超出配置上限 {len(assessments) - max_tabs} 个。",
            ]
        )
    lines.extend(
        [
            "",
            "安全说明：默认清理策略绝不会关闭已填写的表单、已登记的申请或租户会话锚点。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--env", default=str(DEFAULT_ENV))
    parser.add_argument("--adopt", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-clean-legacy", action="store_true")
    parser.add_argument(
        "--close-submitted",
        action="store_true",
        help="关闭申请已提交的已登记标签页。",
    )
    args = parser.parse_args()

    load_env_file(Path(args.env))
    config = load_config(Path(args.config))
    mode, cdp_url = resolve_browser_connection(config)
    if mode != "windows_cdp":
        raise SystemExit("标签页管理器需要 application_browser.mode=windows_cdp")
    health = check_cdp_health(cdp_url)
    conn = connect_db(config)
    tab_config = config.get("tab_management", {})
    max_tabs = int(tab_config.get("max_active_tabs", 15))
    sync_playwright = _playwright_api()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(
            health.connect_url, timeout=30_000
        )
        context = browser.contexts[0]
        adopted = adopt_legacy_tabs(context, conn) if args.adopt else []
        pairs = assess_tabs(context, conn)
        assessments = [item for _, item in pairs]
        closed = []
        if args.apply:
            evidence_dir = JOBBOT_OUTPUT / "tab_management"
            for page, item in pairs:
                permitted = item.decision == "close_safe" or (
                    args.include_clean_legacy
                    and item.decision == "close_clean_legacy_optional"
                ) or (
                    args.close_submitted
                    and item.decision == "close_submitted_explicit"
                )
                if not permitted:
                    continue
                artifact = save_fill_test_artifact(
                    page,
                    evidence_dir,
                    adapter="tab_manager",
                    stage="before_close",
                    status=item.decision,
                    metadata={
                        "target_id": item.target_id,
                        "domain": item.domain,
                        "application_id": item.application_id,
                        "reason": item.reason,
                    },
                    # 展开安装了大量扩展的 Windows CDP 页面时可能会卡住。
                    # 有时间限制的视口截图足以作为
                    # 关闭标签页的证据，并能保证清理行为可预测。
                    use_cdp=False,
                    full_page=False,
                )
                page.close()
                if item.application_id is not None:
                    mark_application_tab_closed(conn, item.application_id)
                closed.append(
                    {
                        "target_id": item.target_id,
                        "title": item.title,
                        "screenshot_path": artifact["screenshot_path"],
                    }
                )

    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = DEFAULT_OUT / f"tab_management_{stamp}"
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "applied": args.apply,
        "include_clean_legacy": args.include_clean_legacy,
        "close_submitted": args.close_submitted,
        "max_active_tabs": max_tabs,
        "adopted": adopted,
        "closed": closed,
        "tabs": [asdict(item) for item in assessments],
    }
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(
        render_report(
            assessments,
            adopted=adopted,
            applied=args.apply,
            closed=closed,
            max_tabs=max_tabs,
        ),
        encoding="utf-8",
    )
    print(md_path)
    print(json_path)
    print(
        f"observed={len(assessments)} adopted={len(adopted)} closed={len(closed)} "
        f"budget={max_tabs}"
    )


if __name__ == "__main__":
    main()
