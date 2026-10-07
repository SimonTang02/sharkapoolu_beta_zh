#!/usr/bin/env python3
"""重新生成基于数据库的每周职位与申请报告。

每日增量文件保持独立。重新运行此模块时，会根据 SQLite 更新当前周报，
而不是追加重复的报告内容。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.bot import db_path, load_config  # noqa: E402
from job_bot.shared_database import connect as connect_database  # noqa: E402
from job_bot.strategy_report import Candidate, collect, configure_strategy  # noqa: E402
from private_paths import APPLICATION_OUTPUT, JOBBOT_OUTPUT  # noqa: E402
from job_bot.campus_employment_report import render_campus_employment  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot" / "config.china_hk_ic_foreign.json"

FUNNEL_GROUPS: dict[str, set[str]] = {
    "submitted": {"submitted"},
    "review_ready": {"draft_saved", "review_ready"},
    "in_progress": {
        "browser_form_started",
        "delivery_confirmation_required",
        "policy_consent_required",
        "profile_ready_final_only",
    },
    "blocked": {
        "authentication_required",
        "captcha_required",
        "external_redirect_unresolved",
        "job_detail_unresolved",
        "portal_unreachable",
        "qr_login_policy_required",
    },
    "queued": {"draft", "queued"},
}


def parse_timestamp(
    value: Any, default_tz: dt.tzinfo = dt.timezone.utc
) -> dt.datetime | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed


def week_window(
    moment: dt.datetime, timezone: ZoneInfo
) -> tuple[dt.datetime, dt.datetime]:
    local = moment.astimezone(timezone)
    start = (local - dt.timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start, start + dt.timedelta(days=7)


def in_window(value: Any, start: dt.datetime, end: dt.datetime) -> bool:
    parsed = parse_timestamp(value)
    if parsed is None:
        return False
    local = parsed.astimezone(start.tzinfo)
    return start <= local < end


def funnel_group(status: str) -> str:
    for group, statuses in FUNNEL_GROUPS.items():
        if status in statuses:
            return group
    return "other"


def filter_new(
    items: list[Candidate], start: dt.datetime, end: dt.datetime
) -> list[Candidate]:
    return [item for item in items if in_window(item.first_seen, start, end)]


def render_candidates(
    title: str, items: list[Candidate], limit: int
) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        return lines + ["- 本周暂无符合当前策略的新岗位。", ""]
    for item in items[:limit]:
        lines.extend(
            (
                f"- **{item.title} — {item.company}**",
                f"  - {item.location or '地点未标注'}；Tier {item.tier}；"
                f"{item.score}/100；`{item.foundation}`",
                f"  - {item.eligibility}；数据库岗位 ID：{item.job_id}",
                f"  - [官方职位页面]({item.url})",
            )
        )
    if len(items) > limit:
        lines.append(f"- 另有 {len(items) - limit} 条，详见同名 JSON 文件。")
    lines.append("")
    return lines


def source_health(
    conn: sqlite3.Connection, start: dt.datetime, end: dt.datetime
) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in conn.execute("SELECT * FROM scan_runs ORDER BY id"):
        if in_window(row["started_at"], start, end):
            grouped[str(row["source_name"] or "unknown")].append(row)
    result: list[dict[str, Any]] = []
    for name, runs in sorted(grouped.items()):
        last = runs[-1]
        errors = [
            str(run["error"] or "unknown error")
            for run in runs
            if run["status"] != "ok"
        ]
        result.append(
            {
                "source": name,
                "runs": len(runs),
                "successful_runs": sum(run["status"] == "ok" for run in runs),
                "failed_runs": len(errors),
                "jobs_seen_total": sum(int(run["jobs_seen"] or 0) for run in runs),
                "jobs_new_total": sum(int(run["jobs_new"] or 0) for run in runs),
                "latest_status": str(last["status"] or ""),
                "latest_error": errors[-1] if errors else "",
            }
        )
    return result


def latest_session_audit() -> dict[str, Any] | None:
    files = sorted(APPLICATION_OUTPUT.glob("session_audit_*.json"))
    if not files:
        return None
    try:
        payload = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["report_path"] = str(files[-1])
    return payload


def application_snapshot(
    conn: sqlite3.Connection, start: dt.datetime, end: dt.datetime
) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT a.id, a.status, a.submitted_at, a.updated_at,
               j.id AS job_id, j.company, j.title, j.location, j.url
        FROM applications AS a
        JOIN jobs AS j ON j.id = a.job_id
        ORDER BY a.id
        """
    ).fetchall()
    groups = Counter(funnel_group(str(row["status"] or "")) for row in rows)
    exact_blockers = Counter(
        str(row["status"] or "unknown")
        for row in rows
        if funnel_group(str(row["status"] or "")) == "blocked"
    )
    return {
        "total": len(rows),
        "funnel": dict(groups),
        "blocked_statuses": dict(exact_blockers),
        "submitted_this_week": [
            dict(row) for row in rows if in_window(row["submitted_at"], start, end)
        ],
        "changed_this_week": sum(
            in_window(row["updated_at"], start, end) for row in rows
        ),
        "review_ready": [
            dict(row)
            for row in rows
            if funnel_group(str(row["status"] or "")) == "review_ready"
        ],
    }


