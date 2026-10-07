#!/usr/bin/env python3
"""创建明确指定或按策略筛选的禁止提交申请批次。"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cv.application_keywords import select_keywords, apply_keyword_selection  # noqa: E402
from cv.bot.bot import (  # noqa: E402
    DEFAULT_PROFILE as EVIDENCE_PROFILE,
    render_report,
    safe_slug,
    select_evidence,
    write_bundle,
)
from job_bot.application_bot import (  # noqa: E402
    DEFAULT_PROFILE as APPLICATION_PROFILE,
    add_event,
    hydrate_known_resume_contacts,
)
from job_bot.bot import connect_db, load_config, utc_now  # noqa: E402
from job_bot.shared_database import connect as connect_database  # noqa: E402
from job_bot.strategy_report import (  # noqa: E402
    collect as collect_strategy_jobs,
    configure_strategy,
)


DEFAULT_CONFIG = ROOT / "job_bot" / "config.china_hk_ic_foreign.json"
from private_paths import (
    APPLICATION_OUTPUT,
    CURRENT_RESUME_TEX,
    CV_VARIANTS_DIR,
    JOB_DATABASE,
)

DEFAULT_OUT = APPLICATION_OUTPUT
DEFAULT_BUNDLES = CV_VARIANTS_DIR


@dataclass(frozen=True)
class Candidate:
    job_id: int
    company: str
    title: str
    location: str
    url: str
    role_kind: str
    score: int
    score_reason: str
    platform: str

    @property
    def foundation(self) -> str:
        match = re.search(r"Foundation:\s*([^;(]+)", self.score_reason)
        return match.group(1).strip() if match else "unknown"


def normalized_role_key(candidate: Candidate) -> tuple[str, str, str]:
    return (
        candidate.company.casefold(),
        re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", candidate.title.casefold()),
        re.sub(r"\s+", " ", candidate.location.casefold()).strip(),
    )


def direction_priority(reason: str) -> int:
    reason_l = reason.casefold()
    if any(term in reason_l for term in (
        "cpu and computer architecture", "digital rtl design", "architecture",
        "digital_design", "accelerator_design", "fpga_design",
    )):
        return 0
    if any(term in reason_l for term in ("eda and logic synthesis", "synthesis_sta")):
        return 1
    if "rtl and silicon verification" in reason_l:
        return 3
    return 2


def select_candidates(
    conn: sqlite3.Connection, limit: int, min_score: int, config: dict | None = None
) -> list[Candidate]:
    """中国/香港校园批次使用与审阅报告相同的策略。"""
    configure_strategy(config or load_config(DEFAULT_CONFIG))
    campus_jobs, _, _, _ = collect_strategy_jobs(conn)
    candidates = [
        Candidate(
            job_id=item.job_id,
            company=item.company,
            title=item.title,
            location=item.location,
            url=item.url,
            role_kind="full_time",
            score=item.score,
            score_reason=f"Foundation: {item.foundation}",
            platform=urlsplit(item.url).hostname or "",
        )
        for item in campus_jobs
        if item.score >= min_score and item.url
    ]

    candidates.sort(
        key=lambda item: (
            direction_priority(item.score_reason),
            -item.score,
            0 if item.role_kind == "internship" else 1,
            item.company.casefold(),
            item.title.casefold(),
        )
    )
    selected: list[Candidate] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        key = normalized_role_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def load_campaign_candidates(
    conn: sqlite3.Connection, campaign_id: int
) -> list[Candidate]:
    rows = conn.execute(
        """
        SELECT jobs.id, jobs.company, jobs.title, jobs.location, jobs.url,
               jobs.role_kind, jobs.fit_score, jobs.score_reason, jobs.platform
        FROM application_campaign_jobs
        JOIN jobs ON jobs.id = application_campaign_jobs.job_id
        WHERE application_campaign_jobs.campaign_id = ?
        ORDER BY application_campaign_jobs.rank
        """,
        (campaign_id,),
    ).fetchall()
    return [
        Candidate(
            job_id=int(row["id"]),
            company=str(row["company"] or "Unknown"),
            title=str(row["title"] or "Untitled"),
            location=str(row["location"] or ""),
            url=str(row["url"]),
            role_kind=str(row["role_kind"] or "unknown"),
            score=int(row["fit_score"] or 0),
            score_reason=str(row["score_reason"] or ""),
            platform=str(row["platform"] or urlsplit(str(row["url"])).hostname or ""),
        )
        for row in rows
    ]


def load_explicit_candidates(
    conn: sqlite3.Connection, job_ids: list[int]
) -> list[Candidate]:
    """载入按序排列的已审阅岗位清单；遇到问题时直接失败，不静默跳过 ID。"""
    candidates: list[Candidate] = []
    seen: set[int] = set()
    for job_id in job_ids:
        if job_id in seen:
            raise ValueError(f"明确指定的岗位 ID 重复：{job_id}")
        seen.add(job_id)
        row = conn.execute(
            """
            SELECT id, company, title, location, url, role_kind, fit_score,
                   score_reason, platform, is_active
            FROM jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"明确指定的岗位 ID 不存在：{job_id}")
        if int(row["is_active"] or 0) != 1:
            raise ValueError(f"明确指定的岗位 ID 已失效：{job_id}")
        if not str(row["url"] or "").strip():
            raise ValueError(f"明确指定的岗位 ID 没有申请网址：{job_id}")
        candidates.append(
            Candidate(
                job_id=int(row["id"]),
                company=str(row["company"] or "Unknown"),
                title=str(row["title"] or "Untitled"),
                location=str(row["location"] or ""),
                url=str(row["url"]),
                role_kind=str(row["role_kind"] or "unknown"),
                score=int(row["fit_score"] or 0),
                score_reason=str(row["score_reason"] or ""),
                platform=str(row["platform"] or urlsplit(str(row["url"])).hostname or ""),
            )
        )
    return candidates


