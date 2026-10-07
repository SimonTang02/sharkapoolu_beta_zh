#!/usr/bin/env python3
"""更新并呈现禁止提交申请批次的进度。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.application_bot import add_event  # noqa: E402
from job_bot.bot import connect_db, load_config, utc_now  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
from private_paths import APPLICATION_OUTPUT

DEFAULT_OUT = APPLICATION_OUTPUT


def set_company_status(
    conn: sqlite3.Connection,
    campaign_id: int,
    company: str,
    status: str,
    note: str,
) -> int:
    rows = conn.execute(
        """
        SELECT acj.job_id, acj.application_id
        FROM application_campaign_jobs acj
        JOIN jobs ON jobs.id = acj.job_id
        WHERE acj.campaign_id = ? AND lower(jobs.company) = lower(?)
        """,
        (campaign_id, company),
    ).fetchall()
    now = utc_now()
    for row in rows:
        conn.execute(
            """
            UPDATE application_campaign_jobs SET status = ?, last_error = ?
            WHERE campaign_id = ? AND job_id = ?
            """,
            (status, note or None, campaign_id, row["job_id"]),
        )
        if row["application_id"]:
            conn.execute(
                """
                UPDATE applications
                SET status = ?, notes = ?, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (status, note, now, row["application_id"]),
            )
            add_event(
                conn,
                row["application_id"],
                "campaign_progress",
                {"campaign_id": campaign_id, "status": status, "note": note},
            )
    conn.commit()
    return len(rows)


def render(conn: sqlite3.Connection, campaign_id: int) -> str:
    rows = conn.execute(
        """
        SELECT acj.rank, acj.status, acj.last_error, acj.application_id,
               jobs.company, jobs.title, jobs.location, jobs.url,
               applications.tailored_resume_path, applications.cover_letter_path,
               applications.draft_url
        FROM application_campaign_jobs acj
        JOIN jobs ON jobs.id = acj.job_id
        LEFT JOIN applications ON applications.id = acj.application_id
        WHERE acj.campaign_id = ? ORDER BY acj.rank
        """,
        (campaign_id,),
    ).fetchall()
    counts = Counter(row["status"] for row in rows)
    submitted_count = counts.get("submitted", 0)
    submission_summary = (
        f"本报告中有 {submitted_count} 份申请标记为已最终提交。"
        if submitted_count
        else "本报告中的申请均未最终提交。"
    )
    lines = [
        f"# 批次 {campaign_id} 申请进度",
        "",
        f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        submission_summary,
        "",
        "## 状态计数",
        "",
    ]
    for status, count in sorted(counts.items()):
        lines.append(f"- {status}: {count}")
    lines.extend(
        [
            "",
            "## 申请记录",
            "",
            "| 序号 | 公司 | 职位 | 地点 | 状态 | 简历/求职信 | 备注 |",
            "|---:|---|---|---|---|:---:|---|",
        ]
    )
    for row in rows:
        docs = "yes" if row["tailored_resume_path"] and row["cover_letter_path"] else "no"
        note = (row["last_error"] or "").replace("|", "/")
        lines.append(
            f"| {row['rank']} | {row['company']} | {row['title']} | "
            f"{row['location']} | {row['status']} | {docs} | {note} |"
        )
    return "\n".join(lines) + "\n"


def sync_application_statuses(conn: sqlite3.Connection, campaign_id: int) -> None:
    conn.execute(
        """
        UPDATE application_campaign_jobs
        SET status = (
          SELECT applications.status FROM applications
          WHERE applications.id = application_campaign_jobs.application_id
        )
        WHERE campaign_id = ?
          AND EXISTS (
            SELECT 1 FROM applications
            WHERE applications.id = application_campaign_jobs.application_id
              AND applications.status <> 'queued'
          )
        """,
        (campaign_id,),
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--campaign-id", type=int, default=2)
    parser.add_argument("--set-company")
    parser.add_argument("--status")
    parser.add_argument("--note", default="")
    parser.add_argument("--output")
    args = parser.parse_args()
    config = load_config(Path(args.config))
    conn = connect_db(config)
    sync_application_statuses(conn, args.campaign_id)
    if args.set_company:
        if not args.status:
            raise SystemExit("使用 --set-company 时必须同时提供 --status")
        count = set_company_status(
            conn, args.campaign_id, args.set_company, args.status, args.note
        )
        print(f"已更新批次中的 {count} 个岗位")
    output = (
        Path(args.output)
        if args.output
        else DEFAULT_OUT
        / f"campaign_{args.campaign_id:03d}_progress_{datetime.now():%Y%m%d_%H%M%S}.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(conn, args.campaign_id), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