def job_snapshot(
    conn: sqlite3.Connection, start: dt.datetime, end: dt.datetime
) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(jobs)")}
    tracks_inactive_since = "inactive_since" in columns
    inactive_expression = (
        "inactive_since"
        if tracks_inactive_since
        else "NULL AS inactive_since"
    )
    rows = conn.execute(
        f"""
        SELECT id, company, title, location, url, role_kind, fit_score,
               source_name, first_seen, last_seen, is_active,
               {inactive_expression}
        FROM jobs
        """
    ).fetchall()
    inactive_this_week = [
        dict(row)
        for row in rows
        if int(row["is_active"] or 0) == 0
        and in_window(row["inactive_since"], start, end)
    ]
    inactive_this_week.sort(
        key=lambda row: (
            str(row.get("role_kind") or "") != "internship",
            -int(row.get("fit_score") or 0),
            str(row.get("company") or "").casefold(),
            str(row.get("title") or "").casefold(),
        )
    )
    return {
        "total": len(rows),
        "active": sum(int(row["is_active"] or 0) == 1 for row in rows),
        "new_this_week": sum(in_window(row["first_seen"], start, end) for row in rows),
        "detected_inactive_this_week": len(inactive_this_week),
        "inactive_this_week": inactive_this_week,
        "tracks_inactive_since": tracks_inactive_since,
    }


