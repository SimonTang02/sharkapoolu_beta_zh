#!/usr/bin/env python3
"""呈现当前 Foundation 评分策略和匹配度最高的职位。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CONFIG = ROOT / "job_bot" / "config.china_hk_ic_foreign.json"
from private_paths import JOBBOT_OUTPUT  # noqa: E402
from job_bot.bot import db_path, load_config  # noqa: E402
from job_bot.shared_database import connect as connect_database  # noqa: E402

DEFAULT_OUT = JOBBOT_OUTPUT


def score_band(score: int | None, bands: dict | None = None) -> str:
    score = score or 0
    bands = bands or {}
    if score >= int(bands.get("high", 75)):
        return "high"
    if score >= int(bands.get("relevant", 60)):
        return "relevant"
    if score >= int(bands.get("adjacent", 45)):
        return "adjacent"
    return "low"


def render(config: dict, rows: list[sqlite3.Row], generated_at: str) -> str:
    scoring = config["scoring"]
    bands = scoring.get("bands", {})
    lines = [
        "# Foundation 评分报告",
        "",
        f"生成时间：{generated_at}",
        "",
        "Foundation 用于定义岗位的核心职业方向。一个职位必须至少匹配一个 Foundation，"
        "之后简历证据或偏好修正项才会参与提升其评分。",
        "",
        "## 评分区间",
        "",
        f"- **{bands.get('high', 75)}–100 — high：通过早期职业阶段检查后，可进入材料草拟/申请队列。",
        f"- **{bands.get('relevant', 60)}–{int(bands.get('high', 75)) - 1} — relevant：较匹配的相邻岗位，需人工审阅。",
        f"- **{bands.get('adjacent', 45)}–{int(bands.get('relevant', 60)) - 1} — adjacent：保留在摘要中，但未达到自动草拟门槛。",
        f"- **0–{int(bands.get('adjacent', 45)) - 1} — low：从重点申请队列中隐藏。",
        "",
        "## Foundation 基础方向",
        "",
        "| Foundation | 基础分 | 仅职位描述修正分 | 职位描述最低匹配数 |",
        "|---|---:|---:|---:|",
    ]
    for group in scoring["foundation_groups"]:
        lines.append(
            f"| {group['name']} | {group['base_score']} | "
            f"{group.get('body_only_adjustment', 0):+d} | "
            f"{group.get('min_body_hits', 1)} |"
        )
    lines.extend(["", "## Foundation 关键词", ""])
    for group in scoring["foundation_groups"]:
        lines.extend(
            [
                f"### {group['name']}",
                "",
                ", ".join(f"`{item}`" for item in group["keywords"]),
                "",
            ]
        )
    lines.extend(
        [
            "## 修正项",
            "",
            "| 修正项 | 分值 | 范围 | 关键词 |",
            "|---|---:|---|---|",
        ]
    )
    for modifier in scoring["modifiers"]:
        keywords = ", ".join(f"`{item}`" for item in modifier["keywords"])
        lines.append(
            f"| {modifier['name']} | {modifier['points']:+d} | "
            f"{modifier.get('scope', 'all')} | {keywords} |"
        )
    lines.extend(
        [
            "",
            "## 当前匹配度最高的在招职位",
            "",
            "| 评分 | 区间 | 岗位类型 | 公司 | 职位 | 地点 | Foundation/原因 |",
            "|---:|---|---|---|---|---|---|",
        ]
    )
    for row in rows:
        title = (row["title"] or "").replace("|", "/")
        company = (row["company"] or "").replace("|", "/")
        location = (row["location"] or "").replace("|", "/")
        reason = (row["score_reason"] or "").replace("|", "/")
        score = row["fit_score"] or 0
        lines.append(
            f"| {score} | {score_band(score, bands)} | {row['role_kind'] or 'unknown'} | "
            f"{company} | {title} | {location} | {reason} |"
        )
    lines.extend(
        [
            "",
            "## 当前简历对应的偏好策略",
            "",
            "数字 RTL 设计和 CPU/计算机体系结构获得最高基础分。验证和 EDA 仍是可考虑的相邻方向。物理设计/DFT 与模拟/混合信号方向仍可检索，但除非有特别有力的证据，否则优先级低于直接匹配数字设计或体系结构的岗位。资深、非设计和以软件为主的职位标题会受到明确扣分。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    conn = connect_database(db_path(config))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT company, title, location, role_kind, fit_score, score_reason
        FROM jobs
        WHERE is_active = 1 AND fit_score IS NOT NULL
        ORDER BY fit_score DESC, company, title
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"foundation_scoring_{datetime.now():%Y%m%d_%H%M%S}.md"
    output.write_text(render(config, rows, generated_at) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
