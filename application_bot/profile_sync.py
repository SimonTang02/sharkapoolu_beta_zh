#!/usr/bin/env python3
"""刷新隔离的批次档案，但不改变申请状态。"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.application_bot import hydrate_known_resume_contacts  # noqa: E402
from cv.application_keywords import select_keywords, apply_keyword_selection  # noqa: E402
from job_bot.bot import connect_db, load_config, utc_now  # noqa: E402
from private_paths import (  # noqa: E402
    APPLICATION_OUTPUT,
    APPLICATION_PROFILE,
    CURRENT_RESUME_TEX,
)


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"
DEFAULT_MASTER = APPLICATION_PROFILE
DEFAULT_OUT = APPLICATION_OUTPUT


def nonempty(value) -> bool:
    return value not in (None, "", [], {})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", type=int, default=2)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--master", default=str(DEFAULT_MASTER))
    args = parser.parse_args()

    config = load_config(Path(args.config))
    conn = connect_db(config)
    master = json.loads(Path(args.master).read_text(encoding="utf-8"))
    master = hydrate_known_resume_contacts(
        master, CURRENT_RESUME_TEX.read_text(encoding="utf-8")
    )
    rows = conn.execute(
        """
        SELECT applications.id, applications.profile_path,
               applications.tailored_resume_path, applications.cover_letter_path,
               applications.status, jobs.role_kind, jobs.company, jobs.title, jobs.description
        FROM application_campaign_jobs
        JOIN applications ON applications.id = application_campaign_jobs.application_id
        JOIN jobs ON jobs.id = applications.job_id
        WHERE application_campaign_jobs.campaign_id = ?
        ORDER BY application_campaign_jobs.rank
        """,
        (args.campaign_id,),
    ).fetchall()
    if not rows:
        raise SystemExit(f"批次 {args.campaign_id} 没有申请记录")

    refreshed = []
    for row in rows:
        profile_path = Path(row["profile_path"])
        existing = (
            json.loads(profile_path.read_text(encoding="utf-8"))
            if profile_path.is_file()
            else {}
        )
        profile = copy.deepcopy(master)
        if "skills" in existing:
            profile["skills"] = copy.deepcopy(existing["skills"])
        if "application_keywords" in existing:
            profile["application_keywords"] = copy.deepcopy(existing["application_keywords"])
        profile = apply_keyword_selection(profile, select_keywords(row["title"] or "", row["description"] or ""))
        # 保留先前已填充或手动提供的非空字段。
        for key, value in existing.get("fields", {}).items():
            if nonempty(value):
                profile.setdefault("fields", {})[key] = value
        for section in ("custom_answers", "voluntary_disclosures"):
            for key, value in existing.get(section, {}).items():
                if nonempty(value):
                    profile.setdefault(section, {})[key] = value
        profile["documents"] = {
            "resume_path": row["tailored_resume_path"],
            "cover_letter_path": row["cover_letter_path"],
        }
        profile.setdefault("safety", {})["allow_submit"] = False
        if row["role_kind"] == "full_time":
            for education in profile.get("education", []):
                if "Pennsylvania" in str(education.get("school", "")):
                    education["to_month"] = 6
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        profile_path.chmod(0o600)
        refreshed.append(
            {
                "application_id": int(row["id"]),
                "company": row["company"],
                "title": row["title"],
                "role_kind": row["role_kind"],
                "status_preserved": row["status"],
                "education_records": len(profile.get("education", [])),
                "work_records": len(profile.get("work_experience", [])),
                "project_records": len(profile.get("projects", [])),
            }
        )

    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = DEFAULT_OUT / f"campaign_{args.campaign_id:03d}_profile_sync_{stamp}.json"
    output.write_text(
        json.dumps(
            {
                "generated_at": utc_now(),
                "campaign_id": args.campaign_id,
                "profiles_refreshed": len(refreshed),
                "application_statuses_changed": 0,
                "profiles": refreshed,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output)
    print(f"refreshed={len(refreshed)} statuses_changed=0")


if __name__ == "__main__":
    main()
