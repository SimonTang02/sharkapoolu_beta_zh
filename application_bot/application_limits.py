"""准备下一份申请前，检查雇主的申请数量限制。

限制是招聘周期的事实，不是候选人资历。应用类别限制前，请为每个岗位设置
``recruitment_category``。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class LimitCheck:
    application_id: int
    state: str
    category: str | None
    submitted_count: int
    max_applications: int | None
    remaining: int | None
    limit_id: int | None
    source_url: str | None


def check_application_limit(
    conn: sqlite3.Connection, application_id: int, *, today: date | None = None
) -> LimitCheck:
    """返回硬性阻止、建议警告或可继续状态。

    若硬性规则匹配到未分类岗位，则暂停处理并等待类别审阅；不得静默绕过限制。
    仅统计日期窗口内且符合招聘类别的既有提交记录。
    """
    today = today or date.today()
    row = conn.execute(
        """SELECT applications.id, applications.status, jobs.company,
                  jobs.recruitment_category
           FROM applications JOIN jobs ON jobs.id=applications.job_id
           WHERE applications.id=?""",
        (application_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"未知的申请 ID： {application_id}")
    category = row["recruitment_category"]
    default = LimitCheck(application_id, "ready", category, 0, None, None, None, None)
    limits = conn.execute(
        """SELECT * FROM application_limits
           WHERE lower(company)=lower(?) AND window_start<=? AND window_end>=?
           ORDER BY CASE enforcement WHEN 'hard' THEN 0 ELSE 1 END, id""",
        (row["company"], today.isoformat(), today.isoformat()),
    ).fetchall()
    if not limits:
        return default
    if not category and any(
        limit["enforcement"] == "hard" and limit["category"] != "all"
        for limit in limits
    ):
        return LimitCheck(application_id, "category_review_required", None, 0, None, None, None, None)

    warnings: list[LimitCheck] = []
    for limit in limits:
        if limit["category"] not in ("all", category):
            continue
        count = conn.execute(
            """SELECT count(DISTINCT applications.id)
               FROM applications JOIN jobs ON jobs.id=applications.job_id
               WHERE lower(jobs.company)=lower(?)
                 AND (?='all' OR jobs.recruitment_category=?)
                 AND applications.status='submitted'
                 AND substr(coalesce(applications.submitted_at,
                           applications.updated_at, applications.created_at),1,10)
                     BETWEEN ? AND ?""",
            (
                limit["company"], limit["category"], limit["category"],
                limit["window_start"], limit["window_end"],
            ),
        ).fetchone()[0]
        maximum = int(limit["max_applications"])
        remaining = max(0, maximum - count)
        if row["status"] == "submitted":
            state = "already_submitted"
        elif count >= maximum:
            state = "application_limit_reached" if limit["enforcement"] == "hard" else "limit_advisory"
        else:
            state = "ready"
        result = LimitCheck(
            application_id, state, category, count, maximum, remaining,
            int(limit["id"]), limit["source_url"],
        )
        if state == "application_limit_reached":
            return result
        warnings.append(result)
    return next((x for x in warnings if x.state != "ready"), warnings[0] if warnings else default)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("application_id", type=int)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    from job_bot.bot import connect_db, load_config

    config = args.config or Path(__file__).resolve().parents[1] / "job_bot/config.china_hk_ic_foreign.json"
    conn = connect_db(load_config(config))
    print(json.dumps(asdict(check_application_limit(conn, args.application_id)), ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
