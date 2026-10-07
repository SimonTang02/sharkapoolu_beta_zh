#!/usr/bin/env python3
"""使用在招职位评估评分配置，不写入数据库。"""

from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_bot.bot import JobPosting, db_path, load_config, score_job  # noqa: E402
from job_bot.shared_database import connect as connect_database  # noqa: E402
from private_paths import JOBBOT_OUTPUT  # noqa: E402


DEFAULT_CONFIG = ROOT / "job_bot/config.china_hk_ic_foreign.json"


def config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evaluate(config: dict[str, Any], database: Path) -> list[dict[str, Any]]:
    conn = connect_database(database)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, source_name, company, title, url, location, description,
               external_id, platform, role_kind, fit_score
        FROM jobs WHERE is_active = 1
        """
    ).fetchall()
    conn.close()
    results: list[dict[str, Any]] = []
    for row in rows:
        job = JobPosting(
            source_name=str(row["source_name"] or ""),
            company=str(row["company"] or ""),
            title=str(row["title"] or ""),
            url=str(row["url"] or ""),
            location=str(row["location"] or ""),
            description=str(row["description"] or ""),
            external_id=str(row["external_id"] or ""),
            platform=str(row["platform"] or ""),
            role_kind=str(row["role_kind"] or "unknown"),
        )
        score, reason = score_job(job, config)
        stored = int(row["fit_score"] or 0)
        results.append(
            {
                "job_id": int(row["id"]),
                "company": job.company,
                "title": job.title,
                "location": job.location,
                "role_kind": job.role_kind,
                "url": job.url,
                "stored_score": stored,
                "experimental_score": score,
                "delta": score - stored,
                "experimental_reason": reason,
            }
        )
    return results


def write_reports(
    config: dict[str, Any], results: list[dict[str, Any]], out_dir: Path
) -> tuple[Path, Path]:
    now = dt.datetime.now().astimezone()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    experiment = config.get("experiment") or {}
    name = str(experiment.get("name") or "unnamed")
    limit = max(1, int(config.get("experiment_tracking", {}).get("report_limit", 100)))
    changed = [row for row in results if row["delta"]]
    changed.sort(key=lambda row: (-abs(row["delta"]), -row["experimental_score"], row["job_id"]))
    top = sorted(
        results,
        key=lambda row: (-row["experimental_score"], row["company"].casefold(), row["title"].casefold()),
    )
    bands = config.get("scoring", {}).get("bands", {})
    high = int(bands.get("high", 75))
    relevant = int(bands.get("relevant", 60))
    adjacent = int(bands.get("adjacent", 45))

    def band(score: int) -> str:
        if score >= high:
            return "high"
        if score >= relevant:
            return "relevant"
        if score >= adjacent:
            return "adjacent"
        return "low"

    old_counts = Counter(band(row["stored_score"]) for row in results)
    new_counts = Counter(band(row["experimental_score"]) for row in results)
    digest = config_hash(config)
    lines = [
        f"# 评分实验：{name}",
        "",
        f"生成时间：{now.isoformat(timespec='seconds')}",
        f"生效配置 SHA-256：`{digest}`",
        "",
        "本报告为只读模拟，未修改已存评分、职位生命周期字段或申请记录。",
        "",
        "## 假设",
        "",
        str(experiment.get("hypothesis") or "未提供说明。"),
        "",
        "## 区间分布对比",
        "",
        "| 区间 | 当前 | 实验 | 变化 |",
        "|---|---:|---:|---:|",
    ]
    for key in ("high", "relevant", "adjacent", "low"):
        lines.append(
            f"| {key} | {old_counts[key]} | {new_counts[key]} | {new_counts[key] - old_counts[key]:+d} |"
        )
    lines.extend(("", f"评分发生变化的职位：{len(changed)}/{len(results)}", "", "## 变化最大的职位", ""))
    for row in changed[:limit]:
        lines.append(
            f"- `{row['delta']:+d}` {row['stored_score']}→{row['experimental_score']} — "
            f"**{row['title']} — {row['company']}**（职位 {row['job_id']}）"
        )
    if not changed:
        lines.append("- 评分没有变化。")
    lines.extend(("", "## 实验评分最高的职位", ""))
    for row in top[:limit]:
        lines.append(
            f"- {row['experimental_score']} ({band(row['experimental_score'])}) — "
            f"**{row['title']} — {row['company']}**（职位 {row['job_id']}）"
        )
    lines.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"scoring_experiment_{name}_{stamp}.md"
    json_path = out_dir / f"scoring_experiment_{name}_{stamp}.json"
    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "experiment": experiment,
        "effective_config_sha256": digest,
        "read_only": True,
        "bands": {"stored": dict(old_counts), "experimental": dict(new_counts)},
        "results": results,
    }
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return md_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--out-dir", type=Path, default=JOBBOT_OUTPUT)
    args = parser.parse_args()
    config = load_config(args.config)
    results = evaluate(config, args.db or db_path(config))
    md_path, json_path = write_reports(config, results, args.out_dir)
    print(md_path)
    print(json_path)
    print(f"evaluated={len(results)} changed={sum(bool(row['delta']) for row in results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
