#!/usr/bin/env python3
"""记录候选人确认已手动提交的申请。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.application_bot import add_event  # noqa: E402
from job_bot.bot import connect_db, load_config, utc_now  # noqa: E402
from application_bot.batch_campaign import ensure_campaign_schema  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("application_ids", nargs="+", type=int)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument(
        "--source",
        default="user_confirmed_manual_submission",
        help="状态变更的审计来源。",
    )
    args = parser.parse_args()

    conn = connect_db(load_config(Path(args.config)))
    ensure_campaign_schema(conn)
    now = utc_now()
    updated: list[tuple[int, str, str]] = []
    for application_id in args.application_ids:
        row = conn.execute(
            """
            SELECT applications.id, applications.status, jobs.company, jobs.title
            FROM applications JOIN jobs ON jobs.id=applications.job_id
            WHERE applications.id=?
            """,
            (application_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"未知的申请 ID： {application_id}")

        conn.execute(
            """
            UPDATE applications
            SET status='submitted', submitted_at=COALESCE(submitted_at, ?),
                last_error=NULL, updated_at=?
            WHERE id=?
            """,
            (now, now, application_id),
        )
        conn.execute(
            """
            UPDATE application_campaign_jobs
            SET status='submitted', last_error=NULL
            WHERE application_id=?
            """,
            (application_id,),
        )
        add_event(
            conn,
            application_id,
            "manual_submission_confirmed",
            {
                "source": args.source,
                "previous_status": row["status"],
                "recorded_at": now,
            },
        )
        updated.append((application_id, row["company"], row["title"]))

    conn.commit()
    for application_id, company, title in updated:
        print(f"{application_id}\t{company}\t{title}\tsubmitted")


if __name__ == "__main__":
    main()