def ensure_campaign_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS application_campaigns (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          target_count INTEGER NOT NULL,
          geographic_scope TEXT NOT NULL,
          min_score INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'planned',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS application_campaign_jobs (
          campaign_id INTEGER NOT NULL REFERENCES application_campaigns(id),
          job_id INTEGER NOT NULL REFERENCES jobs(id),
          application_id INTEGER REFERENCES applications(id),
          rank INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'selected',
          material_bundle TEXT,
          last_error TEXT,
          PRIMARY KEY(campaign_id, job_id)
        );
        """
    )


def create_campaign(
    conn: sqlite3.Connection,
    candidates: list[Candidate],
    *,
    name: str,
    target_count: int,
    min_score: int,
    geographic_scope: str = "mainland_china,hong_kong",
) -> int:
    now = utc_now()
    campaign_id = conn.execute(
        """
        INSERT INTO application_campaigns(
          name, target_count, geographic_scope, min_score, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'selected', ?, ?)
        """,
        (name, target_count, geographic_scope, min_score, now, now),
    ).lastrowid
    for rank, candidate in enumerate(candidates, start=1):
        existing = conn.execute(
            """
            SELECT id, status FROM applications
            WHERE job_id = ? AND status NOT IN ('failed', 'cancelled')
            ORDER BY id DESC LIMIT 1
            """,
            (candidate.job_id,),
        ).fetchone()
        if existing:
            application_id = int(existing["id"])
        else:
            application_id = conn.execute(
                """
                INSERT INTO applications(
                  job_id, status, profile_path, notes, created_at, updated_at
                ) VALUES (?, 'queued', ?, ?, ?, ?)
                """,
                (
                    candidate.job_id,
                    str(APPLICATION_PROFILE),
                    f"Campaign {campaign_id}: {geographic_scope} no-submit batch",
                    now,
                    now,
                ),
            ).lastrowid
            add_event(
                conn,
                application_id,
                "queued",
                {"campaign_id": campaign_id, "rank": rank, "submit_allowed": False},
            )
        conn.execute(
            """
            INSERT INTO application_campaign_jobs(
              campaign_id, job_id, application_id, rank, status
            ) VALUES (?, ?, ?, ?, 'selected')
            """,
            (campaign_id, candidate.job_id, application_id, rank),
        )
    conn.commit()
    return int(campaign_id)


def render_campaign(
    campaign_id: int,
    candidates: list[Candidate],
    app_ids: dict[int, int],
    geographic_scope: str = "mainland_china,hong_kong",
) -> str:
    lines = [
        f"# 申请批次 {campaign_id}",
        "",
        f"范围：`{geographic_scope}`",
        "",
        "安全提示：保存草稿，或在最终提交前停止。最终提交功能已禁用。",
        "",
        "| 排名 | 分数 | 类型 | 职位方向 | 公司 | 岗位 | 地点 | 平台 | 申请编号 |",
        "|---:|---:|---|---|---|---|---|---|---:|",
    ]
    for rank, item in enumerate(candidates, start=1):
        values = [item.company, item.title, item.location, item.platform, item.foundation]
        company, title, location, platform, foundation = (
            value.replace("|", "/") for value in values
        )
        lines.append(
            f"| {rank} | {item.score} | {item.role_kind} | {foundation} | "
            f"{company} | {title} | {location} | {platform} | {app_ids[item.job_id]} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_one_material(
    candidate: Candidate,
    application_id: int,
    evidence_profile: dict,
    application_profile: dict,
) -> tuple[int, int, str, str, str]:
    description_conn = connect_database(JOB_DATABASE)
    row = description_conn.execute(
        "SELECT description FROM jobs WHERE id = ?", (candidate.job_id,)
    ).fetchone()
    description_conn.close()
    job_text = "\n".join(
        value
        for value in (candidate.title, candidate.location, str(row[0] or ""))
        if value
    )
    matches = select_evidence(evidence_profile, job_text)
    keywords = select_keywords(candidate.title, job_text)
    review = render_report(
        evidence_profile,
        candidate.company,
        candidate.title,
        job_text,
        matches,
        (
            "current.tex（毕业时间来自私有证据档案）"
            if candidate.role_kind == "full_time"
            else "current.tex"
        ),
        keyword_selection=keywords,
    )
    bundle_name = (
        f"job-{candidate.job_id}_{safe_slug(candidate.company)}_"
        f"{safe_slug(candidate.title)}"
    )
    bundle = write_bundle(
        evidence_profile,
        candidate.company,
        candidate.title,
        job_text,
        matches,
        review,
        CURRENT_RESUME_TEX,
        DEFAULT_BUNDLES,
        bundle_name=bundle_name,
        graduation_date=evidence_profile.get("expected_graduation_date") or None,
        role_kind=candidate.role_kind,
        keyword_selection=keywords,
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    isolated = copy.deepcopy(application_profile)
    isolated = apply_keyword_selection(isolated, keywords)
    isolated["documents"]["resume_path"] = manifest["resume_pdf"]
    isolated["documents"]["cover_letter_path"] = manifest["cover_letter_pdf"]
    isolated.setdefault("safety", {})["allow_submit"] = False
    output_dir = ROOT / "job_bot" / "out" / "applications" / str(application_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_path = output_dir / "profile.json"
    profile_path.write_text(
        json.dumps(isolated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    profile_path.chmod(0o600)
    return (
        candidate.job_id,
        application_id,
        str(bundle),
        manifest["resume_pdf"],
        manifest["cover_letter_pdf"],
    )


def prepare_materials(
    conn: sqlite3.Connection,
    campaign_id: int,
    candidates: list[Candidate],
    workers: int,
) -> None:
    evidence_profile = json.loads(EVIDENCE_PROFILE.read_text(encoding="utf-8"))
    application_profile = hydrate_known_resume_contacts(
        json.loads(APPLICATION_PROFILE.read_text(encoding="utf-8")),
        CURRENT_RESUME_TEX.read_text(encoding="utf-8"),
    )
    app_rows = conn.execute(
        """
        SELECT job_id, application_id, status
        FROM application_campaign_jobs WHERE campaign_id = ?
        """,
        (campaign_id,),
    ).fetchall()
    app_ids = {int(row["job_id"]): int(row["application_id"]) for row in app_rows}
    pending_ids = {
        int(row["job_id"])
        for row in app_rows
        if row["status"] != "materials_ready"
    }
    pending_candidates = [
        candidate for candidate in candidates if candidate.job_id in pending_ids
    ]
    failures: list[tuple[int, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                build_one_material,
                candidate,
                app_ids[candidate.job_id],
                evidence_profile,
                application_profile,
            ): candidate
            for candidate in pending_candidates
        }
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                job_id, application_id, bundle, resume, cover = future.result()
                now = utc_now()
                conn.execute(
                    """
                    UPDATE applications
                    SET tailored_resume_path = ?, cover_letter_path = ?, profile_path = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        resume,
                        cover,
                        str(ROOT / "job_bot" / "out" / "applications" / str(application_id) / "profile.json"),
                        now,
                        application_id,
                    ),
                )
                conn.execute(
                    """
                    UPDATE application_campaign_jobs
                    SET status = 'materials_ready', material_bundle = ?, last_error = NULL
                    WHERE campaign_id = ? AND job_id = ?
                    """,
                    (bundle, campaign_id, job_id),
                )
                add_event(
                    conn,
                    application_id,
                    "materials_prepared",
                    {"campaign_id": campaign_id, "bundle": bundle},
                )
                conn.commit()
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[-1200:]}"
                failures.append((candidate.job_id, error))
                conn.execute(
                    """
                    UPDATE application_campaign_jobs
                    SET status = 'material_failed', last_error = ?
                    WHERE campaign_id = ? AND job_id = ?
                    """,
                    (error, campaign_id, candidate.job_id),
                )
                conn.commit()
    status = "materials_ready" if not failures else "material_partial"
    conn.execute(
        "UPDATE application_campaigns SET status = ?, updated_at = ? WHERE id = ?",
        (status, utc_now(), campaign_id),
    )
    conn.commit()
    if failures:
        raise RuntimeError(f"为 {len(failures)} 个岗位生成材料失败")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--target", type=int, default=100)
    parser.add_argument("--min-score", type=int, default=60)
    parser.add_argument("--name", default="cn-hk-100-no-submit")
    parser.add_argument("--prepare-materials", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--campaign-id", type=int)
    parser.add_argument(
        "--job-id",
        action="append",
        type=int,
        default=[],
        help="按给定顺序使用此确切的有效岗位 ID；可重复指定以建立已审阅队列",
    )
    parser.add_argument(
        "--scope",
        default="mainland_china,hong_kong",
        help="保存在数据库中的批次范围标签",
    )
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    conn = connect_db(config)
    ensure_campaign_schema(conn)
    if args.campaign_id and args.job_id:
        raise SystemExit("--campaign-id 和 --job-id 不能同时使用")
    geographic_scope = args.scope
    if args.campaign_id:
        campaign_id = args.campaign_id
        candidates = load_campaign_candidates(conn, campaign_id)
        if not candidates:
            raise SystemExit(f"批次 {campaign_id} 不存在或没有岗位")
        scope_row = conn.execute(
            "SELECT geographic_scope FROM application_campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()
        if scope_row:
            geographic_scope = str(scope_row["geographic_scope"])
    else:
        if args.job_id:
            candidates = load_explicit_candidates(conn, args.job_id)
            target_count = len(candidates)
        else:
            candidates = select_candidates(conn, args.target, args.min_score, config)
            target_count = args.target
            if len(candidates) < args.target:
                raise SystemExit(
                    f"仅有 {len(candidates)} 个符合当前策略的中国/香港岗位具备资格"
                )
        campaign_id = create_campaign(
            conn,
            candidates,
            name=args.name,
            target_count=target_count,
            min_score=args.min_score,
            geographic_scope=geographic_scope,
        )
    app_rows = conn.execute(
        "SELECT job_id, application_id FROM application_campaign_jobs WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchall()
    app_ids = {int(row["job_id"]): int(row["application_id"]) for row in app_rows}
    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    if not args.campaign_id:
        output = DEFAULT_OUT / f"campaign_{campaign_id:03d}_{datetime.now():%Y%m%d_%H%M%S}.md"
        output.write_text(
            render_campaign(campaign_id, candidates, app_ids, geographic_scope),
            encoding="utf-8",
        )
        print(f"已为批次 {campaign_id} 选中 {len(candidates)} 个岗位")
        print(output)
    if args.prepare_materials:
        prepare_materials(conn, campaign_id, candidates, args.workers)
        print(f"已为批次 {campaign_id} 准备材料")


if __name__ == "__main__":
    main()