def render_inactive_jobs(items: list[dict[str, Any]], limit: int) -> list[str]:
    lines = ["## 本周检测为下线的岗位", ""]
    if not items:
        return lines + ["- 暂无具有可靠下线时间的新记录。", ""]
    internships = [row for row in items if row.get("role_kind") == "internship"]
    others = [row for row in items if row.get("role_kind") != "internship"]
    for heading, rows in (("实习", internships), ("正职/未分类", others)):
        if not rows:
            continue
        lines.extend((f"### {heading}（{len(rows)}）", ""))
        for row in rows[:limit]:
            lines.extend(
                (
                    f"- **{row.get('title') or '未命名岗位'} — "
                    f"{row.get('company') or '未知公司'}**",
                    f"  - {row.get('location') or '地点未标注'}；"
                    f"最后抓到：{row.get('last_seen') or '未知'}；"
                    f"来源：{row.get('source_name') or '未知'}",
                    f"  - [原职位页面]({row.get('url') or ''})",
                )
            )
        if len(rows) > limit:
            lines.append(f"- 另有 {len(rows) - limit} 条，详见同名 JSON 文件。")
        lines.append("")
    return lines


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def build_report(
    *,
    config: dict[str, Any],
    database: Path,
    output_dir: Path,
    now: dt.datetime,
) -> tuple[Path, Path, dict[str, Any]]:
    reporting = config.get("reporting", {})
    weekly = reporting.get("weekly", {})
    timezone_name = str(reporting.get("timezone", "Asia/Shanghai"))
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"未知的报告时区： {timezone_name}") from exc
    if str(weekly.get("week_start", "monday")).casefold() != "monday":
        raise ValueError("目前仅支持将 week_start 设为 Monday")
    start, end = week_window(now, timezone)
    limit = max(1, int(weekly.get("max_items_per_section", 25)))

    configure_strategy(config)
    conn = connect_database(database)
    strategy_result = collect(conn)
    if len(strategy_result) == 4:
        campus, _us_new_grads, us_summer, strategy_stats = strategy_result
    else:
        campus, us_summer, strategy_stats = strategy_result
    jobs = job_snapshot(conn, start, end)
    applications = application_snapshot(conn, start, end)
    sources = source_health(conn, start, end)
    conn.close()

    new_campus = filter_new(campus, start, end)
    new_us_summer = filter_new(us_summer, start, end)
    configured_sources = [
        source
        for source in config.get("sources", [])
        if source.get("enabled", True) is not False
    ]
    source_names_run = {row["source"] for row in sources}
    source_names_missing = sorted(
        str(source.get("name", ""))
        for source in configured_sources
        if str(source.get("name", "")) not in source_names_run
    )
    session = (
        latest_session_audit()
        if weekly.get("include_session_audit", True)
        else None
    )
    session_results = session.get("results", []) if session else []
    session_counts = Counter(
        str(row.get("state", "unknown")) for row in session_results
    )

    iso_year, iso_week, _ = start.date().isocalendar()
    period = f"{iso_year}-W{iso_week:02d}"
    md_path = output_dir / f"weekly_{period}.md"
    json_path = output_dir / f"weekly_{period}.json"
    generated_at = now.astimezone(timezone).isoformat(timespec="seconds")
    window_text = (
        f"{start.date().isoformat()} 至 "
        f"{(end - dt.timedelta(days=1)).date().isoformat()}"
    )

    lines = [
        f"# 求职周报 {period}",
        "",
        f"统计周期：{window_text}（{timezone_name}）  ",
        f"生成时间：{generated_at}",
        "",
        "> 本文件每次都从 SQLite 完整重建，不累加日报文本；同周重复运行会更新本文件，历史每日增量报告保持独立。",
        "",
        "## 本周总览",
        "",
        f"- 岗位库：{jobs['total']} 条；当前有效 {jobs['active']} 条。",
        f"- 本周首次发现：{jobs['new_this_week']} 条；本周检测为下线："
        f"{jobs['detected_inactive_this_week']} 条。",
        f"- 当前策略有效池：大陆/香港 2027 校招 {len(campus)} 条；"
        f"美国 Summer 2027 实习 {len(us_summer)} 条。",
        f"- 本周策略入选新增：大陆/香港 {len(new_campus)} 条；"
        f"美国暑期 {len(new_us_summer)} 条。",
        f"- 申请库：{applications['total']} 份；累计已提交 "
        f"{applications['funnel'].get('submitted', 0)} 份；本周提交 "
        f"{len(applications['submitted_this_week'])} 份。",
        "",
    ]
    campus_income_lines, campus_income = render_campus_employment(config, output_dir)
    lines += campus_income_lines
    lines += render_candidates(
        "本周新增：大陆/香港 2027 校招", new_campus, limit
    )
    lines += render_candidates(
        "本周新增：美国 Summer 2027 实习", new_us_summer, limit
    )
    lines += render_inactive_jobs(jobs["inactive_this_week"], limit)
    lines += [
        "## 申请漏斗（当前快照）",
        "",
        f"- 已提交：{applications['funnel'].get('submitted', 0)}",
        f"- 待最终审核：{applications['funnel'].get('review_ready', 0)}",
        f"- 填写中：{applications['funnel'].get('in_progress', 0)}",
        f"- 受阻：{applications['funnel'].get('blocked', 0)}",
        f"- 排队中：{applications['funnel'].get('queued', 0)}",
        f"- 其他状态：{applications['funnel'].get('other', 0)}",
        f"- 本周发生状态更新：{applications['changed_this_week']} 份",
        "",
    ]
    if applications["submitted_this_week"]:
        lines.extend(("### 本周已提交", ""))
        for row in applications["submitted_this_week"]:
            lines.append(
                f"- {row['company']} — {row['title']}（申请 ID：{row['id']}）"
            )
        lines.append("")
    if applications["review_ready"]:
        lines.extend(("### 等待你的最终审核", ""))
        for row in applications["review_ready"][:limit]:
            lines.append(
                f"- {row['company']} — {row['title']}"
                f"（`{row['status']}`，申请 ID：{row['id']}）"
            )
        lines.append("")
    if applications["blocked_statuses"]:
        lines.extend(("### 当前阻塞原因", ""))
        for status, count in sorted(applications["blocked_statuses"].items()):
            lines.append(f"- `{status}`：{count}")
        lines.append("")

    latest_ok = sum(row["latest_status"] == "ok" for row in sources)
    latest_failed = sum(row["latest_status"] != "ok" for row in sources)
    lines.extend(
        (
            "## 渠道健康度",
            "",
            f"- 已启用来源：{len(configured_sources)}；本周运行过："
            f"{len(sources)}；本周未运行：{len(source_names_missing)}。",
            f"- 各来源本周最后一次状态：成功 {latest_ok}；失败 {latest_failed}。",
            f"- 本周各轮扫描累计发现新记录："
            f"{sum(row['jobs_new_total'] for row in sources)}。",
            "",
        )
    )
    troubled = [row for row in sources if row["failed_runs"]]
    if troubled:
        lines.extend(("### 出现过失败的来源", ""))
        for row in troubled:
            recovered = (
                "，之后已恢复" if row["latest_status"] == "ok" else "，目前仍失败"
            )
            detail = " ".join(str(row["latest_error"]).split())[:180]
            lines.append(
                f"- {row['source']}：失败 {row['failed_runs']}/{row['runs']} 次"
                f"{recovered}；{detail}"
            )
        lines.append("")
    if source_names_missing:
        lines.extend(("<details>", "<summary>本周未运行的已启用来源</summary>", ""))
        lines.extend(f"- {name}" for name in source_names_missing)
        lines.extend(("", "</details>", ""))

    lines.extend(("## 登录状态（最近一次自动审计）", ""))
    if not session:
        lines.extend(("- 尚无登录状态审计记录。", ""))
    else:
        lines.append(
            f"- 审计时间：{session.get('generated_at', '未知')}；"
            f"浏览器：{session.get('browser', '未知')}。"
        )
        for state, count in sorted(session_counts.items()):
            lines.append(f"- `{state}`：{count}")
        action_required = [
            row
            for row in session_results
            if row.get("state")
            in {"authentication_required", "challenge_required"}
        ]
        if action_required:
            scopes = "、".join(
                str(row.get("scope") or row.get("company"))
                for row in action_required
            )
            lines.append(f"- 需要人工处理：{scopes}")
        lines.append("")

    lines.extend(
        (
            "## 口径说明",
            "",
            "- ‘首次发现’来自 `jobs.first_seen`；‘检测为下线’仅使用独立的 `jobs.inactive_since`，不会再被重新评分时间干扰，也不等价于公司正式宣布关闭。",
            "- 每日增量用于当天提醒；周报以数据库当前状态重建，因此能够吸收更正、去重及申请状态变化。",
            "- 最终提交仍必须由用户审核并完成。",
            "",
        )
    )

    payload = {
        "period": period,
        "generated_at": generated_at,
        "timezone": timezone_name,
        "window": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "jobs": jobs,
        "strategy": {
            "stats": strategy_stats,
            "active_cn_hk_campus": len(campus),
            "active_us_summer": len(us_summer),
            "new_cn_hk_campus": [asdict(item) for item in new_campus],
            "new_us_summer": [asdict(item) for item in new_us_summer],
        },
        "campus_employment": campus_income,
        "applications": applications,
        "sources": {"health": sources, "not_run": source_names_missing},
        "session_audit": session,
    }
    penn_report = output_dir / "penn_channels/latest.md"
    if config.get("penn_channels", {}).get("enabled", False) and penn_report.exists():
        lines.extend(["", "## Penn 校内工作、科研与职业资源", "",
                      "[查看渠道状态、待登录入口和机会清单](penn_channels/latest.md)。",
                      "校内岗位、科研线索和招聘活动单列，不计入上面的企业职位新增数；该清单显示自己的核验时间。", ""])
        payload["penn_channels_report"] = str(penn_report)
    markdown = "\n".join(lines)
    json_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    write_atomic(md_path, markdown)
    write_atomic(json_path, json_text)
    if weekly.get("write_latest_pointer", True):
        write_atomic(output_dir / "weekly_latest.md", markdown)
        write_atomic(output_dir / "weekly_latest.json", json_text)
    return md_path, json_path, payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="重新生成当前的数据库每周报告。"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--out-dir", type=Path, default=JOBBOT_OUTPUT)
    parser.add_argument(
        "--as-of",
        help="目标周内的 ISO 日期/时间；默认为当前时间",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    weekly = config.get("reporting", {}).get("weekly", {})
    if weekly.get("enabled", True) is False:
        print("配置已禁用每周报告")
        return 0
    now = parse_timestamp(args.as_of) if args.as_of else dt.datetime.now().astimezone()
    if now is None:
        raise SystemExit(f"--as-of 时间戳无效： {args.as_of}")
    md_path, json_path, payload = build_report(
        config=config,
        database=args.db or db_path(config),
        output_dir=args.out_dir,
        now=now,
    )
    print(md_path)
    print(json_path)
    print(
        f"period={payload['period']} new={payload['jobs']['new_this_week']} "
        f"submitted={payload['applications']['funnel'].get('submitted', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
